import copy

import pytest

from fxdash.narrative import driver_notes as D
from fxdash.narrative import news_quality as Q

STAMP = "2026-09-05T12:00:00+00:00"


def article(title="VIX rises as equities slip", url="https://example.com/news", **kw):
    return {"title": title, "url": url, "source": "Example", "summary": "",
            "publisher_domain": "https://example.com", "published": "2026-09-04", **kw}


@pytest.mark.parametrize("title,channel,kind,reason", [
    ("Dear Vix: I lost my grandmother", "dVIX", "excluded", "vix_name_collision"),
    ("Chavez opens up in new ViX docuseries", "dVIX", "excluded", "vix_name_collision"),
    ("TV-Chavez-vs-Chavez", "dVIX", "excluded", "vix_name_collision"),
    ("Cboe to launch BITVX based on IBIT options", "dVIX", "review", "different_volatility_market"),
    ("Bitcoin volatility diverges from VIX", "dVIX", "retained", None),
    ("VIX falls despite equity selloff", "dVIX", "retained", None),
    ("CBOE Volatility S&P500 Index(.VIX) Stock Price Today | Quotes & News", "dVIX", "excluded", "profile_or_reference_page"),
    ("Learn All Crypto Topics | Phemex Academy", "dVIX", "excluded", "profile_or_reference_page"),
    ("Fundamental Fund Details for ETF", "DOLLAR_LOO", "excluded", "profile_or_reference_page"),
    ("Federal Reserve", "DOLLAR_LOO", "review", "generic_topic_heading"),
    ("A quiet day in markets", "dVIX", "review", "topic_not_clear_from_title_or_snippet"),
    ("Olympic team wins gold", "GOLD", "review", "metal_market_context_not_clear"),
    ("Copper family opens new restaurant", "COPPER", "review", "metal_market_context_not_clear"),
    ("Gold prices fall despite dollar weakness", "GOLD", "retained", None),
    ("Copper supply disrupted by strike", "COPPER", "retained", None),
    ("US dollar falls as traders trim Fed bets", "DOLLAR_LOO", "retained", None),
    ("Carry trade unwinds", "CARRY_LOO", "retained", None),
    ("Two-year yields dip", "d2Y_DIFF", "retained", None),
    ("Government bond yields climb", "d10Y_DIFF", "retained", None),
    ("WTI falls on supply outlook", "WTI", "retained", None),
    ("Oil supply disrupted", "BRENT", "retained", None),
    ("Emerging market debt under pressure", "EMB", "retained", None),
    ("Junk bonds rally", "HY_EXCESS", "retained", None),
    ("Credit spreads narrow", "dHY_OAS", "retained", None),
])
def test_screening_rules_keep_counterevidence(title, channel, kind, reason):
    assert Q.classify(article(title), "factor:"+channel) == (kind, reason)


@pytest.mark.parametrize("pair,title", [
    ("USDEUR", "Euro weakens ahead of ECB meeting"),
    ("USDJPY", "Yen strengthens"),
    ("USDCAD", "Canadian dollar climbs"),
    ("USDNOK", "Norges Bank fund cuts US Treasury holdings"),
    ("USDAUD", "RBA keeps policy steady"),
    ("USDMXN", "Banxico holds rates"),
])
def test_currency_cues(pair, title):
    assert Q.classify(article(title), "currency:"+pair) == ("retained", None)


def test_fund_flow_ambiguity_and_explicit_property_exclusion():
    assert Q.classify(article("Norway sovereign wealth fund cuts US Treasury holdings"),
                      "currency:USDNOK")[0] == "review"
    assert Q.classify(article("Norges Bank buys shopping center stake"),
                      "currency:USDNOK")[0] == "excluded"
    assert Q.classify(article("A quiet day", summary="Traders discuss VIX futures."),
                      "factor:dVIX") == ("retained", None)


@pytest.mark.parametrize("url", [None, "", "javascript:alert(1)", "file:///tmp/news", "https://u:p@example.com/x"])
def test_invalid_urls(url):
    assert Q.classify(article(url=url), "factor:dVIX") == ("excluded", "invalid_article_url")


def test_canonicalization_keeps_meaningful_differences():
    assert Q.canonical_url("https://EXAMPLE.com/News?id=2&utm_source=x#top") == "https://example.com/News?id=2"
    assert Q.canonical_url("https://example.com/News?id=1") != Q.canonical_url("https://example.com/News?id=2")
    assert Q.canonical_url("https://example.com/News") != Q.canonical_url("https://example.com/news")
    assert Q.headline_key("VIX  rises") == Q.headline_key("vix rises")
    for left, right in [("VIX +2%", "VIX -2%"), ("VIX 1.2", "VIX 12"),
                        ("VIX rises", "VIX does not rise"), ("VIX rises?", "VIX rises")]:
        assert Q.headline_key(left) != Q.headline_key(right)


def screened(items):
    return Q.screen(items, "factor:dVIX", "2026-09-02", "2026-09-05", STAMP)


def test_duplicates_and_coverage_preserve_all_candidates_without_mutation():
    items = [article(), article(url="https://example.com/news?utm_source=x"),
             article("VIX RISES AS EQUITIES SLIP", url="https://second.com/recap", source="Second"),
             article("VIX falls despite equity losses", url="https://example.com/opposing"),
             article("Unclear headline", url="https://example.com/unclear"),
             article("TV-Chavez-vs-Chavez", url="https://example.com/tv")]
    original = copy.deepcopy(items)
    out = screened(items)
    assert items == original
    assert out["coverage"] == dict(candidates=6, retained=2, review=1, excluded=3,
                                  displayed=2, displayed_publishers=1, missing_publisher_metadata=0)
    dupes = [i for i in out["excluded"] if i["reason"] == "duplicate_url_or_headline"]
    assert len(dupes) == 2 and all(i["duplicate_of"] == items[0]["url"] for i in dupes)
    assert len(out["items"]+out["review"]+out["excluded"]) == len(items)


def test_retained_version_precedes_ambiguous_duplicate_and_old_reasons_are_removed():
    out = screened([article("Market update", published="2026-09-05"),
                    article("Market update", url="https://other.com/news", summary="VIX falls",
                            reason="old_exclusion", duplicate_of="old_url")])
    assert len(out["items"]) == 1 and out["items"][0]["url"] == "https://other.com/news"
    assert "reason" not in out["items"][0] and "duplicate_of" not in out["items"][0]
    assert out["excluded"][0]["duplicate_of"] == "https://other.com/news"


def test_date_window_and_missing_title_are_audited():
    out = screened([article(published="2026-09-06"), article(published=None),
                    article(published="2026-09-01"), article(title="")])
    assert not out["items"] and not out["review"]
    assert len(out["excluded"]) == 4
    assert {i["reason"] for i in out["excluded"]} == {"outside_current_date_window_or_undated", "missing_title"}


def test_publisher_rotation_uses_reported_metadata_and_keeps_all_links():
    items = [article(f"VIX story {n}", url=f"https://news.google.com/{n}", source=source,
                     publisher_domain=domain, published=date)
             for n, source, domain, date in [
                 (1, "One", "https://www.one.com", "2026-09-05"),
                 (2, "One", "https://one.com", "2026-09-05"),
                 (3, "Two", "https://two.com", "2026-09-04"),
                 (4, "Three", "https://three.com", "2026-09-03")]]
    out = screened(items)
    assert [i["url"].rsplit("/", 1)[-1] for i in out["items"]] == ["1", "3", "4", "2"]
    assert out["coverage"]["displayed_publishers"] == 3
    assert out["coverage"]["retained"] == 4
    assert Q.publisher_key(article(source="Label", publisher_domain=None)) == "label"
    unknown = screened([article(source="", publisher_domain=None)])
    assert unknown["coverage"]["displayed_publishers"] == 0
    assert unknown["coverage"]["missing_publisher_metadata"] == 1


def test_source_ids_are_legacy_compatible_and_new_packets_merge_cross_channel_duplicates():
    row = {"currency_news": "currency:USDJPY", "leading": [{"news_key": "factor:dVIX"}]}
    first = article()
    duplicate = article(url=first["url"]+"?utm_source=x")
    alternative = article(url="https://second.com/copy")
    unique = article("VIX falls", url="https://second.com/other")
    packet = {"slates": {"currency:USDJPY": {"items": [first, unique]},
                          "factor:dVIX": {"items": [duplicate, alternative],
                                          "review": [article("Review only")],
                                          "excluded": [article("Excluded only")]}}}
    original = copy.deepcopy(packet)
    legacy = D.source_set(packet, row)
    assert [(s["id"], s["url"]) for s in legacy] == [
        ("S1", first["url"]), ("S2", duplicate["url"]),
        ("S3", unique["url"]), ("S4", alternative["url"])]
    packet["source_policy"] = Q.REVISION
    current = D.source_set(packet, row)
    assert [(s["id"], s["url"]) for s in current] == [("S1", first["url"]), ("S2", unique["url"])]
    assert current[0]["channels"] == ["currency:USDJPY", "factor:dVIX"]
    assert packet["slates"] == original["slates"]
    assert len(D.source_set(original, row)) == 4


def test_offline_replay_preserves_observation_times_and_input():
    import runpy
    from pathlib import Path
    replay = runpy.run_path(str(Path(__file__).resolve().parents[1] / "ops/audit_news_quality.py"))["replay"]
    payload = {"drivers": {"as_of":"2026-09-04", "fetched_at":STAMP,
                           "news_window":{"start":"2026-09-02", "end":"2026-09-05"},
                           "slates":{"factor:dVIX":{"items":[article()],
                                       "excluded":[article("TV-Chavez-vs-Chavez")], "observed_at":STAMP}}}}
    original = copy.deepcopy(payload)
    out = replay(payload)
    assert payload == original
    assert out["mode"] == "offline_replay_of_saved_candidates"
    assert out["original_fetched_at"] == STAMP
    assert out["slates"]["factor:dVIX"]["coverage"]["candidates"] == 2
    assert out["slates"]["factor:dVIX"]["items"][0]["observed_at"] == STAMP


def test_generation_records_source_policy(tmp_path):
    from test_morning import packet
    p = packet(tmp_path)
    p["source_policy"] = Q.REVISION
    class UnavailableClient:
        def complete(self, *args):
            raise TimeoutError
    records = D.generate(p, UnavailableClient())
    assert records and all(r["source_policy"] == Q.REVISION for r in records)
    assert all(r["errors"] == ["TimeoutError"] for r in records)
