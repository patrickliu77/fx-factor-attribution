"""Attribution engine.

The core formula is one line: a factor's contribution on a day equals beta times that
factor's move that day, with beta estimated on the rolling window up to the previous
day. residual equals the day's return minus the sum of all contributions; the intercept
is folded into residual, so the identity holds by construction — which is exactly why
it can serve as a correctness check.

Three-bucket output: systematic (DOLLAR_LOO and CARRY_LOO), exogenous (the remaining
factors), residual (SPEC 5.2). The first bucket measures what share of this pair's move
is systematic; only the second is the explanatory power of exogenous economic factors.
The two must not be reported mixed together.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import FX_INTERNAL_FACTORS, RESIDUAL_Z_WINDOW
from ..factors.build import factor_stale_names


@dataclass
class Attribution:
    dates: pd.DatetimeIndex
    factors: list[str]
    betas: np.ndarray
    contributions: np.ndarray
    selected: np.ndarray
    lam: np.ndarray
    r2_full: np.ndarray
    r2_exog: np.ndarray
    y: np.ndarray
    residual: np.ndarray
    residual_z: np.ndarray
    systematic: np.ndarray
    exogenous: np.ndarray
    stale_flags: list[list[str]]
    # the row's inputs include carried-forward values from publication-lag sources;
    # once official data arrives it may and must be recomputed and overwritten
    provisional: np.ndarray


def _residual_z(residual: np.ndarray, index: pd.DatetimeIndex) -> np.ndarray:
    """Standardize by the 126-day rolling std up to t-1; reserved for the Phase 3
    trigger."""
    series = pd.Series(residual, index=index)
    scale = series.rolling(RESIDUAL_Z_WINDOW).std().shift(1)
    return (series / scale).to_numpy()


def _stale_lists(panel: pd.DataFrame, factors: list[str]) -> list[list[str]]:
    names: list[str] = []
    for factor in factors:
        names.extend(n for n in factor_stale_names(factor) if f"stale::{n}" in panel)
    if not names:
        return [[] for _ in range(len(panel))]
    block = panel[[f"stale::{n}" for n in names]].to_numpy(dtype=bool)
    return [[names[j] for j in np.flatnonzero(row)] for row in block]


def attribute(panel: pd.DataFrame, rolling) -> Attribution:
    """Multiply rolling betas by the day's factor moves: per-factor contributions and
    residual."""
    factors = rolling.factors
    aligned = panel.loc[rolling.dates]
    x = aligned[factors].to_numpy(dtype=float)
    y = aligned["y"].to_numpy(dtype=float)

    contributions = rolling.betas * x
    residual = y - np.nansum(contributions, axis=1)

    internal = [factors.index(f) for f in FX_INTERNAL_FACTORS if f in factors]
    exog = [i for i in range(len(factors)) if i not in internal]
    systematic = contributions[:, internal].sum(axis=1) if internal else np.zeros(len(y))
    exogenous = contributions[:, exog].sum(axis=1) if exog else np.zeros(len(y))

    return Attribution(
        dates=rolling.dates,
        factors=factors,
        betas=rolling.betas,
        contributions=contributions,
        selected=rolling.selected,
        lam=rolling.lam,
        r2_full=rolling.r2_full,
        r2_exog=rolling.r2_exog,
        y=y,
        residual=residual,
        residual_z=_residual_z(residual, rolling.dates),
        systematic=systematic,
        exogenous=exogenous,
        stale_flags=_stale_lists(aligned, factors),
        provisional=(
            aligned["provisional"].to_numpy(dtype=bool)
            if "provisional" in aligned
            else np.zeros(len(y), dtype=bool)
        ),
    )


def identity_error(result: Attribution) -> float:
    """Identity closure error, for end-to-end tests."""
    total = result.contributions.sum(axis=1) + result.residual
    return float(np.max(np.abs(total - result.y))) if len(result.y) else 0.0
