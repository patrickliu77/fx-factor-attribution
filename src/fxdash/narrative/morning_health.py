"""Clock observations kept apart from preparation, editions and push receipts.

A late Task Scheduler start cannot recover pre-cutoff evidence. It can leave an
honest local observation without fetching, generating, publishing or creating a
dated edition folder. These observations never count as scheduled delivery.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid

from . import morning as M


def observe_idle(output_dir, moment):
    if M.slot(moment) != "idle":
        raise ValueError("clock_observation_requires_idle_gate")
    local = M.local_time(moment)
    day = local.date().isoformat()
    result = {"schema_version": 1, "source": "scheduled_task", "action": "idle",
              "date": day, "observed_at": moment.isoformat(),
              "target_cutoff": M.cutoff(moment).isoformat(),
              "state": "idle", "phase": "weekend" if local.weekday() >= 5 else "before_window",
              "edition_state": "not_checked", "push_state": "not_checked"}
    if local.weekday() < 5 and local.hour >= 10:
        from .briefing_archive import read_edition, receipt
        root = Path(output_dir) / "briefing" / "days" / day
        path = root / "edition.json"
        edition = read_edition(path) if path.exists() else {}
        push = receipt(root, edition)
        complete = edition.get("state") in {"ready", "numbers_only"} and push["state"] == "published"
        result.update(phase="after_window", state="idle" if complete else "missed_window",
                      edition_state=edition.get("state", "missing"), push_state=push["state"])
    return result


def save_observation(output_dir, observation):
    root = Path(output_dir) / "briefing" / "clock"
    run_id = datetime.fromisoformat(observation["observed_at"]).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
    M.atomic_json(root / observation["date"] / (run_id + ".json"), observation)
    M.atomic_json(root / "latest.json", observation)


def latest_observation(output_dir, *, clock=M.now_utc):
    path = Path(output_dir) / "briefing" / "clock" / "latest.json"
    if not path.exists():
        return {"state": "not_recorded"}
    value = M.read_json(path)
    try:
        stamp = datetime.fromisoformat(value["observed_at"])
        local = M.local_time(stamp)
        valid = (stamp <= clock() and value.get("schema_version") == 1
                 and value.get("source") == "scheduled_task" and value.get("action") == "idle"
                 and value.get("date") == local.date().isoformat() and M.slot(stamp) == "idle"
                 and value.get("state") in {"idle", "missed_window"}
                 and value.get("phase") == ("weekend" if local.weekday() >= 5 else
                                             "after_window" if local.hour >= 10 else "before_window"))
    except (ValueError, TypeError, KeyError):
        valid = False
    return value if valid else {"state": "unreadable"}
