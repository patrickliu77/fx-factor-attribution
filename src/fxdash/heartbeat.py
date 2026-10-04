"""Scheduler heartbeat (SPEC_phase2 1.4 and section 5).

The most dangerous failure mode of an unattended system is not an error but the
scheduled task quietly no longer running: the page keeps showing yesterday's content,
the data-freshness table still shows each source's as of, everything looks normal --
and nothing anywhere says "it did not run today". The heartbeat answers exactly this
one question: is the system still alive.

Only live runs beat the heartbeat. backfill is a manually launched refill; however
often it runs, it says nothing about the scheduler working.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .config import HEARTBEAT_CRIT_HOURS, HEARTBEAT_WARN_HOURS, OUTPUT_DIR
from .data.base import record
from .task_runner import CLOCK_DIAGNOSTIC, stamp as utc_stamp

HEARTBEAT_PATH = OUTPUT_DIR / "heartbeat.json"


def beat(mode: str, when=None) -> dict | None:
    """Drop one heartbeat when a live run finishes successfully."""
    if mode != "live":
        return None
    stamp = pd.Timestamp(when or pd.Timestamp.now())
    payload = {"last_live_success": str(stamp), "mode": mode}
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    record("heartbeat", **payload)
    return payload


def read() -> dict:
    if not HEARTBEAT_PATH.exists():
        return {}
    try:
        payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def assess(now=None, payload=None) -> dict:
    """Return {state, last_live_success, age_hours, note}.

    Never alarms when no live run has ever happened; it just states that no record
    exists -- a freshly installed repo should not start out red.
    """
    recorded = HEARTBEAT_PATH.exists() if payload is None else (not isinstance(payload, dict) or bool(payload))
    payload = read() if payload is None else payload
    unreadable = {
        "state": "red",
        "last_live_success": None,
        "age_hours": None,
        "note": "heartbeat record unreadable",
        "warn_hours": HEARTBEAT_WARN_HOURS,
        "crit_hours": HEARTBEAT_CRIT_HOURS,
    }
    if not isinstance(payload, dict):
        return unreadable
    last = payload.get("last_live_success")
    if not last:
        if recorded:
            return unreadable
        return {
            "state": "green",
            "last_live_success": None,
            "age_hours": None,
            "note": "no live run recorded yet",
        }

    # Historical naive timestamps use the host's local zone, as in the live
    # supervisor. New aware values must also be comparable to a naive caller.
    try:
        observed = utc_stamp(str(pd.Timestamp(now if now is not None else datetime.now(timezone.utc))))
    except (TypeError, ValueError):
        observed = None
    completed = utc_stamp(last)
    if observed is None or completed is None:
        return unreadable
    age = (observed - completed).total_seconds() / 3600.0
    if age < 0:
        state, note = "red", CLOCK_DIAGNOSTIC
    elif age > HEARTBEAT_CRIT_HOURS:
        state, note = "red", f"suspected scheduler stall: {age:.0f} hours without a successful live run"
    elif age > HEARTBEAT_WARN_HOURS:
        state, note = "yellow", f"suspected scheduler stall: {age:.0f} hours without a successful live run"
    else:
        state, note = "green", "scheduler healthy"
    return {
        "state": state,
        "last_live_success": str(last),
        "age_hours": round(age, 1),
        "note": note,
        "warn_hours": HEARTBEAT_WARN_HOURS,
        "crit_hours": HEARTBEAT_CRIT_HOURS,
    }


def humanise(age_hours) -> str:
    """Render the age in a form a human reads at a glance."""
    if age_hours is None:
        return "—"
    if age_hours < 1:
        return f"{age_hours * 60:.0f} 分钟前"
    if age_hours < 48:
        return f"{age_hours:.1f} 小时前"
    return f"{age_hours / 24:.1f} 天前"
