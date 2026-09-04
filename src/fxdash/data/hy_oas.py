"""HY OAS full-history splice (SPEC_phase2 4.2).

FRED clamps ICE BofA licensed series to a rolling three-year window; the full history
of BAMLH0A0HYM2 is no longer available from FRED. The data/user/ file is verified
full history, measured bit-identical to the current FRED window over the overlap.

The splice date is fixed at 2026-02-07 (the day after the user file's last date) and
does not move as the FRED window rolls forward, so the splice is reproducible and the
break day fixed. The diff on that day is blanked.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import (
    HY_OAS_MEDIAN_RANGE,
    HY_OAS_OVERLAP_MAX_GAP_BP,
    HY_OAS_OVERLAP_MIN_CORR,
    HY_OAS_SPLICE_DATE,
    HY_OAS_USER_FILE,
    START,
)
from .base import load_user_csv, record
from .fred_source import fetch_fred

SERIES_ID = "BAMLH0A0HYM2"
NAME = "HY_OAS"


class SpliceMismatch(RuntimeError):
    """Overlap clearly inconsistent with FRED. Stop and report per convention; no
    silent reconciliation."""


def _user_history() -> pd.Series:
    loader = load_user_csv(HY_OAS_USER_FILE, name=NAME)
    series = loader().sort_index()
    series.index = pd.to_datetime(series.index).normalize()
    return series[~series.index.duplicated(keep="last")]


def _fred_window() -> pd.Series:
    series = fetch_fred(SERIES_ID)
    series.index = pd.to_datetime(series.index).normalize()
    return series.sort_index()


def verify_overlap(user: pd.Series, fred: pd.Series) -> dict:
    """Compare the overlap. Measured values should be bit-identical; stop and report
    immediately on a clear inconsistency."""
    overlap = user.index.intersection(fred.index)
    if len(overlap) == 0:
        raise SpliceMismatch(
            f"{NAME} splice segments have no overlap: user ends {user.index[-1].date()}, "
            f"FRED starts {fred.index[0].date()}"
        )
    left, right = user.loc[overlap], fred.loc[overlap]
    gap_bp = float((right - left).abs().max() * 100)
    diff_corr = float(left.diff().corr(right.diff()))
    identical = int((left == right).sum())

    stats = {
        "series": NAME,
        "n_overlap": int(len(overlap)),
        "first": str(overlap[0].date()),
        "last": str(overlap[-1].date()),
        "max_gap_bp": round(gap_bp, 4),
        "mean_gap_bp": round(float((right - left).abs().mean() * 100), 4),
        "diff_corr": round(diff_corr, 6),
        "n_identical": identical,
    }
    record("hy_oas_overlap", **stats)

    if gap_bp > HY_OAS_OVERLAP_MAX_GAP_BP or (
        np.isfinite(diff_corr) and diff_corr < HY_OAS_OVERLAP_MIN_CORR
    ):
        raise SpliceMismatch(
            f"{NAME} overlap clearly inconsistent with FRED: max level gap {gap_bp:.3f}bp"
            f" (limit {HY_OAS_OVERLAP_MAX_GAP_BP}), diff correlation {diff_corr:.6f}"
            f" (floor {HY_OAS_OVERLAP_MIN_CORR}). Stopping to report per convention."
        )
    return stats


def build() -> tuple[pd.Series, pd.Timestamp]:
    """Return the spliced HY OAS levels and the trading day whose diff actually needs
    blanking."""
    splice = pd.Timestamp(HY_OAS_SPLICE_DATE)
    user, fred = _user_history(), _fred_window()

    # Tripwire: once the FRED window rolls past the splice date the user file must be
    # updated, otherwise a stretch in between goes missing
    if fred.index[0] > splice:
        raise SpliceMismatch(
            f"{NAME} earliest available FRED date {fred.index[0].date()} is later than splice date "
            f"{splice.date()}; a gap opened between the segments. Update data/user/{HY_OAS_USER_FILE}."
        )
    verify_overlap(user, fred)

    head = user[user.index < splice]
    tail = fred[fred.index >= splice]
    series = pd.concat([head, tail]).sort_index().rename(NAME)
    series = series[~series.index.duplicated(keep="last")]
    series = series[series.index >= pd.Timestamp(START)]

    low, high = HY_OAS_MEDIAN_RANGE
    median = float(series.median())
    passed = bool(low <= median <= high)
    record(
        "direction_check",
        series=NAME,
        median=round(median, 4),
        low=low,
        high=high,
        passed=passed,
    )
    if not passed:
        raise AssertionError(
            f"{NAME} direction check failed: full-sample median {median:.4f} outside [{low}, {high}]"
        )

    # The splice date 2026-02-07 is a Saturday and absent from the index. The diff
    # across the seam lands on the first trading day after it (the first observation
    # taken from FRED); that is the day to blank, not the nominal splice date.
    after = series.index[series.index >= splice]
    if len(after) == 0:
        raise SpliceMismatch(f"{NAME} no observations after splice date {splice.date()}")
    break_date = after[0]

    record(
        "hy_oas_spliced",
        splice_date=str(splice.date()),
        break_date=str(break_date.date()),
        n=len(series),
        first=str(series.index[0].date()),
        last=str(series.index[-1].date()),
        n_from_user=int((series.index < splice).sum()),
        n_from_fred=int((series.index >= splice).sum()),
    )
    return series, break_date
