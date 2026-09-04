"""Three-scale attribution sums (SPEC_web §0 iron rule 3: the only new math is
summation).

Yesterday / past week / past month = per-key sums of contributions / y /
residual / systematic / exogenous over each pair's last 1 / 5 / 21
**trading-day rows** (not calendar days). The project plan, part five, verbatim: "weekly
and monthly attribution is just the daily contribution summed along time."
Nothing else gets computed.

The attribution identity y = sum(contributions) + residual holds daily, and
after per-key summation it holds automatically over the window -- which is
exactly why it can serve as a test anchor.
"""

from __future__ import annotations

import numpy as np

from .store import Combo, SYSTEMATIC_FACTORS, clean

SCALES = {"1d": 1, "5d": 5, "21d": 21}


def combo_scale(combo: Combo, n_days: int) -> dict:
    """Summed block over a combo's last n_days trading days. Fewer rows than
    asked degrade to what exists, no error."""
    n = min(n_days, len(combo.dates))
    if n == 0:
        return {"n_days": 0}
    lo = len(combo.dates) - n

    contributions = {
        f: clean(np.nansum(series[lo:])) for f, series in combo.contributions.items()
    }
    provisional_dates = [
        combo.dates[i] for i in range(lo, len(combo.dates)) if combo.provisional[i]
    ]
    return {
        "n_days": n,
        "start": combo.dates[lo],
        "end": combo.dates[-1],
        "y": clean(np.nansum(combo.y[lo:])),
        "residual": clean(np.nansum(combo.residual[lo:])),
        "systematic": clean(np.nansum(combo.systematic[lo:])),
        "exogenous": clean(np.nansum(combo.exogenous[lo:])),
        "contributions": contributions,
        "contains_provisional": bool(provisional_dates),
        "provisional_dates": provisional_dates,
    }


def pair_scales(combo: Combo) -> dict:
    """Three-scale blocks for one pair."""
    return {name: combo_scale(combo, n) for name, n in SCALES.items()}


def latest_row(combo: Combo) -> dict:
    """Raw values of the combo's last row, zero processing -- for the overview
    cards."""
    if not combo.dates:
        return {}
    i = len(combo.dates) - 1
    return {
        "pair": combo.pair,
        "date": combo.dates[i],
        "y": clean(combo.y[i]),
        "residual": clean(combo.residual[i]),
        "residual_z": clean(combo.residual_z[i]),
        "r2_full": clean(combo.r2_full[i]),
        "r2_exog": clean(combo.r2_exog[i]),
        "systematic": clean(combo.systematic[i]),
        "exogenous": clean(combo.exogenous[i]),
        "provisional": bool(combo.provisional[i]),
        "contributions": {
            f: clean(series[i]) for f, series in combo.contributions.items()
        },
        "stale_flags": next(
            (e["flags"] for e in reversed(combo.stale_events)
             if e["date"] == combo.dates[i]),
            [],
        ),
        "top_factor": _top_factor(combo, i),
    }


def _top_factor(combo: Combo, i: int) -> str | None:
    best, best_abs = None, 0.0
    for f, series in combo.contributions.items():
        v = series[i]
        if np.isfinite(v) and abs(v) > best_abs:
            best, best_abs = f, abs(v)
    return best


def systematic_split(factors: list[str]) -> tuple[list[str], list[str]]:
    """Split factor names into systematic (DOLLAR_LOO/CARRY_LOO) and exogenous,
    for the frontend legend."""
    sys_f = [f for f in factors if f in SYSTEMATIC_FACTORS]
    exo_f = [f for f in factors if f not in SYSTEMATIC_FACTORS]
    return sys_f, exo_f
