"""Synthetic registry and workers; never read host keys or launch providers/tasks."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from fxdash import scheduled_environment as E, task_runner as T
from fxdash.narrative import automation, catchup, morning, morning_dispatch
from fxdash.narrative import run as narrative_run


read_registry = E._read_user_environment
NOW = datetime(2026, 10, 9, 16, tzinfo=timezone.utc)
SAVED = {
    'FRED_API_KEY': 'synthetic-new-fred', 'BANXICO_TOKEN': 'synthetic-new-banxico',
    'GEMINI_API_KEY': 'synthetic-new-gemini', 'BREVO_API_KEY': 'synthetic-new-brevo',
    'FXDASH_AUDIO': 'azure', 'AZURE_SPEECH_KEY': 'synthetic-new-azure',
    'AZURE_SPEECH_REGION': 'eastus2',
}


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(E.sys, 'platform', 'win32')


@pytest.mark.parametrize('entry', E.ENTRY_NAMES)
def test_rotation_imports_only_entry_scope_and_reports_no_values(windows, monkeypatch, entry, capsys):
    called = []
    def saved(names):
        called.append(names)
        return dict(SAVED, UNRELATED='must-not-import')
    monkeypatch.setattr(E, '_read_user_environment', saved)
    environment = {name: 'synthetic-inherited-secret' for name in E.NAMES}
    environment['UNRELATED'] = 'retained'
    before = dict(environment)
    result = E.refresh(entry, environment=environment)
    assert called == [E.ENTRY_NAMES[entry]]
    for name in E.NAMES:
        assert environment[name] == (SAVED[name] if name in E.ENTRY_NAMES[entry] else before[name])
    assert environment['UNRELATED'] == 'retained'
    assert result['state'] == 'refreshed' and all(result['credentials'].values())
    assert not any(value in json.dumps(result) for value in SAVED.values() if value.startswith('synthetic'))
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('entry', E.ENTRY_NAMES)
def test_deleted_saved_keys_clear_stale_values_without_touching_other_scope(windows, monkeypatch, entry):
    monkeypatch.setattr(E, '_read_user_environment', lambda names: {})
    environment = {name: 'inherited-secret' for name in E.NAMES}
    result = E.refresh(entry, environment=environment)
    for name in E.NAMES:
        if name in E.ENTRY_NAMES[entry]:
            assert environment.get(name) == ('off' if name == 'FXDASH_AUDIO' else None)
        else:
            assert environment[name] == 'inherited-secret'
    assert not any(result['credentials'].values())


@pytest.mark.parametrize('entry', E.ENTRY_NAMES)
def test_denied_registry_fails_closed_and_sanitizes_exception(windows, monkeypatch, entry):
    def denied(names):
        raise PermissionError('private-registry-detail-must-not-escape')
    monkeypatch.setattr(E, '_read_user_environment', denied)
    environment = {name: 'inherited-secret' for name in E.NAMES}
    result = E.refresh(entry, environment=environment)
    assert result['state'] == 'user_environment_unavailable'
    assert not any(result['credentials'].values())
    assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)
    if 'FXDASH_AUDIO' in E.ENTRY_NAMES[entry]:
        assert result['backend'] == environment['FXDASH_AUDIO'] == 'off'


@pytest.mark.parametrize('value', ['', None, 7, 'has space', 'newline\n', 'null\0', '非ASCII', '*' * 20, 'a' * 513])
def test_invalid_saved_key_never_keeps_the_inherited_value(windows, monkeypatch, value):
    monkeypatch.setattr(E, '_read_user_environment', lambda names: {'GEMINI_API_KEY': value})
    environment = {'GEMINI_API_KEY': 'inherited-secret'}
    assert E.refresh('narrative', environment=environment)['credentials']['GEMINI_API_KEY'] is False
    assert 'GEMINI_API_KEY' not in environment


@pytest.mark.parametrize('settings', [
    {'AZURE_SPEECH_KEY': SAVED['AZURE_SPEECH_KEY'], 'AZURE_SPEECH_REGION': 'eastus2'},
    dict(SAVED, FXDASH_AUDIO='off'),
    dict(SAVED, FXDASH_AUDIO='invalid'),
    dict(SAVED, AZURE_SPEECH_KEY=None),
    dict(SAVED, AZURE_SPEECH_REGION=None),
    dict(SAVED, AZURE_SPEECH_REGION='evil.example/path'),
])
def test_opt_out_deleted_selection_or_incomplete_azure_cannot_reuse_old_speech(windows, monkeypatch, settings):
    monkeypatch.setattr(E, '_read_user_environment', lambda names: settings)
    environment = dict(SAVED)
    result = E.refresh('briefing', environment=environment)
    assert result['backend'] == environment['FXDASH_AUDIO'] == 'off'
    assert 'AZURE_SPEECH_KEY' not in environment and 'AZURE_SPEECH_REGION' not in environment


def test_windows_speech_selection_removes_all_cloud_speech_credentials(windows, monkeypatch):
    monkeypatch.setattr(E, '_read_user_environment', lambda names: dict(SAVED, FXDASH_AUDIO='windows'))
    environment = dict(SAVED)
    assert E.refresh('catchup', environment=environment)['backend'] == 'windows'
    assert not any(name in environment for name in E.SPEECH_NAMES[1:])


@pytest.mark.parametrize('entry', E.ENTRY_NAMES)
def test_non_windows_worker_never_reads_registry_or_changes_explicit_environment(monkeypatch, entry):
    monkeypatch.setattr(E.sys, 'platform', 'linux')
    monkeypatch.setattr(E, '_read_user_environment', lambda names: pytest.fail('cloud read HKCU'))
    environment = dict(SAVED)
    assert E.refresh(entry, environment=environment) == {'state': 'unsupported_host'}
    assert environment == SAVED


def test_registry_queries_exact_hkcu_whitelist_and_rejects_expandable_or_nonstring(windows, monkeypatch):
    calls = []
    class Handle:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def open_key(*args):
        calls.append(args)
        return Handle()
    def query(handle, name):
        calls.append(name)
        return {'GEMINI_API_KEY': ('synthetic-key', 1), 'BREVO_API_KEY': ('%UNRELATED_SECRET%', 2),
                'FXDASH_AUDIO': ('off', 1), 'AZURE_SPEECH_KEY': (123, 1),
                'AZURE_SPEECH_REGION': ('eastus2', 1)}[name]
    registry = SimpleNamespace(OpenKey=open_key, QueryValueEx=query, HKEY_CURRENT_USER=10, KEY_READ=20, REG_SZ=1)
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    names = E.ENTRY_NAMES['briefing']
    values = read_registry(names)
    assert calls == [(10, 'Environment', 0, 20), *names]
    assert values['GEMINI_API_KEY'] == 'synthetic-key'
    assert values['BREVO_API_KEY'] is None and values['AZURE_SPEECH_KEY'] is None


def test_missing_user_environment_key_returns_absent_settings(windows, monkeypatch):
    def missing(*args): raise FileNotFoundError()
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=missing, HKEY_CURRENT_USER=10, KEY_READ=20))
    assert read_registry(E.ENTRY_NAMES['live']) == {}


def test_registry_scope_rejects_unrelated_settings_before_opening_hkcu(windows, monkeypatch):
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=lambda *args: pytest.fail('registry opened')))
    with pytest.raises(ValueError, match='invalid_scheduled_environment_scope'):
        read_registry(('PATH',))


@pytest.mark.parametrize('source', ['scheduled_task', 'manual'])
def test_live_supervisor_refreshes_only_scheduled_child_and_never_saves_secrets(windows, monkeypatch, isolated_outputs, source):
    for name in E.ENTRY_NAMES['live']:
        monkeypatch.setenv(name, 'synthetic-inherited-key')
    calls = []
    def saved(names):
        assert source == 'scheduled_task'
        calls.append('registry')
        return SAVED
    monkeypatch.setattr(E, '_read_user_environment', saved)
    def worker(command, **kwargs):
        calls.append('worker')
        for name in E.ENTRY_NAMES['live']:
            assert kwargs['env'][name] == (SAVED[name] if source == 'scheduled_task' else 'synthetic-inherited-key')
            assert os.environ[name] == 'synthetic-inherited-key'
        return SimpleNamespace(returncode=1)
    row = T.supervise(isolated_outputs.parent, isolated_outputs, runner=worker, source=source, clock=lambda: NOW)
    assert calls == (['registry', 'worker'] if source == 'scheduled_task' else ['worker'])
    if source == 'scheduled_task':
        assert row['environment_refresh']['state'] == 'refreshed'
    else:
        assert 'environment_refresh' not in row
    saved = '\n'.join(path.read_text() for path in isolated_outputs.rglob('*.json'))
    assert 'synthetic-new' not in saved and 'synthetic-inherited-key' not in saved


@pytest.mark.parametrize('scheduled', [False, True])
def test_narrative_cli_refreshes_only_with_scheduled_flag_before_work(windows, monkeypatch, scheduled, capsys):
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-interactive-key')
    calls = []
    def saved(names):
        assert scheduled and names == E.ENTRY_NAMES['narrative']
        calls.append('registry')
        return SAVED
    monkeypatch.setattr(E, '_read_user_environment', saved)
    def run(**kwargs):
        calls.append('work')
        assert kwargs['dry_run'] is True
        assert os.environ['GEMINI_API_KEY'] == (SAVED['GEMINI_API_KEY'] if scheduled else 'synthetic-interactive-key')
        return {'date': '2026-10-09', 'dry_run': True, 'trigger': {}, 'fact_tables': {}, 'queries': {}}
    monkeypatch.setattr(narrative_run, 'run', run)
    assert narrative_run.main(['--dry-run', *(['--scheduled-task'] if scheduled else [])]) == 0
    assert calls == (['registry', 'work'] if scheduled else ['work'])
    captured = capsys.readouterr()
    assert 'synthetic' not in captured.out + captured.err


@pytest.mark.parametrize('kind', ['briefing', 'catchup'])
def test_scheduled_wrapper_rotates_model_email_and_speech_before_clock_gate(windows, monkeypatch, tmp_path, kind):
    calls = []
    for name in E.ENTRY_NAMES[kind]:
        monkeypatch.setenv(name, 'synthetic-old-key')
    def saved(names):
        calls.append('registry')
        assert names == E.ENTRY_NAMES[kind]
        return SAVED
    monkeypatch.setattr(E, '_read_user_environment', saved)
    source = Path(__file__).resolve().parents[1] / 'ops' / ('run_' + kind + '_task.py')
    spec = importlib.util.spec_from_file_location('isolated_' + kind, source)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(entry.R, 'runtime_status', lambda repo: {'state': 'ready'})
    monkeypatch.setattr(entry.R, 'bootstrap_tools', lambda: {})
    def gate(*args):
        calls.append('gate')
        assert all(os.environ[name] == SAVED[name] for name in E.ENTRY_NAMES[kind])
        return False if kind == 'catchup' else 'idle'
    if kind == 'catchup':
        monkeypatch.setattr(catchup, 'due', gate)
        monkeypatch.setattr(catchup, 'main', lambda *args: pytest.fail('actual catchup'))
    else:
        monkeypatch.setattr(morning, 'slot', gate)
        monkeypatch.setattr(automation, 'before', lambda *args: {'state': 'idle', 'proceed': True})
        monkeypatch.setattr(morning_dispatch, 'main', lambda *args: 0)
    monkeypatch.setattr(automation, 'after', lambda *args: pytest.fail('actual delivery'))
    assert entry.main(tmp_path) == 0
    assert calls == ['registry', 'gate']
    log = (tmp_path / 'outputs/logs' / (kind + '.log')).read_text()
    assert 'scheduled_environment refreshed' in log and 'synthetic' not in log
