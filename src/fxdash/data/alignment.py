"""Timestamp alignment primitives and the alignment profile.

Offsets are per pair and stop at pair granularity: all usd_close factors of a pair
share one offset; there is no per-factor offset (SPEC 1.2). Offset +1 means factor
day d maps to FX day d+1; the implementation first as-of aligns the factor to the
pair's FX trading-day index, then shifts one slot on that index.

explanation is defined as contemporaneous attribution in event time: the offset
mapping is a measurement correction, not prediction (SPEC 1.5 / CLAUDE.md 6).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import (
    ALIGNMENT_DIR,
    FX_INTERNAL_FACTORS,
    MAX_STALE_BDAYS,
    OFFSETS,
)

# Factor class: decides which offset applies
FX_INTERNAL = "fx_internal"
USD_CLOSE = "usd_close"
FOREIGN = "foreign"

PROFILE_PATH = ALIGNMENT_DIR / "profile.json"


def classify(factor_name: str) -> str:
    """FX-internal constructed factors are always same-day; the rest are usd_close;
    the foreign leg goes through FOREIGN separately."""
    return FX_INTERNAL if factor_name in FX_INTERNAL_FACTORS else USD_CLOSE


def offset_for(pair: str, factor_class: str, overrides: dict | None = None) -> int:
    if factor_class == FX_INTERNAL:
        return 0
    table = (overrides or {}).get(pair) or OFFSETS[pair]
    return int(table[factor_class])


def align_to_index(
    series: pd.Series,
    index: pd.DatetimeIndex,
    offset: int,
    max_stale: int = MAX_STALE_BDAYS,
) -> tuple[pd.Series, pd.Series]:
    """Align one series to the target trading-day index; return values and stale age.

    First an as-of carry forward (capped at max_stale trading days) absorbs staleness
    from foreign holidays and the F2 Friday release; beyond the cap the stretch is a
    vacuum and the row stays missing with no carry forward (SPEC 2.5). Then shift by
    the offset.

    The second return value is the stale age: 0 means a real observation that day, n
    means the value was carried forward from n trading days earlier. The age shifts
    together with the value, otherwise reports would point at the wrong dates. The
    information beyond a boolean flag is what Phase 2 needs to classify provisional
    rows (SPEC_phase2 4.1 route B).
    """
    clean = series.dropna()
    if clean.empty:
        empty = pd.Series(np.nan, index=index)
        return empty, pd.Series(0, index=index, dtype="int64")

    on_index = clean.reindex(index)
    observed = on_index.notna()
    filled = on_index.ffill(limit=max_stale)

    # trading days elapsed since the last real observation
    position = pd.Series(np.arange(len(index)), index=index)
    last_observed = position.where(observed).ffill()
    age = (position - last_observed).where(filled.notna())
    age = age.fillna(0).astype("int64")

    if offset:
        filled = filled.shift(offset)
        age = age.shift(offset, fill_value=0)
    return filled, age.fillna(0).astype("int64")


def align_break_flags(
    flags: pd.Series, index: pd.DatetimeIndex, offset: int
) -> pd.Series:
    """Map break flags onto FX trading days. Diffs on break days carry no economic
    meaning and must be blanked."""
    mapped = flags.reindex(index).fillna(False).astype(bool)
    if offset:
        mapped = mapped.shift(offset, fill_value=False).astype(bool)
    return mapped


def write_profile(entries: list[dict], extra: dict | None = None) -> dict:
    """Write the alignment profile to disk. Freeze semantics: rerun only on a
    data-source change (SPEC 1.7)."""
    ALIGNMENT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "frozen": True,
        "note": (
            "Offsets are frozen per pair. Offset +1 means factor day d maps to FX day "
            "d+1, a measurement correction rather than prediction. evidence=thin only "
            "affects re-check priority on data-source changes; frozen offsets are "
            "unchanged."
        ),
        "entries": entries,
        **(extra or {}),
    }
    PROFILE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def read_profile() -> dict | None:
    if not PROFILE_PATH.exists():
        return None
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
