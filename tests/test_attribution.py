"""SPEC 8's three core items: attribution identity, no lookahead, scale invariance."""

import numpy as np
import pandas as pd
import pytest

from fxdash.attribution.engine import attribute, identity_error
from fxdash.config import DEFAULT_WINDOW, MODELS, baseline_factors, lasso_menu
from fxdash.factors.build import build_pair_panel
from fxdash.models.rolling import rolling_fit

PAIR = "USDCAD"
WINDOW = 60  # small window keeps the test fast; logic identical to 126


def _design(pair, model):
    return lasso_menu(pair) if model == "lasso" else baseline_factors(pair)


def _run(panel, pair, model, window=WINDOW):
    factors = _design(pair, model)
    return attribute(panel, rolling_fit(panel, pair, window, model, factors))


@pytest.mark.parametrize("model", MODELS)
def test_attribution_identity_closes(synthetic_raw, model):
    """Sum of contributions plus residual must equal the day's return, within 1e-12."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    result = _run(panel, PAIR, model)
    assert len(result.y) > 0
    assert identity_error(result) < 1e-12


@pytest.mark.parametrize("model", MODELS)
def test_three_buckets_sum_to_explained(synthetic_raw, model):
    """systematic plus exogenous must equal the sum of all contributions."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    result = _run(panel, PAIR, model)
    total = result.systematic + result.exogenous
    assert np.max(np.abs(total - result.contributions.sum(axis=1))) < 1e-12


@pytest.mark.parametrize("model", MODELS)
def test_no_lookahead(synthetic_raw, model):
    """Delete all data after some day; output before that day must be bit-identical."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    cutoff = panel.index[len(panel) - 40]

    full = _run(panel, PAIR, model)
    truncated = _run(panel[panel.index <= cutoff], PAIR, model)

    keep = full.dates <= cutoff
    assert truncated.dates.equals(full.dates[keep])
    np.testing.assert_array_equal(truncated.betas, full.betas[keep])
    np.testing.assert_array_equal(truncated.contributions, full.contributions[keep])
    np.testing.assert_array_equal(truncated.residual, full.residual[keep])
    np.testing.assert_array_equal(truncated.r2_full, full.r2_full[keep])
    np.testing.assert_array_equal(truncated.selected, full.selected[keep])


@pytest.mark.parametrize("model", MODELS)
def test_scale_invariance(synthetic_raw, model):
    """Multiply any factor by 100: under the standardization pipeline the attribution
    is unchanged, all three engines pass."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    base = _run(panel, PAIR, model)

    scaled = panel.copy()
    scaled["WTI"] = scaled["WTI"] * 100.0
    after = _run(scaled, PAIR, model)

    np.testing.assert_allclose(after.contributions, base.contributions, atol=1e-15)
    np.testing.assert_allclose(after.residual, base.residual, atol=1e-15)
    np.testing.assert_allclose(after.r2_full, base.r2_full, rtol=1e-12)
    np.testing.assert_array_equal(after.selected, base.selected)

    # beta itself scales inversely with the units — the evidence it was scaled back
    j = base.factors.index("WTI")
    np.testing.assert_allclose(after.betas[:, j] * 100.0, base.betas[:, j], rtol=1e-10)


def test_beta_comes_from_strictly_prior_window(synthetic_raw):
    """Day-t beta may only see t-1 and earlier: changing the day's y must not affect
    the day's beta."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    base = _run(panel, PAIR, "ols")

    tampered = panel.copy()
    target = base.dates[len(base.dates) // 2]
    tampered.loc[target, "y"] = tampered.loc[target, "y"] + 10.0
    after = _run(tampered, PAIR, "ols")

    row = list(base.dates).index(target)
    np.testing.assert_array_equal(after.betas[row], base.betas[row])
    # but the day's residual must move with it, or y never entered the identity
    assert abs(after.residual[row] - base.residual[row]) > 1.0


def test_residual_z_uses_prior_window_only(synthetic_raw):
    panel = build_pair_panel(PAIR, synthetic_raw)
    result = _run(panel, PAIR, "ols", window=DEFAULT_WINDOW)
    z = pd.Series(result.residual_z, index=result.dates)
    assert z.notna().sum() > 0
    assert z.iloc[:126].isna().all()  # first 126 days lack history for the std


def test_lasso_empty_selection_puts_everything_in_residual(synthetic_raw):
    """An empty selection degenerates to intercept-only; the day goes fully into
    residual (SPEC 4.5)."""
    panel = build_pair_panel(PAIR, synthetic_raw)
    factors = lasso_menu(PAIR)
    rolling = rolling_fit(panel, PAIR, WINDOW, "lasso", factors)
    empty = ~rolling.selected.any(axis=1)
    if not empty.any():
        pytest.skip("no empty selection occurred in this synthetic data")
    result = attribute(panel, rolling)
    idx = np.flatnonzero(empty)
    np.testing.assert_allclose(result.residual[idx], result.y[idx], atol=1e-15)
