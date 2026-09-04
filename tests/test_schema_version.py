"""Schema version checks (SPEC_phase2 1.6 and section 6).

The contract schema carries a version; changes must be backward compatible or bump
the version and sync downstream (CLAUDE.md 15). These tests turn the version number
and the column set themselves into assertions: a column added without a bump, or one
removed breaking backward compatibility, goes red immediately.
"""

import json
import re

import pandas as pd
import pytest

from fxdash.attribution import contract as contract_mod
from fxdash.config import CONTRACT_SCHEMA_VERSION, PCA_MONITOR_SCHEMA_VERSION
from fxdash.models.pca_monitor import run_monitor
from fxdash.config import PAIRS

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# The 1.0.0 column set. Backward compatibility means these columns must always exist.
V1_COLUMNS = {
    "date", "pair", "window", "model", "betas", "contributions",
    "r2_full", "r2_exog", "selected_factors", "residual", "residual_z",
    "stale_flags", "systematic", "exogenous", "y", "lambda", "schema_version",
}


def test_versions_are_semver():
    assert SEMVER.match(CONTRACT_SCHEMA_VERSION)
    assert SEMVER.match(PCA_MONITOR_SCHEMA_VERSION)


def test_contract_keeps_every_v1_column():
    """Backward compatibility: not a single 1.0.0 column may go missing."""
    assert V1_COLUMNS <= set(contract_mod.COLUMNS)


def test_added_columns_bumped_the_minor_version():
    """provisional was added in 1.1.0; when the column set changes, the version must
    move with it."""
    added = set(contract_mod.COLUMNS) - V1_COLUMNS
    major, minor, _ = CONTRACT_SCHEMA_VERSION.split(".")
    if added:
        assert (major, minor) != ("1", "0"), f"added {added} yet still 1.0.x"
    assert "provisional" in added


def test_every_written_row_carries_the_version():
    from fxdash.attribution.engine import Attribution
    import numpy as np

    dates = pd.bdate_range("2026-08-24", periods=3)
    result = Attribution(
        dates=dates,
        factors=["dVIX"],
        betas=np.zeros((3, 1)),
        contributions=np.zeros((3, 1)),
        selected=np.ones((3, 1), dtype=bool),
        lam=np.full(3, np.nan),
        r2_full=np.full(3, 0.5),
        r2_exog=np.full(3, 0.4),
        y=np.zeros(3),
        residual=np.zeros(3),
        residual_z=np.zeros(3),
        systematic=np.zeros(3),
        exogenous=np.zeros(3),
        stale_flags=[[], [], []],
        provisional=np.zeros(3, dtype=bool),
    )
    frame = contract_mod.build_contract("USDEUR", 126, "ols", result)
    assert list(frame.columns) == contract_mod.COLUMNS
    assert (frame["schema_version"] == CONTRACT_SCHEMA_VERSION).all()


def test_pca_monitor_rows_carry_their_version():
    import numpy as np

    rng = np.random.default_rng(0)
    index = pd.bdate_range("2024-01-01", periods=300)
    common = rng.normal(0, 0.004, 300)
    returns = pd.DataFrame(
        {p: common + rng.normal(0, 0.003, 300) for p in PAIRS}, index=index
    )
    frame = run_monitor(returns, window=126)
    assert (frame["schema_version"] == PCA_MONITOR_SCHEMA_VERSION).all()
    # The projection R² column was added in 1.1.0
    assert "carry_projection_r2" in frame.columns
    assert PCA_MONITOR_SCHEMA_VERSION.startswith("1.1")


def test_snapshot_reports_the_version(isolated_outputs):

    row = {c: None for c in contract_mod.COLUMNS}
    row.update(
        date=pd.Timestamp("2026-08-28"), pair="USDEUR", window=126, model="ols",
        schema_version=CONTRACT_SCHEMA_VERSION, provisional=False,
    )
    summary = contract_mod.write_contract(pd.DataFrame([row]))
    assert summary["schema_version"] == CONTRACT_SCHEMA_VERSION

    snapshot = json.loads((isolated_outputs / "contract_latest.json").read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == CONTRACT_SCHEMA_VERSION


def test_reader_tolerates_a_pre_1_1_contract():
    """Downstream reads legacy data with a default of False -- the practical meaning
    of a "backward-compatible added column"."""
    from fxdash.schedule.merge import merge_contract

    legacy = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-24"), "pair": "USDEUR", "window": 126,
        "model": "ols", "r2_full": 0.5, "schema_version": "1.0.0",
    }])
    incoming = legacy.assign(r2_full=0.9, schema_version=CONTRACT_SCHEMA_VERSION,
                             provisional=False)
    result = merge_contract(legacy, incoming, as_of_advanced=True)
    # Legacy rows lack the provisional column; treated as False they count as frozen
    # and must not be overwritten
    assert result.overwritten == 0
    assert result.frozen_kept == 1
