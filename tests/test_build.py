"""Static site builder (SPEC_web §7).

Reuses the web layer's micro fixture: two pairs, one window, two models, integer
contributions. What is pinned here is the contract between the builder and app.js:
the file naming rule, the request set, the manifest, and that a build is a faithful
copy of what the live server would have answered.
"""

import json
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fxdash.web import build as B
from fxdash.web.app import STATIC_DIR, create_app
from fxdash.web.market import RANGES as MARKET_RANGES
from test_web import EMPTY_RSS, _row, _write_cache, _write_fixture


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from fxdash.web import headlines, market
    from fxdash.narrative import morning
    # The news response now records when run files were observed. Byte-for-byte
    # build comparisons require one clock instant for both reads.
    monkeypatch.setattr(morning, "now_utc", lambda: datetime(2026,9,5,tzinfo=timezone.utc))
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
    assert B.file_for("/briefing/operations") == "api/briefing/operations.json"
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
    # Fixed status/operations endpoints + overview and daily at the canonical basis + weekly per combination
    # + a news feed per pair + a price series per pair and range
    expected = (6 + 2 + 3 * len(windows) * len(models) + len(pairs) + 2 * len(windows)
                + len(pairs) * len(MARKET_RANGES)
                + len(pairs) * len(windows) * len(models))
    assert len(reqs) == expected == len(set(reqs))
    assert "/news" in reqs and "/narrative/status" in reqs
    assert "/briefing/operations" in reqs
    assert f"/overview?window={meta['default_window']}&model={meta['default_model']}" in reqs


# --------------------------------------------------------------- the build

def test_build_writes_assets_every_request_and_the_manifest(site_app, tmp_path):
    root, app = site_app
    out = tmp_path / "site"
    manifest = B.build(out, app=app)

    for name in ("index.html", "app.js", "i18n.js", "charts.js", "methodology.js",
                 "methodology-figures.js", "research.js", "presentation.js", "context.js", "pair-news.js",
                 "style.css", "fonts.css", "vendor/echarts.min.js", ".nojekyll", "build.json"):
        assert (out / name).exists(), name

    for font in (STATIC_DIR / "fonts").iterdir():
        assert (out / "fonts" / font.name).read_bytes() == font.read_bytes()

    # Full-size links on Methodology must work under the Pages project path too.
    for figure in ("pipeline", "timeline", "lasso"):
        for lang in ("en", "zh"):
            rel = f"figures/{figure}-{lang}.svg"
            assert (out / rel).read_bytes() == (STATIC_DIR / rel).read_bytes()

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


@pytest.mark.parametrize("hours", [0, 9, -4])
def test_explicit_build_timezone_is_independent_of_host(site_app, tmp_path, hours):
    _, app = site_app
    moment = datetime(2026, 9, 14, 9, 0, tzinfo=timezone(timedelta(hours=hours)))
    manifest = B.build(tmp_path / "site", app=app, now=moment)
    assert manifest["built_at"] == moment.isoformat(timespec="seconds")
    assert manifest["tz_offset"] == f"{hours:+03d}:00"


def test_cli_builds_from_explicit_directories(site_app, tmp_path, capsys):
    root, _ = site_app
    out = tmp_path / "site"
    code = B.main(["--out", str(out), "--output-dir", str(root),
                   "--cache-dir", str(tmp_path / "cache")])
    assert code == 0
    assert (out / "build.json").exists()
    printed = capsys.readouterr().out
    assert "api files" in printed and printed.isascii()


def test_readonly_git_pack_is_removed_on_rebuild(site_app, tmp_path):
    _, app = site_app
    out = tmp_path / "site"
    pack = out / ".git" / "objects" / "pack" / "pack-test.idx"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"old generated Git metadata")
    pack.chmod(stat.S_IREAD)
    B.build(out, app=app)
    assert not (out / ".git").exists()
    assert (out / "build.json").exists()


def test_build_refuses_input_overlap_before_deleting(site_app, tmp_path):
    root, app = site_app
    before = _tree(root)
    for target in (root, root / "contract", tmp_path, tmp_path / "cache"):
        with pytest.raises(ValueError, match="overlaps"):
            B.build(target, app=app)
    assert _tree(root) == before


def test_build_refuses_repository_source_and_broad_roots(site_app):
    _, app = site_app
    for target in (B.REPO_ROOT, B.REPO_ROOT.parent, B.REPO_ROOT / "src", STATIC_DIR):
        with pytest.raises(ValueError, match="protected root|overlaps"):
            B.build(target, app=app)


def test_unreadable_input_keeps_previous_build(tmp_path):
    out = tmp_path / "site"
    out.mkdir()
    marker = out / "previous.txt"
    marker.write_text("previous build", encoding="utf-8")
    from fxdash.web.store import SnapshotError
    with pytest.raises(SnapshotError):
        B.build(out, output_dir=tmp_path / "missing")
    assert marker.read_text(encoding="utf-8") == "previous build"


def _advance_fixture(root):
    """Complete a second synthetic pipeline commit with one extra trading day."""
    contract = root / "contract/year=2026/part.parquet"
    frame = pd.read_parquet(contract)
    extra = pd.DataFrame([_row("2026-01-08", pair, 9, model=model)
                          for pair in ("USDEUR", "USDAUD") for model in ("ols", "lasso")])
    pd.concat([frame, extra], ignore_index=True).to_parquet(contract, index=False)
    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    status["generated_at"] = "2026-01-08 19:35:00"
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")


@pytest.mark.parametrize("has_previous", [False, True])
def test_api_failure_leaves_previous_site_and_no_partial_export(site_app, tmp_path, has_previous, monkeypatch):
    root, app = site_app
    out = tmp_path / "site"
    if has_previous:
        out.mkdir()
        (out / "index.html").write_bytes(b"previous index")
        (out / "build.json").write_bytes(b"previous manifest")
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}

    @app.middleware("http")
    async def fail_after_initial_api_files(request, call_next):
        if request.url.path == "/api/market/ticker":
            raise RuntimeError("fixture_api_failure")
        return await call_next(request)

    with pytest.raises(RuntimeError, match="fixture_api_failure"):
        B.build(out, app=app)
    assert out.exists() is has_previous
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert not list(tmp_path.glob(".site.build-*"))
    assert not list(tmp_path.glob(".site.previous-*"))

    # A failed export must reset the pin before the ordinary web service reads
    # the next completed pipeline commit.
    app.state.store.settle_s = app.state.store.signature_gap_s = 0
    monkeypatch.setattr("fxdash.web.store.CHECK_INTERVAL_S", 0)
    _advance_fixture(root)
    meta = TestClient(app).get("/api/meta").json()
    assert meta["date_range"]["last"] == "2026-01-08"


def test_install_failure_restores_previous_directory(site_app, tmp_path, monkeypatch):
    _, app = site_app
    out = tmp_path / "site"
    out.mkdir()
    (out / "index.html").write_bytes(b"previous index")
    (out / "build.json").write_bytes(b"previous manifest")
    rename = Path.rename

    def fail_install(source, target):
        if source.name.startswith(".site.build-"):
            raise PermissionError("fixture_install_locked")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(PermissionError, match="fixture_install_locked"):
        B.build(out, app=app)
    assert (out / "index.html").read_bytes() == b"previous index"
    assert (out / "build.json").read_bytes() == b"previous manifest"
    assert sorted(p.name for p in out.iterdir()) == ["build.json", "index.html"]
    assert not list(tmp_path.glob(".site.build-*"))
    assert not list(tmp_path.glob(".site.previous-*"))


def test_export_pins_one_version_across_pipeline_commit_and_other_web_reader(site_app, tmp_path, monkeypatch):
    root, app = site_app
    store = app.state.store
    first = store.snapshot
    store.settle_s = store.signature_gap_s = 0
    monkeypatch.setattr("fxdash.web.store.CHECK_INTERVAL_S", 0)
    client_type = B.TestClient
    requests = []

    class CommittingClient:
        def __init__(self, app):
            self.client = client_type(app)

        def get(self, path):
            response = self.client.get(path)
            requests.append(path)
            if len(requests) == 1:
                assert path == "/api/meta"
                _advance_fixture(root)
                # An independent caller still hot reloads during the export;
                # only the build's request context holds the original snapshot.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    current = executor.submit(store.current).result()
                assert current.date_last == "2026-01-08"
                assert current.data_version != first.data_version
            return response

    monkeypatch.setattr(B, "TestClient", CommittingClient)
    out = tmp_path / "site"
    manifest = B.build(out, app=app)
    assert requests.count("/api/meta") == 1
    meta = json.loads((out / "api/meta.json").read_bytes())
    status = json.loads((out / "api/status.json").read_bytes())
    news = json.loads((out / "api/news.json").read_bytes())
    overview = json.loads((out / B.file_for("/overview?window=126&model=ols")).read_bytes())
    assert manifest["data_version"] == meta["data_version"] == overview["data_version"] == first.data_version
    assert status["server"]["data_version"] == first.data_version
    assert manifest["as_of"] == meta["date_range"]["last"] == overview["as_of"] == news["as_of"] == "2026-01-07"
    live_meta = client_type(app).get("/api/meta").json()
    assert live_meta["date_range"]["last"] == "2026-01-08"
    assert live_meta["data_version"] != manifest["data_version"]
