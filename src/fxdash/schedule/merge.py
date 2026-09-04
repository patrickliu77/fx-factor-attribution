"""Idempotent merge and the provisional overwrite policy (SPEC_phase2 1.3).

The immutable-history boundary is drawn here: non-provisional rows are never touched;
provisional rows may be overwritten by a recompute, and the moment one turns
non-provisional it is frozen for good, treated like any other historical row from
then on.

The only legitimate trigger for an overwrite is the input data's as of advancing. A
code change, a parameter change or a model rerun is no reason to overwrite --
otherwise "immutable history" becomes an empty phrase, since any change could quietly
rewrite past conclusions. All such cases go through an explicit backfill recompute.

The implementation is fully vectorized. Versions before 2026-08-31 did a MultiIndex
.loc per key for 225k keys and then assembled a DataFrame from 225k Series; a single
merge measured 20+ minutes and degraded linearly as the contract grew. Rewritten as
index set operations plus slice concatenation, the same data merges in seconds. The
semantics are identical to the row-by-row version, pinned by every test_idempotency
case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.base import record

KEY = ["date", "pair", "window", "model"]
# Key numeric fields reported in the audit (SPEC_phase2 1.3 constraint 2)
AUDITED_NUMERIC = ["r2_full", "r2_exog", "residual"]


@dataclass
class MergeResult:
    frame: pd.DataFrame
    appended: int = 0
    overwritten: int = 0
    frozen_kept: int = 0
    provisional_kept: int = 0
    audit: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "rows": int(len(self.frame)),
            "appended": self.appended,
            "overwritten": self.overwritten,
            "frozen_kept": self.frozen_kept,
            "provisional_kept": self.provisional_kept,
            "n_audit": len(self.audit),
        }


def _as_bool(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool) if series is not None else series


def _leg_contribution(row: pd.Series, factor: str) -> float | None:
    try:
        payload = json.loads(row["contributions"])
    except Exception:
        return None
    value = payload.get(factor)
    return None if value is None else float(value)


def _audit_entry(before: pd.Series, after: pd.Series, trigger: dict) -> dict:
    entry = {
        "date": str(pd.Timestamp(after["date"]).date()),
        "pair": after["pair"],
        "window": int(after["window"]),
        "model": after["model"],
        "provisional_before": bool(before.get("provisional", False)),
        "provisional_after": bool(after.get("provisional", False)),
        "trigger": trigger,
    }
    for column in AUDITED_NUMERIC:
        old, new = before.get(column), after.get(column)
        if old is not None and new is not None and np.isfinite([old, new]).all():
            entry[f"{column}_before"] = round(float(old), 6)
            entry[f"{column}_after"] = round(float(new), 6)
            entry[f"{column}_delta"] = round(float(new) - float(old), 6)
    # Contribution change on the leg that triggered the overwrite
    for factor in ("d10Y_DIFF", "d2Y_DIFF"):
        old, new = _leg_contribution(before, factor), _leg_contribution(after, factor)
        if old is not None and new is not None:
            entry[f"contribution_{factor}_before"] = round(old, 8)
            entry[f"contribution_{factor}_after"] = round(new, 8)
            entry[f"contribution_{factor}_delta"] = round(new - old, 8)
    return entry


def merge_contract(
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame,
    as_of_advanced: bool,
    trigger: dict | None = None,
    rewrite_history: bool = False,
    purge_beyond: bool = False,
) -> MergeResult:
    """Merge freshly computed rows into the existing contract.

    When as_of_advanced is False, even provisional rows are not overwritten: without
    new data there is no legitimate reason to rewrite history, even if the recompute
    differs (such a difference means precisely that code or parameters moved, which
    should go through backfill).

    rewrite_history is the override switch for explicit backfill recomputes, same
    rank as --allow-coverage-shrink: it allows rewriting even frozen rows, so it is
    used only on an intentional factor-set, schema or model change, and leaves a
    record in the manifest.

    purge_beyond is True only together with rewrite_history and only on a full-range
    recompute (--end not given): the purge range for invalidated rows extends to the
    existing contract's last date instead of stopping at this recompute's range. This
    exists for the 2026-08-31 orphan-row incident -- upstream stamped a still-open bar
    with a date that later never existed, so those rows sat past "this recompute's max
    date" and could never be deleted if the purge stopped at the recompute range's
    end. A partial backfill (--end given explicitly) must never enable it, or all
    history past the range would be wiped out.
    """
    incoming = incoming.copy()
    incoming["date"] = pd.to_datetime(incoming["date"])
    if existing is None or existing.empty:
        result = MergeResult(frame=incoming.reset_index(drop=True))
        result.appended = len(incoming)
        record("contract_merge", **result.summary(), as_of_advanced=as_of_advanced)
        return result

    existing = existing.copy()
    existing["date"] = pd.to_datetime(existing["date"])
    for frame in (existing, incoming):
        if "provisional" not in frame.columns:
            frame["provisional"] = False
        frame["provisional"] = _as_bool(frame["provisional"])

    if rewrite_history and len(incoming):
        # On an explicit recompute, old rows inside the purge range that were not
        # recomputed are invalidated (e.g. a whole day vanished after a factor-set
        # change set the break to missing, or upstream withdrew a date). They must be
        # dropped, or residue computed under the old convention would linger.
        lo, hi = incoming["date"].min(), incoming["date"].max()
        if purge_beyond:
            hi = max(hi, existing["date"].max())
        inside = existing["date"].between(lo, hi)
        superseded = existing.loc[inside].set_index(KEY).index.difference(
            incoming.set_index(KEY).index
        )
        if len(superseded):
            record(
                "contract_superseded",
                n=int(len(superseded)),
                dates=sorted({str(d.date()) for d, *_ in superseded})[:10],
            )
            existing = existing.loc[
                ~existing.set_index(KEY, drop=False).index.isin(superseded)
            ]

    old_indexed = existing.set_index(KEY, drop=False)
    new_indexed = incoming.set_index(KEY, drop=False)
    shared = old_indexed.index.intersection(new_indexed.index)

    only_old = old_indexed.loc[old_indexed.index.difference(shared)]
    only_new = new_indexed.loc[new_indexed.index.difference(shared)]

    result = MergeResult(frame=pd.DataFrame())
    result.appended = int(len(only_new))

    old_shared = old_indexed.loc[shared]
    new_shared = new_indexed.loc[shared]
    prov = old_shared["provisional"].astype(bool)

    if rewrite_history:
        # Explicit recompute: every shared row takes the new version, matching the
        # row-by-row semantics; no per-row audit (nobody reads 220k audit entries,
        # and the rewrite itself already left a record in the manifest)
        take_new = pd.Series(True, index=shared)
        result.overwritten = int(len(shared))
    else:
        take_new = prov & bool(as_of_advanced)
        result.overwritten = int(take_new.sum())
        result.frozen_kept = int((~prov).sum())
        result.provisional_kept = int((prov & ~take_new).sum())

        # Audit entries only for real provisional overwrites, a few dozen a day at most
        for key in shared[take_new.to_numpy()]:
            result.audit.append(
                _audit_entry(old_shared.loc[key], new_shared.loc[key], trigger or {})
            )

    merged = pd.concat(
        [
            only_old,
            only_new,
            old_shared.loc[~take_new.to_numpy()],
            new_shared.loc[take_new.to_numpy()],
        ],
        ignore_index=True,
    )
    merged = merged.sort_values(KEY).reset_index(drop=True)

    result.frame = merged
    record("contract_merge", **result.summary(), as_of_advanced=as_of_advanced)
    for entry in result.audit:
        record("provisional_overwrite", **entry)
    return result
