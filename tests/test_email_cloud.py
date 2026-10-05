"""Real isolated cloud build/send wiring, fixed local Git and fake providers."""
import pytest

from fxdash.cloud import runtime as R, journal as J
from fxdash.cloud.state import StateError
from fxdash.narrative import morning as M
import test_cloud_production as C


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    C.offline.__wrapped__(monkeypatch)
    monkeypatch.setattr('requests.request',lambda *a,**kw:pytest.fail('No real mail or HTTP'))
    original=C.configure
    def configure(root):
        value=original(root)
        value['delivery_policy']='allow_text'
        M.atomic_json(root/'subscriptions/config.json',value)
        return value
    monkeypatch.setattr(C,'configure',configure)


def test_cloud_allow_text_on_one_unavailable_public_mp3_keeps_daily_effects(tmp_path,monkeypatch):
    s=C.harness(tmp_path,monkeypatch)
    R.authorize_delivery(s.store,s.data,tmp_path,local_sender_stopped=True,clock=lambda:s.now[0])
    original=s.factory
    messages=[]
    class Provider:
        def create(self,message):
            messages.append(message)
            s.calls.append('create')
            return len(messages)
        def send(self,identity):
            s.calls.append('send')
    def factory(*a,**kw):
        port=original(*a,**kw)
        get=port.fetcher
        def fetch(url,limit):
            if url.endswith('/zh.mp3'):
                raise TimeoutError('private CDN response')
            return get(url,limit)
        port.fetcher=fetch
        port.provider_factory=Provider
        return port
    s.factory=factory
    for _ in range(2):
        assert C.go(s,tmp_path,mode='delivery',model=False,speech=True)['state']=='submitted'
    assert len(messages)==2 and s.calls.count('send')==2
    assert s.calls.count('audio-en')==s.calls.count('audio-zh')==1
    assert s.calls.count('build')==1 and 'model' not in s.calls
    assert 'media/briefing/' in messages[0]['htmlContent']
    assert 'media/briefing/' not in messages[1]['htmlContent']
    claims=J.Journal(s.store)._read()[1]['claims']
    for lang,message in zip(('en','zh'),messages):
        assert claims['email-create/'+C.DAY+'/'+lang]['fingerprint']==M.digest(message)
        assert claims['email-send/'+C.DAY+'/'+lang]['fingerprint']==M.digest(message)
    assert len([c for c in s.remote.calls if c[0]=='push'])==1
    # The completed text fingerprint stays immutable when audio becomes reachable.
    def restored(*a,**kw):
        port=original(*a,**kw)
        port.provider_factory=Provider
        return port
    s.factory=restored
    assert C.go(s,tmp_path,mode='delivery',model=False,speech=True)['state']=='attention_required'
    assert len(messages)==2 and s.calls.count('send')==2


def test_cloud_tts_failure_keeps_paid_claim_closed_even_with_text_policy(tmp_path,monkeypatch):
    s=C.harness(tmp_path,monkeypatch)
    R.authorize_delivery(s.store,s.data,tmp_path,local_sender_stopped=True,clock=lambda:s.now[0])
    original=s.factory
    calls=[]
    def factory(*a,**kw):
        port=original(*a,**kw)
        def failed(*a,**kw):
            calls.append('audio')
            raise TimeoutError('private speech reply')
        port.renderer=failed
        return port
    s.factory=factory
    for _ in range(2):
        with pytest.raises(StateError):
            C.go(s,tmp_path,mode='delivery',model=False,speech=True)
    assert calls==['audio'] and 'build' not in s.calls and 'send' not in s.calls
    assert not [c for c in s.remote.calls if c[0]=='push']
    claim=J.Journal(s.store)._read()[1]['claims']['audio-en/'+C.DAY+'/edition']
    assert claim['state']=='review_required'
