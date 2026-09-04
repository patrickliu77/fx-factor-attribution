"""Rolling window driver.

Window is [t-w, t-1], strictly up to the previous day: the beta for day t must never
see day-t data — the line the no_lookahead test guards. Standardization acts on X only,
using in-window mean and std; penalized-regression coefficients are scaled back to
original units by the window std before they may enter attribution (SPEC 4.2).

r2_full is the all-factor window R²; r2_exog is the window R² from a complete rerun of
each model's own pipeline after dropping DOLLAR_LOO and CARRY_LOO. r2_exog is for
monitoring and reporting only — it enters neither attribution nor any model or
hyperparameter selection (2026-08-27 ruling 1).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import LAMBDA_REFIT_EVERY, exogenous_factors
from .lasso import solve_lasso
from .ols import solve_ols
from .ridge import solve_ridge

ROLLING_SOLVERS = {"ols": solve_ols, "ridge": solve_ridge, "lasso": solve_lasso}


@dataclass
class RollingResult:
    dates: pd.DatetimeIndex
    factors: list[str]
    betas: np.ndarray  # (n_days, n_factors), original units
    selected: np.ndarray  # (n_days, n_factors), bool
    lam: np.ndarray  # (n_days,), nan for OLS
    r2_full: np.ndarray
    r2_exog: np.ndarray


def standardize(window: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return in-window mean and std. Zero-variance columns get std 1 to avoid /0."""
    mu = window.mean(axis=0)
    sigma = window.std(axis=0)
    sigma = np.where(sigma > 0, sigma, 1.0)
    return mu, sigma


def _window_r2(z: np.ndarray, yc: np.ndarray, beta_std: np.ndarray) -> float:
    ss_tot = float(yc @ yc)
    if ss_tot <= 0:
        return float("nan")
    residual = yc - z @ beta_std
    return 1.0 - float(residual @ residual) / ss_tot


def _fit_once(
    x_window: np.ndarray,
    y_window: np.ndarray,
    solver,
    state: dict,
    refit: bool,
) -> tuple[np.ndarray, np.ndarray, float | None, float]:
    mu, sigma = standardize(x_window)
    z = (x_window - mu) / sigma
    yc = y_window - y_window.mean()

    out = solver(z, yc, state, refit)
    beta_std = np.asarray(out["beta_std"], dtype=float)
    # only after scaling back to original units may the beta enter attribution
    beta = beta_std / sigma
    return beta, np.asarray(out["selected"], dtype=bool), out["lam"], _window_r2(
        z, yc, beta_std
    )


def rolling_fit(
    panel: pd.DataFrame, pair: str, window: int, model: str, factors: list[str]
) -> RollingResult:
    """Daily refit. The returned beta for day t, estimated up to t-1, is what day-t
    attribution uses."""
    solver = ROLLING_SOLVERS[model]
    exog = exogenous_factors(factors)
    exog_idx = [factors.index(name) for name in exog]

    x_all = panel[factors].to_numpy(dtype=float)
    y_all = panel["y"].to_numpy(dtype=float)
    n_days, n_factors = x_all.shape
    if n_days <= window:
        empty = np.empty((0, n_factors))
        return RollingResult(
            dates=panel.index[:0],
            factors=list(factors),
            betas=empty,
            selected=empty.astype(bool),
            lam=np.empty(0),
            r2_full=np.empty(0),
            r2_exog=np.empty(0),
        )

    positions = range(window, n_days)
    betas = np.full((len(positions), n_factors), np.nan)
    selected = np.zeros((len(positions), n_factors), dtype=bool)
    lams = np.full(len(positions), np.nan)
    r2_full = np.full(len(positions), np.nan)
    r2_exog = np.full(len(positions), np.nan)

    full_state = {"tag": f"{pair}/w{window}/{model}/full"}
    exog_state = {"tag": f"{pair}/w{window}/{model}/exog"}

    for step, t in enumerate(positions):
        lo = t - window
        x_window, y_window = x_all[lo:t], y_all[lo:t]  # strictly up to t-1
        refit = step % LAMBDA_REFIT_EVERY == 0

        beta, sel, lam, r2 = _fit_once(x_window, y_window, solver, full_state, refit)
        betas[step] = beta
        selected[step] = sel
        lams[step] = np.nan if lam is None else lam
        r2_full[step] = r2

        # exog subset reruns each model's own pipeline in full; take R² only, no betas
        if exog_idx:
            _, _, _, r2e = _fit_once(
                x_window[:, exog_idx], y_window, solver, exog_state, refit
            )
            r2_exog[step] = r2e

    return RollingResult(
        dates=panel.index[window:],
        factors=list(factors),
        betas=betas,
        selected=selected,
        lam=lams,
        r2_full=r2_full,
        r2_exog=r2_exog,
    )
