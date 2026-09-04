"""Six hard checks (SPEC_phase3 §5).

Fail any one of them and **the whole piece is discarded**; it is never published with
a flag attached. An untrustworthy explanation is worse than no explanation, because it
gets forwarded on.

Two principles (SPEC §5.1):

- When a check conflicts with the prompt, **change the prompt so the output fits the
  check**; do not loosen the check to fit the output.
- **Checks target the truthfulness of the content, not the model's transcription
  accuracy.** Check 1 therefore judges by source id rather than comparing URLs
  verbatim: making the model hand-copy 300 characters of base64 and voiding the piece
  over one wrong letter is a failure mode unrelated to whether the content is true.
  The constraint is not looser for it -- it is stricter, since an id is either valid
  or invalid.

**What these checks can inspect is form, not judgement.** "Is the retrieved reporting
enough to account for a residual of this size" is for a person to decide, not a
regex; SPEC_phase3 §10 puts it in the live acceptance run, where it only counts once
a person has read it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ word lists
# Wording that asserts causation. Between the news and the residual there is only
# co-occurrence in time, no causal evidence.
# Chinese matches by substring, English by word boundary. "归因" is not banned (it is
# this project's own term), only "归因于"; in English only "triggered by" is banned,
# not "triggered" (trigger is a criterion term).
CAUSAL_ZH = [
    "因为", "由于", "导致", "引发", "引起", "受此影响", "受其影响",
    "因而", "致使", "造成", "从而", "归因于", "系因",
]
CAUSAL_EN = [
    r"\bbecause\b", r"\bcaused by\b", r"\bcaused the\b", r"\bled to\b",
    r"\btriggered by\b", r"\bdue to\b", r"\bowing to\b", r"\bresulted in\b",
    r"\bas a result\b", r"\bprompted by\b", r"\bsparked by\b",
    r"\bin response to\b", r"\bdrove\b", r"\bdriven by\b", r"\bthanks to\b",
    r"\bon the back of\b",
]

# Markers of directional forecasting, applied to the third paragraph only.
# Deliberately excludes "预期": inflation expectations is the name of a data series, a
# noun rather than a forecast. This list can be loosened or tightened on evidence;
# change it here.
FORECAST_ZH = ["预计", "预测", "有望", "料将", "应会", "势将", "看涨", "看跌",
               "走高", "走低", "后市"]
FORECAST_EN = [
    r"\bexpect(s|ed)?\b", r"\bforecast(s|ed)?\b", r"\bpredict(s|ed)?\b",
    r"\banticipat(e|es|ed)\b", r"\blikely to\b",
    r"\bshould (rise|fall|strengthen|weaken)\b",
    r"\bwill (rise|fall|strengthen|weaken|appreciate|depreciate)\b",
    r"\boutlook for\b",
]

URL_RE = re.compile(r"https?://[^\s)>\]\"'，。、；：》]+")
NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")

# Date expressions are removed wholesale before the number scan: dates are verified
# separately by check 2 and should not have to enter the number whitelist.
DATE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{4}年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+\d{4}\b", re.I),
    re.compile(
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{4}\b", re.I),
]

PARAGRAPHS = ("what_happened", "why_unexplained", "what_to_watch")
LANGS = ("en", "zh")


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str = ""
    # Structured extra information, stored in the artifact for traceback. Currently
    # used by check 3 only: a number quoted from a source must be recorded together
    # with the source id it came from (SPEC §5.1, principle 3, constraint 2).
    data: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def _texts(narrative: dict) -> list[tuple[str, str, str]]:
    """Full expansion into (lang, paragraph, text). sources_used is a list and is
    never picked up here."""
    out = []
    for lang in LANGS:
        block = narrative.get(lang) or {}
        for name in PARAGRAPHS:
            text = block.get(name)
            if text:
                out.append((lang, name, str(text)))
    return out


def cited_ids(narrative: dict) -> list[str]:
    """Union of the ids from both languages, order preserved."""
    out = []
    for lang in LANGS:
        for sid in ((narrative.get(lang) or {}).get("sources_used") or []):
            if sid not in out:
                out.append(sid)
    for sid in (narrative.get("sources_used") or []):
        if sid not in out:
            out.append(sid)
    return out


def _strip_noise(text: str, factor_names) -> str:
    """Strip URLs, date expressions and factor names; the numbers left over are the
    numeric assertions the model is actually making.

    Factor names (d10Y_DIFF, say) contain digits, but they are labels we assigned, not
    assertions.
    """
    text = URL_RE.sub(" ", text)
    for pattern in DATE_PATTERNS:
        text = pattern.sub(" ", text)
    for name in sorted(factor_names, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


# --------------------------------------------------------------------- check 1
def check_sources_in_set(narrative: dict, sources: list[dict]) -> Finding:
    """Cited source ids must be in the set this retrieval returned, and the body must
    contain no bare URLs.

    Judged by id rather than verbatim URL comparison: see principle 2 at the top of
    the module.
    """
    known = {s.get("id") for s in sources if s.get("id")}
    cited = cited_ids(narrative)

    invented = sorted(sid for sid in cited if sid not in known)
    if invented:
        return Finding("sources_in_set", False,
                       f"cited source ids not in the set: {invented[:5]}")

    stray = []
    for lang, name, text in _texts(narrative):
        for url in URL_RE.findall(text):
            stray.append(f"{lang}.{name}: {url[:60]}")
    if stray:
        return Finding("sources_in_set", False,
                       f"bare URL in body, publisher name expected: {stray[:3]}")

    if not narrative.get("insufficient_evidence") and not cited:
        return Finding("sources_in_set", False,
                       "insufficient_evidence not declared, yet no source cited")
    return Finding("sources_in_set", True, f"{len(cited)} cited, all in the set")


# --------------------------------------------------------------------- check 2
def check_source_dates(
    narrative: dict, sources: list[dict], date: str, calendar: list[str]
) -> Finding:
    """A cited report's publication date must fall within one trading day either side
    of the day being explained.

    Retrieval readily returns older pieces that are on topic but misplaced in time.
    The trading calendar is simply the contract's date index.
    """
    by_id = {s.get("id"): s for s in sources if s.get("id")}
    cited = cited_ids(narrative)
    if not cited:
        return Finding("source_date_window", True, "no source cited")

    days = sorted(calendar)
    if date not in days:
        return Finding("source_date_window", False, f"{date} not in the trading calendar")
    i = days.index(date)
    lo = days[max(0, i - 1)]
    hi = days[min(len(days) - 1, i + 1)]

    bad = []
    for sid in cited:
        if sid not in by_id:
            # Not being in the set is check 1's business. Each check reports only its
            # own concern
            continue
        published = by_id[sid].get("published")
        if not published:
            bad.append(f"{sid} has no publication date")
        elif not (lo <= str(published)[:10] <= hi):
            bad.append(f"{sid} published {published}, window {lo}..{hi}")
    if bad:
        return Finding("source_date_window", False, "; ".join(bad[:3]))
    return Finding("source_date_window", True, f"all within {lo}..{hi}")


# --------------------------------------------------------------------- check 3
def source_numbers(sources: list[dict]) -> dict:
    """Numbers appearing verbatim in source text -> the source ids they came from."""
    out: dict[str, list[str]] = {}
    for s in sources or []:
        sid = s.get("id")
        blob = f"{s.get('title') or ''} {s.get('summary') or ''}"
        for token in NUMBER_RE.findall(blob):
            bucket = out.setdefault(token, [])
            if sid and sid not in bucket:
                bucket.append(sid)
    return out


def check_literal_numbers(narrative: dict, facts, sources=None) -> Finding:
    """Every number must be traceable to text we hold; anything untraceable fails.

    **No fuzzy matching.** The model writing -0.31% as "about 0.3%" is not wrong
    semantically, but allow approximation once and this check becomes ornamental.

    The criterion is "can it be traced to text we hold", not "is it in the fact table"
    (SPEC §5.1, principle 3). Both the fact table and the retrieval set satisfy that
    criterion, so both count -- this widens coverage, not the tolerance. Three
    constraints weld the opening shut:

    1. matched **verbatim** against source text;
    2. recorded **together with the source id**, into `data.from_sources` for
       traceback;
    3. **allowed in the first paragraph only**. The decomposition numbers in the
       second paragraph are our own numbers and may come from the fact table only,
       with no external sources mixed in.
    """
    allowed = set()
    factor_names = set()
    for fact in facts:
        allowed |= fact.allowed_numbers()
        factor_names |= set(fact.contributions_bp)
    # The whitelist also takes the unsigned form: the body writes "154.5 bp" while the
    # fact set gives "+154.5 bp"; the value itself agrees and the sign is carried by
    # the surrounding context
    allowed |= {t.lstrip("+") for t in allowed}
    from_sources = source_numbers(sources)

    offenders, provenance = [], {}
    for lang, name, text in _texts(narrative):
        for token in NUMBER_RE.findall(_strip_noise(text, factor_names)):
            if token in allowed or token.lstrip("+") in allowed:
                continue
            if token in from_sources:
                if name == "what_happened":
                    provenance[token] = from_sources[token]
                    continue
                offenders.append(
                    f"{lang}.{name}: {token} comes from a source, "
                    f"allowed in the first paragraph only")
                continue
            offenders.append(f"{lang}.{name}: {token}")
    if offenders:
        return Finding("literal_numbers", False,
                       f"untraceable numbers: {offenders[:5]}")
    detail = "all numbers traceable"
    if provenance:
        detail += f"; quoted from sources {provenance}"
    return Finding("literal_numbers", True, detail,
                   data={"from_sources": provenance})


# --------------------------------------------------------------------- check 4
def check_no_causal_claims(narrative: dict) -> Finding:
    """Between news and residual there is only co-occurrence, no causal evidence, so
    wording that asserts causation is not allowed."""
    hits = []
    for lang, name, text in _texts(narrative):
        for term in CAUSAL_ZH:
            if term in text:
                hits.append(f"{lang}.{name}: {term}")
        for pattern in CAUSAL_EN:
            m = re.search(pattern, text, re.I)
            if m:
                hits.append(f"{lang}.{name}: {m.group(0)}")
    if hits:
        return Finding("no_causal_claims", False, f"causal wording found: {hits[:5]}")
    return Finding("no_causal_claims", True, "no causal wording")


# --------------------------------------------------------------------- check 5
def check_no_directional_forecast(narrative: dict) -> Finding:
    """The third paragraph may only say "which data or events would test this
    explanation"; no directional forecast is allowed.

    CLAUDE.md rule 6: this project does explanation only.
    """
    hits = []
    for lang in LANGS:
        text = (narrative.get(lang) or {}).get("what_to_watch")
        if not text:
            continue
        for term in FORECAST_ZH:
            if term in text:
                hits.append(f"{lang}: {term}")
        for pattern in FORECAST_EN:
            m = re.search(pattern, text, re.I)
            if m:
                hits.append(f"{lang}: {m.group(0)}")
    if hits:
        return Finding("no_directional_forecast", False,
                       f"forecast wording in the third paragraph: {hits[:5]}")
    return Finding("no_directional_forecast", True,
                   "no forecast wording in the third paragraph")


# --------------------------------------------------------------------- check 6
def check_bilingual_sources_match(narrative: dict) -> Finding:
    """The sources cited in the two languages must match exactly.

    The Chinese and English are two language versions of one piece, not two pieces.
    Content drift is hard to detect mechanically, but **citation drift is not**: on the
    second live run the Chinese paragraphs dropped a source. Exposing drift
    structurally beats adding one more line to the prompt saying "the two languages
    must agree".
    """
    lists = {lang: list((narrative.get(lang) or {}).get("sources_used") or [])
             for lang in LANGS}
    if not any(lists.values()):
        return Finding("bilingual_sources_match", True, "neither side cited anything")
    if sorted(lists["en"]) != sorted(lists["zh"]):
        return Finding("bilingual_sources_match", False,
                       f"citations disagree en={lists['en']} zh={lists['zh']}")
    return Finding("bilingual_sources_match", True,
                   f"both sides agree, {len(lists['en'])} each")


# ----------------------------------------------------------------------- summary
def verify(
    narrative: dict,
    facts,
    sources: list[dict],
    date: str,
    calendar: list[str],
) -> list[Finding]:
    return [
        check_sources_in_set(narrative, sources),
        check_source_dates(narrative, sources, date, calendar),
        check_literal_numbers(narrative, facts, sources),
        check_no_causal_claims(narrative),
        check_no_directional_forecast(narrative),
        check_bilingual_sources_match(narrative),
    ]


def passed(findings: list[Finding]) -> bool:
    return all(f.ok for f in findings)


def failures(findings: list[Finding]) -> list[dict]:
    return [{"check": f.check, "detail": f.detail} for f in findings if not f.ok]
