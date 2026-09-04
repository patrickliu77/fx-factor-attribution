"""dHY_OAS full-history splice (SPEC_phase2 4.2). Offline; both segments use
synthetic data."""

import numpy as np
import pandas as pd
import pytest

from fxdash import config as cfg
from fxdash.data import hy_oas
from fxdash.data.hy_oas import SpliceMismatch, verify_overlap


def _series(start, periods, base=4.5, seed=0):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start, periods=periods)
    return pd.Series(base + np.cumsum(rng.normal(0, 0.02, periods)), index=index)


def test_identical_overlap_passes():
    """Measured 642/642 bit-identical; this is the normal case."""
    user = _series("2023-01-02", 400)
    fred = user.iloc[200:].copy()
    stats = verify_overlap(user, fred)
    assert stats["n_overlap"] == 200
    assert stats["max_gap_bp"] == 0.0
    assert stats["n_identical"] == 200


def test_small_common_shift_still_passes_if_within_tolerance():
    user = _series("2023-01-02", 400)
    fred = user.iloc[200:] + 0.005  # 0.5bp, within the 1bp limit
    stats = verify_overlap(user, fred)
    assert stats["max_gap_bp"] == pytest.approx(0.5, abs=1e-6)


def test_level_mismatch_stops_the_run():
    """A clear inconsistency must stop and report; no silent reconciliation."""
    user = _series("2023-01-02", 400)
    fred = user.iloc[200:] + 0.5  # 50bp
    with pytest.raises(SpliceMismatch, match="clearly inconsistent"):
        verify_overlap(user, fred)


def test_shape_mismatch_stops_the_run():
    user = _series("2023-01-02", 400, seed=1)
    fred = _series("2023-01-02", 400, seed=2).iloc[200:]  # completely different path
    with pytest.raises(SpliceMismatch):
        verify_overlap(user, fred)


def test_no_overlap_stops_the_run():
    user = _series("2020-01-01", 100)
    fred = _series("2024-01-01", 100)
    with pytest.raises(SpliceMismatch, match="no overlap"):
        verify_overlap(user, fred)


def test_build_splices_and_blanks_the_seam(monkeypatch):
    user = _series("2024-01-01", 500)
    splice = pd.Timestamp("2025-06-07")  # Saturday, absent from the index
    fred = user[user.index >= "2025-01-01"].copy()
    fred = pd.concat([fred, _series("2025-11-25", 40, base=float(fred.iloc[-1]))])
    fred = fred[~fred.index.duplicated(keep="first")]

    monkeypatch.setattr(hy_oas, "_user_history", lambda: user)
    monkeypatch.setattr(hy_oas, "_fred_window", lambda: fred)
    monkeypatch.setattr(cfg, "HY_OAS_SPLICE_DATE", str(splice.date()))
    monkeypatch.setattr(hy_oas, "HY_OAS_SPLICE_DATE", str(splice.date()))

    series, break_date = hy_oas.build()
    # nominal splice date is a Saturday; the actual blank day must be the first
    # trading day after it
    assert break_date > splice
    assert break_date == series.index[series.index >= splice][0]
    assert break_date.weekday() < 5
    assert series.index.is_monotonic_increasing
    assert not series.index.has_duplicates


def test_fred_window_rolling_past_splice_is_a_tripwire(monkeypatch):
    """Once FRED rolls past the splice date a gap appears; must go red to prompt a
    user-file update."""
    user = _series("2020-01-01", 300)
    fred = _series("2026-01-01", 100)
    monkeypatch.setattr(hy_oas, "_user_history", lambda: user)
    monkeypatch.setattr(hy_oas, "_fred_window", lambda: fred)
    monkeypatch.setattr(hy_oas, "HY_OAS_SPLICE_DATE", "2021-01-01")
    with pytest.raises(SpliceMismatch, match="gap"):
        hy_oas.build()


def test_direction_range_is_the_finalised_one():
    assert cfg.HY_OAS_MEDIAN_RANGE == (3.0, 8.0)
    assert cfg.HY_OAS_SPLICE_DATE == "2026-02-07"
