"""Matched-arm rolling attribution experiments; no production writes or forecasts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import numpy as np
import pandas as pd

from .. import config
from ..models.rolling import rolling_fit


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    factors: tuple[str, ...]


def arms_for(pair):
    baseline = tuple(config.baseline_factors(pair))
    arms = [Arm(f"{model}_base", model, baseline) for model in config.MODELS]
    arms.append(Arm("lasso_expanded", "lasso", tuple(config.lasso_menu(pair))))
    swap = {"USDCAD": ("WTI", "BRENT"), "USDNOK": ("BRENT", "WTI")}.get(pair)
    if swap:
        old, new = swap
        factors = tuple(new if f == old else f for f in baseline)
        arms.extend(Arm(f"{model}_oil_alt", model, factors) for model in config.MODELS)
    if any(len(a.factors) > config.MAX_FACTORS_PER_PAIR for a in arms):
        raise ValueError("research arm exceeds the eight-factor cap")
    return arms


def comparisons_for(pair):
    values = [("estimator", "ols_base", "ridge_base"),
              ("estimator", "ols_base", "lasso_base"),
              ("menu", "lasso_base", "lasso_expanded")]
    if pair in ("USDCAD", "USDNOK"):
        values.extend(("oil", f"{m}_base", f"{m}_oil_alt") for m in config.MODELS)
    return values


def number(value):
    return float(value) if np.isfinite(value) else None


def fit_path(panel, pair, window, arm, *, fitted=None):
    """Pack an attribution path; optional research fit leaves the default unchanged."""
    fit = fitted if fitted is not None else rolling_fit(panel, pair, window, arm.model, list(arm.factors))
    if list(fit.factors) != list(arm.factors) or not fit.dates.equals(panel.index[window:]):
        raise ValueError("fitted research path has different factors or dates")
    current = panel.loc[fit.dates]
    x = current[list(arm.factors)].to_numpy(float)
    if not np.isfinite(fit.betas).all():
        raise ValueError(f"non-finite coefficients: {pair}/{arm.name}")
    contributions = fit.betas * x
    result = pd.DataFrame({
        "y": current.y, "residual": current.y.to_numpy()-contributions.sum(axis=1),
        "train_r2": fit.r2_full, "lambda": fit.lam,
        "provisional": current.provisional.to_numpy(bool),
        "position": panel.index.get_indexer(fit.dates),
    }, index=fit.dates)
    sigmas = panel[list(arm.factors)].rolling(window).std(ddof=0).shift(1).loc[fit.dates]
    for i, f in enumerate(arm.factors):
        result[f"beta::{f}"] = fit.betas[:, i]
        result[f"contribution::{f}"] = contributions[:, i]
        result[f"selected::{f}"] = fit.selected[:, i]
        result[f"sigma::{f}"] = sigmas[f]
    if not np.allclose(result.residual+contributions.sum(axis=1), result.y, atol=1e-12, rtol=0):
        raise ValueError("attribution identity failed")
    return result


def metrics(frame, arm):
    error, y = frame.residual.to_numpy(), frame.y.to_numpy()
    base_mse = np.mean(y*y)
    adjacent = frame.position.diff().eq(1).to_numpy()
    beta = frame[[f"beta::{f}" for f in arm.factors]].to_numpy()
    sigma = frame[[f"sigma::{f}" for f in arm.factors]].to_numpy()
    delta = np.diff(beta, axis=0)
    valid = adjacent[1:]
    scaled = (np.abs(delta)*sigma[:-1]).sum(axis=1)*1e4
    selection = None
    if arm.model == "lasso":
        selected = frame[[f"selected::{f}" for f in arm.factors]].to_numpy(bool)
        switches = np.any(selected[1:] != selected[:-1], axis=1)[valid]
        selection = {"empty_fraction": float(np.mean(~selected.any(axis=1))),
                     "switch_fraction": float(switches.mean()) if len(switches) else None,
                     "transitions": int(valid.sum()),
                     "frequency": {f: float(selected[:, i].mean()) for i, f in enumerate(arm.factors)}}
    return {
        "observations": len(frame), "start": str(frame.index[0].date()), "end": str(frame.index[-1].date()),
        "mae_bp": float(np.mean(np.abs(error))*1e4),
        "rmse_bp": float(np.sqrt(np.mean(error**2))*1e4),
        "residual_abs_p95_bp": float(np.quantile(np.abs(error), .95)*1e4),
        "zero_mae_bp": float(np.mean(np.abs(y))*1e4),
        "zero_rmse_bp": float(np.sqrt(base_mse)*1e4),
        "mse_relative_to_zero": float(np.mean(error**2)/base_mse) if base_mse > 0 else None,
        "mean_training_r2": number(frame.train_r2.mean()),
        "median_beta_step_scaled_bp": number(np.median(scaled[valid])) if valid.any() else None,
        "beta_change_by_factor": {f: number(np.median(np.abs(delta[valid, i]))) if valid.any() else None
                                  for i, f in enumerate(arm.factors)},
        "selection": selection,
    }


def moving_block_indices(n, block, repetitions, seed):
    if n < 2*block or block < 1 or repetitions < 1:
        raise ValueError("need at least two blocks and a positive repetition count")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n-block+1, size=(repetitions, math.ceil(n/block)))
    return (starts[:, :, None]+np.arange(block)).reshape(repetitions, -1)[:, :n]


def bootstrap_rmse_difference(reference, candidate, *, block, repetitions, seed):
    if block < 1 or repetitions < 1:
        raise ValueError("positive block and repetition count required")
    a, b = np.asarray(reference, float), np.asarray(candidate, float)
    if a.shape != b.shape or a.ndim != 1 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("paired finite one-dimensional errors required")
    if len(a) < 2*block:
        return {"block": block, "available": False, "reason": "fewer_than_two_blocks"}
    indices = moving_block_indices(len(a), block, repetitions, seed)
    differences = (np.sqrt(np.mean(b[indices]**2, axis=1))-
                   np.sqrt(np.mean(a[indices]**2, axis=1)))*1e4
    low, high = np.quantile(differences, [.025, .975])
    return {"block": block, "available": True, "repetitions": repetitions, "seed": seed,
            "low_bp": float(low), "high_bp": float(high)}


def allocation(frame, collapse_oil):
    values = {}
    for column in frame:
        if not column.startswith("contribution::"):
            continue
        factor = column.split("::", 1)[1]
        key = "OIL" if collapse_oil and factor in ("WTI", "BRENT") else factor
        values[key] = values.get(key, 0)+frame[column].to_numpy()
    return values


def paired_metrics(reference, candidate, *, family, blocks, repetitions, seed):
    if not reference.index.equals(candidate.index) or not np.allclose(reference.y, candidate.y, atol=1e-12, rtol=0):
        raise ValueError("paired arms must have identical dates and realised targets")
    a, b = reference.residual.to_numpy(), candidate.residual.to_numpy()
    left, right = allocation(reference, family == "oil"), allocation(candidate, family == "oil")
    # Stable reduction order also makes JSON identical across PYTHONHASHSEED values.
    distance = sum(np.abs(left.get(f, 0)-right.get(f, 0)) for f in sorted(left.keys() | right.keys()))
    return {"observations": len(a), "start": str(reference.index[0].date()), "end": str(reference.index[-1].date()),
            "rmse_change_bp": float((np.sqrt(np.mean(b*b))-np.sqrt(np.mean(a*a)))*1e4),
            "mae_change_bp": float((np.mean(np.abs(b))-np.mean(np.abs(a)))*1e4),
            "allocation_l1_bp": float(np.mean(distance)*1e4),
            "allocation_basis": "oil_collapsed_to_OIL" if family == "oil" else "individual_factor_names",
            "intervals": [bootstrap_rmse_difference(a,b,block=block,repetitions=repetitions,seed=seed)
                          for block in blocks]}


def evaluate_pair(panel, pair, *, start, end, windows=(126,), blocks=(5,21,63), repetitions=1000, seed=20260907, progress=None):
    arms = arms_for(pair)
    factors = sorted({f for a in arms for f in a.factors})
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if any(pd.isna(d) or d.tz is not None or d != d.normalize() for d in (start,end)):
        raise ValueError("start and end must be timezone-naive dates")
    if start > end or not windows or any(w not in config.WINDOWS for w in windows) or len(set(windows)) != len(windows):
        raise ValueError("invalid date range or windows")
    if not blocks or any(b < 1 for b in blocks) or repetitions < 1:
        raise ValueError("invalid bootstrap settings")
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.tz is not None or not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise ValueError("panel dates must be unique, ascending and timezone-naive")
    if not {"y", "provisional", *factors}.issubset(panel):
        raise ValueError("panel lacks required experiment inputs or provisional flag")
    if panel.provisional.isna().any() or not pd.api.types.is_bool_dtype(panel.provisional.dtype):
        raise ValueError("provisional must contain explicit boolean flags")
    panel = panel.loc[:end].copy()
    if panel.empty or not np.isfinite(panel[["y", *factors]].to_numpy(float)).all():
        raise ValueError("panel must be nonempty and jointly finite before any arm is fitted")
    first = int(panel.index.searchsorted(start))
    if first < max(windows) or first == len(panel):
        raise ValueError("insufficient common history or no evaluation dates")
    # Fix one common warm-up and refit phase for all arms, including menu/oil swaps.
    panel = panel.iloc[first-max(windows):].copy()
    target_dates = panel.index[(panel.index >= start) & ~panel.provisional.to_numpy(bool)]
    if not len(target_dates):
        raise ValueError("no final evaluation observations")
    period_dates = {"all": target_dates}
    period_dates.update({str(year): target_dates[target_dates.year == year] for year in sorted(set(target_dates.year))})
    summaries, comparisons, paths = [], [], {}
    for window in windows:
        fitted = {}
        for arm in arms:
            if progress:
                progress(f"{pair} / {window} / {arm.name}")
            path = fit_path(panel, pair, window, arm)
            if not target_dates.isin(path.index).all():
                raise ValueError("an arm did not cover the common evaluation dates")
            fitted[arm.name] = path
            paths[f"w{window}_{arm.name}"] = path
            for period, dates in period_dates.items():
                summaries.append({"pair": pair, "window": window, "arm": arm.name,
                                  "model": arm.model, "factors": list(arm.factors), "period": period,
                                  **metrics(path.loc[dates], arm)})
        for family, reference, candidate in comparisons_for(pair):
            for period, dates in period_dates.items():
                key = f"{pair}/{window}/{reference}/{candidate}/{period}/{seed}"
                stable_seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")
                comparisons.append({"pair": pair, "window": window, "family": family,
                                    "reference": reference, "candidate": candidate, "period": period,
                                    **paired_metrics(fitted[reference].loc[dates], fitted[candidate].loc[dates],
                                                     family=family,blocks=blocks,repetitions=repetitions,seed=stable_seed)})
    audit = {"pair": pair, "training_anchor": str(panel.index[0].date()),
             "evaluation_first": str(target_dates[0].date()), "evaluation_last": str(target_dates[-1].date()),
             "eligible_observations": len(target_dates),
             "excluded_provisional": int(panel.loc[panel.index >= start, "provisional"].sum()),
             "arm_count": len(arms), "checked_columns": factors,
             "phase_note": "Same warm-up start across arms; lambda follows each window's 21-observation phase."}
    return {"summaries": summaries, "comparisons": comparisons, "audit": audit}, paths
