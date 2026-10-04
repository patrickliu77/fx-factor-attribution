"""Browser clock and backend diagnostic agreement; no DOM or providers needed."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_future_and_backend_diagnostics_cannot_appear_healthy(lang):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for runtime presentation checks')
    root = Path(__file__).resolve().parents[1]
    script = r"""
      import assert from 'node:assert/strict';
      const lang=process.argv[1];
      globalThis.localStorage={getItem:()=>lang};
      const {runtimeState,runtimeHtml}=await import('./src/fxdash/web/static/runtime-status.js');
      const now=new Date('2026-10-04T18:00:00Z');
      const data={last_success_state:'green',heartbeat:{state:'green'},runtime:{
        last_success_at:now.toISOString(),latest_attempt:{state:'succeeded'}}};
      const html=()=>runtimeHtml(data,{mode:'static'},now);
      const summary=()=>html().match(/<summary>(.*?)<\/summary>/s)[1];
      assert.equal(runtimeState(data,now).tone,'green');
      data.runtime.last_success_at='2026-10-04T18:00:00.001Z';
      assert.equal(runtimeState(data,now).tone,'red');
      assert.match(html(),/data-runtime-freshness="future"/);
      assert.match(summary(),lang==='zh'?/晚于当前时间/:/time is in the future/);
      data.runtime.last_success_at='2026-10-04T13:00:00-05:00';
      assert.equal(runtimeState(data,now).tone,'green');
      data.heartbeat.state='red';
      assert.equal(runtimeState(data,now).tone,'red');
      assert.match(summary(),lang==='zh'?/运行状态需要检查/:/Run status needs review/);
      data.heartbeat.state='yellow';
      assert.equal(runtimeState(data,now).tone,'yellow');
      data.runtime.last_success_at='2026-10-05T00:00:00Z';
      data.runtime.latest_attempt.state='crashed';
      assert.match(summary(),lang==='zh'?/计算进程异常退出/:/Calculation process crashed/);
    """
    result = subprocess.run([node, '--input-type=module', '-e', script, lang], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
