"""Static site builder (SPEC_web §7).

Reuses the web layer's micro fixture: two pairs, one window, two models, integer
contributions. What is pinned here is the contract between the builder and app.js:
the file naming rule, the request set, the manifest, and that a build is a faithful
copy of what the live server would have answered.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fxdash.web import build as B
from fxdash.web.app import STATIC_DIR, create_app
from fxdash.web.market import RANGES as MARKET_RANGES
from test_web import EMPTY_RSS, _write_cache, _write_fixture


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from fxdash.web import headlines, market
    monkeypatch.setattr(market, "_fetch_dxy", lambda: None)
    monkeypatch.setattr(headlines, "_fetch", lambda q: EMPTY_RSS)


@pytest.fixture
def site_app(tmp_path):
    # not "outputs": the isolated_outputs fixture already owns tmp_path/outputs
    root = tmp_path / "pipeline"
    root.mkdir()
    _write_fixture(root)
    cache = tmp_path / "cache"
    _write_cache(cache)
    return root, create_app(root, cache_dir=cache)


def _tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------------ naming rule

def test_file_for_encodes_sorted_parameters_into_the_name():
    assert B.file_for("/meta") == "api/meta.json"
    assert B.file_for("/overview?window=126&model=ols") == "api/overview.model-ols.window-126.json"
    # order in the query string does not matter, the name is canonical
    assert B.file_for("/overview?model=ols&window=126") == "api/overview.model-ols.window-126.json"
    assert B.file_for("/market/series/USDJPY?range=6m") == "api/market/series/USDJPY.range-6m.json"
    assert B.file_for("/pairs/USDJPY/news") == "api/pairs/USDJPY/news.json"


def test_frontend_range_table_matches_the_market_layer():
    """app.js enumerates the ranges the price chart offers; the builder writes one
    file per range from the market layer's table. They must be the same list."""
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    match = re.search(r"const RANGES = \[([^\]]+)\]", source)
    frontend = re.findall(r'"([^"]+)"', match.group(1))
    assert frontend == list(MARKET_RANGES)


def test_request_set_covers_every_page(site_app):
    root, app = site_app
    meta = TestClient(app).get("/api/meta").json()
    reqs = B.request_set(meta)
    pairs, windows, models = meta["pairs"], meta["windows"], meta["models"]
    # 4 fixed + overview and daily at the canonical basis + weekly per combination
    # + a news feed per pair + a price series per pair and range
    expected = 4 + 2 + len(windows) * len(models) + len(pairs) + len(pairs) * len(MARKET_RANGES)
    assert len(reqs) == expected == len(set(reqs))
    assert "/news" in reqs and "/narrative/status" in reqs
    assert f"/overview?window={meta['default_window']}&model={meta['default_model']}" in reqs


# --------------------------------------------------------------- the build

def test_build_writes_assets_every_request_and_the_manifest(site_app, tmp_path):
    root, app = site_app
    out = tmp_path / "site"
    manifest = B.build(out, app=app)

    for name in ("index.html", "app.js", "i18n.js", "charts.js", "methodology.js",
                 "style.css", "vendor/echarts.min.js", ".nojekyll", "build.json"):
        assert (out / name).exists(), name

    written = sorted(p for p in _tree(out) if p.startswith("api/"))
    assert written == manifest["files"]
    assert len(written) == len(B.request_set(TestClient(app).get("/api/meta").json()))
    for rel in written:
        json.loads((out / rel).read_text(encoding="utf-8"))  # every file parses
    assert json.loads((out / "build.json").read_text(encoding="utf-8")) == manifest


def test_build_is_a_faithful_copy_of_the_live_answers(site_app, tmp_path):
    """Byte for byte what the server would have returned: the build adds nothing
    and changes nothing, it only fixes the moment."""
    root, app = site_app
    out = tmp_path / "site"
    manifest = B.build(out, app=app)
    client = TestClient(app)
    for request, rel in manifest["requests"].items():
        assert (out / rel).read_bytes() == client.get("/api" + request).content, request


def test_manifest_carries_a_zoned_build_time_and_the_data_version(site_app, tmp_path):
    root, app = site_app
    moment = datetime(2026, 9, 4, 20, 45, 3, tzinfo=timezone(timedelta(hours=-5)))
    manifest = B.build(tmp_path / "site", app=app, now=moment)
    assert manifest["built_at"] == "2026-09-04T20:45:03-05:00"
    assert manifest["tz_offset"] == "-05:00"
    meta = TestClient(app).get("/api/meta").json()
    assert manifest["data_version"] == meta["data_version"]
    assert manifest["as_of"] == meta["date_range"]["last"]
    assert re.fullmatch(r"[+-]\d\d:\d\d", manifest["tz_offset"])


def test_build_wipes_the_target_and_never_touches_the_inputs(site_app, tmp_path):
    root, app = site_app
    out = tmp_path / "site"
    out.mkdir()
    (out / "stale.txt").write_text("from last night", encoding="utf-8")
    before = _tree(root)
    B.build(out, app=app)
    assert not (out / "stale.txt").exists()      # a build starts from nothing
    assert _tree(root) == before                 # outputs/ untouched, rule 1
    assert not (STATIC_DIR / "build.json").exists()  # the source tree stays a live server


def test_cli_builds_from_explicit_directories(site_app, tmp_path, capsys):
    root, _ = site_app
    out = tmp_path / "site"
    code = B.main(["--out", str(out), "--output-dir", str(root),
                   "--cache-dir", str(tmp_path / "cache")])
    assert code == 0
    assert (out / "build.json").exists()
    printed = capsys.readouterr().out
    assert "api files" in printed and printed.isascii()
