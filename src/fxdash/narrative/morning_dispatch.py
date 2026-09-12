"""Lightweight clock gate for a five-minute Windows trigger.

No network or model imports outside the NY morning window. A publish receipt is
written only after the site push succeeds. Retrying publication never regenerates
the archived model text or re-fetches the morning's evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

from . import morning as M


def publish_site(repo: Path):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(repo / "ops" / "publish.ps1")], cwd=repo, timeout=600, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError("site_publish_failed")


def _dispatch(output_dir: Path, repo: Path, *, clock=M.now_utc,
             prepare_fn=M.prepare, finalize_fn=M.finalize, publisher=publish_site):
    moment = clock()
    action = M.slot(moment)
    if action == "idle":
        return {"state": "idle"}
    day = M.local_time(moment).date().isoformat()
    root = output_dir / "briefing" / "days" / day
    try:
        with M.DayLock(root / "dispatch.lock"):
            if action == "prepare":
                return prepare_fn(output_dir, clock=clock)
            receipt = root / "publish.json"
            previous = M.read_json(receipt)
            attempts = previous.get("attempts", 0)
            attempts = attempts if isinstance(attempts, int) and attempts >= 0 else 0
            started_at = clock().isoformat(timespec="seconds")
            try:
                edition = finalize_fn(output_dir, clock=clock)
            except Exception as exc:
                result = {"state": "finalize_failed", "date": day, "error": type(exc).__name__,
                          "started_at": started_at, "finished_at": clock().isoformat(timespec="seconds"),
                          "attempts": attempts+1}
                M.atomic_json(receipt, result)
                return result
            if previous.get("state") == "published" and previous.get("edition_hash") == M.digest(edition):
                return {"state": "already_published", "date": day}
            M.atomic_json(receipt, {"state": "publishing", "date": day, "started_at": started_at,
                                    "attempts": attempts+1})
            try:
                publisher(repo)
                result = {"state": "published", "date": day,
                          "edition_state": edition["state"],
                          "edition_hash": M.digest(edition),
                          "started_at": started_at, "attempts": attempts+1,
                          "finished_at": clock().isoformat(timespec="seconds")}
            except Exception as exc:
                result = {"state": "publish_failed", "date": day, "error": type(exc).__name__,
                          "started_at": started_at, "finished_at": clock().isoformat(timespec="seconds"),
                          "attempts": attempts+1}
            M.atomic_json(receipt, result)
            return result
    except M.Busy:
        return {"state": "busy", "date": day}


def dispatch(output_dir: Path, repo: Path, *, clock=M.now_utc,
             prepare_fn=M.prepare, finalize_fn=M.finalize, publisher=publish_site,
             invocation_source="manual"):
    """Durable start and completion observations, separate from frozen editions."""
    if invocation_source not in {"manual", "scheduled_task"}:
        raise ValueError("invalid_invocation_source")
    started = clock()
    action = M.slot(started)
    if action == "idle":
        if invocation_source == "scheduled_task":
            from .morning_health import observe_idle, save_observation
            result = observe_idle(output_dir, started)
            save_observation(output_dir, result)
            return result
        return {"state": "idle"}
    day = M.local_time(started).date().isoformat()
    root = Path(output_dir) / "briefing" / "days" / day / "dispatch"
    run_id = started.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
    observation = {"run_id": run_id, "date": day, "action": action,
                   "source": invocation_source, "started_at": started.isoformat(), "state": "started"}
    M.atomic_json(root / (run_id + ".start.json"), observation)
    try:
        result = _dispatch(output_dir, repo, clock=clock, prepare_fn=prepare_fn,
                           finalize_fn=finalize_fn, publisher=publisher)
    except BaseException as exc:
        M.atomic_json(root / (run_id + ".finish.json"), dict(observation, state="exception",
                      error=type(exc).__name__, finished_at=clock().isoformat()))
        raise
    M.atomic_json(root / (run_id + ".finish.json"), dict(observation,
                  state=result["state"], finished_at=clock().isoformat()))
    return result


def main(argv=None):
    from ..config import OUTPUT_DIR, REPO_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="show the clock gate without writes/network")
    parser.add_argument("--scheduled-task", action="store_true", help="invoked by the registered task wrapper")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"timezone": M.ZONE, "action": M.slot(M.now_utc()),
                          "new_york_time": M.local_time(M.now_utc()).isoformat()}))
        return 0
    result = dispatch(args.output_dir, REPO_ROOT,
                      invocation_source="scheduled_task" if args.scheduled_task else "manual")
    if result["state"] != "idle" or args.scheduled_task:
        print(json.dumps(result, ensure_ascii=True))
    # Retain the timetable observation, but an unavailable optional morning slot
    # is not an execution failure under actual-use acceptance.
    return 1 if result["state"] in ("prepare_failed", "publish_failed", "finalize_failed", "ineligible_packet") else 0


if __name__ == "__main__":
    raise SystemExit(main())
