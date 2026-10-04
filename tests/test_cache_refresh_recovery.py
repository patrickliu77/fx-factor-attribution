"""Display-cache outage, concurrency and recovery checks with fake providers."""
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from fxdash.web import headlines, market


DATES = ["2026-09-01", "2026-09-02"]
EMPTY_RSS = b"<rss version='2.0'><channel></channel></rss>"


def rss(event="saved"):
    return f"""<rss version="2.0"><channel><item>
      <title>Yen moves after Bank of Japan comments - Reuters</title>
      <link>https://fixture.test/yen-{event}</link>
      <pubDate>Tue, 01 Sep 2026 08:00:00 GMT</pubDate>
      <source url="https://fixture.test">Reuters</source>
      <description>Yen and monetary policy</description>
    </item></channel></rss>""".encode()


def dxy_frame():
    return pd.DataFrame({"Close": [100.0, 101.0]},
                        index=pd.to_datetime(DATES).tz_localize("UTC"))


@pytest.fixture
def clock(monkeypatch):
    value = [10_000.0]
    # Both cache modules use this time module; all provider time is synthetic.
    monkeypatch.setattr(market.time, "monotonic", lambda: value[0])
    epoch = datetime(2026, 10, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(headlines, "_now_iso",
                        lambda: (epoch + timedelta(seconds=value[0])).isoformat())
    return value


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("initial_clock", [0.0, 10_000.0])
def test_dxy_failed_refresh_is_shared_and_recovers_after_completion_ttl(
        monkeypatch, clock, warm, initial_clock):
    clock[0] = initial_clock
    saved = (pd.Series([98.0, 99.0], index=pd.to_datetime(DATES))
             if warm else None)
    monkeypatch.setattr(market, "_dxy_cache", {
        "series": saved, "at": initial_clock - market.DXY_TTL_S - 1,
    })
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls = []
    state = {"outage": True}

    def download(*args, **kwargs):
        calls.append(1)
        if state["outage"]:
            started.set()
            assert release.wait(5)
            clock[0] += 45  # The cooldown starts when this attempt finishes.
            raise ConnectionResetError("synthetic DXY outage")
        return dxy_frame()

    def second_request():
        second_started.set()
        return market._fetch_dxy()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(market._fetch_dxy)
        try:
            assert started.wait(5)
            second = pool.submit(second_request)
            assert second_started.wait(5)
        finally:
            release.set()
        assert first.result(timeout=5) is saved
        assert second.result(timeout=5) is saved
    assert len(calls) == 1
    if warm:
        assert saved.index[-1].isoformat() == "2026-09-02T00:00:00"
        assert saved.iloc[-1] == 99.0

    finished = clock[0]
    state["outage"] = False
    for offset in (0, market.DXY_TTL_S - 1):
        clock[0] = finished + offset
        assert market._fetch_dxy() is saved
    assert len(calls) == 1

    clock[0] = finished + market.DXY_TTL_S
    recovered = market._fetch_dxy()
    assert len(calls) == 2
    assert recovered.tolist() == [100.0, 101.0]
    assert recovered.index.tolist() == list(pd.to_datetime(DATES))
    assert market._fetch_dxy() is recovered
    assert len(calls) == 2


@pytest.mark.parametrize("invalid", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime([DATES[0]])),
    pd.DataFrame({"Close": [float("nan"), float("inf")]},
                 index=pd.to_datetime(DATES)),
], ids=["none", "empty", "one-bar", "nonfinite"])
def test_dxy_unusable_response_also_cools_down_and_recovers(monkeypatch, clock, invalid):
    monkeypatch.setattr(market, "_dxy_cache", {"series": None, "at": 0.0})
    calls = []
    state = {"invalid": True}

    def download(*args, **kwargs):
        calls.append(1)
        return invalid if state["invalid"] else dxy_frame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    assert market._fetch_dxy() is None
    state["invalid"] = False
    clock[0] += market.DXY_TTL_S - 1
    assert market._fetch_dxy() is None
    assert len(calls) == 1
    clock[0] += 1
    assert market._fetch_dxy().iloc[-1] == 101.0
    assert len(calls) == 2


def test_headline_successful_empty_feed_replaces_saved_news_and_caches_empty(clock):
    calls = []
    state = {"empty": False}

    def fetch(query):
        calls.append(query)
        return EMPTY_RSS if state["empty"] else rss()

    board = headlines.HeadlineBoard(fetcher=fetch)
    saved = board.snapshot(["USDJPY"])
    assert len(saved["items"]) == 1
    state["empty"] = True
    clock[0] += headlines.TTL_S
    empty = board.snapshot(["USDJPY"])
    assert empty["available"] is True
    assert empty["items"] == empty["all_items"] == empty["errors"] == []
    assert "stale" not in empty
    assert empty["fetched_at"] != saved["fetched_at"]
    assert len(calls) == 2
    state["empty"] = False
    assert board.for_pair(["USDJPY"], "USDJPY") == []
    assert board.snapshot(["USDJPY"]) is empty
    assert len(calls) == 2
    clock[0] += headlines.TTL_S
    assert len(board.snapshot(["USDJPY"])["items"]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("recover_empty", [False, True])
def test_headline_failed_attempt_retains_provenance_and_recovers_at_ttl(
        clock, warm, recover_empty):
    calls = []
    state = {"outage": False, "empty": False}

    def fetch(query):
        calls.append(query)
        if state["outage"]:
            raise ConnectionResetError("synthetic RSS outage")
        return EMPTY_RSS if state["empty"] else rss()

    board = headlines.HeadlineBoard(fetcher=fetch)
    saved = board.snapshot(["USDJPY"]) if warm else None
    if warm:
        clock[0] += headlines.TTL_S
    state["outage"] = True
    failed = board.snapshot(["USDJPY"])
    assert len(failed["errors"]) == 1
    if warm:
        assert failed["items"] is saved["items"]
        assert failed["all_items"] is saved["all_items"]
        assert failed["fetched_at"] == saved["fetched_at"]
        assert failed["stale"] is True
    else:
        assert failed["available"] is False
        assert failed["items"] == []
    calls_after_failure = len(calls)
    state.update(outage=False, empty=recover_empty)
    for offset in (0, headlines.TTL_S - 1):
        if offset:
            clock[0] += offset
        assert board.snapshot(["USDJPY"]) is failed
    assert len(calls) == calls_after_failure
    clock[0] += 1
    recovered = board.snapshot(["USDJPY"])
    assert len(calls) == calls_after_failure + 1
    assert recovered["available"] is True
    assert len(recovered["items"]) == (0 if recover_empty else 1)
    assert recovered["errors"] == []
    assert "stale" not in recovered
    assert recovered["fetched_at"] != failed["fetched_at"]


def test_headline_expired_concurrent_refresh_shares_one_complete_board(clock):
    calls = []
    state = {"gated": False}
    started = threading.Event()
    release = threading.Event()
    callers_ready = threading.Barrier(4)

    def fetch(query):
        calls.append(query)
        if state["gated"]:
            started.set()
            assert release.wait(5)
            return rss("refreshed")
        return rss()

    board = headlines.HeadlineBoard(fetcher=fetch)
    saved = board.snapshot(["USDJPY"])
    state["gated"] = True
    clock[0] += headlines.TTL_S

    def request():
        callers_ready.wait(timeout=5)
        return board.snapshot(["USDJPY"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(request) for _ in range(4)]
        try:
            assert started.wait(5)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in pending]
    assert len(calls) == 2  # One initial fetch and one shared expired refresh.
    assert all(result is results[0] for result in results)
    assert results[0]["fetched_at"] != saved["fetched_at"]
    assert results[0]["items"][0]["url"] == "https://fixture.test/yen-refreshed"
    assert "stale" not in results[0]
