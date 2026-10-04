"""Invalid queries never masquerade as empty data or a cached successful request."""
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fxdash.narrative import store as NS
from fxdash.web.app import create_app
from test_web import _write_fixture, no_network  # noqa: F401


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "pipeline"
    _write_fixture(root)
    app = create_app(root, cache_dir=tmp_path / "empty-cache")
    with TestClient(app) as client:
        yield client, app, root


@pytest.mark.parametrize("name", ["start", "end"])
@pytest.mark.parametrize("value", ["", "not-a-date", "2026-13-01", "2026-02-30", "20260101", "2026-01-01T00:00:00"])
def test_invalid_series_dates_are_rejected(api, name, value):
    client, _, _ = api
    response = client.get("/api/pairs/USDEUR/series", params={name: value})
    assert response.status_code == 422
    assert name in response.json()["detail"]


def test_reversed_date_range_is_rejected(api):
    response = api[0].get("/api/pairs/USDEUR/series", params={"start": "2026-01-07", "end": "2026-01-01"})
    assert response.status_code == 422


def test_valid_inclusive_range_and_tail_keep_order_and_values(api):
    response = api[0].get("/api/pairs/USDEUR/series", params={
        "start": "2026-01-01", "end": "2026-01-06", "observations": 2, "fields": "core,contributions"})
    assert response.status_code == 200
    body = response.json()
    assert body["dates"] == ["2026-01-05", "2026-01-06"]
    assert len(body["y"]) == 2
    for i, value in enumerate(body["y"]):
        assert value == pytest.approx(body["residual"][i] + sum(v[i] for v in body["contributions"].values()))


def test_valid_out_of_sample_dates_still_return_an_empty_series(api):
    response = api[0].get("/api/pairs/USDEUR/series", params={"start": "2027-01-01", "end": "2027-12-31"})
    assert response.status_code == 200
    assert response.json()["dates"] == []


@pytest.mark.parametrize("path", ["/api/meta", "/api/summary?window=126&model=ols",
                                   "/api/pairs/USDEUR/series?window=126&model=ols"])
def test_conditional_responses_keep_validation_headers_without_body(api, path):
    client, _, _ = api
    first = client.get(path)
    assert first.status_code == 200
    cached = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == first.headers["etag"]
    assert cached.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("start,end,fields", [(None, None, "unknown"), ("bad", None, None),
                                               ("2026-01-07", "2026-01-01", None)])
def test_conditional_header_cannot_bypass_query_validation(api, start, end, fields):
    client, app, _ = api
    params = {k: v for k, v in {"start": start, "end": end, "fields": fields}.items() if v is not None}
    tag = f'W/"{app.state.store.current().data_version}:series:USDEUR:126:ols:{start}:{end}:{fields}:None"'
    response = client.get("/api/pairs/USDEUR/series", params=params, headers={"If-None-Match": tag})
    assert response.status_code == 422


@pytest.mark.parametrize("params", [{"window": 999}, {"model": "unknown"}, {"window": 999, "model": "unknown"}])
def test_daily_explicit_invalid_selectors_are_rejected(api, params):
    assert api[0].get("/api/narrative/daily", params=params).status_code == 422


def test_daily_omitted_defaults_still_follow_available_data(tmp_path):
    root = tmp_path / "pipeline"
    _write_fixture(root)
    for path in (root / "contract").glob("year=*/part.parquet"):
        frame = pd.read_parquet(path)
        frame = frame.loc[frame.model == "ols"].copy()
        frame["window"] = 63
        frame["model"] = "ridge"
        frame.to_parquet(path, index=False)
    with TestClient(create_app(root, cache_dir=tmp_path / "empty-cache")) as client:
        response = client.get("/api/narrative/daily")
        assert response.status_code == 200
        assert (response.json()["window"], response.json()["model"]) == (63, "ridge")
        assert client.get("/api/narrative/daily?window=126").status_code == 422
        assert client.get("/api/narrative/daily?model=ols").status_code == 422


@pytest.mark.parametrize("age,expected", [(-0.000001, "red"), (float("nan"), "red"),
    (float("inf"), "red"), (float("-inf"), "red"), (0, "green"), (26, "green"),
    (26.000001, "yellow"), (72, "yellow"), (72.000001, "red")])
def test_narrative_clock_boundaries_keep_existing_thresholds(age, expected):
    assert NS.heartbeat_state(age)[0] == expected


@pytest.mark.parametrize("raw", ["{broken", "[1]", "true", "3", "null"])
def test_unreadable_narrative_status_is_readable_as_a_diagnostic(api, raw):
    client, _, root = api
    folder = root / "narrative"
    folder.mkdir()
    path = folder / "status.json"
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    response = client.get("/api/narrative/status")
    assert response.status_code == 200
    assert response.json()["state"] == "red"
    assert "narrative status.json unreadable" in response.json()["reasons"]
    assert path.read_bytes() == before


def test_future_narrative_run_is_red_but_publication_age_is_independent(api):
    client, _, root = api
    now = datetime.now(timezone.utc)
    folder = root / "narrative"
    folder.mkdir()
    path = folder / "status.json"
    path.write_text(json.dumps({"last_run": (now + timedelta(days=5)).isoformat(),
                                "reasons": ["saved note", 1, None]}), encoding="utf-8")
    result = client.get("/api/narrative/status").json()
    assert result["state"] == "red" and result["age_hours"] < 0
    assert any("clock" in reason for reason in result["reasons"])
    assert "saved note" in result["reasons"]
    assert all(isinstance(reason, str) for reason in result["reasons"])
    path.write_text(json.dumps({"last_run": (now - timedelta(hours=1)).isoformat(),
        "last_published": (now + timedelta(days=5)).isoformat(), "reasons": "invalid-list"}), encoding="utf-8")
    result = client.get("/api/narrative/status").json()
    assert result["state"] == "green" and result["published_age_hours"] < 0
    assert result["reasons"] == []
