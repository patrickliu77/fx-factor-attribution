"""Lightweight clock gate for a five-minute Windows trigger.

No network or model imports outside the NY morning window. A publish receipt is
written only after the site push succeeds. Retrying publication never regenerates
the archived model text or re-fetches the morning's evidence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import morning as M


def publish_site(repo: Path):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(repo / "ops" / "publish.ps1")], cwd=repo, timeout=600, check=False,
    )
    if result.returncode:
        raise RuntimeError("site_publish_failed")


def dispatch(output_dir: Path, repo: Path, *, clock=M.now_utc,
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


def main(argv=None):
    from ..config import OUTPUT_DIR, REPO_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="show the clock gate without writes/network")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"timezone": M.ZONE, "action": M.slot(M.now_utc()),
                          "new_york_time": M.local_time(M.now_utc()).isoformat()}))
        return 0
    result = dispatch(args.output_dir, REPO_ROOT)
    if result["state"] != "idle":
        print(json.dumps(result, ensure_ascii=True))
    return 1 if result["state"] in ("prepare_failed", "publish_failed", "finalize_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
