import copy
from datetime import timedelta
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from fxdash.narrative import audio_briefing as B, audio_script as S, morning as M, briefing_archive as A
from test_catchup import inputs
from test_morning import moment, note


def saved(root, *, commentary=True):
    from fxdash.narrative.driver_notes import PROMPT_VERSION
    packet = inputs(root)
    for i, row in enumerate(packet['pairs']):
        row.update(y=0.0001*(i+1),residual=0.00001)
    packet['pairs'][0].update(provisional=True, y=-0.02, residual=0.0012)
    notes = [{"pair": packet['pairs'][0]['pair'], "raw": note(packet['pairs'][0]),
              "published": True, "prompt_version": PROMPT_VERSION}] if commentary else []
    edition = M.compose_edition(packet, notes, moment=moment(16, 1), mode='catchup')
    edition.update(scheduled=False, morning_target=moment(14, 0).isoformat())
    path = B.edition_path(root, 'catchup', '2026-01-08')
    M.atomic_json(path, edition)
    M.atomic_json(path.parent / 'packet.json', packet)
    return path, edition, packet


def renderer(text, target, lang):
    assert text.read_text(encoding='utf-8')
    target.write_bytes(b'ID3' + b'x' * 4000)
    return {'duration_seconds': 90.125, 'engine': 'windows-system-speech', 'voice': 'Test voice '+lang}


def prepare(root, path, **kwargs):
    return B.ensure(root, path, clock=lambda: moment(17, 0), renderer=renderer, **kwargs)


def brief(path):
    return A.read_edition(path, mode='catchup')


def test_script_signed_numbers_dates_provisional_and_limits(tmp_path):
    path, edition, packet = saved(tmp_path)
    for lang in ('en','zh'):
        text = S.compose(edition, packet, lang)
        assert ('minus 200.0 basis points' if lang=='en' else '负200.0个基点') in text
        assert ('plus 12.0 basis points' if lang=='en' else '正12.0个基点') in text
        assert ('provisional' if lang=='en' else '待确认') in text
        assert ('no economic calendar' if lang=='en' else '未接入经济日历') in text
        assert ('January 7, 2026' if lang=='en' else '2026年1月7日') in text
        assert ('synthetic voice' if lang=='en' else '合成语音') in text
        assert 'Example' in text
        assert not any(c in text for c in '—–')


@pytest.mark.parametrize('change',[
    lambda p:p.update(as_of='2026-01-06'),
    lambda p:p['pairs'].pop(),
    lambda p:p['pairs'][0].update(y=float('nan')),
    lambda p:p['pairs'][0].update(residual=None),
    lambda p:p['pairs'][0].update(date='2026-01-01'),
])
def test_script_rejects_invalid_evidence(tmp_path,change):
    _, edition, packet = saved(tmp_path)
    change(packet)
    with pytest.raises((ValueError,TypeError)):
        S.compose(edition, packet, 'en')


def test_unchecked_news_never_spoken(tmp_path):
    _, edition, packet = saved(tmp_path)
    edition['notes'][0]['note']['en']['event']='The yen will rise.'
    text = S.compose(edition,packet,'en')
    assert 'will rise' not in text and 'No usable news interpretation' in text


def test_success_is_idempotent_and_frozen_text_is_unchanged(tmp_path):
    path, edition, packet = saved(tmp_path)
    before = {p:p.read_bytes() for p in (path,path.parent/'packet.json')}
    assert prepare(tmp_path,path)['state']=='ready'
    root = B.sidecar(tmp_path,brief(path))
    original = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = B.ensure(tmp_path,path,renderer=lambda *a:pytest.fail('Must reuse MP3'),clock=lambda:moment(18,0))
    assert result['state']=='ready'
    assert all(p.read_bytes()==v for p,v in original.items())
    assert all(p.read_bytes()==v for p,v in before.items())
    exported = A.dashboard(tmp_path)['current']
    assert exported['edition_hash']==M.digest(edition) and exported['audio']['state']=='ready'
    assert 'error' not in json.dumps(exported['audio'])


def test_audio_off_does_not_write(tmp_path):
    path,_,_ = saved(tmp_path)
    assert B.ensure(tmp_path,path)=={'state':'disabled'}
    assert not (tmp_path/'briefing/audio').exists()


def test_language_failure_is_partial_bounded_and_recoverable(tmp_path):
    path,_,_ = saved(tmp_path)
    calls=[]
    def partial(script,mp3,lang):
        calls.append(lang)
        if lang=='zh': raise RuntimeError('private error must not escape')
        return renderer(script,mp3,lang)
    assert B.ensure(tmp_path,path,clock=lambda:moment(17,0),renderer=partial)['state']=='partial'
    assert B.ensure(tmp_path,path,clock=lambda:moment(17,1),renderer=partial)['state']=='partial'
    assert calls==['en','zh']
    assert B.ensure(tmp_path,path,clock=lambda:moment(17,16),renderer=partial)['state']=='partial'
    assert B.ensure(tmp_path,path,clock=lambda:moment(18,0),renderer=partial)['state']=='partial'
    assert calls==['en','zh','zh']
    assert 'private' not in json.dumps(B.inspect(tmp_path,brief(path)))
    assert 'private' not in ''.join(p.read_text() for p in B.sidecar(tmp_path,brief(path)).rglob('*.json'))


def test_interruption_retries_without_touching_ready_language(tmp_path):
    path,_,_=saved(tmp_path)
    def interrupt(script,mp3,lang):
        if lang=='zh': raise KeyboardInterrupt()
        return renderer(script,mp3,lang)
    with pytest.raises(KeyboardInterrupt):
        B.ensure(tmp_path,path,clock=lambda:moment(17,0),renderer=interrupt)
    assert B.ensure(tmp_path,path,clock=lambda:moment(17,16),renderer=renderer)['state']=='ready'
    root=B.sidecar(tmp_path,brief(path))
    assert M.read_json(root/'en.json')['attempts']==1
    assert M.read_json(root/'zh.json')['attempts']==2


@pytest.mark.parametrize('name',['en.mp3','en.txt','en.json'])
def test_corrupted_audio_is_not_served_or_silently_replaced(tmp_path,name):
    path,_,_=saved(tmp_path)
    prepare(tmp_path,path)
    root=B.sidecar(tmp_path,brief(path))
    (root/name).write_bytes(b'corrupt')
    result= B.ensure(tmp_path,path,clock=lambda:moment(18,0),renderer=lambda *a:pytest.fail('Never replace committed audio'))
    assert result['state']!='ready'
    with pytest.raises(ValueError):
        B.resolve_asset(tmp_path,'catchup','2026-01-08',brief(path)['edition_hash'],S.VERSION,'en')
    assert (root/name).read_bytes()==b'corrupt'


@pytest.mark.parametrize('mode,day,identity,version,lang',[
    ('../','2026-01-08','a'*64,'audio-v1','en'),
    ('catchup','../../secrets','a'*64,'audio-v1','en'),
    ('catchup','2026-01-08','../secret','audio-v1','en'),
    ('catchup','2026-01-08','a'*64,'../','en'),
    ('catchup','2026-01-08','a'*64,'audio-v1','../'),
])
def test_asset_path_is_allowlisted(tmp_path,mode,day,identity,version,lang):
    with pytest.raises((ValueError,KeyError)):
        B.resolve_asset(tmp_path,mode,day,identity,version,lang)


def test_publish_retry_has_own_receipt_and_keeps_text_receipt(tmp_path,monkeypatch):
    path,_,_=saved(tmp_path)
    prepare(tmp_path,path)
    text_receipt=path.parent/'publish.json'
    M.atomic_json(text_receipt,{'state':'published','original':True})
    original=text_receipt.read_bytes()
    monkeypatch.setenv('FXDASH_AUDIO','windows')
    calls=[]
    def fail(repo):
        calls.append('failed')
        raise RuntimeError('private')
    assert B.retry_publication(tmp_path,brief(path),tmp_path,fail,clock=lambda:moment(17,1))=='publish_failed'
    assert B.retry_publication(tmp_path,brief(path),tmp_path,fail,clock=lambda:moment(17,2))=='retry_later'
    assert B.retry_publication(tmp_path,brief(path),tmp_path,lambda r:calls.append('ok'),clock=lambda:moment(17,16))=='published'
    assert B.retry_publication(tmp_path,brief(path),tmp_path,fail,clock=lambda:moment(18,0))=='already_published'
    assert calls==['failed','ok'] and text_receipt.read_bytes()==original


def test_audio_failure_does_not_block_text_publication(tmp_path,monkeypatch):
    from fxdash.narrative import catchup as C
    from test_catchup import options
    monkeypatch.setenv('FXDASH_AUDIO','windows')
    from fxdash.narrative import speech
    monkeypatch.setattr(speech,'render',lambda *a:(_ for _ in ()).throw(RuntimeError('No voice')))
    packet=inputs(tmp_path)
    assert C.run(tmp_path,tmp_path,**options(packet))['state']=='published'
    assert A.dashboard(tmp_path)['current']['audio']['state']=='unavailable'


def test_api_and_static_build_include_only_verified_assets(tmp_path,monkeypatch):
    from fxdash.web import headlines,market,build as W
    from fxdash.web.app import create_app
    from test_web import EMPTY_RSS,_write_cache
    monkeypatch.setattr(headlines,'_fetch',lambda q:EMPTY_RSS)
    monkeypatch.setattr(market,'_fetch_dxy',lambda:None)
    root=tmp_path/'pipeline'
    root.mkdir()
    path,_,_=saved(root)
    prepare(root,path)
    cache=tmp_path/'cache'
    _write_cache(cache)
    app=create_app(root,cache_dir=cache)
    client=TestClient(app)
    payload=client.get('/api/news').json()['briefing']
    item=payload['audio']['languages']['en']
    reply=client.get('/'+item['url'])
    assert reply.status_code==200 and reply.headers['content-type']=='audio/mpeg'
    assert client.get('/'+item['url'],headers={'Range':'bytes=0-9'}).status_code==206
    assert client.get('/'+item['url'].replace('/en.mp3','/private.json')).status_code==404
    manifest=W.build(tmp_path/'site',app=app)
    assert len(manifest['media_files'])==2
    assert (tmp_path/'site'/item['url']).read_bytes()==reply.content
    assert not list((tmp_path/'site').rglob('packet.json'))


def test_frontend_is_safe_and_never_autoplays():
    from fxdash.web.app import STATIC_DIR
    node=shutil.which('node')
    if not node: pytest.skip('Node required')
    script="""
      import assert from 'node:assert/strict';
      globalThis.localStorage={getItem:()=> 'en',setItem:()=>{}};
      globalThis.document={documentElement:{}};
      const A=await import(AUDIO),I=await import(LANG);
      const ready={state:'ready',duration_seconds:91,url:'media/briefing/catchup/2026-01-08/'+'a'.repeat(64)+'/audio-v1/en.mp3',
        transcript:'<script>bad</script>',voice:'Synthetic',generated_at:'2026-01-09T10:00:00Z'};
      for (const lang of ['en','zh']) {
        I.setLang(lang);
        const html=A.audioHtml({mode:'catchup',date:'2026-01-08',audio:{languages:{[lang]:ready}}});
        assert.ok(html.includes('preload="none"')); assert.ok(html.includes('controls'));
        assert.ok(!html.includes('autoplay')); assert.ok(!html.includes('<script>'));
        assert.ok(html.includes('1:31')); assert.ok(html.includes('2026-01-09'));
        assert.ok(!A.audioHtml({mode:'catchup',audio:{languages:{[lang]:{...ready,url:'https://evil.example/'}}}}).includes('<audio'));
      }
    """.replace('AUDIO',json.dumps((STATIC_DIR/'briefing-audio.js').as_uri())).replace('LANG',json.dumps((STATIC_DIR/'i18n.js').as_uri()))
    result=subprocess.run([node,'--input-type=module','-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_missing_packet_is_visible_without_speech_call(tmp_path):
    path,_,_=saved(tmp_path)
    (path.parent/'packet.json').unlink()
    result=B.ensure(tmp_path,path,clock=lambda:moment(17,0),renderer=lambda *a:pytest.fail('No matching packet'))
    assert result['state']=='unavailable'


def test_one_damaged_language_does_not_hide_the_other(tmp_path):
    path,_,_=saved(tmp_path)
    prepare(tmp_path,path)
    root=B.sidecar(tmp_path,brief(path))
    (root/'en.mp3').write_bytes(b'bad')
    result=B.inspect(tmp_path,brief(path))
    assert result['state']=='partial' and result['languages']['en']['state']=='integrity_failed'
    assert result['languages']['zh']['state']=='ready'


def test_prefix_collision_refuses_existing_attachment(tmp_path):
    path,_,_=saved(tmp_path)
    prepare(tmp_path,path)
    root=B.sidecar(tmp_path,brief(path))
    old=M.read_json(root/'en.json')
    old['edition_hash']='0'*64
    M.atomic_json(root/'en.json',old)
    before=(root/'en.json').read_bytes()
    assert prepare(tmp_path,path)['state']=='identity_mismatch'
    assert (root/'en.json').read_bytes()==before


def test_catchup_automatically_generates_and_publishes_audio_once(tmp_path,monkeypatch):
    from fxdash.narrative import catchup as C,speech
    from test_catchup import options
    monkeypatch.setenv('FXDASH_AUDIO','windows')
    calls=[]
    monkeypatch.setattr(speech,'render',lambda *a:(calls.append(a[2]),renderer(*a))[1])
    packet=inputs(tmp_path)
    settings=options(packet)
    pushes=[]
    def publish(repo):
        assert A.dashboard(tmp_path)['current']['audio']['state']=='ready'
        pushes.append(1)
    settings['publisher']=publish
    assert C.run(tmp_path,tmp_path,**settings)['state']=='published'
    assert C.run(tmp_path,tmp_path,**settings)['state']=='already_available'
    assert calls==['en','zh'] and pushes==[1]


def test_optional_morning_path_also_generates_audio(tmp_path,monkeypatch):
    from fxdash.narrative import morning_dispatch as G,speech
    from test_morning import packet as morning_packet
    monkeypatch.setenv('FXDASH_AUDIO','windows')
    monkeypatch.setattr(speech,'render',renderer)
    packet=morning_packet(tmp_path)
    packet['pairs']=[dict(packet['pairs'][0],pair=p) for p in S.PAIRS]
    edition=M.compose_edition(packet,[],moment=moment(14,0))
    edition['scheduled']=True
    path=B.edition_path(tmp_path,'edition','2026-01-08')
    M.atomic_json(path,edition)
    M.atomic_json(path.parent/'packet.json',packet)
    pushes=[]
    settings=dict(clock=lambda:moment(14,0),finalize_fn=lambda *a,**k:edition,publisher=lambda r:pushes.append(1))
    assert G.dispatch(tmp_path,tmp_path,**settings)['state']=='published'
    assert G.dispatch(tmp_path,tmp_path,**settings)['state']=='already_published'
    assert A.dashboard(tmp_path)['current']['audio']['state']=='ready' and pushes==[1]
