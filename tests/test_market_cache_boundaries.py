"""Market and cache failure boundaries using synthetic data and fake providers."""
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fxdash.data import base
from fxdash.web import market
from fxdash.web.app import create_app
from test_web import _write_fixture


DATES = ["2026-01-06", "2026-01-07"]


def put_cache(folder, ticker, values, dates=DATES, timezone=None):
    index = pd.to_datetime(dates)
    if timezone:
        index = index.tz_localize(timezone)
    folder.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace("=", "_").replace("^", "").replace("/", "_")
    pd.DataFrame({ticker: values}, index=index).to_parquet(folder / f"{safe}.parquet")


@pytest.fixture
def offline_market(monkeypatch):
    monkeypatch.setattr(market, "_fetch_dxy", lambda: None)


def test_timezone_sources_keep_original_daily_labels_in_ticker_and_ytd(tmp_path, offline_market):
    cache = tmp_path / "cache"
    # UTC-to-New-York conversion would incorrectly move these labels a day back.
    dates = ["2026-01-06 00:15", "2026-01-07 00:15"]
    put_cache(cache, "EURUSD=X", [1.2, 1.3], dates, "UTC")
    put_cache(cache, "AUDUSD=X", [0.6, 0.7], dates, "America/New_York")
    output = tmp_path / "outputs"
    _write_fixture(output)
    with TestClient(create_app(output, cache_dir=cache)) as client:
        ticker = client.get("/api/market/ticker").json()
        assert ticker["session_date"] == "2026-01-07"
        assert {item["date"] for item in ticker["items"]} == {"2026-01-07"}
        assert {item["code"] for item in ticker["items"]} == {"USDEUR", "USDAUD"}
        ytd = client.get("/api/market/series/USDEUR?range=ytd")
        assert ytd.status_code == 200
        assert ytd.json()["dates"] == DATES
        assert ytd.json()["values"] == pytest.approx([1 / 1.2, 1 / 1.3])


def test_stale_fx_is_omitted_without_null_ticker_items(tmp_path, offline_market):
    put_cache(tmp_path, "EURUSD=X", [1.2, 1.3], ["2025-12-01", "2025-12-02"])
    put_cache(tmp_path, "AUDUSD=X", [0.6, 0.7])
    snapshot = market.MarketData(tmp_path)
    ticker = snapshot.ticker(pd.Timestamp("2026-01-07", tz="UTC"))
    assert ticker["available"] is True
    assert [item["code"] for item in ticker["items"]] == ["USDAUD"]
    assert snapshot.series("USDEUR", "max")["available"] is True


@pytest.mark.parametrize("values", [[0.0, 0.0], [-1.0, -2.0], ["broken", "data"], [None, None]])
def test_one_invalid_fx_source_keeps_another_usable_source(tmp_path, offline_market, values):
    put_cache(tmp_path, "EURUSD=X", values)
    put_cache(tmp_path, "AUDUSD=X", [0.6, 0.7])
    snapshot = market.MarketData(tmp_path)
    assert snapshot.available is True
    assert [item["code"] for item in snapshot.board] == ["USDAUD"]
    assert snapshot.series("USDEUR", "max")["reason"] == "no_cache"


def test_invalid_date_rows_do_not_disable_other_market_sources(tmp_path, offline_market):
    tmp_path.mkdir(exist_ok=True)
    pd.DataFrame({"close": [1.2, 1.3]}, index=["2026-01-06", "not-a-date"]).to_parquet(
        tmp_path / "EURUSD_X.parquet")
    put_cache(tmp_path, "AUDUSD=X", [0.6, 0.7])
    snapshot = market.MarketData(tmp_path)
    assert snapshot.available is True
    assert [item["code"] for item in snapshot.board] == ["USDAUD"]


def test_daily_duplicates_and_nonfinite_values_cannot_make_two_trading_days(tmp_path, offline_market):
    put_cache(tmp_path, "JPY=X", [150.0, 151.0, np.inf],
              ["2026-01-06 00:00", "2026-01-06 04:00", "2026-01-07 00:00"])
    snapshot = market.MarketData(tmp_path)
    assert snapshot.available is False
    assert snapshot.board == []


def test_dxy_concurrent_refresh_shares_one_fake_download(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_download = threading.Event()
    calls = []

    def download(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
            values = [90.0, 91.0]
        else:
            second_download.set()
            values = [100.0, 101.0]
        return pd.DataFrame({"Close": values}, index=pd.to_datetime(DATES))

    def second_refresh():
        second_started.set()
        return market._fetch_dxy()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    monkeypatch.setattr(market, "_dxy_cache", {"series": None, "at": 0.0})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(market._fetch_dxy)
        try:
            assert started.wait(5)
            second = pool.submit(second_refresh)
            assert second_started.wait(5)
            assert not second_download.wait(0.1)
        finally:
            release.set()
        assert first.result(timeout=5).iloc[-1] == 91.0
        assert second.result(timeout=5).iloc[-1] == 91.0
    assert len(calls) == 1
    assert market._fetch_dxy().iloc[-1] == 91.0
    assert len(calls) == 1


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(base, "RETRIES", 1)
    monkeypatch.setattr(base, "_RECORDS", [])
    return pd.DataFrame({"close": [1.2, 1.3]}, index=pd.to_datetime(DATES))


@pytest.mark.parametrize("values", [[np.nan, np.nan], [np.inf, -np.inf], ["broken", "data"]])
def test_invalid_online_response_preserves_good_cache_and_reports_fallback(isolated_cache, values):
    good = isolated_cache
    base._write_cache("synthetic", good)
    path = base.cache_path("synthetic")
    before = path.read_bytes()
    invalid = pd.DataFrame({"close": values}, index=good.index)
    result = base.get_frame("synthetic", lambda: invalid)
    assert result["close"].tolist() == good["close"].tolist()
    assert path.read_bytes() == before
    assert base.records()[-1]["event"] == "fallback_cache"
    assert "no usable rows" in base.records()[-1]["reason"]
    assert not any(item["event"] == "fetch_online" for item in base.records())


def test_numeric_rows_without_dates_cannot_replace_good_cache(isolated_cache):
    good = isolated_cache
    base._write_cache("synthetic", good)
    before = base.cache_path("synthetic").read_bytes()
    undated = pd.DataFrame({"close": [1.4, 1.5]}, index=pd.DatetimeIndex([pd.NaT, pd.NaT]))
    result = base.get_frame("synthetic", lambda: undated)
    assert result["close"].tolist() == [1.2, 1.3]
    assert base.cache_path("synthetic").read_bytes() == before
    assert base.records()[-1]["event"] == "fallback_cache"


def unavailable():
    raise RuntimeError("synthetic online unavailable")


@pytest.mark.parametrize("bad_index", [False, True])
def test_unusable_cache_reaches_user_fallback(isolated_cache, bad_index):
    good = isolated_cache
    bad = pd.DataFrame({"close": [np.nan, np.nan]}, index=good.index)
    if bad_index:
        bad = pd.DataFrame({"close": [1.2, 1.3]}, index=["invalid", "dates"])
    base._write_cache("synthetic", bad)
    result = base.get_frame("synthetic", unavailable, user_loader=lambda: good)
    assert result["close"].tolist() == [1.2, 1.3]
    assert base.records()[-1]["event"] == "fallback_user"


def test_empty_normalised_online_data_reaches_user_fallback(isolated_cache):
    good = isolated_cache
    invalid = pd.DataFrame({"close": [np.nan, np.nan]}, index=good.index)
    result = base.get_frame("synthetic", lambda: invalid, user_loader=lambda: good)
    assert result["close"].tolist() == [1.2, 1.3]
    assert not base.cache_path("synthetic").exists()
    assert base.records()[-1]["event"] == "fallback_user"


def test_partial_parquet_write_failure_preserves_complete_existing_cache(isolated_cache, monkeypatch):
    good = isolated_cache
    base._write_cache("synthetic", good)
    path = base.cache_path("synthetic")
    before = path.read_bytes()

    def fail_write(self, destination, *args, **kwargs):
        Path(destination).write_bytes(b"partial parquet")
        raise OSError("synthetic partial write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_write)
    base._write_cache("synthetic", good * 2)
    assert path.read_bytes() == before
    assert {item.name for item in path.parent.iterdir()} == {path.name}


def test_reader_keeps_complete_cache_during_concurrent_replacement(isolated_cache, monkeypatch):
    good = isolated_cache
    base._write_cache("synthetic", good)
    path = base.cache_path("synthetic")
    started = threading.Event()
    release = threading.Event()
    original_write = pd.DataFrame.to_parquet

    def delayed_write(self, destination, *args, **kwargs):
        Path(destination).write_bytes(b"partial parquet")
        started.set()
        assert release.wait(5)
        return original_write(self, destination, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", delayed_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(base._write_cache, "synthetic", good * 2)
        try:
            assert started.wait(5)
            assert pd.read_parquet(path)["close"].tolist() == [1.2, 1.3]
        finally:
            release.set()
        writer.result(timeout=5)
    assert pd.read_parquet(path)["close"].tolist() == [2.4, 2.6]
