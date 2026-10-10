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
      data.email.delivery_policy='require_audio';
      data.runtime_observation.credentials.AZURE_SPEECH_KEY.configured=false;
      assert.equal(operationsState(data,now),'attention');
      data.runtime_observation.credentials.AZURE_SPEECH_KEY.configured=true;
      data.email.delivery_policy='allow_text';
      for (const submitted of [false,true]) for (const missing of [false,null]) {
        const textAllowed=make();
        textAllowed.runtime_observation.credentials.AZURE_SPEECH_KEY.configured=missing;
        textAllowed.runtime_observation.credentials.AZURE_SPEECH_REGION.configured=missing;
        if (!submitted) textAllowed.email.today.languages.zh.state='not_recorded';
        assert.equal(operationsState(textAllowed,now),submitted?'submitted':'waiting');
        textAllowed.email.delivery_policy='require_audio';
        assert.equal(operationsState(textAllowed,now),missing===false?'attention':'unconfirmed');
        textAllowed.runtime_observation.runtime.audio_backend='off';
        assert.equal(operationsState(textAllowed,now),'attention');
        textAllowed.email.delivery_policy='allow_text';
        assert.equal(operationsState(textAllowed,now),submitted?'submitted':'waiting');
      }
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
      for (const optional of ['narrative','publish']) for (const missing of [true,false]) {
        const optionalTask=make();
        if (missing) delete optionalTask.runtime_observation.tasks['fxdash-'+optional];
        else optionalTask.runtime_observation.tasks['fxdash-'+optional].enabled=false;
        assert.equal(operationsState(optionalTask,now),'submitted');
        optionalTask.email.today.languages.zh.state='not_recorded';
        assert.equal(operationsState(optionalTask,now),'waiting');
      }
      const eveningFailure=make();
      for (const optional of ['narrative','publish']) Object.assign(eveningFailure.runtime_observation.tasks['fxdash-'+optional],
        {last_run_at:'2026-10-05T13:50:00Z',last_result:1});
      assert.equal(operationsState(eveningFailure,now),'submitted');
      for (const core of ['live','briefing','catchup']) {
        const disabledCore=make(); disabledCore.runtime_observation.tasks['fxdash-'+core].enabled=false;
        assert.equal(operationsState(disabledCore,now),'attention');
      }
      const withExecution=(state='dispatch_failed',code=1)=>{
        const value=make();
        const delivery=state==='dispatch_failed'?'failed':state;
        value.task_execution={date:'2026-10-05',timezone:'America/New_York',tasks:{
          'fxdash-briefing':{kind:'briefing',state,observed_at:'2026-10-05T13:55:00Z',freshness:'current',
            dispatch_exit_code:state==='dispatch_failed'?code:0,exit_code:code,delivery_state:delivery}}};
        return value;
      };
      for (const state of ['dispatch_failed','failed','attention_required','not_observed']) {
        const failed=withExecution(state,['attention_required','not_observed'].includes(state)?2:1);
        assert.equal(operationsState(failed,now),'attention');
        assert.match(summary(failed),lang==='zh'?/需要检查/:/needs attention/);
        assert.match(briefingOperationsHtml(failed,now),lang==='zh'?/已提交邮件服务商/:/Submitted to the email service/);
        failed.runtime_observation.state='stale';
        assert.equal(operationsState(failed,now),'attention');
        assert.match(briefingOperationsHtml(failed,now),lang==='zh'?/暂无当前检查/:/No current check/);
      }
      const crashed=withExecution('dispatch_failed',-1073741510);
      assert.equal(operationsState(crashed,now),'attention');
      assert.match(briefingOperationsHtml(crashed,now),lang==='zh'?/任务执行失败/:/Task execution failed/);
      for (const alter of [
        value=>value.task_execution.date='2026-10-02',
        value=>value.task_execution.tasks['fxdash-briefing'].observed_at='2026-10-05T14:00:01Z',
        value=>value.task_execution.tasks['fxdash-briefing'].observed_at='2026-10-05T13:55:00',
        value=>value.task_execution.tasks['fxdash-briefing'].observed_at='2026-10-05T03:59:59Z',
        value=>value.task_execution.tasks['fxdash-briefing'].exit_code=true,
        value=>value.task_execution.tasks['fxdash-briefing'].kind='catchup',
        value=>value.task_execution.tasks['fxdash-briefing'].state='confirmed',
      ]) {
        const invalid=withExecution(); alter(invalid);
        assert.equal(operationsState(invalid,now),'submitted');
        assert.doesNotMatch(briefingOperationsHtml(invalid,now),lang==='zh'?/任务执行失败/:/Task execution failed/);
      }
      for (const code of [1,2,0x41302,0x41304,0x41305,0x41306,0x4131B,0x4131C,0xC000013A]) {
        const failed=make();
        failed.runtime_observation.tasks['fxdash-briefing'].last_run_at='2026-10-05T13:50:00Z';
        failed.runtime_observation.tasks['fxdash-briefing'].last_result=code;
        assert.equal(operationsState(failed,now),'attention');
        assert.match(briefingOperationsHtml(failed,now),lang==='zh'?/今日报告异常/:/reported an error today/);
      }
      const liveFailure=make();
      liveFailure.runtime_observation.tasks['fxdash-live'].last_run_at='2026-10-05T13:50:00Z';
      liveFailure.runtime_observation.tasks['fxdash-live'].last_result=1;
      assert.equal(operationsState(liveFailure,now),'attention');
      assert.match(briefingOperationsHtml(liveFailure,now),lang==='zh'?/今日记录任务异常/:/Task errors recorded today/);
      for (const code of [0,0x41300,0x41301,0x41303,0x41325]) {
        const informational=make();
        informational.runtime_observation.tasks['fxdash-briefing'].last_run_at='2026-10-05T13:50:00Z';
        informational.runtime_observation.tasks['fxdash-briefing'].last_result=code;
        assert.equal(operationsState(informational,now),'submitted');
      }
      for (const stamp of ['2026-10-02T13:50:00Z','2026-10-05T14:00:01Z','2026-10-05T13:50:00','bad']) {
        const invalid=make();
        invalid.runtime_observation.tasks['fxdash-briefing'].last_run_at=stamp;
        invalid.runtime_observation.tasks['fxdash-briefing'].last_result=1;
        assert.equal(operationsState(invalid,now),'submitted');
      }
      const recovered=withExecution('submitted_pending',0);
      recovered.runtime_observation.observed_at='2026-10-05T13:54:00Z';
      recovered.runtime_observation.tasks['fxdash-briefing'].last_run_at='2026-10-05T13:50:00Z';
      recovered.runtime_observation.tasks['fxdash-briefing'].last_result=1;
      assert.equal(operationsState(recovered,now),'submitted');
      assert.match(briefingOperationsHtml(recovered,now),lang==='zh'?/每日投递任务/:/Daily delivery tasks/);
      const inconsistentProbe=make();
      inconsistentProbe.runtime_observation.observed_at='2026-10-05T13:00:00Z';
      Object.assign(inconsistentProbe.runtime_observation.tasks['fxdash-briefing'], {
        last_run_at:'2026-10-05T13:50:00Z',last_result:1,
      });
      assert.equal(operationsState(inconsistentProbe,now),'submitted');
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
