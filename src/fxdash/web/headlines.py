"""Daily headlines: a direct-read display layer over Google News RSS (SPEC_web §2.5).

The **second documented exception** to rule 1 (web reads only outputs/); the
first is market.py's DXY.

Design ruling (2026-09-02, after the user observed the News page starved in
practice): the narrative layer's trigger gate governs the **LLM commentary that
costs money and needs validation**, and should not also switch off "what is in
the news today". The original design retrieved only on residual anomalies, so on
quiet days the page was blank, which the user saw through immediately: news like
this is everywhere every day, a whole month cannot possibly hold nothing but
last month's items.

So headlines are **fetched every day**: pure RSS, zero LLM, zero cost, and zero
validation burden -- because nothing here produces a judgement, it just hands the
reader the title, source and date as they are. Story rankings with residual
allocation still exist only on trigger days, and the page states the difference.

Same discipline as market.py:
- Memory only, with a TTL, **never written to disk**. outputs/ is still written
  only by the attribution pipeline and the narrative job.
- On fetch failure keep serving the previous cache and return the errors
  alongside; if it never succeeded, return an empty result plus the error list so
  the page says the news source cannot be read, rather than silently pretending
  there is no news.
- Query terms reuse narrative.retrieve.PAIR_TERMS: they describe only currencies
  and central banks, with no causal hint.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..narrative import retrieve as R
from ..narrative.relevance import exclusion_reason
from .newsfeed import similar_titles, story_kind, title_tokens

log = logging.getLogger(__name__)

TTL_S = 30 * 60
PER_PAIR = 6            # cap on what each pair contributes to the merge pool
TOTAL_CAP = 14          # display cap after merging
EARLIER_CAP = 10        # display cap for the "earlier this week" block
FETCH_TIMEOUT_S = 8     # per-feed timeout; six run in parallel, so this is the worst wait
QUERY_RECENCY = "when:2d"  # covers overnight and Monday morning, sorting floats the newest

# display-layer-only query terms (2026-09-02 ruling: fix the recall problem at
# the source, touch the display layer only).
# The narrative layer's retrieve.PAIR_TERMS is untouched; that channel needs full
# recall and has six validation checks behind it.
# Only two differences from the narrative layer, both disambiguation:
# - EUR: bare "euro" recalls same-name noise like Euro NCAP and the Euros, so use
#   institutional and market phrasing instead;
# - NOK: Norges Bank, as a sovereign-fund investor, files Form 8.3 holding
#   disclosures daily, excluded with a minus.
DISPLAY_PAIR_TERMS = {
    "USDJPY": '(yen OR "Bank of Japan" OR "USD/JPY")',
    "USDEUR": '("euro zone" OR eurozone OR ECB OR "European Central Bank"'
              ' OR "EUR/USD" OR "euro dollar")',
    "USDCAD": '("Canadian dollar" OR "Bank of Canada" OR "USD/CAD")',
    "USDNOK": '("Norwegian krone" OR "Norges Bank" OR "USD/NOK") -"Form 8.3"',
    "USDAUD": '("Australian dollar" OR "Reserve Bank of Australia" OR "AUD/USD")',
    "USDMXN": '("Mexican peso" OR Banxico OR "Bank of Mexico" OR "USD/MXN")',
}

# quote pages are not news: "USD/JPY Streaming Chart" and "XAUAUD Quote" really
# did slip in. Block only these two explicit page types, no content judgement
JUNK_TITLE_RE = re.compile(r"(streaming chart|live chart|\bquote\b\s*$)", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    """Wall-clock today. A separate function so tests can stub it and never
    depend on the real date."""
    return datetime.now().date().isoformat()


def calendar_week_start(today: str | None = None) -> str:
    """Monday of this calendar week. "Today" and "earlier this week" split on the
    human calendar, not on a rolling window."""
    from datetime import date, timedelta
    d = date.fromisoformat(today or today_str())
    return (d - timedelta(days=d.weekday())).isoformat()


# module-level function so tests can stub it (the same trick the no_network
# fixture uses on market._fetch_dxy)
def _fetch(query: str) -> bytes:
    return R.fetch(query, timeout=FETCH_TIMEOUT_S)


class HeadlineBoard:
    """Today's headlines for the six pairs, merged and deduped, TTL in-memory cache."""

    def __init__(self, ttl_s: int = TTL_S, fetcher=None):
        self.ttl_s = ttl_s
        self._fetcher = fetcher
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._stamp = 0.0

    # ------------------------------------------------------------------- fetch
    def _pull_pair(self, pair: str) -> tuple[str, list[dict], str | None, list[dict]]:
        query = (f"{DISPLAY_PAIR_TERMS.get(pair, R.PAIR_TERMS.get(pair, pair))}"
                 f" {QUERY_RECENCY}")
        fetch_fn = self._fetcher or _fetch
        try:
            items = R.parse_feed(fetch_fn(query), max_items=PER_PAIR + 2, phase="live")
            excluded = [dict(i, pair=pair, reason=exclusion_reason(i["title"], pair))
                        for i in items if exclusion_reason(i["title"], pair)]
            items = [i for i in items if not exclusion_reason(i["title"], pair)]
            for item in items:
                # display-layer content classification (newsfeed.story_kind):
                # events go to the main list, opinion pieces to the collapsed
                # section. Label only, drop nothing here
                item["kind"] = story_kind(item["title"])
            return pair, items[:PER_PAIR], None, excluded
        except Exception as exc:
            return pair, [], f"{pair}: {type(exc).__name__}: {exc}", []

    def _refresh(self, pairs: list[str]) -> dict:
        errors: list[str] = []
        excluded: list[dict] = []
        merged: dict[str, dict] = {}
        order: list[str] = []
        with ThreadPoolExecutor(max_workers=len(pairs) or 1) as pool:
            for pair, items, err, rejected in pool.map(self._pull_pair, pairs):
                excluded.extend(rejected)
                if err:
                    errors.append(err)
                for item in items:
                    url = item["url"]
                    if url in merged:
                        if pair not in merged[url]["pairs"]:
                            # one story hits several pairs: merge the pair labels,
                            # do not show it twice
                            merged[url]["pairs"].append(pair)
                        continue
                    # near-duplicate titles (two outlets running the same event)
                    # also keep only one, per user ruling
                    tokens = title_tokens(item["title"])
                    twin = next((merged[u] for u in order
                                 if similar_titles(tokens, merged[u]["_tokens"])), None)
                    if twin is not None:
                        if pair not in twin["pairs"]:
                            twin["pairs"].append(pair)
                        continue
                    entry = dict(item)
                    entry["pairs"] = [pair]
                    entry["_tokens"] = tokens
                    entry.pop("phase", None)
                    merged[url] = entry
                    order.append(url)

        items = [merged[u] for u in order]
        for item in items:
            item.pop("_tokens", None)
        # newest first; published has only day precision (a known RSS defect), so
        # same-day items keep feed order
        items.sort(key=lambda i: i.get("published") or "", reverse=True)
        return {
            "available": bool(items) or not errors,
            "fetched_at": _now_iso(),
            # the display board uses round-robin quota; all_items is untruncated
            # and the pair panels read from it
            "items": fair_slice(items, pairs),
            "all_items": items,
            "errors": errors,
            "excluded": excluded,
            "provider": "google_news_rss",
        }

    # -------------------------------------------------------------------- read
    def snapshot(self, pairs: list[str]) -> dict:
        with self._lock:
            fresh = self._cached is not None and self._stamp > 0 and (
                time.monotonic() - self._stamp) < self.ttl_s
            if fresh:
                return self._cached
            board = self._refresh(sorted(pairs))
            if board["items"] or self._cached is None:
                self._cached = board
                self._stamp = time.monotonic()
            else:
                # everything failed this round: keep serving the previous cache
                # and carry the new errors out
                log.warning("headline fetch failed, reusing the last cache: %s",
                            board["errors"])
                self._cached = dict(self._cached, errors=board["errors"], stale=True)
            return self._cached

    def for_pair(self, pairs: list[str], pair: str, cap: int = 5) -> list[dict]:
        board = self.snapshot(pairs)
        pool = board.get("all_items") or board["items"]
        return [i for i in pool if pair in i["pairs"]][:cap]


def fair_slice(items: list[dict], pairs: list[str], cap: int = TOTAL_CAP) -> list[dict]:
    """Round-robin quota: each pair contributes one item per pass, so a
    news-heavy currency cannot crowd the others off the board.

    Lesson from practice: truncating globally by date gave AUD 6 slots on its own
    while NOK, EUR and MXN got none, when what the user wants is precisely what
    is recent for each of the six pairs.
    """
    by_pair = {p: [i for i in items if p in i["pairs"]] for p in pairs}
    out: list[dict] = []
    seen: set[int] = set()
    rank = 0
    while len(out) < cap:
        advanced = False
        for p in sorted(pairs):
            row = by_pair.get(p) or []
            if rank < len(row):
                advanced = True
                item = row[rank]
                if id(item) not in seen:
                    seen.add(id(item))
                    out.append(item)
                    if len(out) >= cap:
                        return out
        if not advanced:
            return out
        rank += 1
    return out
