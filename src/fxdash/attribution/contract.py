"""Output contract.

One record per day per pair per window per model. Nested fields (betas, contributions,
selected_factors, stale_flags) are stored as JSON string columns; parquet is
partitioned by year under outputs/contract/, plus an outputs/contract_latest.json
snapshot (SPEC 6).

The schema carries a version number; changes must be backward compatible or bump the
version and sync downstream (CLAUDE.md 15).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import CONTRACT_DIR, CONTRACT_SCHEMA_VERSION, OUTPUT_DIR
from ..data.base import record

COLUMNS = [
    "date",
    "pair",
    "window",
    "model",
    "betas",
    "contributions",
    "r2_full",
    "r2_exog",
    "selected_factors",
    "residual",
    "residual_z",
    "stale_flags",
    "systematic",
    "exogenous",
    "y",
    "lambda",
    "provisional",
    "schema_version",
]


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _clean(value: float) -> float | None:
    return None if not np.isfinite(value) else float(value)


def build_contract(pair: str, window: int, model: str, result) -> pd.DataFrame:
    factors = result.factors
    rows = []
    for i, date in enumerate(result.dates):
        betas = {f: _clean(result.betas[i, j]) for j, f in enumerate(factors)}
        contributions = {
            f: _clean(result.contributions[i, j]) for j, f in enumerate(factors)
        }
        chosen = [f for j, f in enumerate(factors) if result.selected[i, j]]
        rows.append(
            {
                "date": date,
                "pair": pair,
                "window": window,
                "model": model,
                "betas": _dumps(betas),
                "contributions": _dumps(contributions),
                "r2_full": _clean(result.r2_full[i]),
                "r2_exog": _clean(result.r2_exog[i]),
                "selected_factors": _dumps(chosen),
                "residual": _clean(result.residual[i]),
                "residual_z": _clean(result.residual_z[i]),
                "stale_flags": _dumps(result.stale_flags[i]),
                "systematic": _clean(result.systematic[i]),
                "exogenous": _clean(result.exogenous[i]),
                "y": _clean(result.y[i]),
                "lambda": _clean(result.lam[i]),
                "provisional": bool(result.provisional[i]),
                "schema_version": CONTRACT_SCHEMA_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def write_contract(frame: pd.DataFrame) -> dict:
    """Write parquet partitioned by year, plus a JSON snapshot of the latest day."""
    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    frame = frame.sort_values(["date", "pair", "window", "model"]).reset_index(drop=True)
    years = frame["date"].dt.year

    written = []
    for year, block in frame.groupby(years):
        path = CONTRACT_DIR / f"year={year}"
        path.mkdir(parents=True, exist_ok=True)
        target = path / "part.parquet"
        block.to_parquet(target, index=False)
        written.append(str(target.relative_to(CONTRACT_DIR)))

    latest_date = frame["date"].max()
    latest = frame[frame["date"] == latest_date]
    snapshot = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "date": str(latest_date.date()),
        "n_records": int(len(latest)),
        "records": json.loads(
            latest.assign(date=latest["date"].dt.strftime("%Y-%m-%d")).to_json(
                orient="records"
            )
        ),
    }
    (OUTPUT_DIR / "contract_latest.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "rows": int(len(frame)),
        "partitions": len(written),
        "combos": int(
            frame.groupby(["pair", "window", "model"], observed=True).ngroups
        ),
        "first": str(frame["date"].min().date()),
        "last": str(latest_date.date()),
        "schema_version": CONTRACT_SCHEMA_VERSION,
    }
    record("contract_written", **summary)
    return summary


def read_contract() -> pd.DataFrame:
    """Downstream reads only from here (the Phase 2/3 entry point)."""
    parts = sorted(CONTRACT_DIR.glob("year=*/part.parquet"))
    if not parts:
        raise FileNotFoundError(f"contract is empty: {CONTRACT_DIR}")
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
