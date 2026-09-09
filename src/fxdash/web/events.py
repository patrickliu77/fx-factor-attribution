"""Conservative display-only grouping of same-day rewritten headlines.

Dates, figures and polarity are guardrails. Broad topic overlap alone is not
enough. All source links survive; retrieval and narrative inputs are untouched.
"""
from __future__ import annotations

import copy
import re
import unicodedata

REVISION = "2026-09-07.event-groups-v1"
STOP = set("the a an as amid on in of to for with by after over and at from against us its is are".split())
ALIASES = {
    **dict.fromkeys("rises rise rising gains gain climbs climb surges surge strengthens rallies rally higher up".split(), "rise"),
    **dict.fromkeys("falls fall falling drops drop slides slide slips slip weakens weaken lower down declines decline sinks sink".split(), "fall"),
    **dict.fromkeys("cuts cut cutting reduction reductions".split(), "cut"),
    **dict.fromkeys("hikes hike hiking raises raise increase increases".split(), "hike"),
    **dict.fromkeys("holds hold unchanged maintains keeps".split(), "hold"),
    **dict.fromkeys("rates rate".split(), "rate"),
    **dict.fromkeys("prices price".split(), "price"),
    **dict.fromkeys("remarks comments comment statements statement".split(), "remark"),
}
ACTION = {"rise", "fall", "cut", "hike", "hold", "intervention", "remark"}
OPPOSITES = ({"rise", "fall"}, {"cut", "hike"}, {"hold", "cut"}, {"hold", "hike"})


def signature(item):
    text = unicodedata.normalize("NFKC", item.get("title") or "").casefold()
    publisher = (item.get("source") or "").casefold().strip()
    if publisher:
        text = re.sub(r"\s+[-|–—]\s*" + re.escape(publisher) + r"\s*$", "", text)
    text = re.sub(r"\bu\.?s\.?\b", "us", text)
    for phrase, replacement in (("federal reserve", "fed"), ("bank of japan", "boj"),
                                ("european central bank", "ecb"), ("greenback", "dollar")):
        text = text.replace(phrase, replacement)
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", text)
    return {ALIASES.get(w, w) for w in words if w not in STOP}, set(re.findall(r"\d+(?:\.\d+)?", text))


def same_event(a, b):
    if a.get("url") and a.get("url") == b.get("url"):
        return True
    if not a.get("published") or a.get("published") != b.get("published"):
        return False
    x, nx = signature(a)
    y, ny = signature(b)
    if not x or not y or nx != ny:
        return False
    negations = {"not", "no", "denies", "denied"}
    if bool(x & negations) != bool(y & negations):
        return False
    speculative = {"may", "might", "could", "expected", "expects", "forecast", "likely", "tomorrow"}
    if bool(x & speculative) != bool(y & speculative):
        return False
    # A bag of words cannot tell which actor rose in 'dollar up, yen down'.
    # Preserve both versions whenever such opposing actions occur together.
    if any(pair <= x or pair <= y for pair in OPPOSITES):
        return False
    if any(x & pair and y & pair and x & pair != y & pair for pair in OPPOSITES):
        return False
    # Different currency actors often share almost identical wire templates.
    actors = {"yen", "euro", "peso", "krone", "canadian", "australian", "boj", "ecb", "fed", "banxico"}
    if x & actors != y & actors:
        return False
    similarity = len(x & y) / len(x | y)
    return x == y or (similarity >= .8 and len(x & y) >= 4) or (
        similarity >= .58 and len((x & y) - ACTION) >= 3 and bool(x & y & ACTION))


def group_events(items):
    groups = []
    for item in items:
        # Compare representatives only, avoiding transitive chains across events.
        twin = next((g for g in groups if same_event(g, item)), None)
        if twin is None:
            twin = copy.deepcopy(item)
            twin.setdefault("duplicates", [])
            groups.append(twin)
            continue
        links = {twin.get("url"), *(d.get("url") for d in twin["duplicates"])}
        for other in [item, *item.get("duplicates", [])]:
            if other.get("url") and other["url"] not in links:
                twin["duplicates"].append({k: copy.deepcopy(other.get(k)) for k in (
                    "url", "title", "source", "published", "summary", "pairs")})
                links.add(other["url"])
        twin["pairs"] = sorted(set(twin.get("pairs", [])) | set(item.get("pairs", [])))
        for evidence in item.get("evidence", []):
            if evidence not in twin.setdefault("evidence", []):
                twin["evidence"].append(copy.deepcopy(evidence))
        for pair, context in item.get("context", {}).items():
            twin.setdefault("context", {}).setdefault(pair, copy.deepcopy(context))
    return groups


def group_sections(sections):
    """First section owns an event; later sections contribute source links only."""
    items = [dict(i, _section=name) for name, rows in sections.items() for i in rows]
    result = {name: [] for name in sections}
    for item in group_events(items):
        result[item.pop("_section")].append(item)
    return result
