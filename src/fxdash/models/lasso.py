"""Lasso: variable selection only.

Attribution coefficients come from a post Lasso OLS refit on the selected set
(SPEC 4.5 / CLAUDE.md 17): Lasso coefficients are shrunken and biased, and using them
directly for contributions would systematically understate them. An empty selection
degenerates to intercept-only; the day goes fully into residual and a warning is
recorded.

λ selection matches Ridge: same log grid, same TimeSeriesSplit, same reselection every
21 trading days, same grid expansion at the boundary.
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import Lasso

from ..config import LAMBDA_GRID_LOG10, LAMBDA_GRID_POINTS
from ..data.base import record
from .ridge import time_series_splits
from .validation import prepare_fold

_LASSO = Lasso(fit_intercept=False, max_iter=20000, tol=1e-7, warm_start=False)


def _lasso_beta(z: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    _LASSO.set_params(alpha=lam)
    _LASSO.fit(z, y)
    return np.asarray(_LASSO.coef_, dtype=float)


def _cv_error(z: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    splits = time_series_splits(len(y))
    if not splits:
        return np.zeros(len(grid))
    errors = np.zeros(len(grid))
    for train, test in splits:
        zt, yt, zv, yv = prepare_fold(z, y, train, test)
        if len(yv) == 0:
            continue
        for i, lam in enumerate(grid):
            beta = _lasso_beta(zt, yt, lam)
            errors[i] += float(np.mean((yv - zv @ beta) ** 2))
    return errors


def select_lambda(z: np.ndarray, y: np.ndarray, tag: str) -> float:
    grid = np.logspace(*LAMBDA_GRID_LOG10, LAMBDA_GRID_POINTS)
    for attempt in range(3):
        errors = _cv_error(z, y, grid)
        best = int(np.argmin(errors))
        if 0 < best < len(grid) - 1 or attempt == 2:
            return float(grid[best])
        low, high = np.log10(grid[0]), np.log10(grid[-1])
        if best == 0:
            low -= 2.0
        else:
            high += 2.0
        record(
            "lasso_lambda_boundary",
            tag=tag,
            hit="low" if best == 0 else "high",
            new_grid=[low, high],
        )
        grid = np.logspace(low, high, LAMBDA_GRID_POINTS)
    return float(grid[int(np.argmin(_cv_error(z, y, grid)))])


def solve_lasso(z: np.ndarray, y: np.ndarray, state: dict, refit: bool,
                *, cv_data=None) -> dict:
    if refit or state.get("lam") is None:
        x_cv, y_cv = cv_data if cv_data is not None else (z, y)
        state["lam"] = select_lambda(x_cv, y_cv, state.get("tag", "lasso"))
    lam = state["lam"]

    selected = np.abs(_lasso_beta(z, y, lam)) > 0
    beta = np.zeros(z.shape[1])
    if selected.any():
        # post Lasso OLS refit: rerun unpenalized regression on the selected columns only
        beta[selected], *_ = np.linalg.lstsq(z[:, selected], y, rcond=None)
    else:
        state["empty_selections"] = state.get("empty_selections", 0) + 1
    return {"beta_std": beta, "selected": selected, "lam": lam}
