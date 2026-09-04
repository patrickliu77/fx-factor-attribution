"""Data aggregation for the News and Attribution pages (SPEC_web §2.3).

Reads only `outputs/narrative/` and the contract snapshot already in memory, and
produces no new attribution numbers.

**News is evidence, not allocation (user ruling 2026-09-02, replacing the earlier
equal-split rule).**
Earlier versions split a trigger day's |residual| evenly across the cited stories
and gave each story a share. The user ruled it out: an even same-day split is a
convention, not a measurement; in the degenerate case (a single trigger day)
every story gets the same share, so the number has no discriminating power and
still invites a causal reading. Now each story carries only the pair-day evidence
it was cited for -- which day, which pair, how many bp of residual, what z --
all of them measurements, presented on the page in contemporaneous-associative-
evidence wording. Words like share, contribution or explained by, which read as
causal attribution, are forbidden.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..config import OUTPUT_DIR

log = logging.getLogger(__name__)

NARRATIVE_DIR = OUTPUT_DIR / "narrative"
WEEK_DAYS = 5
TOP_STORIES = 5

# factor buckets. The design draft gave four buckets, Rates / Risk / Commodities
# / Residual, but this model's two systematic factors DOLLAR_LOO and CARRY_LOO fit
# none of them, and they are usually the largest slice. Forcing them in would
# silently drop the biggest contribution, so split into five segments and keep the
# colors synonymous with the FX page's three: purple = systematic, lime = residual,
# blue/cyan/green for the three in between.
BUCKETS = (
    ("systematic", "Dollar and carry", ("DOLLAR_LOO", "CARRY_LOO")),
    ("rates", "Rates", ("d2Y_DIFF", "d10Y_DIFF")),
    ("risk", "Risk", ("dVIX", "EMB", "dHY_OAS", "HY_EXCESS", "dBAA10Y")),
    ("commodities", "Commodities", ("WTI", "BRENT", "GOLD", "COPPER")),
)


# -------------------------------------------------------- content type kinds
# display-layer filter (user ruling 2026-09-02): the headline section keeps event
# stories only; opinion, analysis, market recaps and trade views go into the
# collapsed section. Classified by **content** (title marker words), not by
# source: the WSJ also publishes Opinion, and small sites also report events.
# **Applies to the display layer only, the narrative layer's retrieval input is
# untouched** -- the narrative layer has its own six validation checks, and
# after-the-fact analysis is useful there (in the 07-31 case it was exactly a few
# Analysis pieces that helped the model judge the intervention size). The display
# layer needs to be clean, the narrative layer needs to be complete; different
# requirements (SPEC_web §2.10).
OPINION_TITLE_RE = re.compile(
    r"(\bopinions?\b|\banalysis\b|\bcommentary\b|\bcolumn\b|\beditorial\b"
    r"|\bsetups?\b|\boutlook\b|price action|\beyes\b|\bcould\b|\bwhy\b"
    r"|\bforecasts?\b|\bpreview\b|\btechnicals?\b|week ahead|what to watch"
    r"|\bpredictions?\b|live updates|\bchart\b"
    # technical-level markers (2026-09-02 ruling, narrow additions only): words
    # that appear almost exclusively in technical-analysis titles. Deliberately
    # excludes ordinary words like low, high, level, which event stories use just
    # as often and would misfire.
    # support matches phrases only (narrowed 2026-09-02): bare "support" in its
    # fundamental sense (carry support, US support) is extremely common in event
    # stories -- the BBH item was caught this way in practice
    r"|support holds|support levels?|finds support|tests support|breaks support"
    r"|\bresistance\b|key levels?|\bpivot\b"
    r"|retracements?|fibonacci)",
    re.I)


def story_kind(title: str | None) -> str:
    """Title -> "event" or "opinion". A marker-word hit or a trailing question
    mark classifies it as opinion."""
    text = (title or "").strip()
    if OPINION_TITLE_RE.search(text) or text.rstrip().endswith("?"):
        return "opinion"
    return "event"


# ----------------------------------------------------- near-duplicate titles
_TOKEN_RE = re.compile(r"[^a-z0-9 ]")
SIMILAR_JACCARD = 0.8


def title_tokens(title: str | None) -> frozenset:
    """Title -> word set. Single-character tokens are dropped: spelling
    differences like U.S. vs US should not block dedup."""
    text = _TOKEN_RE.sub(" ", (title or "").lower())
    return frozenset(w for w in text.split() if len(w) > 1)


def similar_titles(a: frozenset, b: frozenset) -> bool:
    """Jaccard near-duplicate. Real case: "U.S. dollar weakens sharply against
    the Japanese yen after market interventions" and "US dollar weakens ..." were
    two outlets running the same event; user ruling: show one item per event
    (2026-09-02)."""
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    return union > 0 and inter / union >= SIMILAR_JACCARD


def _bucket_of(factor: str) -> str:
    for key, _label, members in BUCKETS:
        if factor in members:
            return key
    return "risk"  # a new factor lands in risk first, visible in the logs as soon as it appears


def load_days(root: Path | None = None, limit: int | None = None) -> list[dict]:
    """Read narrative artifacts back in ascending date order. Unreadable means
    empty, nothing is raised."""
    root = Path(root or NARRATIVE_DIR)
    if not root.exists():
        return []
    out = []
    for path in sorted(root.glob("date=*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("narrative artifact unreadable %s: %s", path.name, exc)
    return out[-limit:] if limit else out


def _sources_by_id(record: dict) -> dict:
    return {s.get("id"): s for s in record.get("sources", []) if s.get("id")}


RECENT_FLAGGED_DAYS = 5


def _iter_citations(days: list[dict]):
    """Yield (date, record, facts, source) one at a time: the sources cited by
    the commentary inside published records."""
    for day in days:
        for record in day.get("pairs", []):
            if not record.get("published"):
                continue
            facts = record.get("facts") or {}
            by_id = _sources_by_id(record)
            for sid in (record.get("narrative") or {}).get("sources_used") or []:
                src = by_id.get(sid)
                if src and src.get("url"):
                    yield day.get("date"), record, facts, src


def _round(value, digits):
    return None if value is None else round(float(value), digits)


def cited_stories(days: list[dict]) -> list[dict]:
    """Evidence set for the cited stories, newest evidence first.

    Each story carries the pair-day evidence it was cited for: date, pair, that
    day's residual (bp and z). These are measurements. **No allocation, no
    shares** -- spreading a residual onto a single news item is not measurable.
    """
    stories: dict[str, dict] = {}
    for date, record, facts, src in _iter_citations(days):
        entry = stories.setdefault(src["url"], {
            "url": src["url"],
            "title": src.get("title") or "(no title)",
            "source": src.get("source"),
            "publisher_domain": src.get("publisher_domain"),
            "published": src.get("published"),
            "summary": src.get("summary") or "",
            "pairs": [],
            "evidence": [],
            # the transmission note uses **this system's own judgement**: what
            # kind of event that day was, and whether any of the model's factors
            # can carry it. No economics invented out of thin air.
            "context": {},
        })
        pair = record.get("pair")
        if pair not in entry["pairs"]:
            entry["pairs"].append(pair)
        key = (date, pair)
        if key not in {(e["date"], e["pair"]) for e in entry["evidence"]}:
            ev = record.get("evidence") or {}
            entry["evidence"].append({
                "date": date,
                "pair": pair,
                "residual_bp": _round(facts.get("residual_bp"), 1),
                "residual_z": _round(facts.get("residual_z"), 2),
                "y_bp": _round(facts.get("y_bp"), 1),
                "event_kind": ev.get("event_kind"),
                "assessment": ev.get("assessment"),
            })
        evd = record.get("evidence") or {}
        nar = record.get("narrative") or {}
        entry["context"][pair] = {
            "date": date,
            "event_kind": evd.get("event_kind"),
            "coverage_check": evd.get("coverage_check"),
            "continuity_check": evd.get("continuity_check"),
            "assessment": evd.get("assessment"),
            "residual_bp": _round(facts.get("residual_bp"), 1),
            "residual_z": _round(facts.get("residual_z"), 2),
            "y_bp": _round(facts.get("y_bp"), 1),
            # the second paragraph of the commentary that cited it: why the model
            # says this day cannot be explained. Both languages are carried and
            # the frontend picks by current locale (2026-09-02 ruling)
            "why_unexplained": {
                lang: ((nar.get(lang) or {}).get("why_unexplained") or "")
                for lang in ("en", "zh")
            },
        }

    merged = _merge_near_duplicates(stories)
    items = list(merged.values())
    for item in items:
        item["evidence"].sort(key=lambda e: e["date"] or "", reverse=True)
        item["latest"] = item["evidence"][0] if item["evidence"] else None
        item["kind"] = story_kind(item["title"])
        # this function only collects sources in sources_used, so it is always
        # cited; the field exists for the exception rule (a cited item is kept and
        # labelled regardless of kind) and for possible future uncited sources
        item["cited"] = True
    # newest evidence first (user ruling: new to old, top to bottom), ties broken
    # by published date
    items.sort(key=lambda i: (
        (i["latest"] or {}).get("date") or "", i.get("published") or ""),
        reverse=True)
    return items


def _merge_near_duplicates(stories: dict) -> dict:
    """Merge near-duplicate titles into one: union the evidence, union the pairs,
    keep everything else from the representative.

    Two outlets each publish the same event, but there is only one event (user
    ruling 2026-09-02). Merged-away items are recorded in duplicates and stay
    traceable. The representative is the one with more evidence rows, then the
    newer published date."""
    reps: list[dict] = []
    ordered = sorted(stories.values(),
                     key=lambda s: (len(s["evidence"]), s.get("published") or ""),
                     reverse=True)
    for story in ordered:
        tokens = title_tokens(story["title"])
        for rep in reps:
            if similar_titles(tokens, rep["_tokens"]):
                seen = {(e["date"], e["pair"]) for e in rep["evidence"]}
                rep["evidence"].extend(
                    e for e in story["evidence"]
                    if (e["date"], e["pair"]) not in seen)
                for pair in story["pairs"]:
                    if pair not in rep["pairs"]:
                        rep["pairs"].append(pair)
                for pair, ctx in story["context"].items():
                    rep["context"].setdefault(pair, ctx)
                rep.setdefault("duplicates", []).append({
                    "title": story["title"], "source": story.get("source"),
                    "url": story["url"], "published": story.get("published"),
                })
                break
        else:
            story = dict(story)
            story["_tokens"] = tokens
            story.setdefault("duplicates", [])
            reps.append(story)
    out = {}
    for rep in reps:
        rep.pop("_tokens", None)
        out[rep["url"]] = rep
    return out


def _direction(record: dict) -> str:
    y = (record.get("facts") or {}).get("y_bp")
    if y is None:
        return "flat"
    return "usd up" if y > 0 else "usd down" if y < 0 else "flat"


def pair_directions(days: list[dict]) -> dict:
    """The dollar's direction for the corresponding pair on the day a story was
    cited. Used for the direction label in the expanded panel."""
    out: dict[str, dict] = {}
    for _date, record, _facts, src in _iter_citations(days):
        out.setdefault(src["url"], {})[record["pair"]] = _direction(record)
    return out


def recent_flagged_days(all_days: list[dict],
                        limit: int = RECENT_FLAGGED_DAYS) -> list[dict]:
    """The last `limit` trigger days that have a published record. Evidence
    display uses this scope rather than the week window: triggers fire only every
    4.5 to 5 days, so restricted to this week the evidence panel would be dead
    most of the time."""
    flagged = [d for d in all_days
               if any(p.get("published") for p in d.get("pairs", []))]
    return flagged[-limit:]


def today_headlines(days: list[dict]) -> dict:
    """Every story retrieved on the most recent day, not just the cited ones."""
    if not days:
        return {"date": None, "items": []}
    day = days[-1]
    seen, items = set(), []
    for record in day.get("pairs", []):
        cited = set((record.get("narrative") or {}).get("sources_used") or [])
        for src in record.get("sources", []):
            url = src.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            items.append({
                "url": url,
                "title": src.get("title"),
                "source": src.get("source"),
                "publisher_domain": src.get("publisher_domain"),
                "published": src.get("published"),
                "summary": src.get("summary") or "",
                "phase": src.get("phase"),
                "pairs": [record.get("pair")],
                "cited": src.get("id") in cited,
                "direction": _direction(record) if src.get("id") in cited else None,
            })
    items.sort(key=lambda i: (not i["cited"], i.get("published") or ""), reverse=False)
    return {"date": day.get("date"), "items": items}


def pair_evidence(days: list[dict], pair: str) -> dict:
    """Stories relevant to one pair across the given set of trigger days, for the
    expandable section under the FX page card."""
    directions = pair_directions(days)
    items = []
    for story in cited_stories(days):
        rows = [e for e in story["evidence"] if e["pair"] == pair]
        if not rows:
            continue
        items.append({
            "url": story["url"], "title": story["title"],
            "source": story["source"],
            "publisher_domain": story["publisher_domain"],
            "published": story["published"], "summary": story["summary"],
            "evidence": rows,
            "direction": directions.get(story["url"], {}).get(pair),
            "context": story["context"].get(pair, {}),
            "duplicates": story.get("duplicates", []),
        })
    return {"pair": pair, "count": len(items), "items": items}


def story_counts(days: list[dict], pairs: list[str]) -> dict:
    counts = {p: 0 for p in pairs}
    for story in cited_stories(days):
        for p in story["pairs"]:
            if p in counts:
                counts[p] += 1
    return counts


def citation_matrix(days: list[dict], pairs: list[str]) -> dict:
    """Citation map grouped by trigger day.

    **The residual is a property of the trading day, not of any single news
    item** (2026-09-02 ruling, the same rule as on the News page). So the cells
    hold only a citation mark, and the residual is aggregated once per
    (date, pair) in the group header, shared by every story for that pair on that
    day. Earlier versions printed the same residual into every cell; a table reads
    naturally as "the value of this cell", so the misreading is stronger than in a
    list, and one line of fine print underneath cannot outweigh a matrix."""
    stories = cited_stories(days)
    by_date: dict[str, dict] = {}
    for story in stories:
        for ev in story["evidence"]:
            g = by_date.setdefault(ev["date"], {"date": ev["date"],
                                                "residuals": {}, "rows": {}})
            g["residuals"].setdefault(ev["pair"], {
                "residual_bp": ev["residual_bp"],
                "residual_z": ev["residual_z"],
                "y_bp": ev["y_bp"],
            })
            row = g["rows"].setdefault(story["url"], {
                "title": story["title"], "url": story["url"],
                "source": story.get("source"), "cited_pairs": [],
            })
            if ev["pair"] not in row["cited_pairs"]:
                row["cited_pairs"].append(ev["pair"])

    groups = []
    for date in sorted(by_date, reverse=True):
        g = by_date[date]
        groups.append({
            "date": date,
            "residuals": g["residuals"],
            "rows": [dict(r, cells=[{"pair": p, "cited": p in r["cited_pairs"]}
                                    for p in pairs])
                     for r in g["rows"].values()],
        })
    return {
        "pairs": pairs,
        "groups": groups,
        "note": ("A mark shows the story was cited for that pair on that flagged "
                 "day. The residual belongs to the day, not to any single story."),
    }


# ---------------------------------------------------------- weekly breakdown
def weekly_decomposition(combo, n_days: int = WEEK_DAYS) -> dict:
    """Bucket totals over the last n trading days, in bp.

    The only new maths is still summation (SPEC_web §0 rule three): each factor's
    daily contribution is summed along trading days, then merged by BUCKETS. The
    residual is summed the same way, not derived by subtraction.
    """
    import numpy as np

    n = min(n_days, len(combo.dates))
    if n == 0:
        return {}
    lo = len(combo.dates) - n
    buckets = {key: 0.0 for key, _label, _m in BUCKETS}
    for factor, series in combo.contributions.items():
        value = float(np.nansum(series[lo:])) * 1e4
        buckets[_bucket_of(factor)] += value
    residual = float(np.nansum(combo.residual[lo:])) * 1e4
    y = float(np.nansum(combo.y[lo:])) * 1e4
    return {
        "pair": combo.pair,
        "start": combo.dates[lo],
        "end": combo.dates[-1],
        "n_days": n,
        "y_bp": round(y, 1),
        "residual_bp": round(residual, 1),
        "buckets": {k: round(v, 1) for k, v in buckets.items()},
        "labels": {key: label for key, label, _m in BUCKETS},
    }
