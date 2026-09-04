"""Robustness check (SPEC_phase3 §12, parameters approved 2026-09-03).

Division of labour among the three checks: the health check watches the level of
explanatory power, the benchmark comparison watches consistency with the historical
baseline, **this module watches whether the same attribution holds up across
estimators**. It is not called cross-validation.

Metric: on the canonical window, three-bucket (systematic / exogenous / residual) L1
distances of OLS vs Ridge and OLS vs post-Lasso, **computed separately**, in absolute
bp space; normalization N1 = divide by the pair's median |residual|(OLS) over the last
252 trading days (shift 1). N2 (divide by the recent median |y|) is computed and stored
in the artifacts but not displayed — a zero-cost switch if N1 masks a divergence
(§12.2 records N1's coupling).

State machine (parameters approved 2026-09-03): per pair, the line is the q=0.95
quantile on a 252-day rolling window (shift 1); **entering a state takes 2 consecutive
days over the line, exiting takes 2 consecutive days back under it**. Four states:
three-way agreement / Ridge divergence / Lasso reselect / Lasso abstain; when Ridge and
Lasso cross the line simultaneously they show side by side, not merged. **Lasso
abstain** (all-zero beta, the whole day into residual) is its own class: excluded from
the Lasso distance quantile sample (that day's distance is constructed, not two
estimators' different readings), but displayed as usual, with "how many trading days
the current abstain run has lasted": a single-day abstain is that day's choice, while
50 consecutive abstain days mean the factor set failed wholesale in that period — the
two facts must be distinguishable (§12.4, measured on JPY's three long abstain runs,
2011-08 to 2012-05).

**Badge states are not alerts**: they do not enter the status colour; the 5 to 8%
target zone is defined on the union of distance states, and the abstain overlay does
not consume that budget (abstain frequency is a model fact, not a tunable knob).

This module holds only pure functions, with two consumers: the web snapshot builder
(current state display) and the narrative fact set (states on historical dates, neutral
statements, judgement left to the model).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DEFAULT_WINDOW

CANONICAL_WINDOW = DEFAULT_WINDOW   # 126; robustness is defined on the canonical convention only
QUANTILE = 0.95                     # per-pair rolling quantile (approved 2026-09-03)
PERSIST_DAYS = 2                    # consecutive days needed to enter and to exit a state
ROLL_WINDOW = 252                   # same window as the health check
MIN_PERIODS = 126
N_FMT = "{:.2f}"                    # N1/N2 formatted once, shared by prompt and validation

STATE_RIDGE = "ridge_diverge"
STATE_LASSO = "lasso_reselect"
STATE_ABSTAIN = "lasso_abstain"
MODELS_NEEDED = ("ols", "ridge", "lasso")


def hysteresis(hot: np.ndarray, k: int = PERSIST_DAYS) -> np.ndarray:
    """Enter only after k consecutive days over the line, exit only after k
    consecutive days back under. Filters single-day blips."""
    on = np.zeros(len(hot), dtype=bool)
    state, run = False, 0
    for i, h in enumerate(hot):
        if bool(h) == state:
            run = 0
        else:
            run += 1
            if run >= k:
                state, run = not state, 0
        on[i] = state
    return on


def run_length(flags: np.ndarray) -> np.ndarray:
    """Consecutive True days up to the current day (0 if the day is False). For
    abstain run counting."""
    out = np.zeros(len(flags), dtype=int)
    run = 0
    for i, v in enumerate(flags):
        run = run + 1 if v else 0
        out[i] = run
    return out


def pair_pivot(frame: pd.DataFrame, pair: str,
               window: int = CANONICAL_WINDOW) -> pd.DataFrame | None:
    """Three-model pivot block for one pair on the canonical window, from the contract
    long table.

    frame needs date/pair/window/model/systematic/exogenous/residual/y.
    Returns None if any model is missing: robustness is a three-way comparison and two
    parties cannot make one.
    """
    block = frame[(frame["pair"] == pair) & (frame["window"] == window)]
    if set(block["model"].unique()) < set(MODELS_NEEDED):
        return None
    piv = block.pivot_table(index="date", columns="model",
                            values=["systematic", "exogenous", "residual", "y"])
    return piv.sort_index()


def compute_pair(piv: pd.DataFrame) -> pd.DataFrame | None:
    """Daily distances, normalization, thresholds and states. Input is pair_pivot's
    result."""
    if piv is None or len(piv) <= MIN_PERIODS:
        return None

    def l1(m):
        return (
            (piv[("systematic", "ols")] - piv[("systematic", m)]).abs()
            + (piv[("exogenous", "ols")] - piv[("exogenous", m)]).abs()
            + (piv[("residual", "ols")] - piv[("residual", m)]).abs()
        ) * 1e4

    out = pd.DataFrame(index=piv.index)
    out["d_ridge_bp"] = l1("ridge")
    out["d_lasso_bp"] = l1("lasso")
    out["abstain"] = ((piv[("systematic", "lasso")] == 0.0)
                      & (piv[("exogenous", "lasso")] == 0.0))

    n1 = (piv[("residual", "ols")].abs() * 1e4).rolling(
        ROLL_WINDOW, min_periods=MIN_PERIODS).median().shift(1)
    n2 = (piv[("y", "ols")].abs() * 1e4).rolling(
        ROLL_WINDOW, min_periods=MIN_PERIODS).median().shift(1)
    for comp in ("ridge", "lasso"):
        out[f"d_{comp}_n1"] = out[f"d_{comp}_bp"] / n1
        out[f"d_{comp}_n2"] = out[f"d_{comp}_bp"] / n2

    thr_r = out["d_ridge_n1"].rolling(
        ROLL_WINDOW, min_periods=MIN_PERIODS).quantile(QUANTILE).shift(1)
    # abstain days are excluded from the Lasso quantile sample: constructed distances
    # must not define other days' thresholds
    lasso_clean = out["d_lasso_n1"].where(~out["abstain"])
    thr_l = lasso_clean.rolling(
        ROLL_WINDOW, min_periods=MIN_PERIODS).quantile(QUANTILE).shift(1)
    out["thr_ridge"] = thr_r
    out["thr_lasso"] = thr_l

    out["on_ridge"] = hysteresis(
        (out["d_ridge_n1"] > thr_r).fillna(False).to_numpy())
    out["on_lasso"] = hysteresis(
        ((out["d_lasso_n1"] > thr_l) & ~out["abstain"]).fillna(False).to_numpy())
    out["abstain_run"] = run_length(out["abstain"].to_numpy())
    return out


def _round(value) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value), 2)


def state_at(computed: pd.DataFrame | None, date=None) -> dict:
    """State snapshot for one day (default: the last day). Neutral facts, no
    judgement.

    `available` is False when: the three models are not all present, history is
    insufficient, or the day's threshold is still NaN. Insufficient data does not
    masquerade as "three-way agreement": not having looked is not the same as having
    looked and agreed.
    """
    if computed is None or computed.empty:
        return {"available": False}
    idx = computed.index[-1] if date is None else pd.Timestamp(date)
    if idx not in computed.index:
        return {"available": False}
    row = computed.loc[idx]
    if not np.isfinite(row["thr_ridge"]) or not np.isfinite(row["thr_lasso"]):
        return {"available": False}

    states = []
    if bool(row["on_ridge"]):
        states.append(STATE_RIDGE)
    if bool(row["on_lasso"]):
        states.append(STATE_LASSO)
    if bool(row["abstain"]):
        states.append(STATE_ABSTAIN)
    return {
        "available": True,
        "date": idx.strftime("%Y-%m-%d"),
        "states": states,                       # empty list = three-way agreement; no merging
        "agree": not states,
        "d_ridge_n1": _round(row["d_ridge_n1"]),
        "d_lasso_n1": _round(row["d_lasso_n1"]),
        "d_ridge_n2": _round(row["d_ridge_n2"]),   # stored, not displayed (§12.2)
        "d_lasso_n2": _round(row["d_lasso_n2"]),
        "abstain": bool(row["abstain"]),
        "abstain_run_days": int(row["abstain_run"]),
    }


def state_for_pair(frame: pd.DataFrame, pair: str, date=None,
                   window: int = CANONICAL_WINDOW) -> dict:
    """Contract long table -> one pair's state on one day. The narrative layer uses
    this entry point."""
    return state_at(compute_pair(pair_pivot(frame, pair, window)), date)
