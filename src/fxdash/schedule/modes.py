"""Run modes, source as-of tracking, gap backfill (SPEC_phase2 1.1 and 1.3)."""

from __future__ import annotations

import json
from enum import Enum

import pandas as pd

from ..config import OUTPUT_DIR, PUBLICATION_LAG_LEGS
from ..data.base import record

AS_OF_PATH = OUTPUT_DIR / "source_as_of.json"


class RunMode(str, Enum):
    BACKFILL = "backfill"
    LIVE = "live"


def resolve_range(mode: RunMode, start, end, contract_last=None):
    """backfill covers the explicit range; live resumes after the contract's last date."""
    start, end = pd.Timestamp(start), (pd.Timestamp(end) if end else None)
    if mode is RunMode.BACKFILL or contract_last is None:
        return start, end
    # live: back up to include the last day of existing history in the recompute, so
    # provisional rows get a chance to be overwritten
    resume = pd.Timestamp(contract_last)
    return min(start, resume) if start > resume else start, end


def missing_dates(contract_dates, panel_dates) -> pd.DatetimeIndex:
    """Dates that should be backfilled after some days of downtime."""
    have = pd.DatetimeIndex(pd.to_datetime(pd.Index(contract_dates)).unique())
    want = pd.DatetimeIndex(pd.to_datetime(pd.Index(panel_dates)).unique())
    return want.difference(have).sort_values()


def source_as_of(raw) -> dict[str, str]:
    """Current as of for each "input not yet final" source, used to decide whether an
    overwrite has a legitimate trigger.

    Two kinds: publication-lag legs (AUD's F2) and each pair's FX data frontier. The
    latter was added after the 2026-08-31 incident -- rows at the data frontier are
    marked provisional, and for the next trading day to legitimately overwrite and
    freeze them, the FX source's last date advancing must count as an as-of advance.
    """
    out = {}
    for pair, series in raw.fx_levels.items():
        clean = series.dropna()
        if len(clean):
            out[f"{pair}.fx"] = str(pd.Timestamp(clean.index[-1]).date())
    for pair, legs in PUBLICATION_LAG_LEGS.items():
        frame = raw.foreign.get(pair)
        if frame is None:
            continue
        for leg in legs:
            column = "long" if leg == "foreign" else leg
            series = frame[column].dropna() if column in frame else None
            if series is not None and len(series):
                out[f"{pair}.{leg}"] = str(pd.Timestamp(series.index[-1]).date())
    return out


def load_previous_as_of() -> dict[str, str]:
    if not AS_OF_PATH.exists():
        return {}
    return json.loads(AS_OF_PATH.read_text(encoding="utf-8")).get("sources", {})


def save_as_of(current: dict[str, str]) -> None:
    AS_OF_PATH.parent.mkdir(parents=True, exist_ok=True)
    AS_OF_PATH.write_text(
        json.dumps({"sources": current}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def as_of_advanced(current: dict[str, str], previous: dict[str, str]) -> tuple[bool, dict]:
    """Any publication-lag source's as of advancing is a legitimate trigger to
    overwrite provisional rows."""
    advanced, detail = False, {}
    for name, now in current.items():
        before = previous.get(name)
        if before is None or pd.Timestamp(now) > pd.Timestamp(before):
            advanced = True
            detail[name] = {"before": before, "after": now}
    record("source_as_of", current=current, advanced=advanced, moved=detail)
    return advanced, detail
