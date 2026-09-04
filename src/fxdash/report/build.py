"""One self-contained HTML report page per pair (SPEC 7)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ..config import (
    BENCHMARK_R2_MEAN,
    DEFAULT_WINDOW,
    LITERATURE_BANDS_DAILY,
    REPORT_DIR,
    baseline_factors,
    lasso_menu,
)
from ..data.base import record
from . import plots
from .style import setup_matplotlib

STYLE = """
body{font-family:"Microsoft YaHei",-apple-system,Segoe UI,sans-serif;margin:0;
padding:28px 40px;background:#fafafa;color:#1a1a1a;line-height:1.6}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:30px 0 8px;
border-left:4px solid #4c78a8;padding-left:9px}
.sub{color:#666;font-size:13px;margin-bottom:20px}
img{max-width:100%;border:1px solid #e2e2e2;background:#fff;border-radius:3px}
.note{font-size:12px;color:#666;margin:6px 0 0;background:#f0f0f0;padding:8px 11px;
border-radius:3px}
table{border-collapse:collapse;font-size:13px;margin-top:6px}
th,td{border:1px solid #ddd;padding:4px 10px;text-align:right}
th{background:#f0f0f0}td.l,th.l{text-align:left}
.warn{color:#b00020;font-weight:600}.ok{color:#1a7f37}
"""


def _section(title: str, image: str, note: str = "") -> str:
    note_html = f'<p class="note">{note}</p>' if note else ""
    return (
        f"<h2>{title}</h2>"
        f'<img src="data:image/png;base64,{image}" alt="{title}">{note_html}'
    )


def _full_sample_r2(panel: pd.DataFrame, pair: str) -> float:
    factors = baseline_factors(pair)
    model = sm.OLS(
        panel["y"], sm.add_constant(panel[factors], has_constant="add")
    ).fit()
    return float(model.rsquared)


def _benchmark_table(block: pd.DataFrame, pair: str, full_sample: float) -> str:
    low, high = LITERATURE_BANDS_DAILY[pair]
    mean_full = float(block["r2_full"].mean())
    mean_exog = float(block["r2_exog"].mean())
    expected = BENCHMARK_R2_MEAN[pair]
    ok = abs(mean_full - expected) <= 0.05
    verdict = (
        f'<span class="ok">在 ±0.05 内</span>'
        if ok
        else '<span class="warn">超出 ±0.05，需报告</span>'
    )
    return f"""<table>
<tr><th class="l">量</th><th>值</th></tr>
<tr><td class="l">滚动 r2_full 均值</td><td>{mean_full:.4f}</td></tr>
<tr><td class="l">滚动 r2_exog 均值</td><td>{mean_exog:.4f}</td></tr>
<tr><td class="l">本项目全样本 OLS R²</td><td>{full_sample:.4f}</td></tr>
<tr><td class="l">SPEC 10.1 对照基准</td><td>{expected:.2f}</td></tr>
<tr><td class="l">文献基准带（日频，粗参照）</td><td>{low:.0%} – {high:.0%}</td></tr>
<tr><td class="l">对照结论</td><td>{verdict}</td></tr>
</table>"""


def _pick_window(available: set[int]) -> int:
    """Default to the 126-day window; if it was not run, fall back to the
    nearest one instead of crashing the report."""
    if DEFAULT_WINDOW in available:
        return DEFAULT_WINDOW
    if not available:
        raise ValueError("no windows in contract")
    return min(available, key=lambda w: (abs(w - DEFAULT_WINDOW), -w))


def build_pair_report(
    pair: str,
    contract: pd.DataFrame,
    panel: pd.DataFrame,
    monitor: pd.DataFrame,
    guardrail: tuple | None = None,
) -> str:
    setup_matplotlib()
    rows = contract[contract["pair"] == pair]
    if rows.empty:
        raise ValueError(f"no rows for {pair} in contract")
    window = _pick_window(set(rows["window"].unique()))

    def block(model: str) -> pd.DataFrame:
        return (
            rows[(rows["window"] == window) & (rows["model"] == model)]
            .sort_values("date")
            .reset_index(drop=True)
        )

    ols, lasso = block("ols"), block("lasso")

    factors = baseline_factors(pair)
    menu = lasso_menu(pair)
    latest = ols.iloc[-1]
    recent = ols.tail(21)
    full_sample = _full_sample_r2(panel, pair)

    parts = [
        f"<h1>{pair} · 因子归因</h1>",
        f'<p class="sub">窗口 {window} 天 · 模型 OLS（Lasso 选中图另注） · '
        f'样本 {ols["date"].min().date()} 至 {ols["date"].max().date()} · '
        f'{len(ols)} 个交易日</p>',
        _section(
            "1. 昨日三栏分解",
            plots.three_bucket_bar(latest),
            "systematic 是 DOLLAR_LOO 与 CARRY_LOO 之和，量的是这个 pair 的变动里有多大"
            "比例是系统性的；exogenous 才是外生经济因子的解释力。两者不可混为一谈。",
        ),
        _section("2. 近 21 日贡献堆叠", plots.contribution_stack(recent, factors)),
        _section(
            "3. beta 路径",
            plots.beta_paths(ols, factors),
            "beta 一律来自截至前一日的滚动窗口，罚回归系数已换回原量纲。",
        ),
        _section(
            "4. Lasso 选中因子热图",
            plots.lasso_heatmap(lasso, menu),
            "高度相关的因子组里 Lasso 会近乎随机地保留一个、丢掉其余，"
            "保了 Brent 丢了 WTI 并无经济含义，解读时要克制。",
        ),
        _section(
            "5. 滚动 R²",
            plots.rolling_r2(ols, pair, full_sample),
            "基准带是全样本 kitchen sink 口径的粗参照，与滚动窗口内 R² 不可直接比；"
            "红色虚线才是同类对比，即本项目自己的全样本 OLS R²。",
        ),
        _benchmark_table(ols, pair, full_sample),
        _section("6. residual z", plots.residual_z(ols)),
        _section(
            "7. 面板 PCA 监控",
            plots.pca_monitor_plot(monitor, _pick_window(set(monitor["window"].unique()))),
            "只监控，不出归因数字；也不按主成分排名贴经济标签。",
        ),
    ]

    if pair == "USDJPY":
        parts.append(
            _section(
                "7b. regime 指示器",
                plots.jpy_regime_plot(panel, window),
                "利差因子单变量滚动 R² 是 rates 相关性的生死线。2026 年该 regime 已"
                "记录为假设，持续监控，不作结构性结论；塌陷幅度随样本终点变动，"
                "对照只看方向与单调性，不看具体数值。",
            )
        )

    if pair == "USDMXN" and guardrail is not None:
        table, verdict = guardrail
        status = (
            '<span class="warn">派生失败</span>'
            if verdict["failed"]
            else '<span class="ok">通过</span>'
        )
        parts.append(
            _section(
                "8. MX10Y_DERIVED 月度偏差护栏",
                plots.mx_guardrail_plot(table),
                "交叉验证对手方 SF30057 是一级市场拍卖月均，派生侧是二级市场全月均值，"
                "二者存在一二级基差，这正是基差机制存在的原因，不是派生失败。"
                "护栏作用于扣基差后的残差偏差，阈值 15bp；连续 6 个可得月超限判失败，"
                "官方 N/E 月跳过不清零；单月原始偏差超 50bp 立即告警。",
            )
        )
        parts.append(
            f"<table><tr><th class='l'>护栏</th><th>值</th></tr>"
            f"<tr><td class='l'>结论</td><td>{status}</td></tr>"
            f"<tr><td class='l'>最长连续超限（可得月）</td>"
            f"<td>{verdict['max_consecutive_over_15']}</td></tr>"
            f"<tr><td class='l'>可得月 / 总月</td>"
            f"<td>{verdict['n_available']} / {verdict['n_months']}</td></tr>"
            f"<tr><td class='l'>扣基差后超 15bp 月数</td>"
            f"<td>{verdict['n_over_15']}</td></tr>"
            f"<tr><td class='l'>原始偏差超 50bp 月数</td>"
            f"<td>{verdict['n_over_50']}</td></tr></table>"
        )

    body = "\n".join(parts)
    return (
        f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{pair} 因子归因</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def build_all_reports(contract, panels, monitor, raw) -> list[str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    guardrail = None
    if "USDMXN" in panels:
        try:
            from ..data.foreign.mx import monthly_guardrail

            guardrail = monthly_guardrail(raw.foreign["USDMXN"]["long"].dropna())
        except Exception as exc:  # a guardrail fetch failure must not sink the whole report run
            record("mx_guardrail_failed", error=str(exc)[:200])

    written = []
    for pair in panels:
        html = build_pair_report(pair, contract, panels[pair], monitor, guardrail)
        path = REPORT_DIR / f"{pair}.html"
        path.write_text(html, encoding="utf-8")
        written.append(str(path))
    record("reports_written", n=len(written), dir=str(REPORT_DIR))
    return written
