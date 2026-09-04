"""Panel PCA monitor.

Monitoring only, no attribution numbers (SPEC 4.6 / CLAUDE.md 18). Principal components
have no names; "PC2 contributed 40bp" cannot go into a daily report. Here it answers a
different question: is the whole market moving today, or this currency on its own. No
economic labels are attached by principal-component rank either.

Take the first 2 principal components of the in-window correlation matrix. Sign rules:
PC1 loadings sum positive, PC2 has positive USDCAD loading, and both are aligned with
the previous window by dot-product sign to prevent flips. Record corr(PC1, DOLLAR) and
corr(PC2, CARRY) daily; warn below 0.9 and 0.6.

Phase 2 upgrade (SPEC_phase2 3.1): add the **projection R²** of CARRY onto
span{PC2, PC3}, replacing corr(PC2, CARRY) as the warning line. Rationale:
corr(PC2, CARRY) is not rotation invariant — when the PC2 and PC3 eigenvalues are
close, the plane they span is stable but the basis vectors inside it can rotate
arbitrarily, so CARRY's correlation with the single PC2 swings wildly while the fact
that CARRY lies in that plane is unchanged. Projection R² measures exactly the latter
and is independent of the basis choice. Measured corr(PC2, CARRY) means are only -0.20
to -0.34 — precisely this defect showing. corr(PC1, DOLLAR) stays unchanged; the old
warning line is kept until the new metric is live, then removed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import (
    HIGH_YIELD,
    LOW_YIELD,
    PAIRS,
    PCA_CORR_WARN,
    PCA_MONITOR_SCHEMA_VERSION,
    PCA_N_COMPONENTS,
    PCA_SIGN_REFERENCE_PAIR,
)
from ..data.base import record


def _orient(loadings: np.ndarray, previous: np.ndarray | None) -> np.ndarray:
    """Fix signs by the static rule first, then align with the previous window so
    adjacent days do not flip sign meaninglessly."""
    oriented = loadings.copy()
    ref = PAIRS.index(PCA_SIGN_REFERENCE_PAIR)
    if oriented[:, 0].sum() < 0:
        oriented[:, 0] *= -1
    if oriented.shape[1] > 1 and oriented[ref, 1] < 0:
        oriented[:, 1] *= -1
    if previous is not None:
        for k in range(oriented.shape[1]):
            if float(oriented[:, k] @ previous[:, k]) < 0:
                oriented[:, k] *= -1
    return oriented


def projection_r2(target: np.ndarray, basis: np.ndarray) -> float:
    """Projection R² of target onto the subspace spanned by basis; rotation invariant.

    The columns of basis can be rotated arbitrarily without changing the result, which
    is exactly why it is more robust than corr(target, single PC): it measures "how
    much lies in this plane", not "how similar to this particular axis".
    """
    y = target - target.mean()
    ss_tot = float(y @ y)
    if ss_tot <= 0 or basis.size == 0:
        return float("nan")
    x = basis - basis.mean(axis=0)
    # solve the projection by least squares to avoid inverting a near-singular basis
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ coef
    return 1.0 - float(residual @ residual) / ss_tot


def run_monitor(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    """returns is the daily return panel of the six pairs; column order must match
    PAIRS."""
    panel = returns[PAIRS].dropna()
    dollar = panel.mean(axis=1)
    carry = panel[LOW_YIELD].mean(axis=1) - panel[HIGH_YIELD].mean(axis=1)

    values = panel.to_numpy(dtype=float)
    rows = []
    previous = None
    for t in range(window, len(panel)):
        block = values[t - window : t]  # same convention as attribution, up to t-1
        corr = np.corrcoef(block, rowvar=False)
        if not np.isfinite(corr).all():
            continue
        eigenvalues, eigenvectors = np.linalg.eigh(corr)
        ranked = np.argsort(eigenvalues)[::-1]
        order = ranked[:PCA_N_COMPONENTS]
        loadings = _orient(eigenvectors[:, order], previous)
        previous = loadings

        scores = block @ loadings
        window_slice = slice(t - window, t)
        carry_window = carry.to_numpy()[window_slice]
        pc1_dollar = float(
            np.corrcoef(scores[:, 0], dollar.to_numpy()[window_slice])[0, 1]
        )
        pc2_carry = float(np.corrcoef(scores[:, 1], carry_window)[0, 1])

        # projection R²: CARRY onto span{PC2, PC3}, rotation invariant (SPEC_phase2 3.1)
        span = block @ eigenvectors[:, ranked[1:3]]
        carry_projection_r2 = projection_r2(carry_window, span)

        total = float(eigenvalues.sum())
        warn = []
        if abs(pc1_dollar) < PCA_CORR_WARN["pc1_dollar"]:
            warn.append("pc1_dollar")
        if abs(pc2_carry) < PCA_CORR_WARN["pc2_carry"]:
            warn.append("pc2_carry")  # old line, kept until the new metric is live
        if (
            np.isfinite(carry_projection_r2)
            and carry_projection_r2 < PCA_CORR_WARN["carry_projection_r2"]
        ):
            warn.append("carry_projection_r2")
        rows.append(
            {
                "date": panel.index[t],
                "window": window,
                "corr_pc1_dollar": pc1_dollar,
                "corr_pc2_carry": pc2_carry,
                "carry_projection_r2": carry_projection_r2,
                "var_pc1": float(eigenvalues[ranked[0]] / total),
                "var_pc2": float(eigenvalues[ranked[1]] / total),
                "warn_flags": ",".join(warn),
            }
        )

    frame = pd.DataFrame(rows)
    if len(frame):
        frame["schema_version"] = PCA_MONITOR_SCHEMA_VERSION
        record(
            "pca_monitor",
            window=window,
            n=len(frame),
            mean_corr_pc1_dollar=round(float(frame["corr_pc1_dollar"].mean()), 4),
            mean_corr_pc2_carry=round(float(frame["corr_pc2_carry"].mean()), 4),
            n_warn=int((frame["warn_flags"] != "").sum()),
        )
    return frame
