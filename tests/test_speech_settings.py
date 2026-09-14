import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from fxdash.narrative import speech_settings as P

read_registry = P._read_user_environment


@pytest.fixture(autouse=True)
def isolate_speech_environment(monkeypatch):
    for name in P.NAMES:
        monkeypatch.setenv(name, 'test-placeholder')
        monkeypatch.delenv(name)


def test_audition_credentials_alone_do_not_enable_cloud(monkeypatch):
    monkeypatch.setenv('FXDASH_AUDIO', 'windows')
    monkeypatch.setattr(P, '_read_user_environment', lambda: {'AZURE_SPEECH_KEY':'private-test-key'})
    assert P.refresh_user_speech_environment() == {'state':'unchanged'}
    assert os.environ['FXDASH_AUDIO'] == 'windows'
    assert 'AZURE_SPEECH_KEY' not in os.environ


def test_persisted_selection_refreshes_stale_worker_without_logging_key(monkeypatch, capsys):
    monkeypatch.setenv('FXDASH_AUDIO', 'windows')
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'old-test-key')
    values = {'FXDASH_AUDIO':'azure', 'AZURE_SPEECH_KEY':'new-private-test-key',
              'AZURE_SPEECH_REGION':'eastus2', 'UNRELATED':'must-not-import'}
    monkeypatch.setattr(P, '_read_user_environment', lambda: values)
    result = P.refresh_user_speech_environment()
    assert result == {'state':'refreshed', 'backend':'azure'}
    assert os.environ['AZURE_SPEECH_KEY'] == 'new-private-test-key'
    assert os.environ['AZURE_SPEECH_REGION'] == 'eastus2'
    assert os.environ.get('UNRELATED') != 'must-not-import'
    assert 'private' not in json.dumps(result) and not capsys.readouterr().out


@pytest.mark.parametrize('mode', ['off', 'windows', 'invalid', None])
def test_opt_out_or_invalid_selection_cannot_reuse_old_cloud_credentials(monkeypatch, mode):
    monkeypatch.setenv('FXDASH_AUDIO', 'azure')
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'old-test-key')
    monkeypatch.setattr(P, '_read_user_environment', lambda: {'FXDASH_AUDIO':mode, 'AZURE_SPEECH_KEY':'saved-key'})
    P.refresh_user_speech_environment()
    assert os.environ['FXDASH_AUDIO'] == (mode if mode in {'off','windows'} else 'off')
    assert 'AZURE_SPEECH_KEY' not in os.environ


def test_deleted_key_is_not_replaced_with_an_inherited_key(monkeypatch):
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'inherited-test-key')
    monkeypatch.setattr(P, '_read_user_environment', lambda: {'FXDASH_AUDIO':'azure','AZURE_SPEECH_REGION':'eastus2'})
    P.refresh_user_speech_environment()
    assert 'AZURE_SPEECH_KEY' not in os.environ


def test_user_environment_read_failure_disables_only_audio(monkeypatch):
    monkeypatch.setenv('AZURE_SPEECH_KEY','private-test-key')
    def fail():
        raise PermissionError('private message')
    monkeypatch.setattr(P,'_read_user_environment',fail)
    assert P.refresh_user_speech_environment() == {'state':'user_environment_unavailable','backend':'off'}
    assert os.environ['FXDASH_AUDIO'] == 'off' and 'AZURE_SPEECH_KEY' not in os.environ


def test_registry_reader_queries_only_allowlisted_user_environment_values(monkeypatch):
    calls = []
    class Handle:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def open_key(*args):
        calls.append(args)
        return Handle()
    def query(key, name):
        calls.append(name)
        if name == 'AZURE_SPEECH_REGION': raise FileNotFoundError()
        return ('azure',1) if name == 'FXDASH_AUDIO' else ('private-test-key',2)
    registry = SimpleNamespace(OpenKey=open_key, QueryValueEx=query, HKEY_CURRENT_USER=10, KEY_READ=20, REG_SZ=1)
    monkeypatch.setitem(sys.modules,'winreg',registry)
    monkeypatch.setattr(sys,'platform','win32')
    assert read_registry() == {'FXDASH_AUDIO':'azure', 'AZURE_SPEECH_KEY':None}
    assert calls == [(10,'Environment',0,20), *P.NAMES]


@pytest.mark.parametrize('entry_name', ['run_briefing_task','run_catchup_task'])
def test_scheduled_entry_refreshes_settings_before_dispatch(tmp_path, monkeypatch, entry_name):
    from fxdash.narrative import morning as M, morning_dispatch as G, catchup as C
    source = Path(__file__).resolve().parents[1]/'ops'/(entry_name+'.py')
    spec = importlib.util.spec_from_file_location(entry_name,source)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(entry,'__file__',str(tmp_path/'ops'/(entry_name+'.py')))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setattr(sys,'stdout',sys.stdout)
    monkeypatch.setattr(sys,'stderr',sys.stderr)
    calls=[]
    monkeypatch.setattr(P,'refresh_user_speech_environment',lambda:calls.append('settings'))
    if entry_name == 'run_catchup_task':
        monkeypatch.setattr(C,'due',lambda now:(calls.append('gate'),False)[1])
        assert entry.main() == 0
        assert calls == ['settings','gate']
    else:
        monkeypatch.setattr(M,'slot',lambda now:'idle')
        monkeypatch.setattr(G,'main',lambda args:(calls.append('dispatch'),0)[1])
        assert entry.main() == 0
        assert calls == ['settings','dispatch']
