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


def test_age_is_recomputed_and_does_not_mutate_saved_status(isolated_outputs):
    saved=status()
    result=T.status_view(isolated_outputs,saved,clock=lambda:NOW+timedelta(hours=27))
    assert result['state']=='yellow' and saved==status()


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
