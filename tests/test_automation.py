from types import SimpleNamespace

import pytest

from fxdash.narrative import automation as Q, morning as M
from test_audio_briefing import saved, brief
from test_morning import moment
from test_catchup import inputs


def test_idle_has_no_task_network_or_writes(tmp_path):
    before=set(tmp_path.rglob('*'))
    assert Q.before('morning',tmp_path,clock=lambda:moment(19,0))['state']=='idle'
    assert set(tmp_path.rglob('*'))==before
    assert Q.after(tmp_path,clock=lambda:moment(13,0))['state']=='idle'


def test_missing_inputs_trigger_bounded_existing_task_without_llm_claim(tmp_path):
    calls=[]
    kw=dict(checker=lambda *a:{'state':'stale_inputs'},launcher=lambda:calls.append(1) or {'state':'requested'})
    for hour in (16,16,17,18):
        result=Q.before('catchup',tmp_path,clock=lambda:moment(hour,0),**kw)
        assert not result['proceed']
    assert len(calls)==2 and result['state']=='live_request_limit'
    assert not list(tmp_path.rglob('generation.claim')) and not list(tmp_path.rglob('prepare.claim'))


def test_running_quant_is_not_launched_again(tmp_path,monkeypatch):
    monkeypatch.setattr(Q.T,'latest_attempt',lambda *a,**kw:{'state':'running'})
    result=Q.before('catchup',tmp_path,clock=lambda:moment(16,0),checker=lambda *a:{'state':'stale'},
                    launcher=lambda:pytest.fail('duplicate worker'))
    assert result['state']=='waiting_for_live_task'


def test_ready_inputs_proceed_without_launch(tmp_path):
    result=Q.before('catchup',tmp_path,clock=lambda:moment(16,0),checker=lambda *a:{'state':'ready'},
                    launcher=lambda:pytest.fail('unneeded live run'))
    assert result['proceed'] and result['state']=='inputs_ready'


def test_saved_edition_remains_deliverable_when_current_data_is_unavailable(tmp_path):
    saved(tmp_path)
    result=Q.before('catchup',tmp_path,clock=lambda:moment(17,0),checker=lambda *a:pytest.fail('saved edition'))
    assert result['state']=='saved_edition' and result['proceed']


def test_claimed_packet_resumes_without_new_quant_or_generation(tmp_path):
    p=inputs(tmp_path)
    root=tmp_path/'briefing/catchup/2026-01-08'
    M.atomic_json(root/'packet.json',p)
    M.atomic_json(root/'generation.claim',{'packet_hash':M.digest(p)})
    result=Q.before('catchup',tmp_path,clock=lambda:moment(17,0),checker=lambda *a:pytest.fail('reuse claim'))
    assert result['state']=='claimed_catchup_inputs'


def test_failed_launch_is_sanitized_and_budgeted(tmp_path):
    def fail(): raise RuntimeError('private')
    result=Q.before('catchup',tmp_path,clock=lambda:moment(16,0),checker=lambda *a:{'state':'missing'},launcher=fail)
    assert result['live_request_state']=='request_unconfirmed' and 'private' not in str(result)


def test_after_requires_matching_push_then_public_audio_before_email(tmp_path):
    path,_,_=saved(tmp_path)
    b=brief(path)
    check=lambda *a,**kw:pytest.fail('no public request before push')
    assert Q.after(tmp_path,clock=lambda:moment(17,0),verifier=check)['state']=='waiting_for_push'
    M.atomic_json(path.parent/'publish.json',{'state':'published','edition_hash':b['edition_hash']})
    calls=[]
    result=Q.after(tmp_path,clock=lambda:moment(17,0),verifier=lambda *a,**kw:{'state':'pending'},
                   notifier=lambda *a,**kw:pytest.fail('not public yet'))
    assert result['email']['state']=='waiting_for_public_audio'
    result=Q.after(tmp_path,clock=lambda:moment(17,1),verifier=lambda *a,**kw:{'state':'verified'},
                   notifier=lambda *a,**kw:calls.append(1) or {'state':'disabled'})
    assert len(calls)==1 and result['state']=='checked'


def test_six_pairs_and_same_date_are_required(tmp_path,monkeypatch):
    from fxdash.web import store
    rows={pair:SimpleNamespace(dates=['2026-01-07']) for pair in Q.PAIRS}
    monkeypatch.setattr(store,'Snapshot',lambda root:SimpleNamespace(combo=lambda pair,w,m:rows.get(pair)))
    assert Q.input_state(tmp_path,'2026-01-08','morning')['state']=='ready'
    rows['USDJPY'].dates=['2026-01-06']
    assert Q.input_state(tmp_path,'2026-01-08','catchup')['state']=='stale_inputs'
    del rows['USDEUR']
    assert Q.input_state(tmp_path,'2026-01-08','catchup')['state']=='missing_inputs'
