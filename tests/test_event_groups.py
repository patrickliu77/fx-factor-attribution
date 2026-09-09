import copy

import pytest

from fxdash.web.events import group_events, group_sections, same_event


def story(title, url="https://example.com/a", **extra):
    return {"title": title, "url": url, "published": "2026-09-07", "pairs": ["USDJPY"], **extra}


def test_paraphrases_preserve_links_and_inputs():
    items = [story("Yen rises after Bank of Japan rate hike", source="A"),
             story("Yen climbs after BOJ rate hike", "https://example.com/b", source="B")]
    before = copy.deepcopy(items)
    grouped = group_events(items)
    assert len(grouped) == 1
    assert grouped[0]["duplicates"][0]["url"] == items[1]["url"]
    assert items == before


@pytest.mark.parametrize("other", [
    "Yen falls after Bank of Japan rate hike",
    "Yen rises after Bank of Japan rate cut",
    "Yen rises as Bank of Japan denies rate hike",
    "Euro rises after Bank of Japan rate hike",
    "Yen rises after Bank of Japan rate hike to 1.5",
    "Yen rises as investors assess oil prices",
])
def test_conflicts_and_broad_topics_remain_separate(other):
    assert not same_event(story("Yen rises after Bank of Japan rate hike"), story(other, "https://other.com"))


def test_dates_numbers_and_unknown_titles():
    a = story("BOJ raises rate to 1.25 percent")
    assert not same_event(a, story("BOJ raises rate to 1.5 percent", "https://other.com"))
    assert not same_event(a, story(a["title"], "https://other.com", published="2026-09-08"))
    assert not same_event(story(""), story("", "https://other.com"))
    assert not same_event(story("Dollar rises as yen falls"), story("Dollar falls as yen rises", "https://other.com"))
    assert not same_event(story("BOJ raises rates after inflation report"), story("BOJ likely raises rates after inflation report", "https://other.com"))


def test_cross_section_grouping_and_evidence_union():
    first = story("Yen rises after BOJ rate hike", evidence=[{"pair": "USDJPY", "date": "2026-09-04"}])
    later = story(first["title"], "https://other.com", pairs=["USDEUR"])
    grouped = group_sections({"week": [first], "today": [later]})
    assert len(grouped["week"]) == 1 and grouped["today"] == []
    assert grouped["week"][0]["pairs"] == ["USDEUR", "USDJPY"]
    assert grouped["week"][0]["evidence"] == first["evidence"]


def test_same_url_dedup_keeps_all_alternative_sources():
    a = story("Yen gains")
    b = dict(a, duplicates=[story("Yen climbs", "https://other.com")])
    assert len(group_events([a, b])[0]["duplicates"]) == 1
