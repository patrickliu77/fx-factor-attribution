"""Matched-sample diagnostics of saved attribution, without fitting any model.

Betas were estimated through t-1, but contributions use realised X_t. Residual
error therefore evaluates contemporaneous reconstruction, not an FX forecast.
"""
from __future__ import annotations

import numpy as np

from .store import clean


def compare_pair(snapshot, pair: str, window: int) -> dict:
    models = ("ols", "ridge", "lasso")
    combos = {m: snapshot.combo(pair, window, m) for m in models}
    if any(c is None for c in combos.values()):
        return {"pair": pair, "available": False, "reason": "requires_three_models"}
    indices, counts = {}, {}
    for model, c in combos.items():
        valid = np.isfinite(c.y) & np.isfinite(c.residual) & ~c.provisional
        for values in c.contributions.values():
            valid &= np.isfinite(values)
        indices[model] = {d: i for i, d in enumerate(c.dates) if valid[i]}
        counts[model] = {"saved": len(c.dates), "eligible": int(valid.sum()),
                         "provisional": int(c.provisional.sum())}
    dates = sorted(set.intersection(*(set(v) for v in indices.values())))
    if not dates:
        return {"pair": pair, "available": False, "reason": "no_matched_final_rows",
                "counts": counts}
    samples = {}
    for name, kept in (("all", dates), ("recent", dates[-252:])):
        ix = {m: np.array([indices[m][d] for d in kept]) for m in models}
        y = combos["ols"].y[ix["ols"]]
        if any(not np.allclose(y, combos[m].y[ix[m]], rtol=0, atol=1e-12)
               for m in models):
            raise ValueError("matched models disagree on realised returns")
        base_mse = float(np.mean(y ** 2))
        metrics = []
        reference = combos["ols"]
        for model, c in combos.items():
            idx = ix[model]
            err = c.residual[idx]
            factors = sorted(set(reference.factors) | set(c.factors))
            delta = np.zeros(len(kept))
            for f in factors:
                a = c.contributions[f][idx] if f in c.contributions else 0
                b = reference.contributions[f][ix["ols"]] if f in reference.contributions else 0
                delta += np.abs(a - b)
            drift = {}
            for f, values in c.betas.items():
                changes = np.abs(np.diff(values[idx]))
                changes = changes[np.isfinite(changes)]
                drift[f] = clean(np.median(changes)) if len(changes) else None
            selection = None
            if c.selected is not None:
                sets = [{f for f, v in c.selected.items() if v[i]} for i in idx]
                overlaps = [len(a & b) / len(a | b) if a | b else 1.0
                            for a, b in zip(sets[:-1], sets[1:])]
                selection = {
                    "frequency": {f: float(np.mean(v[idx])) for f, v in c.selected.items()},
                    "empty_fraction": sum(not s for s in sets) / len(sets),
                    "switch_fraction": (sum(a != b for a, b in zip(sets[:-1], sets[1:]))
                                        / (len(sets) - 1)) if len(sets) > 1 else None,
                    "mean_jaccard": float(np.mean(overlaps)) if overlaps else None,
                    "transitions": max(0, len(sets) - 1),
                }
            metrics.append({
                "model": model, "factors": c.factors,
                "mae_bp": float(np.mean(np.abs(err)) * 1e4),
                "rmse_bp": float(np.sqrt(np.mean(err ** 2)) * 1e4),
                "mse_relative_to_zero": float(np.mean(err ** 2) / base_mse) if base_mse else None,
                "allocation_l1_vs_ols_bp": float(np.mean(delta) * 1e4),
                "median_absolute_beta_change": drift, "selection": selection,
            })
        samples[name] = {"start": kept[0], "end": kept[-1], "observations": len(kept),
                         "zero_mae_bp": float(np.mean(np.abs(y)) * 1e4),
                         "zero_rmse_bp": float(np.sqrt(base_mse) * 1e4), "models": metrics}
    return {"pair": pair, "available": True, "counts": counts, "samples": samples}


def report(snapshot, window: int) -> dict:
    cache = getattr(snapshot, "_comparison_cache", None)
    if cache is None:
        cache = snapshot._comparison_cache = {}
    if window not in cache:
        cache[window] = {
            "as_of": snapshot.date_last, "window": window,
            "data_version": snapshot.data_version,
            "model_revision": snapshot.status.get("model_revision"),
            "basis": "matched_final_contemporaneous_reconstruction",
            "notes": [
                "Common dates with finite returns/contributions/residuals; provisional rows excluded.",
                "Recent means the last 252 retained observations, not 252 calendar days.",
                "Betas end at t-1; X_t is realised. These errors are not forecast errors.",
                "Lasso uses a wider factor menu; its comparison mixes selection and menu effects.",
                "Zero is a zero-contribution reference, not a separately fitted dollar-only model.",
                "Allocation L1 sums absolute factor differences against OLS on each matched date.",
                "Beta changes retain each factor's own units and are not comparable across factors.",
                "Selection transitions join consecutive retained observations, including gaps.",
                "The frozen historical archive is not a point-in-time data-vintage backtest.",
            ],
            "pairs": [compare_pair(snapshot, pair, window) for pair in snapshot.pairs],
        }
    return cache[window]
