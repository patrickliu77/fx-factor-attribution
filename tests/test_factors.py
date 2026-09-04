"""Factor construction: LOO self-exclusion, carry grouping, spread sign and break-day
blanking, per-leg stale naming."""

import numpy as np
import pandas as pd
import pytest

from fxdash.config import HIGH_YIELD, LOW_YIELD, PAIRS, lasso_menu
from fxdash.factors.build import _carry_loo, _dollar_loo, build_pair_panel, factor_stale_names


def test_dollar_loo_excludes_the_explained_pair(synthetic_raw):
    """Must strictly exclude the explained pair, or daily R² is inflated by
    tautological leakage."""
    returns = synthetic_raw.fx_returns
    for pair in PAIRS:
        loo = _dollar_loo(returns, pair)
        others = [p for p in PAIRS if p != pair]
        expected = returns[others].mean(axis=1, skipna=True)
        pd.testing.assert_series_equal(loo, expected, check_names=False)
        # perturb the pair's own column; LOO must not move at all
        perturbed = returns.copy()
        perturbed[pair] = perturbed[pair] + 1.0
        pd.testing.assert_series_equal(
            _dollar_loo(perturbed, pair), loo, check_names=False
        )


def test_carry_loo_excludes_self_from_its_own_group(synthetic_raw):
    returns = synthetic_raw.fx_returns
    for pair in LOW_YIELD + HIGH_YIELD:
        base = _carry_loo(returns, pair)
        perturbed = returns.copy()
        perturbed[pair] = perturbed[pair] + 1.0
        pd.testing.assert_series_equal(
            _carry_loo(perturbed, pair), base, check_names=False
        )


def test_carry_for_cad_and_nok_is_the_full_carry(synthetic_raw):
    """CAD and NOK belong to neither group; degenerates to the full carry (SPEC 3.4)."""
    returns = synthetic_raw.fx_returns
    full = returns[LOW_YIELD].mean(axis=1) - returns[HIGH_YIELD].mean(axis=1)
    for pair in ("USDCAD", "USDNOK"):
        pd.testing.assert_series_equal(
            _carry_loo(returns, pair), full, check_names=False
        )


def test_spread_sign_is_us_minus_foreign(synthetic_raw):
    """A rising US leg should turn the spread factor positive: positive beta maps to a
    stronger dollar."""
    raw = synthetic_raw
    pair = "USDCAD"
    base = build_pair_panel(pair, raw)

    bumped = raw.us_yields["DGS10"].copy()
    bumped.iloc[200:] += 1.0  # US long end jumps 100bp from some day on
    raw.us_yields = {**raw.us_yields, "DGS10": bumped}
    after = build_pair_panel(pair, raw)

    common = base.index.intersection(after.index)
    delta = (after.loc[common, "d10Y_DIFF"] - base.loc[common, "d10Y_DIFF"]).dropna()
    assert delta.max() > 0.5  # the spread diff turns clearly positive on the jump day
    assert delta.min() >= -1e-9


def test_break_day_difference_is_blanked(synthetic_raw):
    """Conventions differ across a break day: its difference must be blanked, not
    treated as a real rate move."""
    raw = synthetic_raw
    pair = "USDNOK"  # foreign leg's frozen offset is +1
    frame = raw.foreign[pair].copy()
    break_day = frame.index[250]
    frame.loc[break_day, "break_long"] = True
    frame.loc[break_day:, "long"] += 3.0  # a level jump caused purely by convention
    raw.foreign = {**raw.foreign, pair: frame}

    panel = build_pair_panel(pair, raw)
    # with offset +1 the break-day level jump lands on the next FX trading day's
    # diff, so that day is the one to blank
    assert frame.index[251] not in panel.index
    assert break_day in panel.index
    assert panel["d10Y_DIFF"].abs().max() < 1.0  # the 3.0 jump did not leak in


def test_panel_carries_stale_columns_for_both_legs(synthetic_raw):
    panel = build_pair_panel("USDAUD", synthetic_raw)
    for slot in ("d2Y_DIFF", "d10Y_DIFF"):
        assert factor_stale_names(slot) == (f"{slot}.us", f"{slot}.foreign")
        assert f"stale::{slot}.us" in panel.columns
        assert f"stale::{slot}.foreign" in panel.columns
    assert factor_stale_names("dVIX") == ("dVIX",)


def test_panel_contains_exactly_the_menu(synthetic_raw):
    meta = ("stale::", "stale_age::", "provisional")
    for pair in PAIRS:
        panel = build_pair_panel(pair, synthetic_raw)
        menu = lasso_menu(pair)
        modelled = [c for c in panel.columns if not c.startswith(meta)]
        assert modelled == ["y", *menu]
        assert panel[["y", *menu]].notna().all().all()


def test_tail_staleness_on_a_publication_lag_leg_is_provisional(synthetic_raw):
    """Tail staleness means "not published yet", will be filled later, and counts as
    provisional."""
    raw = synthetic_raw
    frame = raw.foreign["USDAUD"].copy()
    frame.iloc[-6:, frame.columns.get_loc("long")] = np.nan  # source tail not out yet
    raw.foreign = {**raw.foreign, "USDAUD": frame}

    aud = build_pair_panel("USDAUD", raw)
    assert aud["provisional"].any()
    # only the tail, not the whole stretch
    assert aud["provisional"].iloc[-1]
    assert not aud["provisional"].iloc[:-10].any()


def test_interior_staleness_is_never_provisional(synthetic_raw):
    """A local holiday never existed in the source, will never be filled, and is not
    provisional.

    This is the premise of the status tripwire: mislabel holiday staleness as
    provisional and one Australian holiday in 2010 keeps status yellow forever.
    """
    raw = synthetic_raw
    frame = raw.foreign["USDAUD"].copy()
    frame.iloc[100:106, frame.columns.get_loc("long")] = np.nan  # interior hole
    raw.foreign = {**raw.foreign, "USDAUD": frame}

    aud = build_pair_panel("USDAUD", raw)
    # no provisional anywhere except the data-frontier row (which is provisional by
    # definition)
    assert not aud["provisional"].iloc[:-1].any()


def test_non_publication_lag_pairs_are_never_provisional(synthetic_raw):
    raw = synthetic_raw
    frame = raw.foreign["USDCAD"].copy()
    frame.iloc[-6:, frame.columns.get_loc("long")] = np.nan
    raw.foreign = {**raw.foreign, "USDCAD": frame}
    cad = build_pair_panel("USDCAD", raw)
    # publication lag does not apply to CAD; the frontier row is still provisional by
    # definition
    assert not cad["provisional"].iloc[:-1].any()


def test_data_frontier_is_always_provisional(synthetic_raw):
    """The delivered panel's latest row must be provisional (2026-08-31 orphan-row
    incident).

    Upstream released an unclosed bar early and later retracted it; 45 rows of data on
    a nonexistent date got frozen. The frontier row stays overwritable until the next
    trading day appears and a recompute confirms it.
    """
    for pair in PAIRS:
        panel = build_pair_panel(pair, synthetic_raw)
        assert bool(panel["provisional"].iloc[-1]), pair


def test_stale_age_columns_accompany_every_stale_flag(synthetic_raw):
    panel = build_pair_panel("USDAUD", synthetic_raw)
    for column in panel.columns:
        if column.startswith("stale::"):
            assert f"stale_age::{column[len('stale::'):]}" in panel.columns


def test_fx_internal_factors_are_same_day(synthetic_raw):
    """DOLLAR_LOO and CARRY_LOO are always same-day, unaffected by the pair's offset
    (SPEC 1.2)."""
    raw = synthetic_raw
    same = build_pair_panel("USDJPY", raw)  # frozen offset usd_close=+1
    shifted = build_pair_panel(
        "USDJPY", raw, overrides={"USDJPY": {"usd_close": 0, "foreign": 1}}
    )
    common = same.index.intersection(shifted.index)
    for factor in ("DOLLAR_LOO", "CARRY_LOO"):
        pd.testing.assert_series_equal(
            same.loc[common, factor], shifted.loc[common, factor]
        )
