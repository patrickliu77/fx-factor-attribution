"""Email policy and operator reconciliation, with synthetic editions and HTTP only."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from fxdash.narrative import subscriptions as S, public_delivery as P, morning as M
from fxdash.narrative import audio_briefing as A, automation as Q
from fxdash.cloud import delivery as D
from test_subscriptions import configure, setup
from test_public_delivery import fixture
from test_morning import moment
from test_cloud_runtime import make_store, restart


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    monkeypatch.setattr(S, 'api_key', lambda: 'synthetic-test-key')
    monkeypatch.setattr('requests.request', lambda *a, **kw: pytest.fail('Inject every email HTTP response'))
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **kw: pytest.fail('No real HTTP'))


def response(status, body, *, malformed=False, size=None):
    def decode():
        if malformed:
            raise ValueError('private-address@example.test private-api-key')
        return body
    raw = json.dumps(body).encode() if body is not None else b''
    return SimpleNamespace(status_code=status, content=raw if size is None else b'x'*size, json=decode)


@pytest.mark.parametrize('stage', ['create', 'send', 'reconcile'])
@pytest.mark.parametrize('status,code,uncertain', [
    (400,'invalid_parameter',False), (401,'unauthorized',False),
    (500,'private-address@example.test',True), (408,'missing_parameter',True),
])
def test_provider_errors_only_retain_safe_http_diagnostics(monkeypatch, stage, status, code, uncertain):
    monkeypatch.setattr('requests.request', lambda *a, **kw: response(status, {
        'code':code,'message':'private-address@example.test private-api-key', 'htmlContent':'private-body'}))
    provider = S.Provider()
    operation = {'create':lambda:provider.create({}), 'send':lambda:provider.send(42),
                 'reconcile':lambda:provider.report(42)}[stage]
    with pytest.raises(S.EmailProviderError) as raised:
        operation()
    error = raised.value
    assert error.diagnostic() == {'stage':stage,'http_status':status,
        'provider_code':code if code in S.PROVIDER_CODES else None,
        'uncertain_outcome':uncertain and stage != 'reconcile'}
    assert not any(s in str(error) for s in ('private-address','private-api-key','private-body'))


@pytest.mark.parametrize('stage', ['create','send','reconcile'])
def test_transport_errors_do_not_expose_body_or_key(monkeypatch, stage):
    def fail(*a, **kw):
        raise TimeoutError('api-key=private-api-key recipient=private-address@example.test')
    monkeypatch.setattr('requests.request', fail)
    provider = S.Provider()
    with pytest.raises(S.EmailProviderError) as raised:
        {'create':lambda:provider.create({}), 'send':lambda:provider.send(1),
         'reconcile':lambda:provider.report(1)}[stage]()
    assert raised.value.diagnostic()['uncertain_outcome'] is (stage != 'reconcile')
    assert raised.value.http_status is None and 'private' not in str(raised.value)


@pytest.mark.parametrize('body,malformed,size', [({},False,None), ({'id':True},False,None),
    ({'id':0},False,None), ({'id':1},True,None), ({'id':1},False,2_000_001), ([],False,None)])
def test_ambiguous_successful_create_requires_review(monkeypatch, body, malformed, size):
    monkeypatch.setattr('requests.request', lambda *a, **kw:response(201,body,malformed=malformed,size=size))
    with pytest.raises(S.EmailProviderError) as raised:
        S.Provider().create({})
    assert raised.value.stage == 'create' and raised.value.uncertain_outcome
    assert 'private' not in str(raised.value)


@pytest.mark.parametrize('method,path,payload', [
    ('DELETE','/emailCampaigns/1',None), ('GET','/emailCampaigns',None),
    ('GET','/emailCampaigns/1/sendNow',None), ('GET','/emailCampaigns/1',{'unsafe':True}),
    ('GET','/emailCampaigns/0',None), ('GET','/emailCampaigns/01',None),
    ('GET','/emailCampaigns/1?statistics=linksStats',None),
    ('GET','https://evil.test/emailCampaigns/1',None),
    ('POST','/emailCampaigns/1',{}), ('POST','/emailCampaigns/1/../2/sendNow',None),
])
def test_provider_methods_and_exact_paths_are_allowlisted(method,path,payload):
    with pytest.raises(ValueError,match='unsupported_email_operation'):
        S.Provider().request(method,path,payload)


def claim(root, lang='zh', identity=42):
    value={'date':'2026-01-08','language':lang,'edition_hash':'a'*64,'payload_hash':'b'*64,
           'campaign_id':identity,'state':'review_required','error_type':'ValueError'}
    path=root/'subscriptions/deliveries/2026-01-08'/(lang+'.json')
    M.atomic_json(path,value)
    return path,value


@pytest.mark.parametrize('status,sent,delivered,state', [
    ('sent',5,3,'provider_confirmed'), ('sent',0,0,'provider_sent'),
    ('sent',5,0,'provider_sent'), ('queued',0,0,'not_confirmed'),
    ('draft',0,0,'not_confirmed'), ('private-status',0,0,'unreadable'),
])
def test_operator_reconciliation_is_get_only_bound_and_sanitized(tmp_path,monkeypatch,status,sent,delivered,state):
    path,old=claim(tmp_path)
    before=path.read_bytes()
    calls=[]
    def get(method,url,**kwargs):
        calls.append((method,url,kwargs))
        return response(200,{'id':42,'status':status,'htmlContent':'private-body',
            'sender':{'email':'private-address@example.test'},'name':'private-name',
            'statistics':{'globalStats':{'sent':sent,'delivered':delivered,'uniqueViews':True,
                'linksStats':'private-link','complaints':-1}, 'remaining':0},
            'createdAt':'2026-01-08T08:00:00-05:00','modifiedAt':'invalid-private-time',
            'sentDate':'2026-01-08T17:02:00Z'})
    monkeypatch.setattr('requests.request',get)
    result=S.reconcile(tmp_path,'2026-01-08','zh',clock=lambda:moment(18,0))
    assert calls[0][0:2] == ('GET','https://api.brevo.com/v3/emailCampaigns/42') and len(calls)==1
    assert calls[0][2]['params']=={'statistics':'globalStats','excludeHtmlContent':'true'}
    assert 'json' not in calls[0][2] and calls[0][2]['allow_redirects'] is False
    assert path.read_bytes()==before and result['claim_state']=='review_required'
    saved=M.read_json(tmp_path/'subscriptions/provider-observations/2026-01-08/zh.json')
    assert saved==result and result['provider_observation']['state']==state
    assert {k:result[k] for k in ('date','language','edition_hash','payload_hash','campaign_id')}=={
        k:old[k] for k in ('date','language','edition_hash','payload_hash','campaign_id')}
    stats=result['provider_observation']['stats']
    assert stats=={'sent':sent,'delivered':delivered,'remaining':0}
    assert result['provider_observation']['times']=={
        'createdAt':'2026-01-08T13:00:00+00:00','sentDate':'2026-01-08T17:02:00+00:00'}
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('identity', [None,True,0,-1,'42'])
def test_old_claim_without_known_campaign_is_manual_review_not_a_new_request(tmp_path,identity):
    path,_=claim(tmp_path,identity=identity)
    before=path.read_bytes()
    result=S.reconcile(tmp_path,'2026-01-08','zh',provider_factory=lambda:pytest.fail('No known campaign'))
    assert result['state']=='manual_review_required' and result['network_called'] is False
    assert path.read_bytes()==before and not (tmp_path/'subscriptions/provider-observations').exists()


def test_reconciliation_missing_key_and_http_failure_never_change_claim_or_confirm(tmp_path,monkeypatch):
    path,_=claim(tmp_path)
    before=path.read_bytes()
    def no_key():
        raise ValueError('email_key_not_configured')
    monkeypatch.setattr(S,'api_key',no_key)
    assert S.reconcile(tmp_path,'2026-01-08','zh')['state']=='configuration_required'
    monkeypatch.setattr(S,'api_key',lambda:'synthetic-key')
    monkeypatch.setattr('requests.request',lambda *a,**kw:response(401,{'code':'unauthorized','message':'private'}))
    result=S.reconcile(tmp_path,'2026-01-08','zh')
    assert result['state']=='reconciliation_unavailable' and result['error']['stage']=='reconcile'
    assert path.read_bytes()==before and not (tmp_path/'subscriptions/provider-observations').exists()


@pytest.mark.parametrize('reported', [43,True,None])
def test_provider_report_for_other_campaign_is_never_confirmed(tmp_path,monkeypatch,reported):
    claim(tmp_path)
    monkeypatch.setattr('requests.request',lambda *a,**kw:response(200,{
        'id':reported,'status':'sent','statistics':{'globalStats':{'delivered':100}}}))
    assert S.reconcile(tmp_path,'2026-01-08','zh')['state']=='reconciliation_unavailable'
    assert not (tmp_path/'subscriptions/provider-observations').exists()


def test_reconcile_cli_requires_explicit_date_and_language(isolated_outputs,monkeypatch,capsys):
    claim(isolated_outputs)
    for argv in (['--reconcile'],['--reconcile','--date','2026-01-08'],['--check','--lang','zh']):
        with pytest.raises(SystemExit):
            S.main(argv)
    monkeypatch.setattr('requests.request',lambda *a,**kw:response(200,{'id':42,'status':'draft'}))
    assert S.main(['--reconcile','--date','2026-01-08','--lang','zh'])==0
    assert json.loads(capsys.readouterr().out)['state']=='observed'


def test_policy_cli_preserves_operator_metadata_and_all_existing_claims(isolated_outputs,capsys):
    value=configure(isolated_outputs)
    path,_=claim(isolated_outputs)
    before=path.read_bytes()
    assert S.main(['--delivery-policy','allow_text'])==0
    assert json.loads(capsys.readouterr().out)=={'enabled':True,'key_format_valid':True,
        'network_called':False,'delivery_policy':'allow_text'}
    assert S.config(isolated_outputs)=={**value,'delivery_policy':'allow_text'}
    assert path.read_bytes()==before
    config_path=isolated_outputs/'subscriptions/config.json'
    M.atomic_json(config_path,{'enabled':False,'private_note':'preserve'})
    disabled=config_path.read_bytes()
    with pytest.raises(ValueError,match='valid_email_settings_required'):
        S.main(['--delivery-policy','require_audio'])
    assert config_path.read_bytes()==disabled and path.read_bytes()==before


def policy(root,value='allow_text'):
    settings=configure(root)
    settings['delivery_policy']=value
    M.atomic_json(root/'subscriptions/config.json',settings)
    return settings


class Recorder:
    def __init__(self):
        self.messages=[]
        self.sends=[]
    def create(self,message):
        self.messages.append(message)
        return len(self.messages)
    def send(self,identity):
        self.sends.append(identity)


@pytest.mark.parametrize('reason', ['missing','failed','probe'])
def test_allow_text_can_deliver_each_language_without_unverified_audio(tmp_path,reason):
    policy(tmp_path)
    path,brief,responses=fixture(tmp_path,audio=reason!='missing')
    if reason=='failed':
        manifest=A.sidecar(tmp_path,brief,'audio-v1')/'zh.json'
        row=M.read_json(manifest)
        row['state']='failed'
        M.atomic_json(manifest,row)
        body=json.loads(responses['api/news.json'])
        body['briefing']['audio']=A.inspect(tmp_path,brief)
        responses['api/news.json']=json.dumps(body).encode()
    def get(url,limit):
        if reason=='probe' and url.endswith('/zh.mp3'):
            raise TimeoutError('private CDN body')
        return responses[url]
    verified=P.verify(tmp_path,brief,fetcher=get,clock=lambda:moment(17,1))
    assert verified['text_verified'] is True
    provider=Recorder()
    result=S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,2),provider_factory=lambda:provider)
    assert result['state']=='submitted' and provider.sends==[1,2]
    for lang,message in zip(('en','zh'),provider.messages):
        receipt=M.read_json(tmp_path/'subscriptions/deliveries/2026-01-08'/(lang+'.json'))
        has_audio=lang in verified['audio_verified']
        assert receipt['content_mode']==('audio' if has_audio else 'text_only')
        assert receipt['delivery_policy']=='allow_text'
        assert receipt['payload_hash']==M.digest(message)
        assert (P.SITE+'media/briefing/' in message['htmlContent']) is has_audio
        if not has_audio:
            assert ('temporarily unavailable' if lang=='en' else '语音暂不可用') in message['htmlContent']
    # A later successful probe cannot turn today's text mail into another campaign.
    restored=P.verify(tmp_path,brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,6),force=True)
    S.deliver(tmp_path,brief,restored,clock=lambda:moment(17,7),provider_factory=lambda:provider)
    assert len(provider.messages)==2


def test_default_require_audio_stays_closed_without_both_attachments(tmp_path):
    configure(tmp_path)
    _,brief,responses=fixture(tmp_path,audio=False)
    proof=P.verify(tmp_path,brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    assert S.deliver(tmp_path,brief,proof,clock=lambda:moment(17,2),
                     provider_factory=lambda:pytest.fail('default gate'))['state']=='public_delivery_unconfirmed'


@pytest.mark.parametrize('bad', ['manifest','bytes','local_integrity','text','build','stale','identity','future'])
def test_allow_text_never_excuses_wrong_text_audio_or_stale_proof(tmp_path,bad):
    policy(tmp_path)
    _,brief,responses=fixture(tmp_path)
    if bad=='bytes':
        responses[brief['audio']['languages']['zh']['url']]=b'wrong'
    elif bad=='local_integrity':
        A.resolve_asset(tmp_path,brief['mode'],brief['date'],brief['edition_hash'],'audio-v1','zh').write_bytes(b'wrong')
    elif bad=='build':
        responses['build.json']=b'{"briefing":{}}'
    elif bad in {'manifest','text'}:
        body=json.loads(responses['api/news.json'])
        if bad=='manifest':
            body['briefing']['audio']['languages']['zh']['url']='https://evil.test/track'
        else:
            body['briefing']['text']['zh']='unverified text'
        responses['api/news.json']=json.dumps(body).encode()
    proof=P.verify(tmp_path,brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    if bad=='stale': proof['observed_at']=moment(16,0).isoformat()
    if bad=='identity': proof['expectation_hash']='0'*64
    if bad=='future': proof['observed_at']=moment(18,0).isoformat()
    assert S.deliver(tmp_path,brief,proof,clock=lambda:moment(17,2),
                     provider_factory=lambda:pytest.fail('mismatch gate'))['state']=='public_delivery_unconfirmed'
    assert not (tmp_path/'subscriptions/deliveries').exists()


def test_structured_claim_diagnostics_are_preserved_and_not_retried(tmp_path,monkeypatch):
    brief,proof=setup(tmp_path)
    calls=[]
    def rejected(method,url,**kw):
        calls.append(method)
        return response(400,{'code':'invalid_parameter','message':'private-address@example.test private-key'})
    monkeypatch.setattr('requests.request',rejected)
    S.deliver(tmp_path,brief,proof,clock=lambda:moment(17,2))
    rows=list((tmp_path/'subscriptions/deliveries').rglob('*.json'))
    before={p:p.read_bytes() for p in rows}
    for row in rows:
        receipt=M.read_json(row)
        assert receipt['state']=='review_required' and receipt['error']=={
            'stage':'create','http_status':400,'provider_code':'invalid_parameter','uncertain_outcome':False}
        assert 'private' not in row.read_text(encoding='utf-8')
    S.deliver(tmp_path,brief,proof,clock=lambda:moment(17,3))
    assert calls==['POST','POST'] and all(p.read_bytes()==old for p,old in before.items())


def test_automation_can_reach_text_policy_after_matching_publication(tmp_path):
    policy(tmp_path)
    path,brief,responses=fixture(tmp_path,audio=False)
    M.atomic_json(path.parent/'publish.json',{'state':'published','edition_hash':brief['edition_hash']})
    provider=Recorder()
    result=Q.after(tmp_path,clock=lambda:moment(17,2),
        verifier=lambda root,b,**kw:P.verify(root,b,fetcher=lambda u,n:responses[u],**kw),
        notifier=lambda root,b,v,**kw:S.deliver(root,b,v,provider_factory=lambda:provider,**kw))
    assert result['public']['state']=='text_verified' and result['email']['state']=='submitted'
    assert len(provider.messages)==2


def test_cloud_text_payload_and_journal_fingerprint_match_across_runners(tmp_path):
    policy(tmp_path/'seed')
    _,brief,responses=fixture(tmp_path/'seed',audio=False)
    proof=P.verify(tmp_path/'seed',brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    store=make_store(tmp_path)
    provider=Recorder()
    import shutil
    for name in ('first','second'):
        root=tmp_path/name
        shutil.copytree(tmp_path/'seed',root)
        with restart(store).session() as journal:
            result=D.deliver_guarded(root,brief,proof,journal,provider_factory=lambda:provider,clock=lambda:moment(17,2))
        assert result['state']=='submitted'
    assert len(provider.messages)==2 and len(provider.sends)==2
    claims=json.loads(store.read('control/journal-v1').body)['claims']
    for lang,message in zip(('en','zh'),provider.messages):
        assert claims['email-create/2026-01-08/'+lang]['fingerprint']==M.digest(message)
        assert claims['email-send/2026-01-08/'+lang]['fingerprint']==M.digest(message)
        assert 'media/briefing' not in message['htmlContent']


def test_cloud_text_policy_rechecks_probe_age_before_send(tmp_path):
    policy(tmp_path/'seed')
    _,brief,responses=fixture(tmp_path/'seed',audio=False)
    proof=P.verify(tmp_path/'seed',brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    store=make_store(tmp_path)
    now=[moment(17,2)]
    class Slow(Recorder):
        def create(self,message):
            identity=super().create(message)
            now[0]+=timedelta(minutes=16)
            return identity
        def send(self,identity):
            pytest.fail('proof expired during create')
    provider=Slow()
    with restart(store).session() as journal:
        result=D.deliver_guarded(tmp_path/'seed',brief,proof,journal,provider_factory=lambda:provider,clock=lambda:now[0])
    assert result['state']=='attention_required' and len(provider.messages)==1
