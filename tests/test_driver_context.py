from datetime import datetime, timezone

import pytest

from fxdash.narrative.briefing import build_preview, load_preview
from fxdash.narrative.relevance import exclusion_reason
from fxdash.web import drivers
from fxdash.web.headlines import HeadlineBoard
from fxdash.web.store import Snapshot
from test_web import _write_fixture

RSS = b'''<rss><channel>
<item><title>US dollar slips as carry trade unwinds - Example</title><link>https://example.com/dollar</link>
<pubDate>Fri, 04 Sep 2026 10:00:00 GMT</pubDate><source url="https://example.com">Example</source></item>
<item><title>Norges Bank Buys 92% Stake in Spanish Shopping Center Portfolio</title><link>https://example.com/fund</link>
<pubDate>Fri, 04 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Tomorrow's dollar news</title><link>https://example.com/future</link>
<pubDate>Mon, 07 Sep 2026 10:00:00 GMT</pubDate></item>
</channel></rss>'''


def test_narrow_relevance_filter_keeps_policy_and_currency_counterevidence():
    assert exclusion_reason("Norges Bank buys stake in shopping center")
    assert exclusion_reason("USD/JPY Streaming Chart") == "quote_or_chart_page"
    assert exclusion_reason("Norges Bank hikes policy rate despite fund losses") is None
    assert exclusion_reason("Krone falls as sovereign wealth fund shifts flows", "USDNOK") is None
    assert exclusion_reason("Oil slumps despite higher dollar") is None
    assert exclusion_reason("Norges Bank sovereign wealth fund cuts US Treasury holdings", "USDNOK") is None


def test_headline_exclusions_are_auditable():
    out = HeadlineBoard(fetcher=lambda q: RSS).snapshot(["USDNOK"])
    assert len(out["excluded"]) == 1
    assert "sovereign_fund" in out["excluded"][0]["reason"]
    assert all("Shopping" not in i["title"] for i in out["items"])


def packet(tmp_path):
    _write_fixture(tmp_path)
    calls = []
    out = drivers.collect(Snapshot(tmp_path), fetcher=lambda q: (calls.append(q), RSS)[1],
                          clock=lambda: datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc))
    return out, calls


def test_both_channels_are_queried_once_and_dates_are_honest(tmp_path):
    out, calls = packet(tmp_path)
    assert len(calls) == 4  # two currencies and two shared leading factors
    assert len(out["pairs"]) == 2
    assert "factor:dVIX" in out["slates"]
    assert "currency:USDEUR" in out["slates"]
    assert out["as_of"] == "2026-01-07"  # old model data is never relabelled today
    assert out["fetched_at"].startswith("2026-09-05")
    assert out["publication_precision"] == "day"
    assert out["source_policy"] == "driver-sources-1"
    for key, slate in out["slates"].items():
        eligible = key == "factor:CARRY_LOO"
        assert [i["url"] for i in slate["items"]] == (["https://example.com/dollar"] if eligible else [])
        assert len(slate["review"]) == (0 if eligible else 1)
        assert len(slate["excluded"]) == 2
        assert slate["coverage"]["candidates"] == 3


def test_failed_fetch_has_no_fabricated_reporting(tmp_path):
    _write_fixture(tmp_path)
    def fail(q):
        raise TimeoutError("secret provider URL")
    out = drivers.collect(Snapshot(tmp_path), fetcher=fail)
    assert all(s["error"] == "TimeoutError" and not s["items"] for s in out["slates"].values())
    assert "secret" not in str(out)
    assert all(not s["review"] and s["coverage"]["candidates"] == 0 for s in out["slates"].values())


def test_preview_numbers_cutoffs_and_freshness(tmp_path):
    import json
    out, _ = packet(tmp_path)
    brief = build_preview(out)
    assert brief["mode"] == "preview" and not brief["scheduled"]
    assert "+800000.0 bp" in brief["text"]["en"]
    assert "provisional" in brief["text"]["en"]
    assert brief["news_observed_by"] == out["fetched_at"]
    root = tmp_path / "briefing"
    root.mkdir()
    (root / "preview.json").write_text(json.dumps(brief), encoding="utf-8")
    assert load_preview(tmp_path, out["data_version"])["available"]
    assert "evidence" not in load_preview(tmp_path, out["data_version"])
    assert not load_preview(tmp_path, "different")["available"]
    assert not load_preview(tmp_path / "missing", "different")["available"]


def test_preview_refuses_sources_observed_after_cutoff(tmp_path):
    out, _ = packet(tmp_path)
    out["slates"]["factor:CARRY_LOO"]["items"][0]["observed_at"] = "2026-09-06T12:00:00+00:00"
    with pytest.raises(ValueError, match="cutoff"):
        build_preview(out)
