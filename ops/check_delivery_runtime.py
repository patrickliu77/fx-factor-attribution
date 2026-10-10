"""Read-only runtime, tool and scheduler probes; never execute a delivery task."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
TASK_NAMES = ("fxdash-live", "fxdash-narrative", "fxdash-publish", "fxdash-briefing", "fxdash-catchup")
CREDENTIAL_NAMES = ("FRED_API_KEY", "BANXICO_TOKEN", "GEMINI_API_KEY", "BREVO_API_KEY",
                    "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "FXDASH_AUDIO")
PROCESS_ONLY_NAMES = {"FRED_API_KEY", "BANXICO_TOKEN", "GEMINI_API_KEY"}
RUNTIME_STATES = {"ready", "interpreter_missing", "missing_dependencies", "not_persistent", "probe_failed"}


def utc_now():
    return datetime.now(timezone.utc)


def persistent_path(value, temporary_roots=None):
    roots = temporary_roots or [tempfile.gettempdir(), os.environ.get("TEMP"), os.environ.get("TMP")]
    path = Path(value).resolve()
    return not any(path.is_relative_to(Path(root).resolve()) for root in roots if root)


def requirements(repo=REPO):
    pins = {}
    for line in (Path(repo) / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if not match:
            raise ValueError("requirements_must_remain_exact_pins")
        pins[match[1]] = match[2]
    if not pins:
        raise ValueError("requirements_missing")
    return pins


def runtime_status(repo=REPO, *, check_dependencies=True):
    persistent = persistent_path(sys.executable) and persistent_path(sys.base_prefix)
    value = {"state": "ready", "persistent": persistent,
             "dependencies_ready": None, "python_version": ".".join(map(str, sys.version_info[:3]))}
    if not persistent:
        value["state"] = "not_persistent"
    if sys.version_info[:2] != (3, 13):
        value["state"] = "probe_failed"
    if not check_dependencies:
        return value
    try:
        pins = requirements(repo)
        versions = {}
        for name, expected in pins.items():
            try:
                actual = metadata.version(name)
            except metadata.PackageNotFoundError:
                actual = None
            versions[name] = {"expected": expected, "installed": actual, "matches": actual == expected}
        ready = all(item["matches"] for item in versions.values())
        if ready:
            for name in ("pandas", "numpy", "pyarrow", "fastapi", "uvicorn"):
                importlib.import_module(name)
            ZoneInfo("America/New_York")
        value.update(dependencies_ready=ready, dependencies=versions)
        if value["state"] == "ready" and not ready:
            value["state"] = "missing_dependencies"
    except Exception:
        value.update(state="probe_failed", dependencies_ready=False)
    return value


def user_environment():
    """Read only named settings, retaining values in memory until sanitized."""
    if sys.platform != "win32":
        return {}, False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            values = {}
            for name in CREDENTIAL_NAMES:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                    values[name] = value if kind == winreg.REG_SZ and isinstance(value, str) else ""
                except FileNotFoundError:
                    pass
        return values, True
    except FileNotFoundError:
        return {}, True
    except OSError:
        return {}, None


def credential_status(*, saved=None, user_readable=None, environment=None):
    if saved is None:
        saved, user_readable = user_environment()
    environment = os.environ if environment is None else environment
    flags, sources = {}, {}
    for name in CREDENTIAL_NAMES:
        process = bool(environment.get(name, "").strip())
        user = bool(saved.get(name, "").strip()) if user_readable is not None else None
        # Scheduled Windows workers refresh from this user's persisted settings.
        # A stale inherited process value cannot replace a deleted or unreadable
        # registry value. Interactive/cloud invocations still use their process.
        flags[name] = user if user_readable is True else None if user_readable is None else process
        refresh = (name in PROCESS_ONLY_NAMES and user_readable is True
                   and saved.get(name, "") != environment.get(name, ""))
        sources[name] = {"process_present": process, "user_present": user,
                         "fresh_shell_required": refresh}
    if user_readable is True:
        backend = saved.get("FXDASH_AUDIO", "off")
    elif user_readable is None:
        backend = "off"
    else:
        backend = environment.get("FXDASH_AUDIO", "")
    backend = backend.strip().lower()
    backend = backend if backend in {"azure", "windows", "off"} else "not_configured"
    return flags, sources, backend


def fixed_tool_candidates(name, local):
    """Restrict discovery to known user installations, never the full user PATH."""
    if not local:
        return []
    root = Path(local)
    if name == "git":
        paths = [root / "Programs/Git/cmd/git.exe"]
    elif name == "ffprobe":
        paths = [root / "Microsoft/WinGet/Links/ffprobe.exe"]
        package = root / "Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe"
        versions = []
        for path in package.glob("ffmpeg-*-essentials_build/bin/ffprobe.exe"):
            match = re.fullmatch(r"ffmpeg-(\d+(?:\.\d+)*)-essentials_build", path.parents[1].name)
            if match:
                versions.append((tuple(map(int, match[1].split("."))), path))
        paths.extend(path for _, path in sorted(versions, key=lambda item: item[0], reverse=True))
    else:
        return []
    return [path for path in paths if path.is_file()]


def tool_executes(candidate, name, *, runner=None):
    runner = subprocess.run if runner is None else runner
    try:
        process = runner([str(candidate), "--version" if name == "git" else "-version"],
                         capture_output=True, timeout=5, check=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return process.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def fixed_tool_path(name, local, *, runner=None):
    return next((path for path in fixed_tool_candidates(name, local)
                 if tool_executes(path, name, runner=runner)), None)


def bootstrap_tools(environment=None):
    """Make existing fixed user tools available to this scheduled child only."""
    environment = os.environ if environment is None else environment
    local = environment.get("LOCALAPPDATA")
    if not local:
        return {"git_path_added": False, "ffprobe_path_added": False}
    result = {}
    for name in ("git", "ffprobe"):
        old = environment.get("PATH", "")
        entries = {entry.rstrip("/\\").casefold() for entry in old.split(os.pathsep)}
        executable = fixed_tool_path(name, local) if shutil.which(name, path=old) is None else None
        directory = executable.parent if executable else None
        add = directory is not None and str(directory).casefold() not in entries
        if add:
            environment["PATH"] = str(directory) + (os.pathsep + old if old else "")
        result[name + "_path_added"] = add
    return result


def tool_status(name, *, finder=shutil.which, runner=None):
    found = finder(name)
    candidate = found
    fixed = False
    if not candidate:
        candidate = fixed_tool_path(name, os.environ.get("LOCALAPPDATA"), runner=runner)
        fixed = candidate is not None
    result = {"available": False, "path_in_process": bool(found), "fixed_executable": fixed,
              "persistent": persistent_path(candidate) if candidate else None}
    if candidate:
        result["available"] = fixed or tool_executes(candidate, name, runner=runner)
    return result


def task_metadata(*, runner=subprocess.run):
    empty = lambda state: {"registered": None, "enabled": None, "last_run_at": None,
                           "next_run_at": None, "last_result": None, "state": state}
    if sys.platform != "win32":
        return {name: empty("unsupported_host") for name in TASK_NAMES}
    # This script never reads actions, principals, task XML or personal identity.
    script = r"""$ErrorActionPreference='Stop'
$names=@('fxdash-live','fxdash-narrative','fxdash-publish','fxdash-briefing','fxdash-catchup')
function Stamp($value) {
    if (-not $value -or $value.Year -le 1900) { return $null }
    return $value.ToUniversalTime().ToString('o')
}
$rows=@{}
foreach ($name in $names) {
    $row=@{registered=$null;enabled=$null;last_run_at=$null;next_run_at=$null;last_result=$null;state='probe_failed'}
    try {
        $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $row.registered=$true
        $row.enabled=($task.State.ToString() -ne 'Disabled')
        $info=Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
        $row.last_run_at=Stamp $info.LastRunTime
        $row.next_run_at=Stamp $info.NextRunTime
        if ($null -ne $row.last_run_at) { $row.last_result=[long]$info.LastTaskResult }
        $row.state='observed'
    } catch {
        if ($_.CategoryInfo.Category -eq 'ObjectNotFound') {
            $row.registered=$false;$row.enabled=$null;$row.state='not_registered'
        } elseif ($_.CategoryInfo.Category -eq 'PermissionDenied' -or $_.Exception.HResult -eq -2147024891) {
            $row.state='access_denied'
        }
    }
    $rows[$name]=$row
}
$rows | ConvertTo-Json -Depth 4 -Compress
"""
    try:
        response = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=20, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode:
            raise ValueError("scheduler_probe_failed")
        rows = json.loads(response.stdout)
        return {name: rows.get(name, empty("probe_failed")) for name in TASK_NAMES}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {name: empty("probe_failed") for name in TASK_NAMES}


def timestamp(value, *, now=None, future_allowed=False):
    if value is None:
        return None
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or (not future_allowed and moment > (now or utc_now())):
        raise ValueError("untrusted_timestamp")
    return moment.astimezone(timezone.utc).isoformat()


def collect_readiness(repo=REPO, *, clock=utc_now, tasks=None):
    moment = clock()
    if moment.tzinfo is None:
        raise ValueError("readiness_requires_aware_clock")
    flags, sources, backend = credential_status()
    result = {"schema_version": 1, "observed_at": moment.astimezone(timezone.utc).isoformat(),
              "runtime": dict(runtime_status(repo), audio_backend=backend),
              "credentials": flags, "credential_sources": sources,
              "tools": {name: tool_status(name) for name in ("git", "ffprobe")},
              "publish_requires_ffprobe": True,
              "tasks": {}}
    result["fresh_shell_required"] = (any(item["fresh_shell_required"] for item in sources.values())
                                      or any(tool["fixed_executable"] for tool in result["tools"].values()))
    raw_tasks = task_metadata() if tasks is None else tasks
    # A bad or future last run is unknown, never a fresh success observation.
    task_states = {"observed", "not_registered", "access_denied", "probe_failed", "unsupported_host", "unreadable"}
    for name in TASK_NAMES:
        raw = raw_tasks.get(name, {})
        raw = raw if isinstance(raw, dict) else {}
        task = {field: raw.get(field) if type(raw.get(field)) is bool else None
                for field in ("registered", "enabled")}
        state = raw.get("state")
        task.update(last_run_at=None, next_run_at=None, last_result=None,
                    state=state if isinstance(state, str) and state in task_states else "probe_failed")
        try:
            task["last_run_at"] = timestamp(raw.get("last_run_at"), now=moment)
            task["next_run_at"] = timestamp(raw.get("next_run_at"), now=moment, future_allowed=True)
            code = raw.get("last_result")
            if task["last_run_at"] and type(code) is int and 0 <= code < 2**32:
                task["last_result"] = code
        except (ValueError, TypeError, OverflowError):
            task.update(last_run_at=None, next_run_at=None, last_result=None, state="unreadable")
        if task["registered"] is not True:
            task.update(enabled=None, last_run_at=None, next_run_at=None, last_result=None)
        result["tasks"][name] = task
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_readiness(result, path, *, clock=utc_now):
    if result.get("schema_version") != 1 or result.get("runtime", {}).get("state") not in RUNTIME_STATES:
        raise ValueError("invalid_runtime_readiness")
    timestamp(result.get("observed_at"), now=clock())
    if result.get("observed_at") is None:
        raise ValueError("missing_observation_timestamp")
    atomic_json(path, result)


def delivery_outcome(result):
    """Separate a dispatch exit from saved public/email observations, without retrying."""
    state = result.get("state")
    public = (result.get("public") or {}).get("state")
    email = result.get("email") or {}
    mail = email.get("state")
    languages = email.get("languages") or {}
    language_states = {item.get("state") if isinstance(item, dict) else item for item in languages.values()}
    states = {state, public, mail, *language_states}
    if states & {"failed", "delivery_check_failed", "publish_failed", "integrity_failed"}:
        return {"state": "failed", "severity": "error", "exit_code": 1}
    if states & {"review_required", "attention_required", "configuration_required", "unconfirmed"}:
        return {"state": "attention_required", "severity": "attention", "exit_code": 2}
    if mail in {"submitted", "submitted_pending"}:
        return {"state": "submitted_pending", "severity": "pending", "exit_code": 0}
    if state == "idle" or mail == "outside_delivery_day":
        return {"state": "idle", "severity": "info", "exit_code": 0}
    if states & {"pending", "text_verified", "busy", "waiting_for_edition", "waiting_for_push",
                 "waiting_for_public_audio", "public_delivery_unconfirmed", "waiting_for_live_task"}:
        return {"state": "pending", "severity": "pending", "exit_code": 0}
    if mail == "disabled":
        return {"state": "disabled", "severity": "info", "exit_code": 0}
    if mail in {"confirmed", "sent_confirmed"}:
        return {"state": "confirmed", "severity": "info", "exit_code": 0}
    return {"state": "not_observed", "severity": "attention", "exit_code": 2}


def record_task_status(output_dir, kind, dispatch_exit_code, delivery, *, clock=utc_now):
    if kind not in {"briefing", "catchup"} or type(dispatch_exit_code) is not int:
        raise ValueError("invalid_task_status")
    moment = clock()
    if moment.tzinfo is None:
        raise ValueError("task_status_requires_aware_clock")
    outcome = delivery_outcome(delivery)
    exit_code = dispatch_exit_code or outcome["exit_code"]
    state = "dispatch_failed" if dispatch_exit_code else outcome["state"]
    try:
        day = moment.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        day = moment.date().isoformat()
    result = {"schema_version": 1, "observed_at": moment.astimezone(timezone.utc).isoformat(),
              "date": day, "kind": kind, "state": state, "dispatch_exit_code": dispatch_exit_code,
              "delivery": outcome, "exit_code": exit_code}
    atomic_json(Path(output_dir) / "automation" / day / "task-status" / (kind + ".json"), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--assert-ready", action="store_true")
    parser.add_argument("--tool-directories", action="store_true",
                        help="Resolve verified known tool directories for a scheduled action; no settings read.")
    args = parser.parse_args(argv)
    if args.tool_directories:
        paths = [fixed_tool_path(name, os.environ.get("LOCALAPPDATA")) for name in ("git", "ffprobe")]
        print(json.dumps([str(path.parent) for path in paths if path is not None], ensure_ascii=True))
        return 0
    if args.runtime_only or args.base_only:
        result = {"runtime": runtime_status(args.repo, check_dependencies=not args.base_only)}
    else:
        result = collect_readiness(args.repo)
        if not args.no_save:
            destination = (args.output_dir or args.repo / "outputs") / "automation" / "runtime-readiness.json"
            save_readiness(result, destination)
    print(json.dumps(result, ensure_ascii=True))
    return 3 if args.assert_ready and result["runtime"]["state"] != "ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
