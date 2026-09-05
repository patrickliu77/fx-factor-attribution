"""Deterministic screening for new driver searches, not a truth/reliability score.

Rules use title/snippet cues. Ambiguous candidates remain in a review bucket.
Opposing views are never rejected for disagreeing with a factor contribution.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .relevance import exclusion_reason

REVISION = "driver-sources-1"
TRACKING = {"gclid", "fbclid", "oc", "mc_cid", "mc_eid"}
PROFILE = re.compile(r"fundamental fund details|stock price today.*quotes|"
                     r"^(?:learn all|all) crypto topics|\b(?:currency|exchange rate) calculator\b", re.I)
VIX_COLLISION = re.compile(r"^dear vix\b|docuseries|tv-chavez|\b(?:comedy|streaming service|telenovela)\b", re.I)
FINANCE = re.compile(r"\b(?:stocks?|equities|equity|traders?|trading|yields?|treasur\w*|inflation|monetary|volatility)\b", re.I)
FACTORS = {
    "DOLLAR_LOO": r"\b(?:US dollar|U\.S\. dollar|dollar index|Federal Reserve|Fed|DXY|greenback)\b",
    "CARRY_LOO": r"\b(?:carry trades?|funding currenc\w*|risk appetite)\b",
    "d2Y_DIFF": r"\b(?:(?:two|2)[ -]year|yields?|interest rates?|monetary policy|rate (?:cut|hike|decision|outlook))\b",
    "d10Y_DIFF": r"\b(?:(?:ten|10)[ -]year|yields?|government bonds?|treasur\w*)\b",
    "dVIX": r"\b(?:VIX|CBOE|equity volatility|stock.market volatility)\b",
    "WTI": r"\b(?:WTI|crude|oil (?:prices?|supply|demand|production)|OPEC\w*)\b",
    "BRENT": r"\b(?:Brent|crude|oil (?:prices?|supply|demand|production)|OPEC\w*)\b",
    "GOLD": r"\b(?:gold|bullion)\b",
    "COPPER": r"\bcopper\b",
    "EMB": r"\b(?:emerging.market (?:bonds?|debt)|EM debt|sovereign (?:bonds?|debt))\b",
    "HY_EXCESS": r"\b(?:high.yield|junk bonds?|credit spreads?|HYG|IEI)\b",
    "dHY_OAS": r"\b(?:high.yield|junk bonds?|credit spreads?|option.adjusted spreads?)\b",
}
CURRENCIES = {
    "USDEUR": r"\b(?:euro|ECB|European Central Bank|EUR/?USD|USD/?EUR)\b",
    "USDJPY": r"\b(?:yen|BOJ|Bank of Japan|USD/?JPY)\b",
    "USDCAD": r"\b(?:Canadian dollar|loonie|Bank of Canada|USD/?CAD)\b",
    "USDNOK": r"\b(?:krone|NOK|Norges Bank|USD/?NOK)\b",
    "USDAUD": r"\b(?:Australian dollar|Aussie|RBA|Reserve Bank of Australia|AUD/?USD|USD/?AUD)\b",
    "USDMXN": r"\b(?:Mexican peso|Banxico|Bank of Mexico|USD/?MXN)\b",
}
METAL_CONTEXT = re.compile(r"\b(?:prices?|bullion|ounces?|futures|metals?|commodit\w*|mines?|mining|"
                           r"supply|demand|exports?|imports?|rall\w*|slump\w*|record highs?)\b", re.I)


def canonical_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
        # Keep path case and meaningful query parameters: article ids may live there.
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
                           urlencode(sorted(query)), ""))
    except (TypeError, ValueError, AttributeError):
        return None


def headline_key(title):
    # Preserve punctuation, signs and decimal separators. A minus sign or a
    # question mark can change a headline's meaning.
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def publisher_key(item):
    # Publisher metadata comes from RSS, not the Google redirect host. A reported
    # hostname or label does not establish ownership or independent journalism.
    domain = canonical_url(item.get("publisher_domain"))
    if domain:
        return re.sub(r"^www\.", "", urlsplit(domain).hostname)
    label = item.get("source")
    return headline_key(label) if isinstance(label, str) and label.strip() else None


def classify(item, channel):
    title = item.get("title") or ""
    if not title.strip():
        return "excluded", "missing_title"
    if not canonical_url(item.get("url")):
        return "excluded", "invalid_article_url"
    if PROFILE.search(title):
        return "excluded", "profile_or_reference_page"
    pair = channel.partition(":")[2] if channel.startswith("currency:") else None
    reason = exclusion_reason(title, pair)
    if reason:
        return "excluded", reason
    factor = channel.partition(":")[2] if channel.startswith("factor:") else None
    if factor == "dVIX" and VIX_COLLISION.search(title) and not FINANCE.search(title):
        return "excluded", "vix_name_collision"
    if factor == "dVIX" and re.search(r"\b(?:BITVX|IBIT|bitcoin|crypto)\b", title, re.I) and not re.search(
            r"\bVIX\b|equity|S&P", title, re.I):
        return "review", "different_volatility_market"
    if headline_key(title) in {"federal reserve", "vix", "gold", "copper", "dollar index"}:
        return "review", "generic_topic_heading"
    text = title + " " + (item.get("summary") or "")
    pattern = (FACTORS if factor else CURRENCIES).get(factor or pair)
    if not pattern or not re.search(pattern, text, re.I):
        return "review", "topic_not_clear_from_title_or_snippet"
    if factor in {"GOLD", "COPPER"} and not METAL_CONTEXT.search(text):
        return "review", "metal_market_context_not_clear"
    return "retained", None


def publisher_round_robin(items):
    pending = list(items)
    ordered = []
    while pending:
        seen, deferred = set(), []
        for item in pending:
            key = publisher_key(item) or "unknown"
            if key in seen:
                deferred.append(item)
            else:
                seen.add(key)
                ordered.append(item)
        pending = deferred
    return ordered


def screen(candidates, channel, lo, hi, observed_at):
    retained, review, excluded = [], [], []
    urls, titles = {}, {}
    classified = []
    for item in candidates:
        record = {k:v for k,v in item.items() if k not in {"reason", "duplicate_of"}}
        record["observed_at"] = observed_at
        kind, reason = classify(record, channel)
        if not record.get("published") or not lo <= record["published"] <= hi:
            kind, reason = "excluded", "outside_current_date_window_or_undated"
        classified.append((record, kind, reason))
    # Prefer a version with a clear topic cue over an ambiguous duplicate, then
    # the latest publication date. Same-day ties retain retrieval order.
    classified.sort(key=lambda x:x[0].get("published") or "", reverse=True)
    classified.sort(key=lambda x:{"retained":0, "review":1, "excluded":2}[x[1]])
    for record, kind, reason in classified:
        url, title = canonical_url(record.get("url")), headline_key(record.get("title") or "")
        if kind != "excluded":
            duplicate = urls.get(url) or (titles.get(title) if title else None)
            if duplicate:
                kind, reason = "excluded", "duplicate_url_or_headline"
                record["duplicate_of"] = duplicate
                urls[url] = duplicate
                if title:
                    titles[title] = duplicate
            else:
                urls[url] = record["url"]
                if title:
                    titles[title] = record["url"]
        if kind == "retained":
            retained.append(record)
        else:
            (review if kind == "review" else excluded).append(dict(record, reason=reason))
    retained = publisher_round_robin(retained)
    displayed = retained[:3]
    publishers = {publisher_key(i) for i in displayed} - {None}
    return {"items": retained, "review": review, "excluded": excluded,
            "coverage": {"candidates": len(candidates), "retained": len(retained),
                         "review": len(review), "excluded": len(excluded), "displayed": len(displayed),
                         "displayed_publishers": len(publishers),
                         "missing_publisher_metadata": sum(publisher_key(i) is None for i in displayed)},
            "source_policy": REVISION}
