import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import os
from types import SimpleNamespace
import urllib.error

import pytest
from fxdash import task_runner as T
from fxdash.narrative import client as C

NOW = datetime(2026, 9, 8, 22, tzinfo=timezone.utc)


def status(at=NOW):
    return {'mode':'live','state':'green','contract_last_date':'2026-09-08',
            'provisional_rows':3,'heartbeat':{'last_live_success':at.isoformat()}}


@pytest.mark.parametrize('code,expected', [(0,'succeeded'),(1,'failed'),(3221225501,'crashed'),(-1073741795,'crashed')])
def test_supervisor_tracks_native_exit_without_rewriting_data(isolated_outputs, code, expected):
    root=isolated_outputs
    T.atomic_json(root/'status.json', status(NOW-timedelta(days=1)))
    def runner(command, **kwargs):
        assert command[-2:]==['--mode','live']
        assert '-u' in command and 'faulthandler' in command
        assert kwargs['env']['PYTHONUNBUFFERED']=='1'
        assert kwargs['stdout'] is not None
        if not code:
            T.atomic_json(root/'status.json',status())
        return SimpleNamespace(returncode=code)
    result=T.supervise(root.parent,root,runner=runner,clock=lambda:NOW)
    assert result['state']==expected
    assert T.latest_attempt(root)['state']==expected
    assert len(list((root/'task_runs/live').glob('*/start.json')))==1
    assert len(list((root/'task_runs/live').glob('*/finish.json')))==1
    if code:
        assert T.read_json(root/'status.json')==status(NOW-timedelta(days=1))
        assert T.status_view(root,T.read_json(root/'status.json'),clock=lambda:NOW)['state']=='red'


def test_zero_exit_with_old_success_is_not_success(isolated_outputs):
    T.atomic_json(isolated_outputs/'status.json',status(NOW-timedelta(days=1)))
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=lambda *a,**k:SimpleNamespace(returncode=0),clock=lambda:NOW)
    assert result['state']=='completion_unconfirmed'


@pytest.mark.parametrize('at', [NOW, NOW+timedelta(days=5)])
def test_zero_exit_without_a_new_saved_success_is_not_success(isolated_outputs, at):
    previous=status(at)
    T.atomic_json(isolated_outputs/'status.json', previous)
    result=T.supervise(isolated_outputs.parent,isolated_outputs,
                       runner=lambda *a,**k:SimpleNamespace(returncode=0),clock=lambda:NOW)
    assert result['state']=='completion_unconfirmed'
    assert T.read_json(isolated_outputs/'status.json')==previous


def test_changed_metadata_with_unchanged_heartbeat_is_not_a_new_success(isolated_outputs):
    previous=status(NOW+timedelta(days=5))
    T.atomic_json(isolated_outputs/'status.json', previous)
    def runner(*args, **kwargs):
        T.atomic_json(isolated_outputs/'status.json', {**previous, 'provisional_rows':2})
        return SimpleNamespace(returncode=0)
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=runner,clock=lambda:NOW)
    assert result['state']=='completion_unconfirmed'


@pytest.mark.parametrize('pulse', [None, [], {'last_live_success':'not-a-date'}])
def test_zero_exit_with_invalid_saved_heartbeat_is_unconfirmed(isolated_outputs, pulse):
    T.atomic_json(isolated_outputs/'status.json',status(NOW-timedelta(days=1)))
    def runner(*args, **kwargs):
        T.atomic_json(isolated_outputs/'status.json', {**status(), 'heartbeat':pulse})
        return SimpleNamespace(returncode=0)
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=runner,clock=lambda:NOW)
    assert result['state']=='completion_unconfirmed'


@pytest.mark.parametrize('offset,expected', [(-1,'completion_unconfirmed'), (0,'succeeded'),
    (5,'succeeded'), (10,'succeeded'), (11,'completion_unconfirmed')])
def test_new_success_must_fall_within_the_child_run(isolated_outputs, offset, expected):
    previous=status(NOW-timedelta(days=1))
    T.atomic_json(isolated_outputs/'status.json', previous)
    # Child returned at +10s; the final supervisor record is written at +12s.
    observations=iter((NOW, NOW+timedelta(seconds=10), NOW+timedelta(seconds=12)))
    def runner(*args, **kwargs):
        T.atomic_json(isolated_outputs/'status.json',status(NOW+timedelta(seconds=offset)))
        return SimpleNamespace(returncode=0)
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=runner,clock=lambda:next(observations))
    assert result['state']==expected
    assert result['finished_at']==(NOW+timedelta(seconds=12)).isoformat()
    expected_success=status(NOW+timedelta(seconds=offset)) if expected=='succeeded' else previous
    assert T.read_json(isolated_outputs/'task_runs/live/last_success.json')==expected_success


@pytest.mark.skipif(os.name!='nt', reason='Windows native process exit status')
def test_actual_child_native_exit_status_is_recorded(isolated_outputs):
    # ExitProcess reports the native status without creating a crash dialog or
    # intentionally corrupting memory. The child never imports the FX pipeline.
    def runner(command, **kwargs):
        return subprocess.run([sys.executable, '-c',
            'import ctypes; ctypes.windll.kernel32.ExitProcess(0xc000001d)'], **kwargs)
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=runner,clock=lambda:NOW)
    assert result['state']=='crashed' and result['exit_hex']=='0xc000001d'
    assert T.latest_attempt(isolated_outputs)['state']=='crashed'


@pytest.mark.parametrize('exception,state',[(subprocess.TimeoutExpired('private-command',1),'timed_out'),(OSError('secret-token'),'launch_failed')])
def test_supervisor_failure_metadata_is_safe(isolated_outputs,exception,state):
    def fail(*a,**k):raise exception
    result=T.supervise(isolated_outputs.parent,isolated_outputs,runner=fail,clock=lambda:NOW)
    assert result['state']==state
    assert 'secret' not in json.dumps(result) and 'private' not in json.dumps(result)


def test_second_supervisor_cannot_replace_active_record(isolated_outputs):
    root=isolated_outputs/'task_runs/live'
    with T.RunLock(root/'run.lock'):
        assert T.supervise(isolated_outputs.parent,isolated_outputs)['state']=='busy'
    assert not (root/'latest.json').exists()


def test_running_record_expires_and_malformed_record_fails_closed(isolated_outputs):
    target=isolated_outputs/'task_runs/live/latest.json'
    T.atomic_json(target,{'state':'running','run_id':'test','started_at':NOW.isoformat(),'deadline':(NOW+timedelta(hours=1)).isoformat(),'private':'secret'})
    assert T.latest_attempt(isolated_outputs,clock=lambda:NOW)['state']=='running'
    assert T.latest_attempt(isolated_outputs,clock=lambda:NOW+timedelta(hours=2))['state']=='interrupted'
    assert 'private' not in T.latest_attempt(isolated_outputs)
    T.atomic_json(target,{'state':'succeeded'})
    assert T.latest_attempt(isolated_outputs)['state']=='unreadable'


@pytest.mark.parametrize('extra', [{}, {'deadline':None}, {'deadline':'not-a-date'},
    {'deadline':(NOW-timedelta(seconds=1)).isoformat()}])
def test_running_record_requires_a_valid_ordered_deadline(isolated_outputs, extra):
    target=isolated_outputs/'task_runs/live/latest.json'
    T.atomic_json(target,{'state':'running','run_id':'test','started_at':NOW.isoformat(),**extra})
    assert T.latest_attempt(isolated_outputs,clock=lambda:NOW+timedelta(days=30))=={'state':'unreadable'}


def test_running_deadline_boundary_preserves_expiry_rule(isolated_outputs):
    target=isolated_outputs/'task_runs/live/latest.json'
    T.atomic_json(target,{'state':'running','run_id':'test','started_at':NOW.isoformat(),'deadline':NOW.isoformat()})
    assert T.latest_attempt(isolated_outputs,clock=lambda:NOW)['state']=='running'
    assert T.latest_attempt(isolated_outputs,clock=lambda:NOW+timedelta(microseconds=1))['state']=='interrupted'


def test_age_is_recomputed_and_does_not_mutate_saved_status(isolated_outputs):
    saved=status()
    result=T.status_view(isolated_outputs,saved,clock=lambda:NOW+timedelta(hours=27))
    assert result['state']=='yellow' and saved==status()


@pytest.mark.parametrize('stored', [None, [], ['bad'], 'bad', 1])
def test_nonobject_saved_status_is_a_serializable_red_view(isolated_outputs, stored):
    T.atomic_json(isolated_outputs/'task_runs/live/last_success.json',status())
    view=T.status_view(isolated_outputs,stored,clock=lambda:NOW)
    assert view['state']=='red'
    assert view['runtime']['attribution_as_of']=='2026-09-08'
    assert 'saved calculation status unreadable' in view['reasons']
    json.dumps(view,allow_nan=False)


@pytest.mark.parametrize('pulse', ['bad', [1], 1, None, [['last_live_success',NOW.isoformat()]]])
def test_nonobject_saved_heartbeat_cannot_be_coerced_green(isolated_outputs, pulse):
    view=T.status_view(isolated_outputs,{**status(),'heartbeat':pulse},clock=lambda:NOW)
    assert view['state']=='red' and view['heartbeat']['state']=='red'
    assert view['heartbeat']['note']=='heartbeat record unreadable'
    assert view['runtime']['last_success_at'] is None
    json.dumps(view,allow_nan=False)


@pytest.mark.parametrize('thresholds', [{'warn_hours':'26'}, {'crit_hours':None},
    {'warn_hours':float('inf')}, {'crit_hours':float('nan')}, {'warn_hours':True},
    {'warn_hours':73,'crit_hours':72}, {'warn_hours':0}, {'crit_hours':-1},
    {'crit_hours':10**1000}])
def test_invalid_saved_thresholds_are_red_and_use_safe_defaults(isolated_outputs, thresholds):
    saved={**status(),'heartbeat':{'last_live_success':NOW.isoformat(),**thresholds}}
    view=T.status_view(isolated_outputs,saved,clock=lambda:NOW)
    assert view['state']=='red' and view['heartbeat']['state']=='red'
    assert view['heartbeat']['warn_hours']==26 and view['heartbeat']['crit_hours']==72
    assert 'heartbeat thresholds unreadable' in view['reasons']
    json.dumps(view,allow_nan=False)


@pytest.mark.parametrize('state', ['unrecognized', None, [], {}])
def test_invalid_saved_state_is_a_red_diagnostic(isolated_outputs, state):
    view=T.status_view(isolated_outputs,{**status(),'state':state},clock=lambda:NOW)
    assert view['state']=='red' and view['last_success_state']=='red'
    assert 'saved calculation state unreadable' in view['reasons']
    json.dumps(view,allow_nan=False)


@pytest.mark.parametrize('future', [timedelta(microseconds=1),timedelta(hours=120)])
def test_future_success_is_red_without_replacing_latest_attempt(isolated_outputs, future):
    T.atomic_json(isolated_outputs/'task_runs/live/latest.json',
                  {'state':'succeeded','run_id':'test','started_at':NOW.isoformat()})
    view=T.status_view(isolated_outputs,status(NOW+future),clock=lambda:NOW)
    assert view['state']=='red' and view['heartbeat']['state']=='red'
    assert view['heartbeat']['note']==T.CLOCK_DIAGNOSTIC
    assert T.CLOCK_DIAGNOSTIC in view['reasons']
    assert view['runtime']['latest_attempt']['state']=='succeeded'
    assert view['heartbeat']['age_hours']<0


def test_success_equal_to_observation_is_valid(isolated_outputs):
    view=T.status_view(isolated_outputs,status(),clock=lambda:NOW)
    assert view['state']=='green' and view['heartbeat']['age_hours']==0


@pytest.mark.parametrize('naive_clock', [False, True])
def test_saved_status_mixed_timezones_use_host_local_semantics(isolated_outputs, naive_clock):
    last=NOW-timedelta(hours=2)
    saved_at=last if naive_clock else last.astimezone().replace(tzinfo=None)
    observed=NOW.astimezone().replace(tzinfo=None) if naive_clock else NOW
    view=T.status_view(isolated_outputs,status(saved_at),clock=lambda:observed)
    assert view['state']=='green' and view['heartbeat']['age_hours']==2
    assert view['runtime']['observed_at']==NOW.isoformat()


def test_running_attempt_accepts_a_naive_host_local_clock(isolated_outputs):
    T.atomic_json(isolated_outputs/'task_runs/live/latest.json',
                  {'state':'running','run_id':'test','started_at':NOW.isoformat(),
                   'deadline':(NOW+timedelta(hours=1)).isoformat()})
    local_clock=NOW.astimezone().replace(tzinfo=None)
    assert T.latest_attempt(isolated_outputs,clock=lambda:local_clock)['state']=='running'
    assert T.latest_attempt(isolated_outputs,clock=lambda:local_clock+timedelta(hours=2))['state']=='interrupted'


def test_previous_data_identity_survives_python_exception_status(isolated_outputs):
    T.atomic_json(isolated_outputs/'task_runs/live/last_success.json',status())
    bad={'state':'red','mode':'unknown','contract_last_date':None,'rows':0,
         'heartbeat':{'last_live_success':NOW.isoformat()}}
    view=T.status_view(isolated_outputs,bad,clock=lambda:NOW)
    assert view['state']=='red' and view['runtime']['attribution_as_of']=='2026-09-08'
    assert view['runtime']['provisional_rows']==3


class Response:
    status=200
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.body).encode()


OK={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}],
    'usageMetadata':{'promptTokenCount':9,'candidatesTokenCount':5,'totalTokenCount':14}}


def http_error(code,body):
    return urllib.error.HTTPError('https://provider.invalid/?key=SECRET',code,'SECRET',{},io.BytesIO(json.dumps(body).encode()))


def test_api_retries_only_rejected_requests_and_key_never_enters_url(monkeypatch):
    seen=[]
    def open_(request,**kwargs):
        seen.append(request)
        assert 'SECRET' not in request.full_url
        assert request.get_header('X-goog-api-key')=='SECRET'
        if len(seen)==1:raise http_error(503,{'error':{'message':'SECRET'}})
        return Response(OK)
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    sleeps=[]
    client=C.GeminiClient(api_key='SECRET',max_requests=3,sleeper=sleeps.append)
    assert client.complete('s','u',{})=={'ok':True}
    assert len(seen)==2 and len(sleeps)==1 and 1 <= sleeps[0] <= 1.25
    assert client.attempts[0]['category']=='service_unavailable'
    assert client.attempts[1]['state']=='completed'
    assert 'SECRET' not in json.dumps(client.totals)
    assert client.totals['total_tokens']==14


@pytest.mark.parametrize('code,body,category',[
    (400,{'error':{'message':'SECRET'}},'invalid_request'),
    (403,{'error':{'message':'SECRET'}},'permission_denied'),
    (429,{'error':{'message':'limit: 0 SECRET'}},'quota_exhausted'),
    (429,{'error':{'details':[{'violations':[{'quotaId':'RequestsPerDay'}]}]}},'quota_exhausted'),
    (429,{'error':{'message':'SECRET'}},'quota_or_rate_limit'),
])
def test_permanent_and_unknown_quota_errors_do_not_retry(monkeypatch,code,body,category):
    def open_(*a,**k):raise http_error(code,body)
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    client=C.GeminiClient(api_key='SECRET',sleeper=lambda _:pytest.fail('must not retry'))
    with pytest.raises(C.GenerationError) as exc:client.complete('s','u',{})
    assert C.failure_details(exc.value)['category']==category
    assert len(client.attempts)==1
    assert 'SECRET' not in str(exc.value) and exc.value.__cause__ is None


def test_timeouts_are_ambiguous_and_never_resent(monkeypatch):
    def open_(*a,**k):raise TimeoutError('SECRET')
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    client=C.GeminiClient(api_key='SECRET',sleeper=lambda _:pytest.fail('must not retry'))
    with pytest.raises(C.GenerationError):client.complete('s','u',{})
    assert len(client.attempts)==1
    assert client.attempts[0]['category']=='transport_failure'


def test_retry_budget_is_shared_by_all_currency_calls(monkeypatch):
    def open_(*a,**k):raise http_error(503,{})
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    client=C.GeminiClient(api_key='SECRET',max_requests=3,sleeper=lambda _:None)
    for _ in range(4):
        with pytest.raises(C.GenerationError):client.complete('s','u',{})
    assert len(client.attempts)==3


def test_long_retry_delay_is_deferred(monkeypatch):
    def open_(*a,**k):raise http_error(503,{'error':{'details':[{'retryDelay':'60s'}]}})
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    client=C.GeminiClient(api_key='SECRET',sleeper=lambda _:pytest.fail('must defer'))
    with pytest.raises(C.GenerationError):client.complete('s','u',{})
    assert len(client.attempts)==1 and client.attempts[0]['retry_deferred']


def test_driver_records_failures_and_clamps_wire_budget(isolated_outputs,monkeypatch):
    from test_morning import packet
    from fxdash.narrative import driver_notes as D
    def open_(*a,**k):raise http_error(503,{})
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    client=C.GeminiClient(api_key='SECRET',sleeper=lambda _:None)
    rows=D.generate(packet(isolated_outputs),client,max_calls=2)
    assert len(client.attempts)==2
    assert rows[0]['generation_failure']['category']=='service_unavailable'
    assert rows[1]['generation_failure']['category']=='request_budget_exhausted'
    assert 'SECRET' not in json.dumps(rows)


def test_runtime_frontend_clock_failure_and_static_wording():
    root=Path(__file__).resolve().parents[1]
    script="""
      import assert from 'node:assert/strict';
      globalThis.localStorage={getItem:()=> 'en'};
      const {runtimeState,runtimeHtml}=await import('./src/fxdash/web/static/runtime-status.js');
      const now=new Date('2026-09-08T22:00:00Z');
      const data={last_success_state:'green',runtime:{last_success_at:now.toISOString(),
        attribution_as_of:'2026-09-07',provisional_rows:3,latest_attempt:{state:'crashed',
        started_at:now.toISOString(),exit_hex:'0xc000001d'}}};
      assert.equal(runtimeState(data,now).tone,'red');
      assert.match(runtimeHtml(data,{mode:'static'},now),/build time/);
      data.runtime.latest_attempt={state:'running',deadline:'2026-09-08T21:00:00Z'};
      assert.equal(runtimeState(data,now).state,'interrupted');
      data.runtime.latest_attempt={state:'succeeded'};
      assert.equal(runtimeState(data,now).tone,'green');
      assert.equal(runtimeState(data,new Date('2026-09-12T22:00:00Z')).tone,'red');
    """
    subprocess.run(['node','--input-type=module','-e',script],cwd=root,check=True,capture_output=True)


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_runtime_frontend_explains_stale_success_and_keeps_failure_visible(lang):
    root = Path(__file__).resolve().parents[1]
    script = """
      import assert from 'node:assert/strict';
      const lang=process.argv[1];
      globalThis.localStorage={getItem:()=>lang};
      const {runtimeState,runtimeHtml}=await import('./src/fxdash/web/static/runtime-status.js');
      const start=new Date('2026-09-08T22:00:00Z');
      const data={last_success_state:'green',runtime:{last_success_at:start.toISOString(),
        latest_attempt:{state:'succeeded',started_at:start.toISOString()}}};
      const render=hours=>runtimeHtml(data,{mode:'static'},new Date(start.getTime()+hours*3600000));
      const summary=html=>html.match(/<summary>(.*?)<\\/summary>/s)[1];
      assert.match(render(26),/data-runtime-freshness="current"/);
      assert.match(render(26.01),/data-runtime-tone="yellow"/);
      assert.match(summary(render(26.01)),lang==='zh'?/超过 26 小时/:/over 26 hours old/);
      assert.match(render(72),/data-runtime-freshness="delayed"/);
      const stale=render(72.01);
      assert.match(stale,/data-runtime-tone="red"/);
      assert.match(summary(stale),lang==='zh'?/超过 72 小时/:/over 72 hours old/);
      assert.match(stale,lang==='zh'?/最近计算成功/:/Last attempt succeeded/);
      assert.equal(runtimeState(data,new Date(start.getTime()+73*3600000)).state,'succeeded');
      data.runtime.latest_attempt.state='crashed';
      assert.match(summary(render(73)),lang==='zh'?/计算进程异常退出/:/Calculation process crashed/);
      data.runtime.latest_attempt.state='succeeded';
      data.runtime.last_success_at='invalid';
      assert.match(render(0),/data-runtime-freshness="unknown"/);
      assert.match(summary(render(0)),lang==='zh'?/时间未知/:/time is unknown/);
      delete data.runtime.last_success_at;
      assert.match(render(0),/data-runtime-tone="red"/);
    """
    subprocess.run(['node', '--input-type=module', '-e', script, lang],
                   cwd=root, check=True, capture_output=True)


@pytest.mark.parametrize('body,category',[
    ({'candidates':[]},'no_candidate'),
    ({'candidates':[{'finishReason':'MAX_TOKENS'}]},'output_limit'),
    ({'candidates':[{'finishReason':'SAFETY'}]},'output_blocked'),
    ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'SECRET'}]}}]},'invalid_json'),
])
def test_output_failures_are_separate_from_http_failures(monkeypatch,body,category):
    monkeypatch.setattr(C.urllib.request,'urlopen',lambda *a,**k:Response(body))
    client=C.GeminiClient(api_key='SECRET')
    with pytest.raises(C.GenerationError) as exc:client.complete('s','u',{})
    assert C.failure_details(exc.value)['category']==category
    assert len(client.attempts)==1 and len(client.calls)==1
    assert 'SECRET' not in json.dumps(client.totals)


def test_status_endpoint_observes_failure_without_contract_reload(isolated_outputs,monkeypatch):
    from fastapi.testclient import TestClient
    from fxdash.web.app import create_app
    from fxdash.narrative import morning
    from test_web import _write_fixture
    _write_fixture(isolated_outputs)
    monkeypatch.setattr(morning,'now_utc',lambda:NOW)
    client=TestClient(create_app(isolated_outputs,cache_dir=isolated_outputs/'empty'))
    initial=client.get('/api/status').json()
    T.atomic_json(isolated_outputs/'task_runs/live/latest.json',{'state':'crashed','run_id':'one',
        'started_at':NOW.isoformat(),'finished_at':NOW.isoformat(),'exit_hex':'0xc000001d'})
    changed=client.get('/api/status').json()
    assert changed['state']=='red'
    assert changed['runtime']['latest_attempt']['state']=='crashed'
    assert changed['server']['data_version']==initial['server']['data_version']
    assert client.get('/api/overview').json()['status_digest']['state']=='red'


@pytest.mark.parametrize('pulse,diagnostic', [
    ({'last_live_success':(NOW+timedelta(hours=1)).isoformat()},T.CLOCK_DIAGNOSTIC),
    ([1],'heartbeat record unreadable'),
    ({'last_live_success':NOW.isoformat(),'crit_hours':None},'heartbeat thresholds unreadable'),
])
def test_overview_digest_includes_observed_clock_and_saved_record_diagnostics(isolated_outputs,monkeypatch,pulse,diagnostic):
    from fastapi.testclient import TestClient
    from fxdash.web.app import create_app
    from fxdash.web import market
    from fxdash.narrative import morning
    from test_web import _write_fixture
    _write_fixture(isolated_outputs)
    saved={**status(),'heartbeat':pulse,'reasons':['saved explanation']}
    T.atomic_json(isolated_outputs/'status.json',saved)
    original=(isolated_outputs/'status.json').read_bytes()
    T.atomic_json(isolated_outputs/'task_runs/live/latest.json',
                  {'state':'succeeded','run_id':'test','started_at':NOW.isoformat()})
    monkeypatch.setattr(morning,'now_utc',lambda:NOW)
    monkeypatch.setattr(market,'_fetch_dxy',lambda:None)
    client=TestClient(create_app(isolated_outputs,cache_dir=isolated_outputs/'empty'))
    status_response=client.get('/api/status')
    overview_response=client.get('/api/overview')
    assert status_response.status_code==200 and overview_response.status_code==200
    observed=status_response.json()
    digest=overview_response.json()['status_digest']
    assert observed['state']=='red' and digest['state']=='red'
    assert diagnostic in digest['reasons'] and 'saved explanation' in digest['reasons']
    assert digest['reasons']==observed['reasons']
    assert digest['runtime']['latest_attempt']['state']=='succeeded'
    assert (isolated_outputs/'status.json').read_bytes()==original
