"""Sanitized read-only host probes with synthetic registry/task/tool results."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import check_delivery_runtime as R


NOW = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)


def test_credentials_emit_presence_only_and_explain_an_older_process_environment():
    private = "synthetic-secret-must-not-appear"
    flags, sources, backend = R.credential_status(
        saved={"GEMINI_API_KEY": private, "FXDASH_AUDIO": "azure"}, user_readable=True,
        environment={"GEMINI_API_KEY": "older-secret"},
    )
    assert flags["GEMINI_API_KEY"] is True and flags["BREVO_API_KEY"] is False
    assert sources["GEMINI_API_KEY"]["fresh_shell_required"] is True
    assert backend == "azure"
    assert private not in json.dumps([flags, sources, backend])
    assert "older-secret" not in json.dumps([flags, sources, backend])


def test_registry_access_denied_is_unknown_not_absent():
    flags, sources, _ = R.credential_status(saved={}, user_readable=None, environment={})
    assert all(value is None for value in flags.values())
    assert all(value["user_present"] is None for value in sources.values())


def test_deleted_user_key_cannot_be_replaced_by_a_stale_scheduler_environment():
    flags, sources, backend = R.credential_status(
        saved={}, user_readable=True,
        environment={"GEMINI_API_KEY": "stale-secret", "FXDASH_AUDIO": "azure",
                     "AZURE_SPEECH_KEY": "stale-speech-secret"},
    )
    assert flags["GEMINI_API_KEY"] is False
    assert flags["AZURE_SPEECH_KEY"] is False
    assert sources["GEMINI_API_KEY"]["process_present"] is True
    assert sources["GEMINI_API_KEY"]["fresh_shell_required"] is True
    assert backend == "off"


def test_unreadable_user_settings_cannot_borrow_process_keys_or_enable_speech():
    flags, sources, backend = R.credential_status(
        saved={}, user_readable=None,
        environment={"GEMINI_API_KEY": "stale-secret", "FXDASH_AUDIO": "azure"},
    )
    assert flags["GEMINI_API_KEY"] is None
    assert sources["GEMINI_API_KEY"]["process_present"] is True
    assert backend == "off"


def test_non_windows_readiness_uses_the_invocations_process_settings():
    flags, sources, backend = R.credential_status(
        saved={}, user_readable=False,
        environment={"GEMINI_API_KEY": "explicit-cloud-secret", "FXDASH_AUDIO": "azure"},
    )
    assert flags["GEMINI_API_KEY"] is True
    assert flags["FRED_API_KEY"] is False
    assert sources["GEMINI_API_KEY"]["fresh_shell_required"] is False
    assert backend == "azure"


def test_invalid_audio_backend_is_not_echoed():
    _, _, backend = R.credential_status(saved={"FXDASH_AUDIO": "private arbitrary value"},
                                       user_readable=True, environment={})
    assert backend == "not_configured"


def test_fixed_tools_repair_only_child_path_and_do_not_duplicate(tmp_path, monkeypatch):
    git = tmp_path / "Programs/Git/cmd/git.exe"
    probe = tmp_path / "Microsoft/WinGet/Links/ffprobe.exe"
    for executable in (git, probe):
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"synthetic executable, never run")
    environment = {"LOCALAPPDATA": str(tmp_path), "PATH": "/unchanged/bin",
                   "GEMINI_API_KEY": "do-not-touch", "FXDASH_AUDIO": "off"}
    original = dict(environment)
    monkeypatch.setattr(R.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(R, "tool_executes", lambda *args, **kwargs: True)
    assert R.bootstrap_tools(environment) == {"git_path_added": True, "ffprobe_path_added": True}
    first = environment["PATH"]
    assert first.endswith(original["PATH"])
    assert {k: v for k, v in environment.items() if k != "PATH"} == {k: v for k, v in original.items() if k != "PATH"}
    assert R.bootstrap_tools(environment) == {"git_path_added": False, "ffprobe_path_added": False}
    assert environment["PATH"] == first


def test_ffprobe_discovery_uses_only_known_package_and_latest_working_version(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("tools")
    package = tmp_path / "Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe"
    candidates = []
    for version in ("8.9", "9.0.1", "10.0.0", "invalid"):
        path = package / ("ffmpeg-" + version + "-essentials_build/bin/ffprobe.exe")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic executable, never run")
        candidates.append(path)
    unrelated = tmp_path / "unrelated/bin/ffprobe.exe"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"never search here")
    calls = []

    def run(command, **kwargs):
        calls.append(Path(command[0]))
        return SimpleNamespace(returncode=1 if "10.0.0" in command[0] else 0)

    found = R.fixed_tool_path("ffprobe", str(tmp_path), runner=run)
    assert found == candidates[1]
    assert calls == [candidates[2], candidates[1]]
    assert unrelated not in R.fixed_tool_candidates("ffprobe", str(tmp_path))


def test_existing_child_tool_is_not_replaced_or_reprobed(tmp_path, monkeypatch):
    environment = {"LOCALAPPDATA": str(tmp_path), "PATH": "/existing/bin"}
    monkeypatch.setattr(R.shutil, "which", lambda *args, **kwargs: "/existing/bin/tool")
    monkeypatch.setattr(R, "fixed_tool_path", lambda *args, **kwargs: pytest.fail("existing tool replaced"))
    assert not any(R.bootstrap_tools(environment).values())
    assert environment["PATH"] == "/existing/bin"


def test_missing_fixed_tool_never_adds_an_unverified_directory(tmp_path):
    environment = {"LOCALAPPDATA": str(tmp_path), "PATH": "/old/bin"}
    assert not any(R.bootstrap_tools(environment).values())
    assert environment["PATH"] == "/old/bin"


def test_tool_probe_sanitizes_stdout_stderr_and_detects_failed_execution():
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[-1] == "-version" and kwargs["capture_output"]
        return SimpleNamespace(returncode=1, stdout="private host path", stderr="private details")

    result = R.tool_status("ffprobe", finder=lambda name: "/persistent/ffprobe", runner=run)
    assert result["available"] is False and len(calls) == 1
    assert "private" not in json.dumps(result)


def test_task_probe_has_no_actions_principals_or_launch_commands(monkeypatch):
    monkeypatch.setattr(R.sys, "platform", "win32")

    def run(command, **kwargs):
        script = command[-1]
        assert "Get-ScheduledTaskInfo" in script
        assert "Start-ScheduledTask" not in script and "Register-ScheduledTask" not in script
        assert ".Actions" not in script and ".Principal" not in script
        rows = {name: {"registered": None, "state": "access_denied"} for name in R.TASK_NAMES}
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows))

    rows = R.task_metadata(runner=run)
    assert all(row["registered"] is None and row["state"] == "access_denied" for row in rows.values())


def test_task_probe_failure_is_unknown_not_not_registered(monkeypatch):
    monkeypatch.setattr(R.sys, "platform", "win32")
    rows = R.task_metadata(runner=lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert all(row["registered"] is None for row in rows.values())


@pytest.fixture
def readonly_probe(monkeypatch):
    monkeypatch.setattr(R, "runtime_status", lambda *args: {"state": "ready", "persistent": True, "dependencies_ready": True})
    monkeypatch.setattr(R, "credential_status", lambda: ({}, {}, "off"))
    monkeypatch.setattr(R, "tool_status", lambda name: {"available": False, "fixed_executable": False})


def test_absent_task_and_future_next_run_are_distinguished(readonly_probe):
    tasks = {"fxdash-live": {"registered": False, "enabled": True, "last_result": 0, "state": "not_registered"},
             "fxdash-briefing": {"registered": True, "enabled": True, "state": "observed",
                                 "last_run_at": (NOW - timedelta(hours=1)).isoformat(),
                                 "next_run_at": (NOW + timedelta(hours=1)).isoformat(), "last_result": 0,
                                 "Actions": "must not be saved", "identity": "must not be saved"}}
    result = R.collect_readiness(clock=lambda: NOW, tasks=tasks)
    absent = result["tasks"]["fxdash-live"]
    assert absent["registered"] is False and absent["enabled"] is None and absent["last_result"] is None
    ready = result["tasks"]["fxdash-briefing"]
    assert ready["last_result"] == 0 and ready["next_run_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert "must not be saved" not in json.dumps(result)


@pytest.mark.parametrize("last_run", [(NOW + timedelta(seconds=1)).isoformat(), "2026-10-05T12:00:00", "bad"])
def test_future_or_invalid_last_run_cannot_be_reported_as_current_success(readonly_probe, last_run):
    result = R.collect_readiness(clock=lambda: NOW, tasks={"fxdash-live": {
        "registered": True, "enabled": True, "state": "observed", "last_run_at": last_run, "last_result": 0,
    }})
    task = result["tasks"]["fxdash-live"]
    assert task["state"] == "unreadable" and task["last_run_at"] is None and task["last_result"] is None


@pytest.mark.parametrize("observed", [(NOW + timedelta(seconds=1)).isoformat(), "2026-10-05T12:00:00", None])
def test_probe_save_rejects_future_or_missing_observation(tmp_path, observed):
    path = tmp_path / "runtime-readiness.json"
    with pytest.raises((ValueError, TypeError)):
        R.save_readiness({"schema_version": 1, "observed_at": observed, "runtime": {"state": "ready"}},
                         path, clock=lambda: NOW)
    assert not path.exists()
