"""Read-only delivery observations from sanitized probes and saved receipts.

No host, credential store or provider is queried here. An old observation never
establishes current readiness, and provider submissions are not inbox receipts.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import math
from pathlib import Path
import re

from ..config import HEARTBEAT_WARN_HOURS
from . import morning as M

TASKS = ("fxdash-live", "fxdash-narrative", "fxdash-briefing", "fxdash-catchup", "fxdash-publish")
CREDENTIALS = ("BREVO_API_KEY", "GEMINI_API_KEY", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "FXDASH_AUDIO",
               "FRED_API_KEY", "BANXICO_TOKEN")
LANGUAGES = ("en", "zh")
HISTORY_LIMIT = 20
HASH = re.compile(r"[0-9a-f]{64}\Z")
RUNTIME_STATES = {"ready", "interpreter_missing", "missing_dependencies", "not_persistent",
                  "probe_failed", "not_configured", "not_ready", "unreadable"}
EMAIL_STATES = {"creating", "submitting", "submitted", "review_required"}
PROVIDER_STATUSES = {"draft", "sent", "archive", "queued", "suspended", "inProcess", "inReview",
                     "queuedForSmtp", "queuedForTrigger"}
PROVIDER_CODES = {"unauthorized", "permission_denied", "invalid_parameter", "missing_parameter",
                  "document_not_found", "method_not_allowed", "not_enough_credits", "duplicate_parameter",
                  "out_of_range", "duplicate_request", "account_under_validation"}
STAT_FIELDS = ("delivered", "sent", "processed", "requests", "hardBounces", "softBounces", "complaints")
TASK_OUTCOMES = {"failed": ("error", 1), "attention_required": ("attention", 2),
                 "submitted_pending": ("pending", 0), "pending": ("pending", 0),
                 "disabled": ("info", 0), "confirmed": ("info", 0), "idle": ("info", 0),
                 "not_observed": ("attention", 2)}


def _stamp(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _bool(value):
    return value if type(value) is bool else None


def _enum(value, allowed, default=None):
    return value if isinstance(value, str) and value in allowed else default


def _number(value):
    try:
        return value if type(value) in {int, float} and math.isfinite(value) else None
    except OverflowError:
        return None


def _safe_path(root, path):
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return False
        for parent in (path, *path.parents):
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                return False
            if parent == root:
                return True
    except OSError:
        pass
    return False


def _read(root, path):
    if not _safe_path(root, path):
        return "unreadable", {}
    try:
        if not path.exists():
            return "missing", {}
        if path.stat().st_size > 2_000_000:
            return "unreadable", {}
        value = json.loads(path.read_text(encoding="utf-8-sig"),
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return ("readable", value) if isinstance(value, dict) else ("unreadable", {})
    except (OSError, ValueError, TypeError, RecursionError):
        return "unreadable", {}


def _observation(stamp, now):
    parsed = _stamp(stamp)
    result = {"observed_at": parsed.isoformat() if parsed else None, "age_hours": None,
              "freshness": "unreadable"}
    if parsed is None or now is None:
        return result
    age = (now - parsed).total_seconds() / 3600
    result.update(age_hours=round(age, 4), freshness="future" if age < 0 else
                  "stale" if age > HEARTBEAT_WARN_HOURS else "current")
    return result


def _folders(root, area):
    parent = root / area
    if not _safe_path(root, parent) or not parent.is_dir():
        return []
    try:
        return sorted((p for p in parent.iterdir() if _day(p.name) and p.is_dir()
                       and _safe_path(root, p)), reverse=True)
    except OSError:
        return []


def _runtime(root, now):
    read_state, raw = _read(root, root / "automation/runtime-readiness.json")
    observation = _observation(raw.get("observed_at"), now)
    probe_stamp = _stamp(raw.get("observed_at"))
    state = "not_observed" if read_state == "missing" else observation["freshness"]
    if read_state == "unreadable" or type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        state = "unreadable" if read_state != "missing" else "not_observed"
    runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {}
    runtime_state = _enum(runtime.get("state"), RUNTIME_STATES, "unreadable")
    persistent, dependencies = _bool(runtime.get("persistent")), _bool(runtime.get("dependencies_ready"))
    if runtime_state == "ready" and (persistent is not True or dependencies is not True):
        runtime_state = "unreadable"
    if read_state == "missing":
        runtime_state = "not_observed"
    credentials = raw.get("credentials") if isinstance(raw.get("credentials"), dict) else {}
    credential_view = {}
    for name in CREDENTIALS:
        saved = _bool(credentials.get(name))
        configured = saved if state == "current" else None
        credential_view[name] = {"configured": configured, "last_observed_configured": saved,
                                 "state": "configured_unverified" if configured is True else
                                 "not_configured" if configured is False else "not_observed"}
    tasks = raw.get("tasks") if isinstance(raw.get("tasks"), dict) else {}
    task_view = {}
    for name in TASKS:
        task = tasks.get(name) if isinstance(tasks.get(name), dict) else {}
        registered, enabled = _bool(task.get("registered")), _bool(task.get("enabled"))
        last, next_run = _stamp(task.get("last_run_at")), _stamp(task.get("next_run_at"))
        invalid_time = any(task.get(k) is not None and _stamp(task.get(k)) is None
                           for k in ("last_run_at", "next_run_at"))
        invalid_time |= last is not None and (now is None or last > now or probe_stamp is None or last > probe_stamp)
        result = task.get("last_result")
        result = result if type(result) is int and 0 <= result <= 0xFFFFFFFF else None
        task_state = ("not_observed" if registered is None else "not_registered" if not registered else
                      "registered_enabled" if enabled is True else "registered_disabled" if enabled is False else "unreadable")
        if invalid_time:
            task_state = "unreadable"
        if state != "current":
            task_state = "not_observed" if state == "not_observed" else state
        task_view[name] = {"state": task_state, "registered": registered if state == "current" else None,
                           "enabled": enabled if state == "current" else None,
                           "last_run_at": last.isoformat() if last else None,
                           "next_run_at": next_run.isoformat() if next_run else None, "last_result": result}
    backend = runtime.get("audio_backend")
    tools = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
    tool_view = {}
    for name in ("git", "ffprobe"):
        tool = tools.get(name) if isinstance(tools.get(name), dict) else {}
        tool_view[name] = {key: _bool(tool.get(key)) if state == "current" else None for key in
                          ("available", "path_in_process", "fixed_executable", "persistent")}
        tool_view[name]["last_observed_available"] = _bool(tool.get("available"))
    return {"state": state, **observation, "stale_after_hours": HEARTBEAT_WARN_HOURS,
            "runtime": {"state": runtime_state if state == "current" else state,
                        "last_observed_state": runtime_state,
                        "persistent": persistent if state == "current" else None,
                        "dependencies_ready": dependencies if state == "current" else None,
                        "audio_backend": _enum(backend, {"azure", "windows", "off", "not_configured"}) if state == "current" else None},
            "credentials": credential_view, "tasks": task_view, "tools": tool_view}


def _task_execution(root, day, now):
    """Read today's completed worker outcomes, separately from runtime readiness."""
    rows = {}
    for kind in ("briefing", "catchup"):
        row = {"kind": kind, "state": "not_observed", "observed_at": None,
               "freshness": "missing", "dispatch_exit_code": None, "exit_code": None,
               "delivery_state": None}
        rows["fxdash-" + kind] = row
        if day is None:
            row.update(state="unreadable", freshness="unreadable")
            continue
        read_state, raw = _read(root, root / "automation" / day / "task-status" / (kind + ".json"))
        if read_state == "missing":
            continue
        row["state"] = "unreadable"
        timing = _observation(raw.get("observed_at"), now)
        row.update(observed_at=timing["observed_at"], freshness=timing["freshness"])
        stamp = _stamp(raw.get("observed_at"))
        delivery = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
        outcome = TASK_OUTCOMES.get(_enum(delivery.get("state"), TASK_OUTCOMES))
        dispatch_code, exit_code = raw.get("dispatch_exit_code"), raw.get("exit_code")
        if (read_state != "readable" or type(raw.get("schema_version")) is not int
                or raw.get("schema_version") != 1 or raw.get("date") != day or raw.get("kind") != kind
                or stamp is None or now is None or stamp > now or M.local_time(stamp).date().isoformat() != day
                or type(dispatch_code) is not int or not -0x80000000 <= dispatch_code <= 0xFFFFFFFF
                or type(exit_code) is not int or not -0x80000000 <= exit_code <= 0xFFFFFFFF or outcome is None
                or delivery.get("severity") != outcome[0] or type(delivery.get("exit_code")) is not int
                or delivery.get("exit_code") != outcome[1]):
            continue
        expected_state = "dispatch_failed" if dispatch_code else delivery["state"]
        if raw.get("state") != expected_state or exit_code != (dispatch_code or outcome[1]):
            continue
        row.update(state=expected_state, dispatch_exit_code=dispatch_code, exit_code=exit_code,
                   delivery_state=delivery["state"])
    return {"date": day, "timezone": M.ZONE, "tasks": rows}


def _configuration(root):
    read_state, raw = _read(root, root / "subscriptions/config.json")
    policy = raw.get("delivery_policy", "require_audio")
    policy_valid = _enum(policy, {"require_audio", "allow_text"}) is not None
    if not policy_valid:
        policy = "require_audio"
    enabled = _bool(raw.get("enabled")) if read_state == "readable" else False if read_state == "missing" else None
    try:
        from .subscriptions import validate_settings
        valid = read_state == "readable" and policy_valid and validate_settings(raw) is not None
    except (ValueError, TypeError, AttributeError):
        valid = False
    state = ("unreadable" if read_state == "unreadable" or enabled is None else
             "disabled" if not enabled else "enabled" if valid else "configuration_required")
    return {"config_enabled": enabled, "configuration_valid": valid, "configuration_state": state,
            "policy": policy, "delivery_policy": policy}


def _editions(root, now):
    from . import briefing_archive as A, audio_briefing as B, public_delivery as P
    records, identities = [], {}
    for area, mode in (("days", "edition"), ("catchup", "catchup")):
        for folder in _folders(root, "briefing/" + area):
            path = folder / "edition.json"
            if not path.exists():
                continue
            read_state, raw = _read(root, path)
            try:
                edition = A.read_edition(path, mode=mode) if read_state == "readable" else {}
            except (ValueError, TypeError, AttributeError, KeyError, OSError):
                edition = {}
            identity = edition.get("edition_hash")
            generated = _observation(edition.get("generated_at"), now)
            as_of = _day(edition.get("attribution_as_of"))
            valid = (_enum(edition.get("state"), {"ready", "numbers_only", "inputs_unavailable"}) is not None
                     and isinstance(identity, str) and HASH.fullmatch(identity)
                     and (as_of is not None or edition.get("state") == "inputs_unavailable")
                     and (as_of is None or as_of <= folder.name)
                     and now is not None and folder.name <= M.local_time(now).date().isoformat()
                     and generated["freshness"] not in {"future", "unreadable"})
            if valid and edition.get("state") in {"ready", "numbers_only"}:
                identities.setdefault(folder.name, set()).add(identity)
            record = {"date": folder.name, "mode": mode,
                      "state": edition["state"] if valid else "unreadable", "as_of": as_of,
                      "generated_at": generated["observed_at"], "age_hours": generated["age_hours"],
                      "freshness": generated["freshness"],
                      "audio": {lang: {"state": "not_observed"} for lang in LANGUAGES},
                      "public": {"state": "not_observed", "observed_at": None, "freshness": "missing"}}
            records.append((record, edition if valid else None))
    records.sort(key=lambda item: (item[0]["date"], item[0]["mode"] == "edition"), reverse=True)
    if not records:
        return {"edition_count": 0, "latest": None}, identities
    latest, edition = records[0]
    if edition:
        audio = B.inspect(root, edition)
        for lang in LANGUAGES:
            item = audio.get("languages", {}).get(lang, {})
            audio_time = _observation(item.get("generated_at"), now)
            state = _enum(item.get("state"), {"ready", "not_generated", "failed", "generating", "integrity_failed"}, "unreadable")
            if state == "ready" and audio_time["freshness"] in {"future", "unreadable"}:
                state = "unreadable"
            duration = _number(item.get("duration_seconds"))
            latest["audio"][lang] = {"state": state, "version": _enum(audio.get("script_version"),
                                     {"audio-v1", "audio-v2", "audio-v3", "audio-v4", "audio-v5"}),
                                     "generated_at": audio_time["observed_at"], "freshness": audio_time["freshness"],
                                     "duration_seconds": duration if duration is not None and duration > 0 else None}
        public_path = root / "delivery" / latest["mode"] / latest["date"] / edition["edition_hash"][:20] / "public.json"
        public_read, saved = _read(root, public_path)
        public_time = _observation(saved.get("observed_at"), now)
        public_state = "not_observed" if public_read == "missing" else "unreadable"
        if public_read == "readable":
            try:
                match = (saved.get("date") == latest["date"] and saved.get("mode") == latest["mode"]
                         and saved.get("site") == P.SITE
                         and saved.get("edition_hash") == edition["edition_hash"]
                         and saved.get("expectation_hash") == M.digest(P.expectation(root, edition)))
            except (ValueError, TypeError, AttributeError, KeyError, OSError):
                match = False
            if match and public_time["freshness"] not in {"future", "unreadable"}:
                public_state = _enum(saved.get("state"), {"verified", "text_verified", "pending", "unavailable", "mismatch", "checking"}, "unreadable")
                if public_state in {"verified", "text_verified", "pending"}:
                    verified_at = _stamp(saved.get("observed_at"))
                    audio_verified = saved.get("audio_verified")
                    text_verified = _bool(saved.get("text_verified"))
                    consistent = (text_verified is not None and verified_at >= _stamp(edition.get("generated_at"))
                                  and isinstance(audio_verified, list)
                                  and all(lang in LANGUAGES for lang in audio_verified)
                                  and len(set(audio_verified)) == len(audio_verified)
                                  and (text_verified is True or not audio_verified)
                                  and (public_state == "pending" or text_verified is True)
                                  and (public_state != "verified" or set(audio_verified) == set(LANGUAGES))
                                  and (public_state != "text_verified" or len(audio_verified) < 2)
                                  and all(latest["audio"][lang]["state"] == "ready"
                                          and _stamp(latest["audio"][lang].get("generated_at")) <= verified_at
                                          for lang in audio_verified))
                    if not consistent:
                        public_state = "unreadable"
        latest["public"] = {"state": public_state, **public_time,
                            "text_verified": saved.get("text_verified") is True if public_state in {"verified", "text_verified", "pending"} else None,
                            "audio_verified": {lang: lang in saved.get("audio_verified", []) if
                                               public_state in {"verified", "text_verified", "pending"} and isinstance(saved.get("audio_verified"), list) else None
                                               for lang in LANGUAGES}}
    return {"edition_count": len(records), "latest": latest}, identities


def _confirmation(root, day, lang, receipt, now):
    read_state, saved = _read(root, root / "subscriptions/provider-observations" / day / (lang + ".json"))
    result = {"state": "not_observed", "observed_at": None, "freshness": "missing", "provider_status": None, "stats": {}}
    if read_state == "missing":
        return result
    observation = saved.get("provider_observation") if isinstance(saved.get("provider_observation"), dict) else {}
    timing = _observation(observation.get("observed_at"), now)
    result.update(timing, state="unreadable")
    match = (read_state == "readable" and type(saved.get("schema_version")) is int and saved.get("schema_version") == 1 and saved.get("date") == day
             and saved.get("language") == lang and saved.get("edition_hash") == receipt.get("edition_hash")
             and saved.get("payload_hash") == receipt.get("payload_hash")
             and type(receipt.get("campaign_id")) is int and receipt["campaign_id"] > 0
             and type(saved.get("campaign_id")) is int and saved["campaign_id"] > 0
             and saved.get("campaign_id") == receipt["campaign_id"])
    started = _stamp(receipt.get("started_at"))
    observed = _stamp(observation.get("observed_at"))
    if not match or timing["freshness"] in {"future", "unreadable"} or started is None or observed < started:
        return result
    status = observation.get("provider_status")
    if (_enum(status, PROVIDER_STATUSES) is None or _enum(observation.get("state"),
            {"provider_confirmed", "provider_sent", "not_confirmed"}) is None):
        return result
    stats = observation.get("stats") if isinstance(observation.get("stats"), dict) else {}
    safe_stats = {key: value for key in STAT_FIELDS if type(value := stats.get(key)) is int and 0 <= value <= 1_000_000_000}
    delivered = safe_stats.get("delivered", 0)
    confirmed = delivered > 0
    result.update(provider_status=status, stats=safe_stats,
                  state="provider_confirmed" if confirmed else "provider_sent" if status == "sent" else "not_confirmed")
    return result


def _email_row(root, day, lang, now, identities):
    read_state, raw = _read(root, root / "subscriptions/deliveries" / day / (lang + ".json"))
    result = {"state": "not_recorded" if read_state == "missing" else "unreadable", "started_at": None,
              "finished_at": None, "freshness": "missing", "content_mode": None, "error": None,
              "delivery_policy": None,
              "confirmation": {"state": "not_observed", "observed_at": None, "freshness": "missing", "stats": {}}}
    if read_state != "readable":
        return result
    started, finished = _stamp(raw.get("started_at")), _stamp(raw.get("finished_at"))
    stamp = raw.get("finished_at") if finished else raw.get("started_at")
    timing = _observation(stamp, now)
    result.update(started_at=started.isoformat() if started else None, finished_at=finished.isoformat() if finished else None,
                  freshness=timing["freshness"])
    identity = raw.get("edition_hash")
    valid = (raw.get("date") == day and raw.get("language") == lang and _enum(raw.get("state"), EMAIL_STATES) is not None
             and isinstance(identity, str) and identity in identities.get(day, set()) and isinstance(raw.get("payload_hash"), str)
             and HASH.fullmatch(raw["payload_hash"]) and started is not None
             and M.local_time(started).date().isoformat() == day
             and timing["freshness"] not in {"future", "unreadable"}
             and (finished is None or finished >= started)
             and (raw["state"] not in {"submitted", "review_required"} or finished is not None))
    if not valid:
        return result
    result["state"] = raw["state"]
    result["content_mode"] = _enum(raw.get("content_mode"), {"audio", "text_only"})
    result["delivery_policy"] = _enum(raw.get("delivery_policy", "require_audio"), {"require_audio", "allow_text"})
    error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    if error:
        status = error.get("http_status")
        result["error"] = {"stage": _enum(error.get("stage"), {"create", "send", "reconcile"}),
                           "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                           "provider_code": _enum(error.get("provider_code"), PROVIDER_CODES),
                           "uncertain_outcome": _bool(error.get("uncertain_outcome"))}
    result["confirmation"] = _confirmation(root, day, lang, raw, now)
    return result


def snapshot(output_dir, *, clock=M.now_utc):
    """Project saved observations without reading host settings or changing files."""
    root = Path(output_dir).absolute()
    try:
        observed = clock()
        now = observed.astimezone(timezone.utc) if isinstance(observed, datetime) and observed.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        now = None
    runtime = _runtime(root, now)
    config = _configuration(root)
    briefing, identities = _editions(root, now)
    local = M.local_time(now) if now is not None else None
    day = local.date().isoformat() if local else None
    phase = ("unreadable" if local is None else "weekend" if local.weekday() >= 5 else
             "before_delivery" if local.time() < time(9) else "delivery_due")
    folders = _folders(root, "subscriptions/deliveries")[:HISTORY_LIMIT]
    history = [{"date": p.name, "languages": {lang: _email_row(root, p.name, lang, now, identities)
                                               for lang in LANGUAGES}} for p in folders]
    today_languages = {lang: _email_row(root, day, lang, now, identities) if day else {"state": "unreadable"}
                       for lang in LANGUAGES}
    states = {item["state"] for item in today_languages.values()}
    confirmed = all(item.get("confirmation", {}).get("state") == "provider_confirmed" for item in today_languages.values())
    today_state = ("unreadable" if now is None else "attention_required" if states & {"unreadable", "review_required"} else
                   "provider_confirmed" if confirmed else "submitted" if states == {"submitted"} else
                   "in_progress" if states & {"creating", "submitting"} else "not_recorded")
    counts = {state: sum(item["state"] == state for row in history for item in row["languages"].values())
              for state in ("submitted", "review_required", "unreadable")}
    counts["confirmed"] = sum(item.get("confirmation", {}).get("state") == "provider_confirmed"
                               for row in history for item in row["languages"].values())
    email = {**config, "credential_configured": runtime["credentials"]["BREVO_API_KEY"]["configured"],
             "account_validation": "not_verified", "scope": "provider_submission_not_inbox_receipt",
             "confirmation_scope": "provider_reported_delivery_not_inbox", "history": history,
             "history_limit": HISTORY_LIMIT, "counts": counts,
             "today": {"date": day, "timezone": M.ZONE, "phase": phase, "due": phase == "delivery_due",
                       "state": today_state, "languages": today_languages}}
    return {"schema_version": 1, "observed_at": now.isoformat() if now else None,
            "scope": "saved_artifacts_only", "runtime_observation": runtime,
            "task_execution": _task_execution(root, day, now), "email": email, "briefing": briefing}
