"""Saved delivery evidence cannot become current success as the browser clock advances."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_operations_ui_distinguishes_configuration_submission_and_stale_checks(lang):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for delivery presentation checks')
    root = Path(__file__).resolve().parents[1]
    script = r"""
      import assert from 'node:assert/strict';
      const lang=process.argv[1];
      globalThis.localStorage={getItem:()=>lang};
      const {operationsState,runtimeObservationState,briefingOperationsHtml}=await import('./src/fxdash/web/static/brief-operations.js');
      const now=new Date('2026-10-05T14:00:00Z');
      const make=()=>({schema_version:1,runtime_observation:{state:'current',observed_at:now.toISOString(),
        runtime:{state:'ready',persistent:true,dependencies_ready:true,audio_backend:'azure'},
        tools:{git:{available:true},ffprobe:{available:true}},
        credentials:{BANXICO_TOKEN:{configured:true},AZURE_SPEECH_KEY:{configured:true},AZURE_SPEECH_REGION:{configured:true}},tasks:Object.fromEntries(
          ['live','narrative','publish','briefing','catchup'].map(name=>['fxdash-'+name,{registered:true,enabled:true}]))},
        email:{config_enabled:true,configuration_valid:true,credential_configured:true,delivery_policy:'allow_text',
          today:{date:'2026-10-05',languages:{en:{state:'submitted',content_mode:'audio'},zh:{state:'submitted',content_mode:'text_only'}}},history:[]}});
      const summary=data=>briefingOperationsHtml(data,now).match(/<summary>(.*?)<\/summary>/s)[1];
      assert.equal(operationsState(null,now),'not_observed');
      assert.match(briefingOperationsHtml(null,now),lang==='zh'?/语音策略<\/dt><dd>未知/:/Audio policy<\/dt><dd>Unknown/);
      assert.equal(operationsState({schema_version:3},now),'not_observed');
      assert.equal(operationsState(make(),new Date(NaN)),'not_observed');
      const data=make();
      assert.equal(operationsState(data,now),'submitted');
      data.runtime_observation.state='stale';
      assert.equal(operationsState(data,now),'stale');
      data.runtime_observation.state='current';
      assert.match(summary(data),lang==='zh'?/今日邮件已提交/:/emails submitted/);
      assert.match(briefingOperationsHtml(data,now),lang==='zh'?/文字版/:/Text only/);
      assert.match(briefingOperationsHtml(data,now),lang==='zh'?/不证明进入收件箱/:/does not prove inbox arrival/);
      data.runtime_observation.tools.ffprobe.available=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation.tools.ffprobe.available=true;
      data.runtime_observation.credentials.BANXICO_TOKEN.configured=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation.credentials.BANXICO_TOKEN.configured=true;
      data.runtime_observation.credentials.AZURE_SPEECH_KEY.configured=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation.credentials.AZURE_SPEECH_KEY.configured=true;
      data.email.today.languages.en.confirmation={state:'provider_confirmed'};
      data.email.today.languages.zh.confirmation={state:'provider_sent'};
      assert.match(briefingOperationsHtml(data,now),lang==='zh'?/原发送记录保留/:/send claim is retained/);
      assert.match(briefingOperationsHtml(data,now),lang==='zh'?/送达尚未确认/:/delivery is not confirmed/);
      data.email.today.languages.zh.state='review_required';
      assert.equal(operationsState(data,now),'attention');
      assert.match(summary(data),lang==='zh'?/需要检查/:/needs attention/);
      data.email.today.date='2026-09-18';
      assert.equal(operationsState(data,now),'waiting');
      data.email.credential_configured=false;
      assert.equal(operationsState(data,now),'attention');
      data.email.credential_configured=null;
      assert.equal(operationsState(data,now),'unconfirmed');
      data.email.credential_configured=true;
      data.runtime_observation.tasks['fxdash-briefing'].registered=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation=make().runtime_observation;
      data.runtime_observation.runtime.persistent=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation=make().runtime_observation;
      data.runtime_observation.observed_at='2026-10-04T11:59:59Z';
      assert.equal(operationsState(data,now),'stale');
      assert.doesNotMatch(summary(data),lang==='zh'?/今日邮件已提交/:/emails submitted/);
      data.runtime_observation.observed_at='2026-10-05T14:00:00.001Z';
      assert.equal(operationsState(data,now),'unconfirmed');
      data.runtime_observation.observed_at='2026-10-05T09:00:00';
      assert.equal(operationsState(data,now),'unconfirmed');
      data.runtime_observation.observed_at='NaN';
      assert.equal(operationsState(data,now),'unconfirmed');
      data.runtime_observation={state:'not_observed'};
      assert.equal(operationsState(data,now),'not_observed');
      assert.match(summary(data),lang==='zh'?/尚未检查/:/not checked/);
      const later=make();
      assert.equal(operationsState(later,new Date('2026-10-06T14:00:00Z')),'waiting');
      const before=make(); before.runtime_observation.observed_at='2026-10-05T11:00:00Z';
      assert.equal(operationsState(before,new Date('2026-10-05T11:30:00Z')),'before_window');
      const weekend=make(); weekend.runtime_observation.observed_at='2026-10-10T14:00:00Z';
      assert.equal(operationsState(weekend,new Date('2026-10-10T14:00:00Z')),'weekend');
      const hostile=make(); hostile.email.today.languages.zh.state='<script>alert(1)</script>';
      hostile.email.today.languages.zh.content_mode='<script>alert(2)</script>';
      hostile.email.history=[{date:'<img src=x onerror=alert(1)>',languages:{}}];
      hostile.runtime_observation.observed_at='<script>alert(3)</script>';
      assert.doesNotMatch(briefingOperationsHtml(hostile,now),/<script>|<img|onerror=|alert\(/);
    """
    result = subprocess.run([node, '--input-type=module', '-e', script, lang], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
