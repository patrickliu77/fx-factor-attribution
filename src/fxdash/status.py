"""outputs/status.json, the green/yellow/red tri-state (SPEC_phase2 1.4).

Downstream reads only outputs/contract/ and status.json; that contract does not change.
"""

from __future__ import annotations

import json

import pandas as pd

from . import heartbeat
from .config import OUTPUT_DIR, PROVISIONAL_AGE_LIMIT_DAYS
from .data.base import record

STATUS_PATH = OUTPUT_DIR / "status.json"

GREEN, YELLOW, RED = "green", "yellow", "red"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}


def worst(*states: str) -> str:
    return max(states, key=lambda s: _RANK.get(s, 0)) if states else GREEN


def stale_provisional(contract: pd.DataFrame, today=None) -> list[dict]:
    """An overage provisional row trips the wire: the official source may have stopped
    publishing or changed its interface (SPEC_phase2 1.3 constraint 3)."""
    if "provisional" not in contract.columns:
        return []
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    rows = contract[contract["provisional"].fillna(False).astype(bool)]
    if rows.empty:
        return []
    oldest = rows.groupby("pair")["date"].min()
    offenders = []
    for pair, date in oldest.items():
        age = int((today - pd.Timestamp(date)).days)
        if age > PROVISIONAL_AGE_LIMIT_DAYS:
            offenders.append(
                {
                    "pair": pair,
                    "oldest_provisional": str(pd.Timestamp(date).date()),
                    "age_days": age,
                    "limit_days": PROVISIONAL_AGE_LIMIT_DAYS,
                    "note": "official source may have stopped publishing or changed "
                    "its interface; do not wait indefinitely for the backfill",
                }
            )
    return offenders


def build_status(
    contract: pd.DataFrame,
    mode: str,
    manifest: dict,
    findings=None,
    today=None,
) -> dict:
    findings = findings or []
    overdue = stale_provisional(contract, today)
    pulse = heartbeat.assess(now=today)

    state = GREEN
    reasons = []
    # Heartbeat first: on a scheduler stall the page keeps showing yesterday's
    # content and everything looks normal
    if pulse["state"] != GREEN:
        state = worst(state, pulse["state"])
        reasons.append(pulse["note"])
    if overdue:
        state = worst(state, YELLOW)
        reasons.extend(
            f"{o['pair']} provisional rows unfilled for {o['age_days']} days"
            f" (limit {o['limit_days']})"
            for o in overdue
        )
    if findings:
        state = worst(state, YELLOW)
        reasons.extend(f"health check {f['check']} on {f.get('pair', '?')}" for f in findings)
    if manifest.get("coverage_shrunk"):
        state = worst(state, YELLOW)
        reasons.append("history range shorter than the previous run; explicit override used")
    if manifest.get("failed"):
        state = RED
        reasons.append(str(manifest["failed"]))

    provisional_rows = (
        int(contract["provisional"].fillna(False).astype(bool).sum())
        if "provisional" in contract.columns
        else 0
    )
    status = {
        "state": state,
        "mode": mode,
        "generated_at": str(pd.Timestamp(today or pd.Timestamp.now())),
        "contract_last_date": str(pd.Timestamp(contract["date"].max()).date())
        if len(contract)
        else None,
        "rows": int(len(contract)),
        "provisional_rows": provisional_rows,
        "overdue_provisional": overdue,
        "heartbeat": pulse,
        "health_findings": findings,
        "reasons": reasons,
        "source_as_of": manifest.get("source_as_of", {}),
        "schema_version": manifest.get("contract", {}).get("schema_version"),
        "model_revision": manifest.get("model_revision"),
    }
    record(
        "status",
        state=state,
        reasons=reasons,
        provisional_rows=provisional_rows,
        heartbeat=pulse["state"],
    )
    return status


def write_status(status: dict) -> dict:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(status, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return status
