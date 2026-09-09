import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fxdash.web.app import create_app, STATIC_DIR
from fxdash.web.build import request_set, file_for
from test_web import _write_fixture


def test_saved_variance_exposed_without_changing_monitor(tmp_path):
    _write_fixture(tmp_path)
    path = tmp_path / "pca_monitor.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.index[-1], "var_pc1"] = np.nan
    frame.iloc[::-1].to_parquet(path, index=False)
    original = path.read_bytes()
    client = TestClient(create_app(tmp_path, cache_dir=tmp_path / "empty-cache"))
    data = client.get("/api/pca?window=126").json()
    assert data["available"] and data["role"] == "panel_structure_monitor"
    assert data["training_end"] == "preceding_common_observation"
    assert data["dates"] == sorted(data["dates"])
    assert data["var_pc1"][-1] is None and data["var_pc1"][0] == .6
    assert data["var_pc2"][0] == .2
    assert client.get("/api/pca?window=63").json() == {"available":False, "window":63}
    assert path.read_bytes() == original


def test_static_request_set_covers_all_pca_windows():
    requests = request_set({"pairs":["USDJPY"], "windows":[63,126,252], "models":["ols"]})
    for window in (63,126,252):
        request = f"/pca?window={window}"
        assert requests.count(request) == 1
        assert file_for(request) == f"api/pca.window-{window}.json"


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for display helper tests")
    setup = """
      import assert from 'node:assert/strict';
      globalThis.localStorage = {getItem:()=> 'en', setItem:()=>{}};
      globalThis.document = {documentElement:{}};
      globalThis.getComputedStyle = () => ({getPropertyValue:()=> ''});
      const S = await import(MODULE);
      const I = await import(LANGUAGE);
      const data = {available:true, window:126, dates:['2026-09-04','2026-09-07'],
        var_pc1:[.6,.7], corr_pc1_dollar:[-.95,.96], carry_projection_r2:[.5,.6]};
    """.replace("MODULE", json.dumps((STATIC_DIR / "structure.js").as_uri())).replace(
        "LANGUAGE", json.dumps((STATIC_DIR / "i18n.js").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", setup + body], capture_output=True,
                            text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_percentages_are_distinct_and_negative_correlations_use_magnitude():
    run_js("""
      const snapshot = S.structureSnapshot(data);
      assert.deepEqual(snapshot.dollar, [.95,.96]);
      const opts = S.structureOptions(data);
      assert.deepEqual(opts.variance.series[0].data, [.6,.7]);
      assert.equal(opts.alignment.series.length, 2);
      assert.equal(opts.alignment.yAxis.min, 0);
      assert.equal(opts.alignment.yAxis.max, 1);
      for (const lang of ['en','zh']) {
        I.setLang(lang);
        const html = S.structureHtml(data);
        assert.ok(html.includes('70.0%') && html.includes('96.0%') && html.includes('60.0%'));
        assert.ok(!/[—–]/.test(html));
        assert.ok(!/不是.*而是/.test(html));
        assert.ok(!S.structureShell().includes(' open'));
      }
    """)


def test_missing_values_do_not_become_zero_or_repeat_previous_readings():
    run_js("""
      const missing = {...data,var_pc1:[.6,null],carry_projection_r2:null,corr_pc1_dollar:[null,2]};
      const value = S.structureSnapshot(missing);
      assert.deepEqual(value.variance, [.6,null]);
      assert.deepEqual(value.carry, [null,null]);
      assert.deepEqual(value.dollar, [null,null]);
      assert.ok(!S.structureHtml(missing).includes('60.0%'));
      assert.equal(S.structureOptions(missing).variance.series[0].connectNulls, false);
      for (const bad of [null,{}, {...data,available:false}, {...data,dates:[]},
        {...data,dates:['2026-09-07','2026-09-07']}, {...data,window:'<img src=x>'},
        {...data,dates:['2026-02-30','2026-09-07']}]) {
        assert.equal(S.structureSnapshot(bad).available, false);
        assert.deepEqual(S.structureOptions(bad), {});
      }
    """)


def test_display_limits_history_without_modifying_input():
    run_js("""
      const dates = Array.from({length:300},(_,i)=>new Date(Date.UTC(2025,0,i+1)).toISOString().slice(0,10));
      const full = {...data,dates,var_pc1:dates.map(()=>.6),corr_pc1_dollar:dates.map(()=>-.9),carry_projection_r2:dates.map(()=>.5)};
      const before = JSON.stringify(full), value = S.structureSnapshot(full);
      assert.equal(value.dates.length, 252);
      assert.equal(value.dates[0], dates[48]);
      assert.equal(JSON.stringify(full), before);
    """)
