import copy
import io
import json

import pytest

from fxdash.narrative import subscriptions as S, public_delivery as P, morning as M
from test_public_delivery import fixture
from test_morning import moment


def configure(root):
    value={'enabled':True,'double_opt_in_confirmed':True,'quota_approved':True,'sender_id':4,
           'sender_footer':'Example sender, example postal address',
           'forms':{'en':'https://test.sibforms.com/serve/english','zh':'https://test.sibforms.com/serve/chinese'},
           'lists':{'en':10,'zh':11},'private_note':'not for export'}
    M.atomic_json(root/'subscriptions/config.json',value)
    return value


def setup(root):
    configure(root)
    _,brief,responses=fixture(root)
    verified=P.verify(root,brief,fetcher=lambda u,n:responses[u],clock=lambda:moment(17,1))
    return brief,verified


def test_unconfigured_subscriptions_are_hidden_and_make_no_provider_calls(tmp_path):
    assert S.public_config(tmp_path)=={'enabled':False}
    assert S.deliver(tmp_path,{}, {},provider_factory=lambda:pytest.fail('not enabled'))=={'state':'disabled'}


def test_public_settings_have_no_sender_list_ids_or_credentials(tmp_path):
    configure(tmp_path)
    result=S.public_config(tmp_path)
    assert result['enabled'] and 'forms' in result
    assert not any(s in json.dumps(result) for s in ('sender','lists','private_note','key'))


@pytest.mark.parametrize('padding',['','=','=='])
def test_hosted_form_urls_accept_optional_trailing_padding(padding):
    url='https://test.sibforms.com/serve/Abc_123-'+padding
    assert S.form_url(url)==url


@pytest.mark.parametrize('token',['','=','==','Abc===','Ab=c','Ab==c','Ab=c=',
    'Abc=def==','Abc%3D','Abc/def','Abc+def'])
def test_hosted_form_urls_reject_invalid_tokens(token):
    assert S.form_url('https://test.sibforms.com/serve/'+token) is None


@pytest.mark.parametrize('padding',['','=','=='])
@pytest.mark.parametrize('url',['http://test.sibforms.com/serve/a{padding}',
    'https://evil.test/serve/a{padding}','https://sibforms.com/serve/a{padding}',
    'https://test.sibforms.com.evil.test/serve/a{padding}',
    'https://test.sibforms.com:8443/serve/a{padding}',
    'https://test.sibforms.com/serve/a{padding}?email=x',
    'https://u:p@test.sibforms.com/serve/a{padding}','javascript:alert(1)',
    'https://test.sibforms.com/serve/a{padding}#x'])
def test_hosted_form_urls_are_allowlisted(url,padding):
    assert S.form_url(url.format(padding=padding)) is None


@pytest.mark.parametrize('padding',['=','=='])
def test_configure_accepts_padded_forms(isolated_outputs,monkeypatch,capsys,padding):
    value=configure(isolated_outputs)
    value.pop('private_note')
    value['forms']={lang:url+padding for lang,url in value['forms'].items()}
    monkeypatch.setattr('sys.stdin',io.StringIO(json.dumps(value)))
    monkeypatch.setattr(S,'api_key',lambda:'fake-test-key')
    monkeypatch.setattr('requests.request',lambda *args,**kwargs:pytest.fail('no network'))
    assert S.main(['--configure'])==0
    assert json.loads(capsys.readouterr().out)=={
        'enabled':True,'key_format_valid':True,'network_called':False}
    assert S.config(isolated_outputs)==value
    assert S.public_config(isolated_outputs)['forms']==value['forms']


@pytest.mark.parametrize('token',['Ab=c','Ab==c','Ab=c=','Abc==='])
def test_configure_rejects_malformed_padding(isolated_outputs,monkeypatch,token):
    previous=configure(isolated_outputs)
    value=copy.deepcopy(previous)
    value.pop('private_note')
    value['forms']['en']='https://test.sibforms.com/serve/'+token
    monkeypatch.setattr('sys.stdin',io.StringIO(json.dumps(value)))
    with pytest.raises(ValueError,match='invalid_email_settings'):
        S.main(['--configure'])
    assert S.config(isolated_outputs)==previous


@pytest.mark.parametrize('field',['enabled','double_opt_in_confirmed','quota_approved'])
def test_operator_consent_and_activation_are_required(tmp_path,field):
    value=configure(tmp_path)
    value[field]=False
    M.atomic_json(tmp_path/'subscriptions/config.json',value)
    assert S.public_config(tmp_path)=={'enabled':False}


def test_bilingual_campaigns_submit_once_and_use_public_audio(tmp_path, monkeypatch):
    brief,verified=setup(tmp_path)
    calls=[]
    class Provider:
        def create(self,value):
            calls.append(value)
            assert 'to' not in value and '{{ unsubscribe }}' in value['htmlContent']
            assert P.SITE+'media/briefing/' in value['htmlContent']
            assert '(provisional)' not in value['htmlContent'] and '（待确认）' not in value['htmlContent']
            return len(calls)
        def send(self,identity): pass
    result=S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,2),provider_factory=Provider)
    assert result['state']=='submitted' and len(calls)==2
    assert {c['recipients']['listIds'][0] for c in calls}=={10,11}
    monkeypatch.setattr(S, 'email_text', lambda *a: pytest.fail('A copy change must not resend this edition'))
    S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,3),provider_factory=Provider)
    assert len(calls)==2
    serialized=''.join(p.read_text() for p in (tmp_path/'subscriptions/deliveries').rglob('*.json'))
    assert 'Example sender' not in serialized and 'listIds' not in serialized


@pytest.mark.parametrize('lang', ['en', 'zh'])
@pytest.mark.parametrize('pair', ['AUD', 'CAD', 'EUR', 'JPY', 'MXN', 'NOK'])
def test_email_omits_only_numeric_status_labels(lang, pair):
    label = ' (provisional)' if lang == 'en' else '（待确认）'
    tail = '; residual -7.9 bp.' if lang == 'en' else '，残差 -7.9 bp。'
    news = ' Source: provisional GDP estimate <news>.' if lang == 'en' else '来源：待确认的经济数据 <news>。'
    # Signs and numeric values are preserved, including a displayed negative zero.
    for change in ('+55.8', '-12.3', '-0.0'):
        lead = f'USD/{pair} {change} bp'
        brief = {'text': {lang: lead + label + tail + news}}
        original = copy.deepcopy(brief)
        assert S.email_text(brief, lang) == lead + tail + news
        assert brief == original


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_email_view_keeps_frozen_website_text_and_data_status(tmp_path, lang):
    brief, _ = setup(tmp_path)
    original = copy.deepcopy(brief)
    label = '(provisional)' if lang == 'en' else '（待确认）'
    assert label in brief['text'][lang]
    audio = brief['audio']['languages'][lang]
    rendered = S.payload(S.config(tmp_path), brief, audio, lang)['htmlContent']
    assert label not in rendered
    assert brief == original
    assert P.SITE + audio['url'] in rendered
    assert '{{ unsubscribe }}' in rendered
    # The formatter runs before escaping and does not turn news copy into HTML.
    brief['text'][lang] += '<script>unsafe</script>'
    rendered = S.payload(S.config(tmp_path), brief, audio, lang)['htmlContent']
    assert '&lt;script&gt;unsafe&lt;/script&gt;' in rendered and '<script>' not in rendered


@pytest.mark.parametrize('phase',['create','send'])
def test_uncertain_requests_are_not_blindly_repeated(tmp_path,phase):
    brief,verified=setup(tmp_path)
    calls=[]
    class Provider:
        def create(self,value):
            calls.append(1)
            if phase=='create': raise TimeoutError('secret')
            return 10+len(calls)
        def send(self,identity): raise TimeoutError('secret')
    result=S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,2),provider_factory=Provider)
    assert set(result['languages'].values())=={'review_required'}
    S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,3),provider_factory=Provider)
    assert len(calls)==2 and 'secret' not in str(result)


@pytest.mark.parametrize('stamp',['2026-01-08T13:59:00+00:00','2026-01-09T17:00:00+00:00','2026-01-10T17:00:00+00:00'])
def test_no_early_weekend_or_historical_backlog_email(tmp_path,stamp):
    from datetime import datetime
    brief,verified=setup(tmp_path)
    assert S.deliver(tmp_path,brief,verified,clock=lambda:datetime.fromisoformat(stamp),
                     provider_factory=lambda:pytest.fail('outside window'))['state']=='outside_delivery_day'


@pytest.mark.parametrize('change',[lambda v:v.update(state='pending'),lambda v:v.update(expectation_hash='bad'),
    lambda v:v.update(audio_verified=['en']),lambda v:v.update(observed_at=moment(15,0).isoformat())])
def test_public_delivery_is_required_before_any_send(tmp_path,change):
    brief,verified=setup(tmp_path)
    change(verified)
    assert S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,2),
                     provider_factory=lambda:pytest.fail('not verified'))['state']=='public_delivery_unconfirmed'


def test_caller_cannot_change_the_frozen_email_text(tmp_path):
    brief,verified=setup(tmp_path)
    brief['text']={'en':'injected','zh':'injected'}
    class Provider:
        def create(self,value):
            assert 'injected' not in value['htmlContent']
            return 5
        def send(self,identity): pass
    assert S.deliver(tmp_path,brief,verified,clock=lambda:moment(17,2),provider_factory=Provider)['state']=='submitted'
