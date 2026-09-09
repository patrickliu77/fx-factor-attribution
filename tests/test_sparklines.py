"""Mini quote semantics run under Node without a browser or production data."""
import json
import shutil
import subprocess

import pytest

from fxdash.web.app import STATIC_DIR


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node needed for JavaScript display helpers")
    setup = """
      import assert from 'node:assert/strict';
      const CH = await import(MODULE);
      globalThis.document = {documentElement:{}};
      globalThis.getComputedStyle = () => ({getPropertyValue:() => ''});
      globalThis.echarts = {graphic:{LinearGradient:class {
        constructor(x,y,x2,y2,stops) {this.colorStops=stops;}
      }}};
    """.replace("MODULE", json.dumps((STATIC_DIR / "charts.js").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", setup + body],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("values,period_direction,daily_direction,color", [
    ([160, 159, 157, 155, 156], -1, 1, "up"),
    ([150, 154, 158, 157, 156], 1, -1, "down"),
    ([150, 153, 155, 156, 156], 1, 0, "mute"),
    ([155, 156], 1, 1, "up"),
])
def test_only_latest_segment_uses_daily_color(values, period_direction, daily_direction, color):
    data = {"available": True, "values": values, "direction": period_direction,
            "dates": [f"2026-09-{i+1:02d}" for i in range(len(values))]}
    run_js(f"""
      const data = {json.dumps(data)};
      const before = JSON.stringify(data);
      const observed = CH.sparkObservation(data), option = CH.sparkOption(data), C = CH.tokens();
      assert.equal(observed.direction, {daily_direction});
      assert.equal(observed.count, data.values.length);
      assert.equal(observed.last, data.values.at(-1));
      assert.equal(observed.changePct, (data.values.at(-1)/data.values.at(-2)-1)*100);
      assert.equal(option.series.length, 2);
      assert.equal(option.series[0].lineStyle.color, C.mute);
      assert.equal(option.series[1].lineStyle.color, C.{color});
      assert.deepEqual(option.series[1].data, data.values.map((v,i) => i >= data.values.length-2 ? v : null));
      assert.equal(option.series[1].connectNulls, false);
      assert.equal(option.series[1].areaStyle, undefined);
      assert.equal(option.yAxis.scale, true);
      assert.equal(JSON.stringify(data), before);
    """)


def test_invalid_daily_points_do_not_produce_a_colored_chart():
    run_js("""
      const good = {available:true,dates:['2026-09-04','2026-09-07'],values:[155,156]};
      for (const data of [null, {}, {...good,available:false}, {...good,dates:['2026-09-07']},
        {...good,values:[155,null]}, {...good,values:[0,156]}, {...good,values:[155,'156']},
        {...good,values:[155,Infinity]}, {...good,values:[155,NaN]},
        {...good,dates:['2026-09-07','2026-09-07']}, {...good,dates:['2026-09-07','2026-09-04']},
        {...good,dates:['2026-13-01','2026-13-02']}, {...good,dates:['2026-02-30','2026-03-07']},
        {...good,dates:[],values:[]}]) {
        assert.equal(CH.sparkObservation(data), null);
        assert.deepEqual(CH.sparkOption(data).series.map(s=>s.data), [[],[]]);
      }
      const observed = CH.sparkObservation(good);
      assert.equal(observed.previousDate, '2026-09-04');
      assert.equal(observed.date, '2026-09-07');
    """)
