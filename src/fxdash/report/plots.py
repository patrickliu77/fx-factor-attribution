"""Report figures. Each function returns a base64 PNG.

The benchmark band in the rolling R² figure is only a rough full-sample
kitchen-sink reference and is not directly comparable to in-window rolling R²;
the caption must say so, and a horizontal dashed line at this project's own
full-sample OLS R² is drawn as the like-for-like comparison (SPEC 7).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import (
    FX_INTERNAL_FACTORS,
    LITERATURE_BANDS_DAILY,
    MX_GUARDRAIL_LINES,
)
from .style import figure_to_base64, label, setup_matplotlib

SYSTEMATIC_COLOR = "#4c78a8"
EXOGENOUS_COLOR = "#f58518"
RESIDUAL_COLOR = "#999999"


def _plt():
    setup_matplotlib()
    import matplotlib.pyplot as plt

    return plt


def three_bucket_bar(row: pd.Series) -> str:
    """Yesterday's three-bucket decomposition: systematic, exogenous, residual."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    names = [
        label("系统性", "systematic"),
        label("外生因子", "exogenous"),
        label("残差", "residual"),
    ]
    values = [row["systematic"] * 1e4, row["exogenous"] * 1e4, row["residual"] * 1e4]
    ax.bar(names, values, color=[SYSTEMATIC_COLOR, EXOGENOUS_COLOR, RESIDUAL_COLOR])
    ax.axhline(0, color="#333333", lw=0.8)
    total = row["y"] * 1e4
    ax.set_ylabel("bp")
    ax.set_title(
        f"{row['date'].date()}  {label('当日收益', 'return')} {total:.1f} bp"
        f"  =  {values[0]:.1f} + {values[1]:.1f} + {values[2]:.1f}"
    )
    for i, value in enumerate(values):
        ax.text(i, value, f"{value:.1f}", ha="center",
                va="bottom" if value >= 0 else "top", fontsize=9)
    return figure_to_base64(fig)


def contribution_stack(block: pd.DataFrame, factors: list[str]) -> str:
    """Per-factor contribution stack over the last 21 days."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10, 4))
    dates = block["date"].to_numpy()
    contributions = pd.DataFrame(
        [json.loads(s) for s in block["contributions"]], index=block.index
    ).reindex(columns=factors).fillna(0.0) * 1e4

    positive = np.zeros(len(block))
    negative = np.zeros(len(block))
    colormap = plt.get_cmap("tab10")
    for i, factor in enumerate(factors):
        values = contributions[factor].to_numpy()
        base = np.where(values >= 0, positive, negative)
        ax.bar(dates, values, bottom=base, width=0.8, label=factor,
               color=colormap(i % 10))
        positive += np.where(values >= 0, values, 0)
        negative += np.where(values < 0, values, 0)

    ax.plot(dates, block["y"].to_numpy() * 1e4, color="black", lw=1.4, marker="o",
            ms=3, label=label("当日收益", "return"))
    ax.axhline(0, color="#333333", lw=0.8)
    ax.set_ylabel("bp")
    ax.set_title(label("近 21 日贡献堆叠", "last 21 days, contributions"))
    ax.legend(fontsize=7, ncol=4, loc="upper left")
    fig.autofmt_xdate()
    return figure_to_base64(fig)


def beta_paths(block: pd.DataFrame, factors: list[str]) -> str:
    plt = _plt()
    betas = pd.DataFrame([json.loads(s) for s in block["betas"]], index=block["date"])
    n = len(factors)
    rows = (n + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(11, 1.9 * rows), sharex=True)
    for ax, factor in zip(np.ravel(axes), factors, strict=False):
        ax.plot(betas.index, betas[factor], lw=1.0, color=SYSTEMATIC_COLOR)
        ax.axhline(0, color="#cccccc", lw=0.7)
        ax.set_title(factor, fontsize=9)
        ax.tick_params(labelsize=7)
    for ax in np.ravel(axes)[n:]:
        ax.axis("off")
    fig.suptitle(label("beta 路径（原量纲）", "beta paths (original units)"), fontsize=11)
    fig.tight_layout()
    return figure_to_base64(fig)


def lasso_heatmap(block: pd.DataFrame, factors: list[str]) -> str:
    """Heatmap of Lasso-selected factors, i.e. "different drivers in different
    periods" made visible."""
    plt = _plt()
    chosen = [set(json.loads(s)) for s in block["selected_factors"]]
    matrix = np.array([[1.0 if f in s else 0.0 for s in chosen] for f in factors])
    fig, ax = plt.subplots(figsize=(11, 0.42 * len(factors) + 1.6))
    ax.imshow(matrix, aspect="auto", cmap="Blues", vmin=0, vmax=1,
              interpolation="nearest")
    ax.set_yticks(range(len(factors)))
    ax.set_yticklabels(factors, fontsize=8)
    dates = pd.to_datetime(block["date"])
    ticks = np.linspace(0, len(dates) - 1, min(10, len(dates))).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(dates.iloc[i].date()) for i in ticks], fontsize=7,
                       rotation=45, ha="right")
    ax.set_title(label("Lasso 选中因子", "Lasso selected factors"))
    ax.grid(False)
    return figure_to_base64(fig)


def rolling_r2(block: pd.DataFrame, pair: str, full_sample_r2: float) -> str:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 4))
    dates = pd.to_datetime(block["date"])
    ax.plot(dates, block["r2_full"], lw=1.0, color=SYSTEMATIC_COLOR,
            label=label("全模型 r2_full", "r2_full"))
    ax.plot(dates, block["r2_exog"], lw=1.0, color=EXOGENOUS_COLOR,
            label=label("外生 r2_exog", "r2_exog"))

    low, high = LITERATURE_BANDS_DAILY[pair]
    ax.axhspan(low, high, color="#2ca02c", alpha=0.10,
               label=label("文献基准带（粗参照）", "literature band (rough)"))
    ax.axhline(full_sample_r2, color="#d62728", ls="--", lw=1.2,
               label=label(f"本项目全样本 OLS R²={full_sample_r2:.3f}",
                           f"full-sample OLS R²={full_sample_r2:.3f}"))
    ax.set_ylim(0, 1)
    ax.set_ylabel("R²")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title(label("滚动 R²", "rolling R²"))
    fig.autofmt_xdate()
    return figure_to_base64(fig)


def residual_z(block: pd.DataFrame) -> str:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(11, 3.2))
    dates = pd.to_datetime(block["date"])
    ax.plot(dates, block["residual_z"], lw=0.8, color="#444444")
    for level in (2, 3):
        ax.axhline(level, color="#d62728", ls=":", lw=0.9)
        ax.axhline(-level, color="#d62728", ls=":", lw=0.9)
    ax.axhline(0, color="#cccccc", lw=0.8)
    ax.set_ylabel("z")
    ax.set_title(label("residual z（Phase 3 触发器预留）",
                       "residual z (reserved for Phase 3)"))
    fig.autofmt_xdate()
    return figure_to_base64(fig)


def pca_monitor_plot(monitor: pd.DataFrame, window: int) -> str:
    plt = _plt()
    block = monitor[monitor["window"] == window]
    fig, ax = plt.subplots(figsize=(11, 3.6))
    dates = pd.to_datetime(block["date"])
    ax.plot(dates, block["corr_pc1_dollar"], lw=1.0, color=SYSTEMATIC_COLOR,
            label="corr(PC1, DOLLAR)")
    if "carry_projection_r2" in block.columns:
        ax.plot(dates, block["carry_projection_r2"], lw=1.2, color="#2ca02c",
                label=label("CARRY 对 span{PC2,PC3} 投影 R²（新线）",
                            "CARRY projection R² on span{PC2,PC3} (new)"))
        ax.axhline(0.5, color="#2ca02c", ls=":", lw=0.9)
    ax.plot(dates, block["corr_pc2_carry"], lw=0.8, color=EXOGENOUS_COLOR, alpha=0.55,
            label=label("corr(PC2, CARRY)（旧线，待移除）",
                        "corr(PC2, CARRY) (legacy)"))
    ax.axhline(0.9, color=SYSTEMATIC_COLOR, ls=":", lw=0.9)
    ax.axhline(0.6, color=EXOGENOUS_COLOR, ls=":", lw=0.9)
    ax.set_ylim(-1, 1)
    ax.legend(fontsize=7, loc="lower left", ncol=2)
    ax.set_title(label("面板 PCA 监控（只监控，不出归因数字）",
                       "panel PCA monitor (monitoring only)"))
    fig.autofmt_xdate()
    return figure_to_base64(fig)


def jpy_regime_plot(panel: pd.DataFrame, window: int) -> str:
    """USDJPY rates-correlation lifeline: univariate rolling R² of the spread
    factors (SPEC_phase2 2.4)."""
    plt = _plt()
    y = panel["y"].to_numpy()
    fig, ax = plt.subplots(figsize=(11, 3.4))
    for factor, color in (("d2Y_DIFF", SYSTEMATIC_COLOR), ("d10Y_DIFF", EXOGENOUS_COLOR)):
        x = panel[factor].to_numpy()
        r2 = np.full(len(y), np.nan)
        for t in range(window, len(y)):
            xs, ys = x[t - window:t], y[t - window:t]
            xc, yc = xs - xs.mean(), ys - ys.mean()
            denom = (xc @ xc) * (yc @ yc)
            if denom > 0:
                r2[t] = (xc @ yc) ** 2 / denom
        ax.plot(panel.index, r2, lw=1.1, color=color, label=factor)
    ax.set_ylabel("R²")
    ax.set_ylim(0, None)
    ax.legend(fontsize=8)
    ax.set_title(label("利差因子单变量滚动 R²（regime 指示器，按假设监控）",
                       "spread factors, univariate rolling R² (regime, hypothesis only)"))
    fig.autofmt_xdate()
    return figure_to_base64(fig)


def mx_guardrail_plot(table: pd.DataFrame) -> str:
    """USDMXN only: monthly deviation bars (raw and after basis), basis line,
    reference lines (SPEC 7 section 8)."""
    plt = _plt()
    months = pd.PeriodIndex(table["month"], freq="M").to_timestamp()
    raw = table["raw_dev_bp"].astype(float).to_numpy()
    resid = table["resid_dev_bp"].astype(float).to_numpy()
    basis = table["basis_bp"].astype(float).to_numpy()

    fig, ax = plt.subplots(figsize=(11, 4.2))
    width = 12
    ax.bar(months, raw, width=width, color="#c6dbef",
           label=label("原始偏差", "raw deviation"))
    # Breaches in red, but a reverse breach during basis reversion is downgraded
    # to a pale annotation, not red (SPEC_phase2 3.2).
    over = np.abs(resid) > MX_GUARDRAIL_LINES[0]
    reverse = (
        table["reverse_breach"].to_numpy(dtype=bool)
        if "reverse_breach" in table.columns
        else np.zeros(len(table), dtype=bool)
    )
    colors = np.where(over & ~reverse, "#d62728",
                      np.where(reverse, "#f2b5b5", "#4c78a8"))
    ax.bar(months, resid, width=width * 0.55, color=colors,
           label=label("扣基差后", "after basis"))
    for i in np.flatnonzero(reverse):
        ax.annotate(label("基差回归期", "basis reverting"),
                    (months[i], resid[i]), fontsize=6, color="#8c564b",
                    ha="center", va="bottom" if resid[i] >= 0 else "top")
    ax.plot(months, basis, color="#333333", lw=1.3,
            label=label("基差", "basis"))
    for level in MX_GUARDRAIL_LINES:
        for sign in (1, -1):
            ax.axhline(sign * level, color="#d62728" if level == 15 else "#8c564b",
                       ls=":" if level == 15 else "--", lw=0.9)
    ax.axhline(0, color="#333333", lw=0.8)
    ax.set_ylabel("bp")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title(label("MX10Y_DERIVED 对 SF30057 的月度偏差",
                       "MX10Y_DERIVED monthly deviation vs SF30057"))
    fig.autofmt_xdate()
    return figure_to_base64(fig)
