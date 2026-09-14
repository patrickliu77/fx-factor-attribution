import copy
from datetime import timedelta
import json

import pytest

from fxdash.narrative import public_delivery as P, audio_briefing as B, morning as M
from test_audio_briefing import saved, prepare, brief
from test_morning import moment


def fixture(root, audio=True):
    path, _, _ = saved(root)
    if audio:
        prepare(root,path)
    current = brief(path)
    current['audio'] = B.inspect(root,current)
    build = {'briefing':{k:current[k] for k in ('date','mode','edition_hash')},'built_at':'2026-01-08T17:00:00Z'}
    responses = {'build.json':json.dumps(build).encode(),'api/news.json':json.dumps({'briefing':current}).encode()}
    for lang,item in current['audio']['languages'].items():
        if item.get('state')=='ready':
            responses[item['url']] = B.resolve_asset(root,current['mode'],current['date'],current['edition_hash'],'audio-v1',lang).read_bytes()
    return path,current,responses


def test_public_text_and_audio_match_without_changing_archives(tmp_path):
    path,current,responses=fixture(tmp_path)
    before=path.read_bytes()
    calls=[]
    def get(url,limit):
        calls.append(url)
        return responses[url]
    result=P.verify(tmp_path,current,fetcher=get,clock=lambda:moment(17,1))
    assert result['state']=='verified' and set(result['audio_verified'])=={'en','zh'}
    assert len(calls)==4 and path.read_bytes()==before
    assert P.verify(tmp_path,current,fetcher=lambda *a:pytest.fail('cooldown'),clock=lambda:moment(17,2))==result


@pytest.mark.parametrize('bad',['build','text','audio_metadata','audio_bytes','calendar'])
def test_incomplete_public_deployment_never_passes(tmp_path,bad):
    _,current,responses=fixture(tmp_path)
    if bad=='build':
        responses['build.json']=b'{"briefing":{"date":"2020-01-01"}}'
    elif bad=='audio_bytes':
        responses[current['audio']['languages']['en']['url']]=b'wrong'
    else:
        body=json.loads(responses['api/news.json'])
        if bad=='text': body['briefing']['text']['en']='wrong'
        if bad=='calendar': body['briefing']['calendar']={'events':['invented']}
        if bad=='audio_metadata': body['briefing']['audio']['languages']['en']['url']='https://evil.test/track'
        responses['api/news.json']=json.dumps(body).encode()
    result=P.verify(tmp_path,current,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    assert result['state']=='pending' and result['checks']


def test_missing_audio_can_only_verify_text(tmp_path):
    _,current,responses=fixture(tmp_path,audio=False)
    result=P.verify(tmp_path,current,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    assert result['state']=='text_verified' and result['audio_verified']==[]


def test_probe_failure_is_sanitized_and_next_check_can_succeed(tmp_path):
    _,current,responses=fixture(tmp_path)
    def fail(*args): raise RuntimeError('secret from response')
    r=P.verify(tmp_path,current,fetcher=fail,clock=lambda:moment(17,1))
    assert r['state']=='pending' and 'secret' not in json.dumps(r)
    assert P.verify(tmp_path,current,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,6))['state']=='verified'


def test_version_changes_invalidate_cached_verification(tmp_path):
    _,current,responses=fixture(tmp_path)
    P.verify(tmp_path,current,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    forged=copy.deepcopy(current)
    forged['edition_hash']='0'*64
    with pytest.raises(ValueError): P.verify(tmp_path,forged)


def test_saved_observation_is_read_only_and_keeps_its_real_date(tmp_path):
    _,current,responses=fixture(tmp_path)
    assert P.observation(tmp_path,current)['state']=='not_checked'
    P.verify(tmp_path,current,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    assert P.observation(tmp_path,current,clock=lambda:moment(17,0))['state']=='unconfirmed'
    observed=P.observation(tmp_path,current,clock=lambda:moment(17,2))
    assert observed['observed_at']==moment(17,1).isoformat() and observed['state']=='verified'
