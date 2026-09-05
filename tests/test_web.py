"""Web layer (SPEC_web §5).

Tiny fixture: 2 year partitions (crossing the year boundary), 2 pairs, integer
contributions. Three core anchors: the 1d block equals the last row untouched;
the 5d total is a hand-checkable integer; the identity still closes after
aggregation -- together they prove that the only new maths is summation.
"""

import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fxdash.web.app import create_app
from fxdash.web.store import DataStore, Snapshot, SnapshotError

PAIR_A, PAIR_B = "USDEUR", "USDAUD"
FACTORS = ["DOLLAR_LOO", "CARRY_LOO", "dVIX"]
# trading-day sequence crossing a weekend (Friday -> Monday), pinning
# "trading-day window, not calendar-day window"
DATES = [
    "2025-12-29", "2025-12-30", "2025-12-31",  # year partition boundary
    "2026-01-01", "2026-01-02",
    "2026-01-05", "2026-01-06", "2026-01-07",
]


def _row(date, pair, i, model="ols", provisional=False):
    # integer contribution series: DOLLAR=i, CARRY=2i, dVIX=3i; residual=4i;
    # y closes the identity
    contrib = {"DOLLAR_LOO": float(i), "CARRY_LOO": 2.0 * i, "dVIX": 3.0 * i}
    residual = 4.0 * i
    return {
        "date": pd.Timestamp(date),
        "pair": pair,
        "window": 126,
        "model": model,
        "betas": json.dumps({f: 0.1 for f in FACTORS}),
        "contributions": json.dumps(contrib),
        "r2_full": 0.5,
        "r2_exog": 0.4 if i != 3 else float("nan"),  # plant a NaN to pin sanitisation
        "selected_factors": json.dumps(FACTORS[:2] if model == "lasso" else []),
        "residual": residual,
        "residual_z": 0.1 * i,
        "stale_flags": json.dumps(["d2Y_DIFF.foreign"] if i == 5 else []),
        "systematic": contrib["DOLLAR_LOO"] + contrib["CARRY_LOO"],
        "exogenous": contrib["dVIX"],
        "y": sum(contrib.values()) + residual,
        "lambda": 0.01 if model == "lasso" else None,
        "provisional": provisional,
        "schema_version": "1.1.0",
    }


def _write_fixture(root, n_days=len(DATES)):
    rows = []
    for i, date in enumerate(DATES[:n_days], start=1):
        last = i == n_days
        for pair in (PAIR_A, PAIR_B):
            for model in ("ols", "lasso"):
                rows.append(_row(date, pair, i, model=model, provisional=last))
    frame = pd.DataFrame(rows)
    for year, block in frame.groupby(frame["date"].dt.year):
        d = root / "contract" / f"year={year}"
        d.mkdir(parents=True, exist_ok=True)
        block.to_parquet(d / "part.parquet", index=False)

    (root / "status.json").write_text(json.dumps({
        "state": "green",
        "generated_at": "2026-01-07 19:35:00",
        "provisional_rows": 4,
        "heartbeat": {"state": "green", "age_hours": 0.5,
                      "last_live_success": "2026-01-07 19:35:00"},
        "source_as_of": {"USDEUR.fx": "2026-01-07"},
        "reasons": [],
    }), encoding="utf-8")
    (root / "run_manifest.json").write_text(json.dumps({
        "mode": "live",
        "benchmark": {"all_within_tol": True, "rank_ok": True, "table": []},
        "health_findings": [], "health_current": [],
        "merge": {"overwritten": 4, "frozen_kept": 100},
        "provisional_overwrites": [], "source_as_of": {},
        "coverage_shrink_allowed": False, "rewrite_history_allowed": False,
    }), encoding="utf-8")
    (root / "coverage.json").write_text(json.dumps({
        "backfill_start": "2010-01-01",
        "pairs": {PAIR_A: {"first": DATES[0], "last": DATES[-1], "rows": n_days}},
    }), encoding="utf-8")
    pd.DataFrame({
        "date": pd.to_datetime(DATES[:n_days]),
        "window": 126,
        "corr_pc1_dollar": 0.95,
        "corr_pc2_carry": -0.2,
        "carry_projection_r2": 0.6,
        "var_pc1": 0.6, "var_pc2": 0.2,
        "warn_flags": "", "schema_version": "1.1.0",
    }).to_parquet(root / "pca_monitor.parquet", index=False)


# tiny copy of the market cache. Dates align with the contract fixture's last
# three days.
CACHE_DATES = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])


def _write_cache(root):
    """Six FX tickers + oil + US 10Y. CL=F gets one extra bar beyond FX's last
    day, to pin the "whole board aligns to the FX trading day" defence."""
    root.mkdir(parents=True, exist_ok=True)

    def put(name, values, index=CACHE_DATES):
        safe = name.replace("=", "_")
        pd.DataFrame({name: values}, index=index).to_parquet(root / (safe + ".parquet"))

    put("JPY=X", [150.0, 151.0, 152.0])          # USDJPY is already USD/XXX
    put("EURUSD=X", [1.25, 1.24, 1.23])          # inverted, USDEUR rises
    put("AUDUSD=X", [0.80, 0.80, 0.80])          # inverted, USDAUD is a constant 1.25
    put("CAD=X", [1.30, 1.31, 1.32])
    put("NOK=X", [10.0, 10.1, 10.2])
    put("MXN=X", [17.0, 17.0, 16.83])
    put("DGS10", [4.00, 4.10, 4.25])             # yield: bp is the level diff times 100
    put("CL=F", [80.0, 81.0, 82.0, 999.0],
        index=CACHE_DATES.append(pd.to_datetime(["2026-01-08"])))


EMPTY_RSS = b"<rss><channel><title>empty</title></channel></rss>"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Web tests never hit the network. There are only two external fetches, DXY
    and the daily headlines; both are stubbed."""
    from fxdash.web import headlines, market
    monkeypatch.setattr(market, "_fetch_dxy", lambda: None)
    monkeypatch.setattr(headlines, "_fetch", lambda q: EMPTY_RSS)


@pytest.fixture
def site(tmp_path):
    """Attribution-side tests use an empty market cache to stay hermetic: never
    read the repo's real data/cache."""
    _write_fixture(tmp_path)
    empty = tmp_path / "nocache"
    empty.mkdir()
    app = create_app(tmp_path, cache_dir=empty)
    return TestClient(app), app


@pytest.fixture
def market_site(tmp_path):
    _write_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_cache(cache)
    return TestClient(create_app(tmp_path, cache_dir=cache))


def test_meta_reflects_the_data_not_the_config(site):
    client, _ = site
    meta = client.get("/api/meta").json()
    assert set(meta["pairs"]) == {PAIR_A, PAIR_B}
    assert meta["windows"] == [126]
    assert set(meta["models"]) == {"ols", "lasso"}
    # factor list derived from the data's betas key order
    assert meta["factors"][PAIR_A]["baseline"] == FACTORS
    assert meta["date_range"] == {"first": DATES[0], "last": DATES[-1]}
    assert meta["schema_version"] == "1.1.0"


def test_scale_1d_equals_the_last_row_untouched(site):
    """The 1d block equals the last row untouched."""
    client, _ = site
    s = client.get("/api/summary", params={"window": 126, "model": "ols"}).json()
    block = s["scales"]["1d"]["pairs"][PAIR_A]
    i = len(DATES)  # i of the last row
    assert block["n_days"] == 1
    assert block["contributions"] == {
        "DOLLAR_LOO": float(i), "CARRY_LOO": 2.0 * i, "dVIX": 3.0 * i,
    }
    assert block["residual"] == 4.0 * i
    assert block["y"] == 10.0 * i  # 1+2+3+4 = 10 times i


def test_scale_5d_is_a_hand_checkable_sum(site):
    """The 5d total is hand-checkable: i runs 4..8 over the last 5 days, sum 30."""
    client, _ = site
    s = client.get("/api/summary").json()
    block = s["scales"]["5d"]["pairs"][PAIR_A]
    total_i = sum(range(4, 9))  # 30
    assert block["n_days"] == 5
    assert block["contributions"]["DOLLAR_LOO"] == pytest.approx(total_i)
    assert block["contributions"]["CARRY_LOO"] == pytest.approx(2 * total_i)
    assert block["contributions"]["dVIX"] == pytest.approx(3 * total_i)
    assert block["residual"] == pytest.approx(4 * total_i)


def test_identity_survives_aggregation(site):
    """The identity closes after aggregation: y_sum = Σ contrib + residual_sum,
    within 1e-12."""
    client, _ = site
    s = client.get("/api/summary").json()
    for scale in ("1d", "5d", "21d"):
        for pair, block in s["scales"][scale]["pairs"].items():
            total = sum(block["contributions"].values()) + block["residual"]
            assert total == pytest.approx(block["y"], abs=1e-12), (scale, pair)
            assert block["systematic"] + block["exogenous"] == pytest.approx(
                sum(block["contributions"].values()), abs=1e-12
            )


def test_window_counts_trading_days_not_calendar_days(site):
    """The fixture dates cross a weekend and a year end: the 5d window spans 9
    calendar days and still takes exactly 5 rows."""
    client, _ = site
    block = client.get("/api/summary").json()["scales"]["5d"]["pairs"][PAIR_A]
    assert block["start"] == "2026-01-01"
    assert block["end"] == "2026-01-07"


def test_short_history_degrades_to_available_days(site):
    """With only 8 rows the 21d block reports n_days=8 instead of erroring."""
    client, _ = site
    block = client.get("/api/summary").json()["scales"]["21d"]["pairs"][PAIR_A]
    assert block["n_days"] == len(DATES)


@pytest.mark.parametrize("days", [1, 5, 21])
def test_attribution_horizon_matches_existing_summary(site, days):
    client, _ = site
    result = client.get('/api/attribution/weekly', params={'days': days}).json()
    rows = {r['pair']: r for r in result['pairs']}
    expected = client.get('/api/summary').json()['scales'][f'{days}d']['pairs'][PAIR_A]
    row = rows[PAIR_A]
    assert result['days'] == days
    assert row['n_days'] == expected['n_days']
    assert row['y_bp'] == pytest.approx(expected['y'] * 1e4)
    assert row['residual_bp'] == pytest.approx(expected['residual'] * 1e4)
    assert row['contains_provisional'] == expected['contains_provisional']
    for key, value in row['contributions_bp'].items():
        assert value == pytest.approx(expected['contributions'][key] * 1e4)


def test_research_tail_slices_every_series_consistently(site):
    client, _ = site
    path = f'/api/pairs/{PAIR_A}/series'
    full = client.get(path, params={'model': 'lasso'}).json()
    tail = client.get(path, params={'model': 'lasso', 'observations': 3}).json()
    assert tail['dates'] == full['dates'][-3:]
    for key in ('y', 'residual', 'r2_full', 'r2_exog', 'provisional', 'lambda'):
        assert tail[key] == full[key][-3:]
    for key in ('contributions', 'betas', 'selected'):
        for factor, values in full[key].items():
            assert tail[key][factor] == values[-3:]
    assert client.get(path, params={'observations': 0}).status_code == 422
    assert client.get('/api/attribution/weekly', params={'days': 7}).status_code == 422


def test_provisional_contaminates_the_window(site):
    client, _ = site
    s = client.get("/api/summary").json()
    for scale in ("1d", "5d", "21d"):
        block = s["scales"][scale]["pairs"][PAIR_A]
        assert block["contains_provisional"] is True
        assert block["provisional_dates"] == [DATES[-1]]


def test_series_is_columnar_and_parsed(site):
    client, _ = site
    r = client.get(f"/api/pairs/{PAIR_A}/series", params={"model": "ols"})
    body = r.json()
    n = len(DATES)
    assert body["dates"] == DATES
    assert len(body["y"]) == n
    for f in FACTORS:
        assert len(body["contributions"][f]) == n
        assert isinstance(body["contributions"][f][0], (int, float))
    # ols carries none of the lasso-only keys
    assert "selected" not in body and "lambda" not in body
    assert body["factor_groups"] == {
        "systematic": ["DOLLAR_LOO", "CARRY_LOO"], "exogenous": ["dVIX"],
    }
    # stale sparse event table
    assert body["stale_events"] == [
        {"date": DATES[4], "flags": ["d2Y_DIFF.foreign"]}
    ]


def test_lasso_series_carries_selection_and_lambda(site):
    client, _ = site
    body = client.get(f"/api/pairs/{PAIR_A}/series", params={"model": "lasso"}).json()
    assert body["selected"]["DOLLAR_LOO"] == [1] * len(DATES)
    assert body["selected"]["dVIX"] == [0] * len(DATES)
    assert body["lambda"] == [0.01] * len(DATES)


def test_nan_is_sanitised_to_null(site):
    """r2_exog has a NaN planted at i=3; the response must parse and hold null."""
    client, _ = site
    body = client.get(f"/api/pairs/{PAIR_A}/series").json()
    assert body["r2_exog"][2] is None
    assert body["r2_exog"][0] == 0.4


def test_bad_params_are_rejected_legibly(site):
    client, _ = site
    assert client.get("/api/pairs/USDXXX/series").status_code == 404
    r = client.get("/api/summary", params={"window": 100})
    assert r.status_code == 422
    assert "126" in r.text


def test_overview_is_one_request(site):
    client, _ = site
    body = client.get("/api/overview").json()
    assert body["status_digest"]["state"] == "green"
    assert {p["pair"] for p in body["pairs"]} == {PAIR_A, PAIR_B}
    row = next(p for p in body["pairs"] if p["pair"] == PAIR_A)
    assert row["date"] == DATES[-1]
    assert row["provisional"] is True
    assert row["top_factor"] == "dVIX"  # 3i is always the largest
    assert "scales" in body["summary"]
    assert body["freshness"]["coverage"][PAIR_A]["rows"] == len(DATES)


def test_narrative_is_pending_mount_point(site):
    client, _ = site
    body = client.get(f"/api/narrative/{PAIR_A}").json()
    assert body["status"] == "pending"
    assert body["trigger"]["residual_z"] == pytest.approx(0.1 * len(DATES))


def test_system_and_pca_endpoints(site):
    client, _ = site
    sys_body = client.get("/api/system").json()
    assert sys_body["benchmark"]["all_within_tol"] is True
    assert sys_body["flags"]["rewrite_history_allowed"] is False
    pca = client.get("/api/pca").json()
    assert pca["available"] is True
    assert pca["thresholds"]["carry_projection_r2"] == 0.5


def test_hot_reload_picks_up_new_data(tmp_path):
    """status.json mtime advances + files are stable -> snapshot updates and
    data_version advances."""
    _write_fixture(tmp_path, n_days=6)
    store = DataStore(tmp_path, settle_s=0, signature_gap_s=0)
    v1 = store.snapshot.data_version
    assert store.snapshot.combo(PAIR_A, 126, "ols").dates[-1] == DATES[5]

    time.sleep(0.01)
    _write_fixture(tmp_path, n_days=8)  # data advances by two days
    store._last_check = -1e9  # force past the 30s throttle
    snap = store.current()
    assert snap.data_version != v1
    assert snap.combo(PAIR_A, 126, "ols").dates[-1] == DATES[7]
    assert store.reload_state == "fresh"


def test_corrupt_reload_keeps_serving_the_old_snapshot(tmp_path):
    """Live race: truncate a parquet + advance status.json -> keep the old
    snapshot, do not blow up, report stale_retrying."""
    _write_fixture(tmp_path)
    store = DataStore(tmp_path, settle_s=0, signature_gap_s=0)
    v1 = store.snapshot.data_version

    # truncate one partition to half a file and advance the commit marker
    part = next(tmp_path.glob("contract/year=2026/part.parquet"))
    raw = part.read_bytes()
    part.write_bytes(raw[: len(raw) // 2])
    time.sleep(0.01)
    (tmp_path / "status.json").write_text(
        (tmp_path / "status.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    store._last_check = -1e9
    snap = store.current()  # triggers a reload, which fails
    assert snap.data_version == v1  # the old snapshot is still being served
    assert store.reload_state == "stale_retrying"
    # data on the old snapshot is still queryable
    assert snap.combo(PAIR_A, 126, "ols") is not None


def test_row_count_regression_is_rejected(tmp_path):
    """A new snapshot with fewer rows is rejected -- reusing the pipeline's
    overwrite-discipline intuition."""
    _write_fixture(tmp_path)
    store = DataStore(tmp_path, settle_s=0, signature_gap_s=0)
    v1 = store.snapshot.data_version

    time.sleep(0.01)
    # rewrite with less data
    import shutil

    shutil.rmtree(tmp_path / "contract")
    _write_fixture(tmp_path, n_days=3)
    store._last_check = -1e9
    snap = store.current()
    assert snap.data_version == v1
    assert store.reload_state == "stale_retrying"


def test_api_routes_are_not_swallowed_by_static(site):
    """Static mounted at the root must not swallow /api; with no static directory
    the root route may 404 but the API must stay alive."""
    client, _ = site
    assert client.get("/api/healthz").json()["ok"] is True
    assert client.get("/api/meta").status_code == 200


def test_etag_round_trip(site):
    client, _ = site
    r1 = client.get("/api/meta")
    tag = r1.headers["etag"]
    r2 = client.get("/api/meta", headers={"If-None-Match": tag})
    assert r2.status_code == 304


# ---------------------------------------------------- market and placeholders

def test_ticker_hidden_when_cache_is_absent(site):
    """An unreadable cache must not 500; degrade wholesale to a placeholder the
    frontend can recognise."""
    client, _ = site
    body = client.get("/api/market/ticker").json()
    assert body["available"] is False
    assert body["items"] == []


def test_ticker_board_is_pinned_to_one_session(market_site):
    body = market_site.get("/api/market/ticker").json()
    assert body["available"] is True
    assert body["session_date"] == "2026-01-07"
    by_code = {q["code"]: q for q in body["items"]}

    # six pairs + oil + 10Y (DXY goes over the network, stubbed empty in this
    # fixture; it has its own dedicated tests)
    assert set(by_code) >= {"USDJPY", "USDEUR", "USDCAD", "USDNOK", "USDAUD",
                            "USDMXN", "WTI", "US10Y"}
    # every item must land on the same trading day
    assert {q["date"] for q in body["items"]} == {"2026-01-07"}

    jpy = by_code["USDJPY"]
    assert jpy["last"] == pytest.approx(152.0)
    assert jpy["chg_pct"] == pytest.approx((152.0 / 151.0 - 1) * 100)
    assert jpy["chg_bp"] == pytest.approx(jpy["chg_pct"] * 100)
    assert jpy["direction"] == 1

    # EURUSD=X and AUDUSD=X quote the other way round; only inverted are they
    # USD/XXX (CLAUDE.md 3)
    assert by_code["USDAUD"]["last"] == pytest.approx(1.25)
    assert by_code["USDAUD"]["direction"] == 0
    assert by_code["USDEUR"]["last"] == pytest.approx(1 / 1.23)
    assert by_code["USDEUR"]["direction"] == 1


def test_board_ignores_bars_beyond_the_fx_session(market_site):
    """The 2026-01-08 bar in the CL=F cache must not be broadcast as the day's
    quote."""
    by_code = {q["code"]: q for q in market_site.get("/api/market/ticker").json()["items"]}
    assert by_code["WTI"]["last"] == pytest.approx(82.0)
    assert by_code["WTI"]["chg_pct"] == pytest.approx((82.0 / 81.0 - 1) * 100)


def test_yield_bp_is_a_level_difference_not_a_return(market_site):
    """bp for a yield and bp for a price are different things; mixing them
    reports 15bp as 366bp."""
    by_code = {q["code"]: q for q in market_site.get("/api/market/ticker").json()["items"]}
    us10y = by_code["US10Y"]
    assert us10y["kind"] == "yield"
    assert us10y["chg_bp"] == pytest.approx((4.25 - 4.10) * 100)  # 15.0


def test_price_series_ranges(market_site):
    # intraday is not collected yet; the frontend shows a placeholder based on
    # reason rather than an empty chart
    intraday = market_site.get(f"/api/market/series/{PAIR_A}?range=1d").json()
    assert intraday["available"] is False
    assert intraday["reason"] == "intraday_pending"

    full = market_site.get(f"/api/market/series/{PAIR_A}?range=max").json()
    assert full["available"] is True
    assert full["dates"] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert full["values"] == pytest.approx([1 / 1.25, 1 / 1.24, 1 / 1.23])
    assert full["first"] == pytest.approx(0.8)
    assert full["direction"] == 1

    # too few rows degrades to what exists rather than erroring
    assert market_site.get(f"/api/market/series/{PAIR_A}?range=5y").json()["available"] is True


def test_price_series_rejects_bad_arguments(market_site):
    assert market_site.get(f"/api/market/series/{PAIR_A}?range=42y").status_code == 422
    assert market_site.get("/api/market/series/USDXXX?range=6m").status_code == 404


def test_news_is_evidence_only_no_allocation_fields(site):
    """News is contemporaneous associative evidence only (2026-09-02 ruling,
    replacing the even-split allocation).

    share, allocated_bp and allocation_rule must not appear in the response: once
    such a field exists the frontend will eventually draw it, and readers will
    certainly read it as "caused"."""
    client, _ = site
    body = client.get("/api/news").json()
    assert "allocation_rule" not in body
    # the fixture's output_dir has no narrative/, so a structurally complete empty
    # result is required instead of reading the repo's real artifacts. Reading the
    # global would make the tmp_path fixture useless
    assert body["week"]["items"] == [] and body["today"]["items"] == []
    assert body["covered_days"] == []



def test_attribution_weekly_buckets_cover_every_factor(site):
    """The five buckets must hold every factor. Missing one silently drops a
    contribution, and the total will not show it."""
    from fxdash.web import newsfeed as NF
    client, _ = site
    body = client.get("/api/attribution/weekly?window=126&model=ols").json()
    assert body["bucket_order"][-1] == "residual"
    known = {f for _k, _l, members in NF.BUCKETS for f in members}
    assert {"DOLLAR_LOO", "CARRY_LOO", "d2Y_DIFF", "d10Y_DIFF", "dVIX"} <= known
    for row in body["pairs"]:
        assert set(row["buckets"]) == {k for k, _l, _m in NF.BUCKETS}


def test_daily_narrative_facts_rank_by_absolute_move(site):
    client, _ = site
    body = client.get("/api/narrative/daily?window=126&model=ols").json()
    assert body["status"] == "pending"
    movers = body["facts"]["movers"]
    assert len(movers) == 2
    moves = [abs(m["y"]) for m in movers]
    assert moves == sorted(moves, reverse=True)
    for m in movers:
        shares = m["shares"]
        assert sum(shares.values()) == pytest.approx(1.0)


def test_trading_day_gate(monkeypatch):
    from fxdash.web import market
    # 2026-01-03 is a Saturday
    assert market.is_trading_day(pd.Timestamp("2026-01-03 12:00", tz="America/New_York")) is False
    # 2026-01-01 New Year's Day
    assert market.is_trading_day(pd.Timestamp("2026-01-01 12:00", tz="America/New_York")) is False
    # 2026-01-05 Monday, not a holiday
    assert market.is_trading_day(pd.Timestamp("2026-01-05 12:00", tz="America/New_York")) is True


def test_dxy_is_aligned_to_the_session_even_with_timestamped_bars(tmp_path, monkeypatch):
    """Yahoo's timestamps carry a time of day; without normalising to midnight
    today's bar is judged later than the session and dropped entirely, leaving
    DXY permanently a day behind everything else."""
    from fxdash.web import market

    stamped = pd.Series(
        [98.0, 99.0, 100.0, 123.0],
        index=pd.to_datetime([
            "2026-01-05 04:00", "2026-01-06 04:00",
            "2026-01-07 04:00",          # same day as FX's last, just with a time
            "2026-01-08 04:00",          # beyond FX's last day, must not enter the board
        ]).tz_localize("UTC"),
    )
    monkeypatch.setattr(market, "_fetch_dxy", lambda: stamped)

    _write_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_cache(cache)
    client = TestClient(create_app(tmp_path, cache_dir=cache))

    board = client.get("/api/market/ticker").json()["items"]
    dxy = next(q for q in board if q["code"] == "DXY")
    assert dxy["date"] == "2026-01-07"
    assert dxy["last"] == pytest.approx(100.0)
    assert dxy["prev"] == pytest.approx(99.0)
    assert dxy["chg_pct"] == pytest.approx((100.0 / 99.0 - 1) * 100)
    # the whole board still has exactly one session
    assert {q["date"] for q in board} == {"2026-01-07"}


def test_board_omits_dxy_when_the_fetch_fails(market_site):
    """A failed network fetch should cost one item, not a 500, and must not drag
    down the rest of the board."""
    board = market_site.get("/api/market/ticker").json()
    assert board["available"] is True
    codes = {q["code"] for q in board["items"]}
    assert "DXY" not in codes
    assert {"USDJPY", "WTI", "US10Y"} <= codes


def test_board_drops_a_source_that_fell_a_week_behind(tmp_path, monkeypatch):
    """A single missing day still displays; trailing by a week means the source
    is dead and its stale value must not be broadcast as today's change."""
    from fxdash.web import market

    dead = pd.Series(
        [90.0, 91.0],
        index=pd.to_datetime(["2025-12-01", "2025-12-02"]),
    )
    monkeypatch.setattr(market, "_fetch_dxy", lambda: dead)

    _write_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_cache(cache)
    client = TestClient(create_app(tmp_path, cache_dir=cache))

    board = client.get("/api/market/ticker").json()
    assert board["available"] is True
    assert "DXY" not in {q["code"] for q in board["items"]}


def test_board_tolerates_a_single_missing_day(tmp_path, monkeypatch):
    """The upstream site occasionally misses a day (DXY really has no 2026-08-28
    bar); that case must still display."""
    from fxdash.web import market

    gapped = pd.Series(
        [98.0, 99.0],
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),  # 01-07 missing
    )
    monkeypatch.setattr(market, "_fetch_dxy", lambda: gapped)

    _write_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_cache(cache)
    client = TestClient(create_app(tmp_path, cache_dir=cache))

    dxy = next(q for q in client.get("/api/market/ticker").json()["items"]
               if q["code"] == "DXY")
    assert dxy["date"] == "2026-01-06"
    assert dxy["last"] == pytest.approx(99.0)


def test_static_assets_always_revalidate(site):
    """Static assets must carry no-cache. Without it the browser caches
    heuristically, the frontend changes and a user refresh shows nothing, leaving
    only "remember to hard-refresh" as the remedy."""
    client, _ = site
    for path in ("/", "/app.js", "/style.css"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", path


def test_narrative_heartbeat_ages_even_when_the_file_stops_updating(site, tmp_path):
    """This is the entire reason the heartbeat exists, and the easiest place to
    get wrong.

    The age_hours inside status.json is computed at **write** time. Once the job
    dies the file stops updating, so that number freezes at the last successful
    run and the heartbeat fails to detect exactly the thing it guards against.
    The endpoint must trust only last_run and recompute the age at **read** time.
    """
    import json
    from datetime import datetime, timedelta, timezone

    client, app = site
    root = app.state.store.snapshot.output_dir / "narrative"
    root.mkdir(parents=True, exist_ok=True)

    stale = datetime.now(timezone.utc).astimezone() - timedelta(hours=40)
    # deliberately write an old snapshot that claims it "just ran": at the moment
    # of writing it really was green
    (root / "status.json").write_text(json.dumps({
        "state": "green",
        "last_run": stale.isoformat(timespec="seconds"),
        "last_published": stale.isoformat(timespec="seconds"),
        "age_hours": 0.1,
        "reasons": [],
    }), encoding="utf-8")

    body = client.get("/api/narrative/status").json()
    assert body["state"] == "yellow", body       # 40 hours is well past the 26h amber line
    assert body["age_hours"] > 39
    assert body["reasons"]


def test_narrative_heartbeat_is_red_when_it_has_never_run(site):
    """An unreadable heartbeat must report red. Silently treating it as normal is
    exactly the failure mode this heartbeat guards against."""
    client, _ = site
    body = client.get("/api/narrative/status").json()
    assert body["state"] == "red"
    assert body["age_hours"] is None


def _narrative_day(date, url="https://example.com/story", title="Yen intervention"):
    """The minimal narrative artifact that newsfeed can aggregate."""
    return {
        "date": date,
        "pairs": [{
            "pair": PAIR_A,
            "published": True,
            "facts": {"residual_bp": 80.0, "y_bp": -80.0},
            "narrative": {"sources_used": ["S1"]},
            "sources": [{"id": "S1", "url": url, "title": title,
                         "source": "Reuters", "published": date}],
            "evidence": {"event_kind": "intervention", "assessment": "accounts_for"},
        }],
    }


def _write_narrative(app, day):
    import json
    root = app.state.store.snapshot.output_dir / "narrative"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"date={day['date']}.json").write_text(
        json.dumps(day), encoding="utf-8")


def test_news_week_is_a_trading_day_window_not_the_last_files(site):
    """The week window must filter by trading days, not by "the last N files".

    Narrative artifacts are sparse, only trigger days have content. Taking them by
    file count puts a month-old story on the page labelled this week; it happened
    on real data.
    """
    client, app = site
    _write_narrative(app, _narrative_day(DATES[-1], url="https://in.example/a",
                                         title="Fresh intervention story"))
    _write_narrative(app, _narrative_day("2025-11-03", url="https://out.example/b",
                                         title="Ancient unrelated story"))

    body = client.get("/api/news").json()
    # the window is contract's last 5 trading days, the same as the Attribution page
    assert body["week_start"] == DATES[-5]
    assert body["week_end"] == DATES[-1]
    urls = [i["url"] for i in body["week"]["items"]]
    assert urls == ["https://in.example/a"]
    assert body["covered_days"] == [DATES[-1]]
    assert body["fallback"] is None  # this week has content, no fallback needed
    # evidence fields complete, allocation fields absent
    story = body["week"]["items"][0]
    assert story["evidence"] == [{
        "date": DATES[-1], "pair": PAIR_A, "residual_bp": 80.0,
        "residual_z": None, "y_bp": -80.0,
        "event_kind": "intervention", "assessment": "accounts_for",
    }]
    assert "share" not in story and "allocated_bp" not in story

    # the pair panel uses the "recent trigger days" scope (spanning weeks); both
    # days are present, newest first
    feed = client.get(f"/api/pairs/{PAIR_A}/news").json()
    assert [i["url"] for i in feed["items"]] == [
        "https://in.example/a", "https://out.example/b"]
    assert feed["items"][0]["evidence"][0]["date"] == DATES[-1]



def test_news_falls_back_to_the_latest_flagged_day_with_its_date(site):
    """When the week window is quiet, fall back to the most recent trigger day,
    but always with its date, never passing it off as this week."""
    client, app = site
    _write_narrative(app, _narrative_day("2025-11-03", url="https://out.example/b"))

    body = client.get("/api/news").json()
    assert body["week"]["items"] == []
    assert body["covered_days"] == []
    assert body["fallback"]["date"] == "2025-11-03"
    assert [i["url"] for i in body["fallback"]["items"]] == ["https://out.example/b"]
    # today's headlines likewise only accept in-window artifacts
    assert body["today"]["items"] == []


HEAD_RSS = b"""<rss version="2.0"><channel>
<item><title>Oil surge lifts krone - Reuters</title>
<link>https://news.google.com/rss/articles/head1</link>
<pubDate>Wed, 02 Sep 2026 07:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source>
<description>Brent jumped after supply news.</description></item>
<item><title>Central bank holds rates - Bloomberg</title>
<link>https://news.google.com/rss/articles/head2</link>
<pubDate>Tue, 01 Sep 2026 08:00:00 GMT</pubDate>
<source url="https://bloomberg.com">Bloomberg</source>
<description>Rates unchanged.</description></item>
</channel></rss>"""


def test_headline_board_dedupes_across_pairs_and_caches(monkeypatch):
    """One story hitting several pairs merges the pair labels; no refetch within
    the TTL."""
    from fxdash.web.headlines import HeadlineBoard

    calls = []

    def fetcher(query):
        calls.append(query)
        return HEAD_RSS

    board = HeadlineBoard(fetcher=fetcher)
    out = board.snapshot([PAIR_A, PAIR_B])
    assert len(calls) == 2                      # one call per pair
    assert [i["url"] for i in out["items"]] == [
        "https://news.google.com/rss/articles/head1",
        "https://news.google.com/rss/articles/head2",
    ]                                           # deduped, newest date first
    assert out["items"][0]["pairs"] == sorted([PAIR_A, PAIR_B])  # fetched in pair-name order
    assert out["items"][0]["source"] == "Reuters"

    board.snapshot([PAIR_A, PAIR_B])
    assert len(calls) == 2                      # served straight from cache within the TTL

    # when every fetch fails, keep serving the last cache and surface the errors,
    # never pretend there is no news
    def boom(query):
        raise ConnectionResetError("down")

    board._fetcher = boom
    board._stamp = 0.0                          # force expiry
    stale = board.snapshot([PAIR_A, PAIR_B])
    assert len(stale["items"]) == 2
    assert stale.get("stale") is True
    assert stale["errors"]


def test_today_headlines_are_live_and_reach_the_pair_panel(site, monkeypatch):
    """Today's headlines are decoupled from the trigger gate: even on quiet days
    the page must carry that day's news.

    The original design retrieved only on residual anomalies, so the News page
    starved in a quiet week; the user spotted it immediately by comparing against
    a GPT daily brief. Headlines became a pure RSS direct read, while commentary
    still goes through the trigger gate.

    "Today" holds only wall-clock-today stories; earlier ones in this calendar
    week go in their own earlier list (user ruling: Today should not carry 8/31
    items). The wall clock is stubbed so the test does not depend on the real date.
    """
    from fxdash.web import headlines
    client, app = site
    monkeypatch.setattr(headlines, "_fetch", lambda q: HEAD_RSS)
    monkeypatch.setattr(headlines, "today_str", lambda: "2026-09-02")

    body = client.get("/api/news").json()
    # a snapshot taken at fetched_at, never called "live" (2026-09-04 ruling)
    assert body["today"]["mode"] == "fetched"
    assert body["today"]["fetched_at"]
    assert body["today"]["date"] == "2026-09-02"  # wall-clock date, not contract as_of
    assert [i["url"] for i in body["today"]["items"]] == [
        "https://news.google.com/rss/articles/head1"]   # today's only
    assert body["today"]["items"][0]["pairs"]           # carries pair labels
    # the 09-01 item lands in "earlier this week" (2026-09-02 is a Wednesday, so
    # Monday is 08-31)
    assert body["earlier"]["start"] == "2026-08-31"
    assert [i["url"] for i in body["earlier"]["items"]] == [
        "https://news.google.com/rss/articles/head2"]

    feed = client.get(f"/api/pairs/{PAIR_A}/news").json()
    # the pair panel shows the last two days, both today and earlier
    assert {h["url"] for h in feed["headlines"]} == {
        "https://news.google.com/rss/articles/head1",
        "https://news.google.com/rss/articles/head2"}
    assert feed["headlines_fetched_at"]          # the pair panel states its fetch time too
    assert feed["items"] == []                   # residual-linked stories still only on trigger days


def test_news_survives_a_dead_feed_without_lying(site, monkeypatch):
    """Dead news source: no 500, no silently pretending there is no news today,
    and the errors ride along in the response."""
    from fxdash.web import headlines

    def boom(query):
        raise TimeoutError("feed down")

    client, _ = site
    monkeypatch.setattr(headlines, "_fetch", boom)
    body = client.get("/api/news").json()
    assert body["today"]["mode"] == "empty"
    assert body["today"]["errors"]


DUP_RSS = b"""<rss version="2.0"><channel>
<item><title>U.S. dollar weakens sharply against the Japanese yen after market interventions - Reuters</title>
<link>https://news.google.com/rss/articles/dupA</link>
<pubDate>Wed, 02 Sep 2026 07:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>US dollar weakens sharply against the Japanese yen after market interventions - AP</title>
<link>https://news.google.com/rss/articles/dupB</link>
<pubDate>Wed, 02 Sep 2026 08:00:00 GMT</pubDate>
<source url="https://apnews.com">AP</source></item>
</channel></rss>"""


def test_near_duplicate_headlines_collapse_to_one():
    """Two outlets running the same event keep only one item (user ruling). The
    U.S. vs US spelling difference does not block dedup."""
    from fxdash.web.headlines import HeadlineBoard

    board = HeadlineBoard(fetcher=lambda q: DUP_RSS)
    out = board.snapshot([PAIR_A])
    assert len(out["items"]) == 1
    assert out["items"][0]["url"] == "https://news.google.com/rss/articles/dupA"


def test_near_duplicate_stories_merge_with_evidence_intact(site):
    """Near-duplicate titles merge into one, merged-away items are recorded in
    duplicates and stay traceable; evidence is not diluted.

    Real case: on the same day Reuters and AP each ran "US dollar weakens
    sharply...", the same event. After merging, that pair-day's residual evidence
    appears once, because it was always the same measurement and does not change
    with the number of outlets.
    """
    client, app = site
    day = {
        "date": DATES[-1],
        "pairs": [{
            "pair": PAIR_A,
            "published": True,
            "facts": {"residual_bp": 80.0, "y_bp": -80.0, "residual_z": -2.4},
            "narrative": {"sources_used": ["S1", "S2", "S3"]},
            "sources": [
                {"id": "S1", "url": "https://x/1", "published": DATES[-1],
                 "title": "U.S. dollar weakens sharply against the Japanese yen after market interventions",
                 "source": "Reuters"},
                {"id": "S2", "url": "https://x/2", "published": DATES[-1],
                 "title": "US dollar weakens sharply against the Japanese yen after market interventions",
                 "source": "AP"},
                {"id": "S3", "url": "https://x/3", "published": DATES[-1],
                 "title": "Bank of Japan leaves rates unchanged at its meeting",
                 "source": "WSJ"},
            ],
            "evidence": {"event_kind": "intervention"},
        }],
    }
    _write_narrative(app, day)

    body = client.get("/api/news").json()
    items = body["week"]["items"]
    assert len(items) == 2                       # three sources, two events
    merged = next(i for i in items if i.get("duplicates"))
    assert [d["source"] for d in merged["duplicates"]] == ["AP"]
    # one evidence row per pair-day: the residual is a measurement and does not
    # double with the number of outlets
    assert len(merged["evidence"]) == 1
    assert merged["evidence"][0]["residual_bp"] == 80.0
    assert merged["evidence"][0]["residual_z"] == -2.4


def test_citation_matrix_reaches_across_the_week_window(site):
    """The matrix scope is "the last few trigger days", not the week window:
    triggers fire every 4.5 to 5 days, so restricted to this week the matrix
    would be empty most of the time (the user caught this in practice)."""
    client, app = site
    _write_narrative(app, _narrative_day("2025-11-03", url="https://old.example/b",
                                         title="Old flagged day story"))

    body = client.get("/api/attribution/weekly?window=126&model=ols").json()
    groups = body["matrix"]["groups"]
    assert [g["date"] for g in groups] == ["2025-11-03"]   # old trigger days count too, matrix does not starve
    g = groups[0]
    assert g["residuals"][PAIR_A]["residual_bp"] == 80.0
    assert len(g["rows"]) == 1
    assert [c["cited"] for c in g["rows"][0]["cells"] if c["pair"] == PAIR_A] == [True]
    assert body["story_counts"][PAIR_A] == 1
    # evidence wording, not allocation wording
    assert "allocat" not in body["matrix"]["note"].lower()



def test_overview_serves_robustness_and_degrades_honestly(site):
    """Robustness state ships with overview; the fixture has only the ols and
    lasso models, so a three-way comparison cannot be formed and it must report
    available=False rather than faking three-way agreement."""
    client, _ = site
    body = client.get("/api/overview").json()
    assert set(body["robustness"]) == {PAIR_A, PAIR_B}
    for state in body["robustness"].values():
        assert state == {"available": False}
    attr = client.get("/api/attribution/weekly").json()
    assert attr["robustness"] == body["robustness"]


def test_story_kind_is_content_based_not_source_based():
    """Display-layer content classification (2026-09-02 ruling): marker words and
    question marks classify as opinion, events pass through. Classified by content
    not by source: the WSJ also publishes Opinion, small sites also report events."""
    from fxdash.web.newsfeed import story_kind

    assert story_kind("Japan intervenes in currency market for first time since 2022") == "event"
    assert story_kind("Euro zone inflation rises above 3%, cementing ECB rate hike bets") == "event"
    # marker words the user named
    assert story_kind("Opinion | Can the U.S. Treasury Save the Yen?") == "opinion"
    assert story_kind("Analysis: How Bessent is pushing the Fed") == "opinion"
    assert story_kind("AUD/USD Price Forecast: Slides to one-week low") == "opinion"
    assert story_kind("AU GDP Unlikely to Derail RBA Hike, AUD/USD Eyes ISM, NFP") == "opinion"
    assert story_kind("Yen could fall further, strategists say") == "opinion"
    assert story_kind("Why the dollar is rising") == "opinion"
    assert story_kind("Week Ahead for FX, Bonds: U.S. Jobs Data in Focus") == "opinion"
    # a trailing question mark classifies as opinion
    assert story_kind("Is the yen done falling?") == "opinion"
    # technical-level markers (2026-09-02 ruling: narrow word list)
    assert story_kind("AUD/USD Dips as 0.7125 Support Holds, Key Levels to Watch") == "opinion"
    assert story_kind("USD/JPY tests resistance at 161.20") == "opinion"
    assert story_kind("EUR/USD pivots near 1.0850 fibonacci retracement") == "opinion"
    # support matches phrases only, pinned in both directions (2026-09-02 narrowing ruling)
    assert story_kind("USD/JPY finds support at 158.40 ahead of BoJ") == "opinion"
    assert story_kind("EUR/USD breaks support as bears take charge") == "opinion"
    assert story_kind("Australian Dollar: Strong GDP and carry support, BBH") == "event"
    assert story_kind("Yen intervention wins US support, officials hint") == "event"
    # ordinary words deliberately left out do not misfire
    assert story_kind("Canadian dollar weakens to one-week low after BoC holds") == "event"
    assert story_kind("Yen hits three-month high after intervention") == "event"


def test_display_search_terms_diverge_only_where_ambiguity_demands(monkeypatch):
    """Display-layer query terms are decoupled from the narrative layer
    (2026-09-02 ruling): they differ only where disambiguation demands it, and the
    narrative layer's PAIR_TERMS is untouched."""
    from fxdash.narrative import retrieve as R
    from fxdash.web import headlines as HL

    assert "euro OR" in R.PAIR_TERMS["USDEUR"]          # narrative layer keeps full recall
    assert "euro OR" not in HL.DISPLAY_PAIR_TERMS["USDEUR"]
    assert "ECB" in HL.DISPLAY_PAIR_TERMS["USDEUR"]
    assert '-"Form 8.3"' in HL.DISPLAY_PAIR_TERMS["USDNOK"]
    # the other four pairs match the narrative layer, avoiding a pointless fork
    for pair in ("USDJPY", "USDCAD", "USDAUD", "USDMXN"):
        assert HL.DISPLAY_PAIR_TERMS[pair] == R.PAIR_TERMS[pair]

    queries = []
    board = HL.HeadlineBoard(fetcher=lambda q: (queries.append(q), EMPTY_RSS)[1])
    board.snapshot(["USDEUR", "USDNOK"])
    assert any("ECB" in q for q in queries)
    assert any('-"Form 8.3"' in q for q in queries)
    assert not any(" euro OR" in q for q in queries)


OPINION_RSS = b"""<rss version="2.0"><channel>
<item><title>Japan intervenes in the currency market - Reuters</title>
<link>https://news.google.com/rss/articles/ev1</link>
<pubDate>Wed, 02 Sep 2026 07:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Opinion: The yen has further to fall - WSJ</title>
<link>https://news.google.com/rss/articles/op1</link>
<pubDate>Wed, 02 Sep 2026 08:00:00 GMT</pubDate>
<source url="https://wsj.com">WSJ</source></item>
</channel></rss>"""


def test_opinion_headlines_fold_into_their_own_bucket(site, monkeypatch):
    """Opinion pieces stay out of the main list without being dropped: they go
    into the opinions collapsed section (2026-09-02 ruling two)."""
    from fxdash.web import headlines
    client, _ = site
    monkeypatch.setattr(headlines, "_fetch", lambda q: OPINION_RSS)
    monkeypatch.setattr(headlines, "today_str", lambda: "2026-09-02")

    body = client.get("/api/news").json()
    assert [i["url"] for i in body["today"]["items"]] == [
        "https://news.google.com/rss/articles/ev1"]      # main list holds events only
    ops = body["opinions"]["items"]
    assert [i["url"] for i in ops] == ["https://news.google.com/rss/articles/op1"]
    assert ops[0]["kind"] == "opinion"


def test_cited_stories_carry_kind_and_cited_for_the_exception_rule(site):
    """Exception rule for the flagged list (ruling four): a cited source is kept
    and marked cited regardless of kind. cited_stories is all-cited by
    construction, and the field is an evidence-chain marker."""
    client, app = site
    _write_narrative(app, _narrative_day(
        DATES[-1], url="https://x/op",
        title="Analysis: How the intervention reshaped positioning"))
    body = client.get("/api/news").json()
    story = body["week"]["items"][0]
    assert story["kind"] == "opinion"
    assert story["cited"] is True


def test_cited_story_context_carries_the_commentary_paragraph(site):
    """Explain must show the relevant paragraph of the commentary that cited it
    plus that day's information (2026-09-02 ruling), not a restatement of internal
    system state."""
    client, app = site
    day = _narrative_day(DATES[-1], url="https://x/1", title="Intervention reported")
    rec = day["pairs"][0]
    rec["facts"]["residual_z"] = -4.17
    rec["narrative"]["en"] = {"why_unexplained": "The model left 80 bp unexplained."}
    rec["narrative"]["zh"] = {"why_unexplained": "模型留下 80 bp 未解释。"}
    _write_narrative(app, day)

    ctx = client.get("/api/news").json()["week"]["items"][0]["context"][PAIR_A]
    assert ctx["why_unexplained"]["en"] == "The model left 80 bp unexplained."
    assert ctx["why_unexplained"]["zh"] == "模型留下 80 bp 未解释。"
    assert ctx["residual_bp"] == 80.0 and ctx["residual_z"] == -4.17
    assert ctx["date"] == DATES[-1]


def test_citation_matrix_groups_by_day_and_keeps_residual_off_the_cells(site):
    """The residual is a property of the trading day, not of any single news item
    (2026-09-02 ruling).

    Cells carry only a citation mark; the residual appears once per group, keyed
    by date and pair. The old version printed the same residual into every cell,
    and a table reads naturally as "the value of this cell".
    """
    client, app = site
    day = {
        "date": DATES[-1],
        "pairs": [{
            "pair": PAIR_A, "published": True,
            "facts": {"residual_bp": 80.0, "y_bp": -80.0, "residual_z": -2.4},
            "narrative": {"sources_used": ["S1", "S2"]},
            "sources": [
                {"id": "S1", "url": "https://x/1", "published": DATES[-1],
                 "title": "Central bank steps in", "source": "Reuters"},
                {"id": "S2", "url": "https://x/2", "published": DATES[-1],
                 "title": "Officials decline to confirm", "source": "AP"},
            ],
            "evidence": {"event_kind": "intervention"},
        }],
    }
    _write_narrative(app, day)

    m = client.get("/api/attribution/weekly").json()["matrix"]
    assert [g["date"] for g in m["groups"]] == [DATES[-1]]
    g = m["groups"][0]
    # the residual appears once per (date, pair)
    assert g["residuals"][PAIR_A]["residual_bp"] == 80.0
    assert g["residuals"][PAIR_A]["residual_z"] == -2.4
    assert PAIR_B not in g["residuals"]
    # two stories; the cells carry only citation marks and no bp at all
    assert len(g["rows"]) == 2
    for row in g["rows"]:
        assert {c["pair"] for c in row["cells"]} == {PAIR_A, PAIR_B}
        assert [c["cited"] for c in row["cells"] if c["pair"] == PAIR_A] == [True]
        assert [c["cited"] for c in row["cells"] if c["pair"] == PAIR_B] == [False]
        for c in row["cells"]:
            assert "residual_bp" not in c and "residual_z" not in c
    assert "belongs to the day" in m["note"]
