"""Robustness check (SPEC_phase3 §12).

Pinned behaviour: hysteresis needs 2 consecutive days in and out; abstain days are
excluded from the Lasso quantile sample yet are a state themselves; abstain run
counting; insufficient data reports available=False rather than masquerading as
three-way agreement; concurrent states are not merged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import fxdash.robustness as RB


# ------------------------------------------------------------------ primitives
def test_hysteresis_needs_k_consecutive_days_both_ways():
    hot = np.array([0, 1, 0, 1, 1, 1, 0, 1, 0, 0, 1, 0], dtype=bool)
    on = RB.hysteresis(hot, k=2)
    # single-day blips (positions 1, 7, 10) do not enter the state; positions 3-4 take
    # two consecutive days to enter; once in, the single day back under at position 6
    # does not exit, positions 8-9 take two consecutive days to exit
    assert on.tolist() == [False, False, False, False, True, True,
                           True, True, True, False, False, False]


def test_run_length_counts_consecutive_days():
    flags = np.array([0, 1, 1, 1, 0, 1], dtype=bool)
    assert RB.run_length(flags).tolist() == [0, 1, 2, 3, 0, 1]


# ------------------------------------------------------------------ synthetic data
def _synthetic_pivot(n=400, spike_at=None, spike_len=0, abstain_at=None,
                     abstain_len=0):
    """Small constant baseline gap between OLS and Ridge/Lasso; the spike range blows
    up Ridge and Lasso together; the abstain range zeroes Lasso's sys and exo
    exactly."""
    dates = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(7)
    y = rng.normal(0, 30e-4, n)
    data = {}
    for m in ("ols", "ridge", "lasso"):
        shift = {"ols": 0.0, "ridge": 1e-4, "lasso": 2e-4}[m]
        sys = y * 0.5 + shift
        exo = y * 0.2 - shift
        if m in ("ridge", "lasso") and spike_at is not None:
            sl = slice(spike_at, spike_at + spike_len)
            sys = sys.copy()
            sys[sl] += 200e-4          # huge divergence, guaranteed over the q95 line
        if m == "lasso" and abstain_at is not None:
            sl = slice(abstain_at, abstain_at + abstain_len)
            sys, exo = sys.copy(), exo.copy()
            sys[sl] = 0.0
            exo[sl] = 0.0
        res = y - sys - exo
        data[("systematic", m)] = sys
        data[("exogenous", m)] = exo
        data[("residual", m)] = res
        data[("y", m)] = y
    piv = pd.DataFrame(data, index=dates)
    piv.columns = pd.MultiIndex.from_tuples(piv.columns)
    return piv


def test_spike_enters_state_after_two_days_and_shows_both_chips():
    """State flips only on the 2nd consecutive day over the line; Ridge and Lasso over
    together show side by side, not merged."""
    piv = _synthetic_pivot(spike_at=350, spike_len=8)
    out = RB.compute_pair(piv)

    first_spike = piv.index[350]
    second = piv.index[351]
    assert not out.loc[first_spike, "on_ridge"]      # day one does not flip yet
    assert out.loc[second, "on_ridge"]               # day two enters
    assert out.loc[second, "on_lasso"]

    st = RB.state_at(out, second)
    assert st["available"] and not st["agree"]
    assert st["states"] == [RB.STATE_RIDGE, RB.STATE_LASSO]   # side by side
    assert st["d_ridge_n1"] > 1.0                    # spike divergence far above one typical residual
    assert st["d_ridge_n2"] is not None              # N2 stored, not displayed


def test_quiet_day_reads_agree_with_numbers_attached():
    piv = _synthetic_pivot()
    st = RB.state_at(RB.compute_pair(piv))
    assert st["available"] and st["agree"] and st["states"] == []
    assert st["d_ridge_n1"] is not None and st["d_lasso_n1"] is not None
    assert st["abstain_run_days"] == 0


def test_abstain_is_its_own_state_with_run_counter():
    """Abstain is its own state with a run counter: single-day and consecutive
    abstains are two different facts (§12.4)."""
    piv = _synthetic_pivot(abstain_at=380, abstain_len=25)   # covers through the last day
    out = RB.compute_pair(piv)
    st = RB.state_at(out)
    assert st["abstain"] is True
    assert RB.STATE_ABSTAIN in st["states"]
    assert st["abstain_run_days"] == 400 - 380       # 20 consecutive abstain days as of the last day


def test_abstain_days_are_excluded_from_the_lasso_quantile_sample():
    """An abstain day's constructed distance must not define other days' thresholds.

    Controlled experiment: same data, one run with a stretch of abstain days (huge
    constructed distances) versus one without; the Lasso threshold on non-abstain days
    must not be pushed up by that stretch."""
    clean = RB.compute_pair(_synthetic_pivot())
    with_abstain = RB.compute_pair(_synthetic_pivot(abstain_at=300, abstain_len=40))
    last = clean.index[-1]
    assert with_abstain.loc[last, "thr_lasso"] == pytest.approx(
        clean.loc[last, "thr_lasso"], rel=0.05)


def test_insufficient_data_is_unavailable_not_fake_agreement():
    """Insufficient data reports available=False. Not having looked is not the same as
    having looked and agreed."""
    piv = _synthetic_pivot(n=100)                    # below MIN_PERIODS
    assert RB.state_at(RB.compute_pair(piv)) == {"available": False}
    assert RB.state_at(None) == {"available": False}


def test_pair_pivot_requires_all_three_models():
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02"] * 2),
        "pair": ["USDEUR"] * 2,
        "window": [126] * 2,
        "model": ["ols", "lasso"],                   # ridge missing
        "systematic": [0.1, 0.1], "exogenous": [0.0, 0.0],
        "residual": [0.0, 0.0], "y": [0.1, 0.1],
    })
    assert RB.pair_pivot(frame, "USDEUR") is None
    assert RB.state_for_pair(frame, "USDEUR") == {"available": False}


# ------------------------------------------------------------------ fact set
def test_fact_carries_neutral_robustness_lines():
    """Fact-set form (§12.6): state labels + the two N1 numbers + abstain run count,
    stated neutrally; the numbers enter the rendered whitelist before the body text is
    allowed to write them."""
    from fxdash.narrative import compose as C
    from fxdash.narrative.trigger import Fact

    fact = Fact(pair="USDJPY", date="2026-08-03", window=126,
                y_bp=-163.7, residual_bp=-156.8, residual_z=-4.17,
                systematic_bp=-12.4, exogenous_bp=5.5,
                r2_full=0.38, r2_exog=0.20, top_factor="DOLLAR_LOO",
                robustness={
                    "available": True, "agree": False,
                    "states": ["ridge_diverge", "lasso_abstain"],
                    "d_ridge_n1": 1.42, "d_lasso_n1": 0.31,
                    "d_ridge_n2": 1.1, "d_lasso_n2": 0.2,
                    "abstain": True, "abstain_run_days": 12,
                })
    table = C.fact_table(fact)
    assert "estimator agreement" in table
    assert "Ridge reads this day differently from OLS" in table
    assert "Lasso selected no factors this day" in table
    assert "1.42" in table and "0.31" in table and "12" in table
    # neutral: no judgemental wording allowed
    assert "unreliable" not in table.lower() and "distrust" not in table.lower()
    allowed = fact.allowed_numbers()
    assert {"1.42", "0.31", "12"} <= allowed
    # N2 goes to the artifacts (to_dict) but not into the prompt table
    assert fact.to_dict()["robustness"]["d_ridge_n2"] == 1.1
    assert "1.10" not in table
