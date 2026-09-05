"""Ridge: addresses beta instability.

λ is selected on a 25-point log grid from 10^-4 to 10^4 via in-window TimeSeriesSplit
(3 folds), reselected every 21 trading days and reused in between; when λ lands on a
grid boundary the grid is expanded, λ reselected, and a log entry recorded (SPEC 4.4).

Closed-form solution beta = (Z'Z + λI)^-1 Z'y. At the 8-factor scale this is an order
of magnitude faster than calling sklearn, and each fold's Z'Z and Z'y need computing
only once to sweep the entire λ grid.
"""

from __future__ import annotations

import numpy as np

from ..config import CV_SPLITS, LAMBDA_GRID_LOG10, LAMBDA_GRID_POINTS
from ..data.base import record
from .validation import prepare_fold


def default_grid() -> np.ndarray:
    return np.logspace(*LAMBDA_GRID_LOG10, LAMBDA_GRID_POINTS)


def _ridge_beta(z: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    gram = z.T @ z
    gram.flat[:: gram.shape[0] + 1] += lam
    return np.linalg.solve(gram, z.T @ y)


def time_series_splits(n: int, n_splits: int = CV_SPLITS):
    """Forward-chaining splits, shuffle forbidden (CLAUDE.md 16)."""
    fold = n // (n_splits + 1)
    if fold < 2:
        return []
    return [
        (slice(0, fold * (k + 1)), slice(fold * (k + 1), fold * (k + 2)))
        for k in range(n_splits)
    ]


def _cv_error(z: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    splits = time_series_splits(len(y))
    if not splits:
        return np.zeros(len(grid))
    errors = np.zeros(len(grid))
    for train, test in splits:
        zt, yt, zv, yv = prepare_fold(z, y, train, test)
        if len(yv) == 0:
            continue
        gram = zt.T @ zt
        rhs = zt.T @ yt
        for i, lam in enumerate(grid):
            reg = gram.copy()
            reg.flat[:: reg.shape[0] + 1] += lam
            beta = np.linalg.solve(reg, rhs)
            errors[i] += float(np.mean((yv - zv @ beta) ** 2))
    return errors


def select_lambda(z: np.ndarray, y: np.ndarray, tag: str) -> float:
    """Select λ; on hitting a grid boundary, expand two decades that way, reselect, log."""
    grid = default_grid()
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
            "ridge_lambda_boundary",
            tag=tag,
            hit="low" if best == 0 else "high",
            new_grid=[low, high],
        )
        grid = np.logspace(low, high, LAMBDA_GRID_POINTS)
    return float(grid[int(np.argmin(_cv_error(z, y, grid)))])


def solve_ridge(z: np.ndarray, y: np.ndarray, state: dict, refit: bool,
                *, cv_data=None) -> dict:
    if refit or state.get("lam") is None:
        x_cv, y_cv = cv_data if cv_data is not None else (z, y)
        state["lam"] = select_lambda(x_cv, y_cv, state.get("tag", "ridge"))
    lam = state["lam"]
    return {
        "beta_std": _ridge_beta(z, y, lam),
        "selected": np.ones(z.shape[1], dtype=bool),
        "lam": lam,
    }
