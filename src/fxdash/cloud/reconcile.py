"""Read-only inspection and explicitly authorized abandoned-owner release.

This command never clears claims or retries an external action. A failed job's
pending model, audio, publication and email operations remain blocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re

from . import journal as J
from .briefing import encode
from .runtime import read_head, azure_store
from .state import StateError
from ..narrative import morning as M

REPOSITORY = "patrickliu77/fx-factor-attribution"


def inspect(store):
    row, journal = J.Journal(store)._read()
    _, head = read_head(store)
    active = head.get("active") or {}
    return {"state": "owned" if journal["owner"] else "unowned",
        "journal_sha256": hashlib.sha256(row.body).hexdigest(), "owner": journal["owner"],
        "run_id": active.get("run_id"), "run_attempt": active.get("run_attempt"),
        "date": active.get("day"), "claims": {k: v["state"] for k, v in journal["claims"].items()}}


def completed_run(run_id, attempt):
    """Read one fixed public repository; no token, redirect or provider payload log."""
    import requests
    with requests.Session() as session:
        session.trust_env = False
        url = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}"
        try:
            with session.get(url, timeout=20, allow_redirects=False, stream=True,
                             headers={"Accept": "application/vnd.github+json"}) as response:
                if response.status_code != 200:
                    raise ValueError()
                body = bytearray()
                for chunk in response.iter_content(16384):
                    body.extend(chunk)
                    if len(body) > 256 * 1024:
                        raise ValueError()
                value = json.loads(body, object_pairs_hook=J.unique_object)
            return (value.get("id") == run_id and value.get("run_attempt") == attempt
                and value.get("status") == "completed" and value.get("head_branch") == "main"
                and value.get("repository", {}).get("full_name") == REPOSITORY
                and value.get("path", "").split("@")[0] == ".github/workflows/cloud-production.yml")
        except Exception:
            raise StateError("github_run_not_confirmed") from None


def release(store, *, expected_sha256, owner, run_id, run_attempt, scheduler_stopped=False,
            proof=completed_run, clock=M.now_utc):
    if (scheduler_stopped is not True or not J.valid_hash(expected_sha256)
            or not isinstance(owner, str) or not re.fullmatch(r"[0-9a-f]{32}", owner)
            or type(run_id) is not int or run_id <= 0 or type(run_attempt) is not int or run_attempt <= 0):
        raise StateError("explicit_recovery_review_required")
    row, journal = J.Journal(store)._read()
    _, head = read_head(store)
    active = head.get("active") or {}
    if (hashlib.sha256(row.body).hexdigest() != expected_sha256 or journal["owner"] != owner
            or active.get("owner") != owner or active.get("run_id") != run_id
            or active.get("run_attempt") != run_attempt):
        raise StateError("recovery_target_changed")
    if proof(run_id, run_attempt) is not True:
        raise StateError("old_worker_not_confirmed_stopped")
    # Record intent before the conditional release. An unacknowledged write is
    # investigated using a new inspection; it is never treated as a clean reset.
    key = "recovery/" + expected_sha256
    store.compare_and_swap(key, None, encode({"state": "release_requested", "owner": owner,
        "run_id": run_id, "run_attempt": run_attempt, "observed_at": clock().isoformat(),
        "scheduler_stop_attestation": True, "claims_preserved": True}))
    journal["owner"] = None
    store.compare_and_swap(J.KEY, row.version, J.encode(journal))
    return {"state": "owner_released", "claims_preserved": True, "external_actions_retried": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "release"))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--owner")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int)
    parser.add_argument("--scheduler-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        store = azure_store(local_operator=True)
        result = inspect(store) if args.command == "inspect" else release(store,
            expected_sha256=args.expected_sha256, owner=args.owner, run_id=args.run_id,
            run_attempt=args.run_attempt, scheduler_stopped=args.scheduler_stopped)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"state": "failed", "code": str(exc) if isinstance(exc, StateError) else "recovery_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
