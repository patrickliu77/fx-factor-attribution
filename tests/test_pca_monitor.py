"""PCA projection R² (SPEC_phase2 3.1).

The core reason projection R² replaces corr(PC2, CARRY) is rotation invariance: when
the PC2 and PC3 eigenvalues are close, the plane they span is stable but the basis
inside it can rotate arbitrarily, so the correlation with the single PC2 swings wildly
while "CARRY lies in that plane" is unchanged. The first group of tests below pins
exactly this property.
"""

import numpy as np
import pandas as pd
import pytest

from fxdash.config import PAIRS, PCA_CORR_WARN, PCA_MONITOR_SCHEMA_VERSION
from fxdash.models.pca_monitor import projection_r2, run_monitor


def _rotate(basis, angle):
    c, s = np.cos(angle), np.sin(angle)
    return basis @ np.array([[c, -s], [s, c]])


def test_projection_r2_is_rotation_invariant():
    rng = np.random.default_rng(0)
    basis = rng.normal(size=(200, 2))
    target = 0.7 * basis[:, 0] - 0.4 * basis[:, 1] + rng.normal(0, 0.3, 200)

    base = projection_r2(target, basis)
    for angle in (0.3, 1.1, 2.7):
        assert projection_r2(target, _rotate(basis, angle)) == pytest.approx(base, abs=1e-12)


def test_correlation_with_a_single_axis_is_not_rotation_invariant():
    """Counterexample: the old convention swings with rotation — exactly why it is
    being replaced."""
    rng = np.random.default_rng(1)
    basis = rng.normal(size=(200, 2))
    target = 0.7 * basis[:, 0] - 0.4 * basis[:, 1]

    corrs = [
        abs(np.corrcoef(target, _rotate(basis, a)[:, 0])[0, 1])
        for a in (0.0, 0.6, 1.2)
    ]
    assert max(corrs) - min(corrs) > 0.1  # clearly swinging


def test_target_inside_the_span_gives_one():
    rng = np.random.default_rng(2)
    basis = rng.normal(size=(150, 2))
    target = 2.0 * basis[:, 0] - 3.0 * basis[:, 1]
    assert projection_r2(target, basis) == pytest.approx(1.0, abs=1e-10)


def test_target_orthogonal_to_the_span_gives_about_zero():
    n = 200
    basis = np.zeros((n, 2))
    basis[:, 0] = np.sin(np.arange(n))
    basis[:, 1] = np.cos(np.arange(n))
    target = np.arange(n, dtype=float) ** 2  # essentially unrelated to the two sines
    assert projection_r2(target, basis) < 0.2


def test_degenerate_inputs_return_nan():
    basis = np.random.default_rng(3).normal(size=(50, 2))
    assert np.isnan(projection_r2(np.zeros(50), basis))
    assert np.isnan(projection_r2(np.arange(50.0), np.empty((50, 0))))


def _returns(n=400, seed=4):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=n)
    common = rng.normal(0, 0.004, n)  # the shared dollar factor
    return pd.DataFrame(
        {p: common + rng.normal(0, 0.003, n) for p in PAIRS}, index=index
    )


def test_monitor_emits_the_new_column_and_schema_bump():
    frame = run_monitor(_returns(), window=126)
    assert "carry_projection_r2" in frame.columns
    assert "corr_pc2_carry" in frame.columns  # old line kept until the new metric is live
    assert frame["schema_version"].iloc[0] == PCA_MONITOR_SCHEMA_VERSION


def test_projection_r2_is_bounded_and_finite():
    frame = run_monitor(_returns(), window=126)
    values = frame["carry_projection_r2"].dropna()
    assert len(values)
    assert values.between(-1e-9, 1.0 + 1e-9).all()


def test_dollar_dominated_panel_keeps_pc1_correlation_high():
    frame = run_monitor(_returns(), window=126)
    assert frame["corr_pc1_dollar"].abs().mean() > PCA_CORR_WARN["pc1_dollar"]


def test_warn_flags_name_the_failing_line():
    frame = run_monitor(_returns(), window=126)
    for flags in frame["warn_flags"]:
        for flag in filter(None, flags.split(",")):
            assert flag in PCA_CORR_WARN


def test_monitor_matches_standardized_pca_with_unequal_volatilities():
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    from fxdash.config import HIGH_YIELD, LOW_YIELD

    panel = _returns(n=127) * np.array([0.2, 4, 1, 2, 0.5, 3])
    block = panel.iloc[:126]
    scores = PCA().fit_transform(StandardScaler().fit_transform(block))
    dollar = block.mean(axis=1).to_numpy()
    carry = (block[LOW_YIELD].mean(axis=1) - block[HIGH_YIELD].mean(axis=1)).to_numpy()
    result = run_monitor(panel, window=126).iloc[0]
    assert abs(result.corr_pc1_dollar) == pytest.approx(
        abs(np.corrcoef(scores[:, 0], dollar)[0, 1]), abs=1e-12)
    assert abs(result.corr_pc2_carry) == pytest.approx(
        abs(np.corrcoef(scores[:, 1], carry)[0, 1]), abs=1e-12)
    assert result.carry_projection_r2 == pytest.approx(
        projection_r2(carry, scores[:, 1:3]), abs=1e-12)
