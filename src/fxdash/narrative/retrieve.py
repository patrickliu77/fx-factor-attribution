"""News retrieval: Google News RSS (SPEC_phase3 §3, revised 2026-09-01).

The original design used the model's built-in server-side search; measurement showed
this key's grounding quota is 0 (plain generation works, grounding returns 429
continuously -- a quota, not rate limiting). Derivation and data in SPEC §3.1.

Switching to RSS is not only about it being free: it gives **real source names and
real publication dates**, which is exactly what checks 1 and 2 need. Grounding returns
redirect links, which are both hard to verify for source authenticity and unsuitable
for front-end display.

**This layer only fetches candidate sources back; it produces no judgements.**
Retrieval results go into the artifact verbatim, including the ones that were never
cited -- they are the only evidence for judging whether the model cherry-picked.

Two known defects (SPEC §3.3): the time part of `pubDate` is a placeholder while the
date itself is accurate, so use it at day granularity; `link` is a Google News
redirect link rather than the article URL -- readers can follow it, but it must be
labelled.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

RSS_BASE = "https://news.google.com/rss/search"
RSS_PARAMS = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
USER_AGENT = "Mozilla/5.0 (compatible; fxdash-narrative/0.1)"
SAME_DAY_ITEMS = 8      # the day itself and the day before
AFTER_ITEMS = 4         # after-the-fact recaps: allowed, but must not fill the slate
DEFAULT_MAX_ITEMS = SAME_DAY_ITEMS + AFTER_ITEMS
TIMEOUT_S = 45

# Search terms per pair. They describe only the currency and its central bank, with
# no causal hint of any kind.
PAIR_TERMS = {
    "USDJPY": '(yen OR "Bank of Japan" OR "USD/JPY")',
    "USDEUR": '(euro OR "European Central Bank" OR "EUR/USD")',
    "USDCAD": '("Canadian dollar" OR "Bank of Canada" OR "USD/CAD")',
    "USDNOK": '("Norwegian krone" OR "Norges Bank" OR "USD/NOK")',
    "USDAUD": '("Australian dollar" OR "Reserve Bank of Australia" OR "AUD/USD")',
    "USDMXN": '("Mexican peso" OR Banxico OR "Bank of Mexico" OR "USD/MXN")',
}

TAG_RE = re.compile(r"<[^>]+>")
TRAILING_SOURCE_RE = re.compile(r"\s+-\s+[^-]{2,40}$")


def window(date: str, calendar: list[str]) -> tuple[str, str]:
    """One trading day either side of the day being explained.

    Aligned with check 2's acceptance window so we do not manufacture technical
    failures for ourselves. Check 2 is thereby demoted from primary filter to second
    line of defence: it still blocks sources with no date and fabricated URLs.
    """
    days = sorted(calendar)
    if date not in days:
        return date, date
    i = days.index(date)
    return days[max(0, i - 1)], days[min(len(days) - 1, i + 1)]


def build_query(fact, calendar: list[str]) -> str:
    """The search query. Deliberately does not ask "what caused this move".

    Push a causal hypothesis in at the retrieval stage and the model goes looking for
    things that confirm it; the verification checks all run at the writing stage, and
    they catch fabrication but not selection. So only the currency and the date range
    are given.
    """
    lo, hi = window(fact.date, calendar)
    terms = PAIR_TERMS.get(fact.pair, fact.pair)
    # Google News after/before are open intervals; widen each by a day so lo and hi
    # themselves are covered
    after = _shift(lo, -1)
    before = _shift(hi, +1)
    return f"{terms} after:{after} before:{before}"


def _shift(date: str, days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(date, "%Y-%m-%d")
            + timedelta(days=days)).strftime("%Y-%m-%d")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(TAG_RE.sub(" ", text)).strip()


def _published(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        return m.group(0) if m else None


def parse_feed(payload: bytes, keep: tuple[str, str] | None = None,
               max_items: int = DEFAULT_MAX_ITEMS, phase: str = "same_day") -> list[dict]:
    """RSS -> source list. When keep is given, filter by publication date to that
    closed interval.

    `<source>` is a plain element with **no namespace**, and carries a `url` attribute
    giving the publisher's real domain. Only found out on the first live run -- the
    original implementation looked for `{http://news.google.com}source` and got None
    every time. The publisher domain also fixes half of the "link is a Google redirect"
    defect: the source becomes verifiable.
    """
    root = ET.fromstring(payload)
    out = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        published = _published(item.findtext("pubDate"))
        if keep and (published is None or not (keep[0] <= published <= keep[1])):
            continue
        node = item.find("source")
        source = _clean(node.text) if node is not None else None
        publisher = node.get("url") if node is not None else None
        # The trailing " - Publisher" in the title is added by Google; strip it, the
        # source gets its own field
        title = TRAILING_SOURCE_RE.sub("", _clean(item.findtext("title"))).strip()
        out.append({
            "url": link,
            "title": title,
            "source": source,
            "publisher_domain": publisher,
            "published": published,
            "phase": phase,
            "summary": _clean(item.findtext("description"))[:400],
            # The front end must label this: it is a Google News redirect link, not
            # the article URL (SPEC §3.3)
            "url_kind": "google_news_redirect",
        })
        if len(out) >= max_items:
            break
    return out


def fetch(query: str, timeout: int = TIMEOUT_S) -> bytes:
    params = dict(RSS_PARAMS, q=query)
    url = RSS_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def retrieve(fact, calendar: list[str], fetcher=None,
             same_day_items: int = SAME_DAY_ITEMS,
             after_items: int = AFTER_ITEMS) -> dict:
    """Two separate retrievals (changed after the first live run, 2026-09-01).

    Lesson from that first run: a single retrieval over the whole window came back
    with 11 of 12 items being recap analysis published three days later, and **not one
    same-day report**. The recap genre is itself an argument for how significant the
    event was, and on that input judging accounts_for is close to the only reasonable
    answer -- **what looked like a judgement problem was really an input problem**.

    So it splits in two:
    - `same_day`: strictly the day being explained and the previous trading day;
    - `after`: the rest of the window, i.e. after-the-fact recaps. Allowed, but must
      not fill the slate.

    Same-day reports come first in the candidate set and carry a `phase` label, so the
    model can tell same-day reporting from later recaps. That distinction alone helps
    its judgement.
    """
    fetch_fn = fetcher or fetch
    lo, hi = window(fact.date, calendar)
    errors, sources = [], []
    queries = {}

    def run(label, after, before, keep, cap):
        query = f"{PAIR_TERMS.get(fact.pair, fact.pair)} after:{after} before:{before}"
        queries[label] = query
        try:
            return parse_feed(fetch_fn(query), keep=keep, max_items=cap, phase=label)
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            log.warning("retrieval failed %s/%s: %s", fact.pair, label, exc)
            return []

    sources += run("same_day", _shift(lo, -1), _shift(fact.date, +1),
                   (lo, fact.date), same_day_items)
    if hi > fact.date:
        sources += run("after", fact.date, _shift(hi, +1),
                       (_shift(fact.date, +1), hi), after_items)

    seen, unique = set(), []
    for item in sources:  # same_day comes first, so it wins ties naturally
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        # Short ids. The model cites ids rather than copying URLs: these URLs are 185
        # to 410 characters of base64, one mistyped letter voids the whole piece, and
        # that failure mode has nothing to do with whether the content is truthful
        item["id"] = f"S{len(unique) + 1}"
        unique.append(item)

    counts = {"same_day": sum(1 for s in unique if s["phase"] == "same_day"),
              "after": sum(1 for s in unique if s["phase"] == "after")}
    return {
        "provider": "google_news_rss",
        "queries": queries,
        "query": queries.get("same_day", ""),
        "window": [lo, hi],
        "counts": counts,
        "sources": unique,
        "errors": errors,
    }
