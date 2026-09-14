import json
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from fxdash.narrative import azure_speech as Z, audio_briefing as B, audio_script as S, morning as M
from test_audio_briefing import saved, brief, prepare, renderer
from test_morning import moment


@pytest.fixture(autouse=True)
def no_real_speech(monkeypatch):
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'test-private-key')
    monkeypatch.setenv('AZURE_SPEECH_REGION', 'eastus')
    monkeypatch.setattr(Z.requests, 'post', lambda *a, **k: pytest.fail('Unmocked network request'))
    monkeypatch.setattr(Z.shutil, 'which', lambda name: 'test-ffprobe')


class Response:
    status_code = 200
    headers = {'Content-Type': 'audio/mpeg'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield b'ID3' + b'x' * 4000


def mock_service(monkeypatch, response=None, duration=90):
    calls = []
    monkeypatch.setattr(Z.requests, 'post', lambda *a, **k: (calls.append((a, k)), response or Response())[1])
    monkeypatch.setattr(Z.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({'format': {'duration': duration}})))
    return calls


def transcript(tmp_path):
    path = tmp_path / 'text.txt'
    path.write_text('First paragraph.\n\nSecond paragraph.', encoding='utf-8')
    return path


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_ssml_escapes_saved_text_and_sets_voice_and_pauses(lang):
    body = Z.ssml('A <audio src="https://evil.example"/> & B\n\nSecond.', lang)
    root = ET.fromstring(body)
    ns = {'s': 'http://www.w3.org/2001/10/synthesis'}
    assert not root.findall('.//s:audio', ns)
    assert len(root.findall('.//s:p', ns)) == 2
    assert len(root.findall('.//s:break', ns)) == 1
    assert Z.VOICES[lang][1] in body and Z.VOICES[lang][2] in body
    assert 'rate="+33%"' in body and '321ms' in body
    assert 1.33 / 0.95 == pytest.approx(1.4)


@pytest.mark.parametrize('text,lang', [('', 'en'), ('x'*4001, 'en'), ('hello', 'fr'), ('bad\x00text', 'en')])
def test_invalid_copy_does_not_reach_service(tmp_path, text, lang):
    path = transcript(tmp_path)
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        Z.render(path, tmp_path/'en.mp3', lang)


@pytest.mark.parametrize('region', ['', 'https://evil.example', 'eastus/../../', 'eastus.evil.example'])
def test_missing_or_invalid_region_cannot_send_credentials(tmp_path, monkeypatch, region):
    monkeypatch.setenv('AZURE_SPEECH_REGION', region)
    with pytest.raises((RuntimeError, ValueError)):
        Z.render(transcript(tmp_path), tmp_path/'en.mp3', 'en')


@pytest.mark.parametrize('key', ['\x16', 'a\x16b', 'a\x7fb', 'a\u200bb', 'a b', '*', '***'])
def test_hidden_control_or_placeholder_key_cannot_reach_service(tmp_path, monkeypatch, key):
    monkeypatch.setenv('AZURE_SPEECH_KEY', key)
    with pytest.raises(ValueError, match='invalid_azure_speech_key'):
        Z.render(transcript(tmp_path), tmp_path/'en.mp3', 'en')


def test_render_has_one_request_fixed_host_no_redirect_and_no_key_in_metadata(tmp_path, monkeypatch):
    calls = mock_service(monkeypatch)
    target = tmp_path/'en.mp3'
    result = Z.render(transcript(tmp_path), target, 'en')
    assert result == {'engine': 'azure-neural-speech', 'voice': 'en-US-GuyNeural', 'duration_seconds': 90}
    assert len(calls) == 1 and target.is_file()
    args, kwargs = calls[0]
    assert args[0] == 'https://eastus.tts.speech.microsoft.com/cognitiveservices/v1'
    assert kwargs['allow_redirects'] is False and kwargs['timeout'] == (10, 60)
    assert kwargs['headers']['Ocp-Apim-Subscription-Key'] == 'test-private-key'
    assert 'test-private-key' not in kwargs['data'].decode()
    assert 'rate="+33%"' in kwargs['data'].decode()
    assert 'test-private-key' not in json.dumps(result)


@pytest.mark.parametrize('code', [301, 302, 400, 401, 403, 429, 500])
def test_provider_failure_has_no_retry_or_saved_mp3(tmp_path, monkeypatch, code):
    response = Response()
    response.status_code = code
    calls = mock_service(monkeypatch, response)
    target = tmp_path/'en.mp3'
    with pytest.raises(RuntimeError, match='^azure_speech_render_failed$'):
        Z.render(transcript(tmp_path), target, 'en')
    assert len(calls) == 1 and not target.exists()


@pytest.mark.parametrize('duration', [0, 59, 181, float('nan'), float('inf')])
def test_wrong_real_duration_is_withheld(tmp_path, monkeypatch, duration):
    mock_service(monkeypatch, duration=duration)
    target = tmp_path/'en.mp3'
    with pytest.raises(RuntimeError):
        Z.render(transcript(tmp_path), target, 'en')
    assert not target.exists()


def test_preview_allows_short_audio_but_never_overwrites_it(tmp_path, monkeypatch):
    calls = mock_service(monkeypatch, duration=30)
    target = tmp_path/'en.mp3'
    Z.render(transcript(tmp_path), target, 'en', preview=True)
    original = target.read_bytes()
    with pytest.raises(ValueError, match='audio_destination_exists'):
        Z.render(tmp_path/'text.txt', target, 'en', preview=True)
    assert target.read_bytes() == original and len(calls) == 1


def test_check_is_read_only_and_never_prints_key(monkeypatch, capsys):
    assert Z.main(['--check']) == 0
    result = capsys.readouterr().out
    assert 'test-private-key' not in result and json.loads(result)['network_called'] is False
    monkeypatch.delenv('AZURE_SPEECH_KEY')
    assert Z.main(['--check']) == 1
    assert json.loads(capsys.readouterr().out)['state'] == 'azure_speech_not_configured'


@pytest.mark.parametrize('failure', ['wrong_type', 'tiny', 'oversized', 'transport', 'probe'])
def test_invalid_response_and_private_errors_are_withheld(tmp_path, monkeypatch, failure):
    response = Response()
    if failure == 'wrong_type':
        response.headers = {'Content-Type': 'text/html'}
    if failure == 'tiny':
        response.iter_content = lambda **kwargs: iter([b'bad'])
    if failure == 'oversized':
        response.iter_content = lambda **kwargs: iter([b'x'*(Z.MAX_AUDIO_BYTES+1)])
    mock_service(monkeypatch, response)
    if failure == 'transport':
        def leak(*args, **kwargs):
            raise Z.requests.RequestException('test-private-key: provider body')
        monkeypatch.setattr(Z.requests, 'post', leak)
    if failure == 'probe':
        monkeypatch.setattr(Z.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    target = tmp_path/'en.mp3'
    with pytest.raises(RuntimeError) as raised:
        Z.render(transcript(tmp_path), target, 'en')
    assert str(raised.value) == 'azure_speech_render_failed'
    assert raised.value.__suppress_context__ is True
    assert not target.exists()


def test_neural_publication_receipt_is_separate_from_old_voice_receipt(tmp_path, monkeypatch):
    path, _, _ = saved(tmp_path)
    prepare(tmp_path, path)
    B.record_publication(tmp_path, brief(path), clock=lambda: moment(17, 0))
    old = B.sidecar(tmp_path, brief(path))/'publication.json'
    original = old.read_bytes()
    monkeypatch.setenv('FXDASH_AUDIO', 'azure')
    B.ensure(tmp_path, path, renderer=neural_renderer, clock=lambda: moment(17, 0))
    pushes = []
    assert B.retry_publication(tmp_path, brief(path), tmp_path, lambda repo: pushes.append(1), clock=lambda: moment(18, 0)) == 'published'
    assert old.read_bytes() == original and pushes == [1]
    receipt = B.sidecar(tmp_path, brief(path), S.NEURAL_VERSION)/'publication.json'
    assert M.read_json(receipt)['state'] == 'published'


def neural_renderer(text, target, lang):
    return {**renderer(text, target, lang), 'engine': 'azure-neural-speech', 'voice': Z.VOICES[lang][1]}


def legacy_neural(root, path):
    """Previously frozen v2 attachments, independently of the current renderer."""
    current = brief(path)
    folder = B.sidecar(root, current, 'audio-v2')
    folder.mkdir(parents=True)
    edition = M.read_json(path)
    packet = M.read_json(path.parent / 'packet.json')
    for lang in ('en', 'zh'):
        script, media = folder / (lang + '.txt'), folder / (lang + '.mp3')
        script.write_text(S.compose(edition, packet, lang, version='audio-v2'), encoding='utf-8')
        info = neural_renderer(script, media, lang)
        M.atomic_json(folder / (lang + '.json'), dict(B.identity(current), **info,
            script_version='audio-v2', language=lang, state='ready',
            generated_at=moment(17, 0).isoformat(),
            audio_sha256=B.file_hash(media), script_sha256=B.file_hash(script)))
    return folder


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_v3_omits_spoken_disclosure_and_preserves_v2_body(tmp_path, lang):
    _, edition, packet = saved(tmp_path)
    old = S.compose(edition, packet, lang, version='audio-v2')
    new = S.compose(edition, packet, lang, version='audio-v3')
    disclosure = ', read by a synthetic voice' if lang == 'en' else '，采用合成语音'
    assert old.count(disclosure) == 1
    assert disclosure not in new
    assert new == old.replace(disclosure, '')


def test_legacy_neural_is_playable_when_no_new_recording_exists(tmp_path):
    path, _, _ = saved(tmp_path)
    legacy_neural(tmp_path, path)
    result = B.inspect(tmp_path, brief(path))
    assert result['state'] == 'ready' and result['script_version'] == 'audio-v2'


def test_opt_in_creates_new_version_without_replacing_legacy_or_frozen_text(tmp_path, monkeypatch):
    path, edition, packet = saved(tmp_path)
    prepare(tmp_path, path)
    old_root = B.sidecar(tmp_path, brief(path))
    original = {p: p.read_bytes() for p in old_root.rglob('*') if p.is_file()}
    v2_root = legacy_neural(tmp_path, path)
    B.record_publication(tmp_path, brief(path), clock=lambda: moment(17, 0))
    original.update({p: p.read_bytes() for p in v2_root.rglob('*') if p.is_file()})
    original[path] = path.read_bytes()
    original[path.parent/'packet.json'] = (path.parent/'packet.json').read_bytes()
    monkeypatch.setenv('FXDASH_AUDIO', 'azure')
    result = B.ensure(tmp_path, path, renderer=neural_renderer, clock=lambda: moment(17, 0))
    assert result['state'] == 'ready' and result['script_version'] == S.NEURAL_VERSION
    assert B.inspect(tmp_path, brief(path))['script_version'] == S.NEURAL_VERSION
    for version in S.VERSIONS:
        assert B.resolve_asset(tmp_path, 'catchup', edition['date'], brief(path)['edition_hash'], version, 'en').is_file()
    assert all(p.read_bytes() == value for p, value in original.items())
    B.ensure(tmp_path, path, renderer=lambda *a: pytest.fail('Do not regenerate'), clock=lambda: moment(18, 0))
    for lang in ('en', 'zh'):
        concise = S.compose(edition, packet, lang, version=S.NEURAL_VERSION)
        assert len(concise) < len(S.compose(edition, packet, lang))
        assert ('minus 200.0 basis points' if lang == 'en' else '负200.0个基点') in concise
        assert ('provisional' if lang == 'en' else '待确认') in concise
        assert ('log return' if lang == 'en' else '对数收益') in concise
        assert ('no economic calendar' if lang == 'en' else '未接入经济日历') in concise


def test_unconfigured_opt_in_preserves_legacy_and_does_not_consume_attempt(tmp_path, monkeypatch):
    path, _, _ = saved(tmp_path)
    prepare(tmp_path, path)
    monkeypatch.setenv('FXDASH_AUDIO', 'azure')
    monkeypatch.delenv('AZURE_SPEECH_KEY')
    assert B.ensure(tmp_path, path) == {'state': 'configuration_required'}
    assert not B.sidecar(tmp_path, brief(path), S.NEURAL_VERSION).exists()
    assert B.inspect(tmp_path, brief(path))['state'] == 'ready'


def test_neural_failure_does_not_silently_fall_back_to_legacy_voice(tmp_path, monkeypatch):
    path, _, _ = saved(tmp_path)
    prepare(tmp_path, path)
    legacy_neural(tmp_path, path)
    monkeypatch.setenv('FXDASH_AUDIO', 'azure')
    calls = []
    def failure(*args):
        calls.append(args[2])
        raise RuntimeError('private-provider-error')
    result = B.ensure(tmp_path, path, renderer=failure, clock=lambda: moment(17, 0))
    assert result['state'] == 'unavailable' and result['script_version'] == S.NEURAL_VERSION
    assert B.inspect(tmp_path, brief(path))['state'] == 'unavailable'
    B.ensure(tmp_path, path, renderer=failure, clock=lambda: moment(17, 1))
    B.ensure(tmp_path, path, renderer=failure, clock=lambda: moment(17, 16))
    B.ensure(tmp_path, path, renderer=failure, clock=lambda: moment(18, 0))
    assert calls == ['en', 'zh', 'en', 'zh']
    assert B.inspect(tmp_path, brief(path), version=S.VERSION)['state'] == 'ready'
    assert B.inspect(tmp_path, brief(path), version='audio-v2')['state'] == 'ready'
    manifests = B.sidecar(tmp_path, brief(path), S.NEURAL_VERSION).rglob('*.json')
    assert all('private-provider-error' not in p.read_text() for p in manifests)
