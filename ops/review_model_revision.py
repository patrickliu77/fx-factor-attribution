"""Compare a candidate engine to a separate checkout using read-only inputs.

The fixed 2025 sample uses archived HY OAS values. No downloads, cache updates or
contract writes occur. Each fit starts with the same data and reselection phase.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fxdash.config import PAIRS, WINDOWS, FX_TICKERS, CMDTY_TICKERS, ETF_TICKERS, US_LEG, baseline_factors, lasso_menu
from fxdash.data.panel import RawData, _fx_return_panel
from fxdash.factors.build import build_pair_panel
from fxdash.models import rolling
from fxdash.models.pca_monitor import run_monitor


def legacy(name, source):
    spec = importlib.util.spec_from_file_location("fxdash.models._review_" + name, source / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Choose a new output directory for this review.")
    cache = args.baseline / "data/cache"
    def series(name):
        safe = name.replace("=", "_").replace("^", "").replace("/", "_")
        return pd.read_parquet(cache / (safe + ".parquet")).iloc[:, 0].loc[:"2025-12-31"]
    fx = {pair: (1 / series(ticker) if invert else series(ticker)).rename(pair)
          for ticker, (pair, invert) in FX_TICKERS.items()}
    foreign_names = dict(zip(PAIRS, ["de_bund", "jp_jgb", "ca_boc", "no_nb", "au_rba", "mx_banxico"]))
    foreign = {}
    for pair, name in foreign_names.items():
        frame = pd.read_parquet(cache / f"foreign_{name}.parquet").loc[:"2025-12-31"]
        for column in ("break_short", "break_long"):
            frame[column] = frame[column].fillna(0).astype(bool) if column in frame else False
        foreign[pair] = frame
    hy = pd.read_csv(args.baseline / "data/user/fred_BAMLH0A0HYM2.csv", index_col=0, parse_dates=True).iloc[:, 0]
    hy = pd.to_numeric(hy, errors="coerce").dropna().loc[:"2025-12-31"]
    raw = RawData(fx, _fx_return_panel(fx),
                  {name: series(ticker) for ticker, name in CMDTY_TICKERS.items()},
                  {name: series(ticker + "_adj") for ticker, name in ETF_TICKERS.items()},
                  {name: series(name) for name in set(sum((list(x) for x in US_LEG.values()), []))},
                  series("VIXCLS"), series("BAA10Y"), foreign=foreign, hy_oas=hy)
    previous = args.baseline / "src/fxdash/models"
    old = {name: legacy(name, previous) for name in ("ridge", "lasso", "pca_monitor")}
    current = rolling.ROLLING_SOLVERS.copy()
    rows = []
    for pair in PAIRS:
        panel = build_pair_panel(pair, raw)
        for window in WINDOWS:
            sample = panel.tail(window + 63)
            for model in ("ols", "ridge", "lasso"):
                factors = lasso_menu(pair) if model == "lasso" else baseline_factors(pair)
                rolling.ROLLING_SOLVERS[model] = current[model]
                after = rolling.rolling_fit(sample, pair, window, model, factors)
                if model in old:
                    solve = getattr(old[model], "solve_" + model)
                    rolling.ROLLING_SOLVERS[model] = lambda z, y, state, refit, cv_data=None: solve(z, y, state, refit)
                before = rolling.rolling_fit(sample, pair, window, model, factors)
                rolling.ROLLING_SOLVERS[model] = current[model]
                x = sample.loc[after.dates, factors].to_numpy()
                delta = (after.betas - before.betas) * x * 1e4
                daily_distance = np.abs(delta).sum(axis=1)
                rows.append(dict(pair=pair, window=window, model=model,
                                 first=str(after.dates[0].date()), last=str(after.dates[-1].date()),
                                 observations=len(delta),
                                 median_factor_distance_bp=float(np.median(daily_distance)),
                                 p95_factor_distance_bp=float(np.quantile(daily_distance, .95)),
                                 max_residual_change_bp=float(np.abs(delta.sum(axis=1)).max()),
                                 selection_changed_days=int(np.any(after.selected != before.selected, axis=1).sum())))
                print(pair, window, model, "median factor distance bp", round(float(np.median(daily_distance)), 3), flush=True)
    pca = []
    for window in WINDOWS:
        sample = raw.fx_returns.dropna().tail(window + 63)
        before, after = old["pca_monitor"].run_monitor(sample, window), run_monitor(sample, window)
        pca.append(dict(window=window, observations=len(after),
                        median_projection_change=float((after.carry_projection_r2 - before.carry_projection_r2).median()),
                        changed_warning_days=int((after.warn_flags != before.warn_flags).sum())))
    args.out.mkdir(parents=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(args.out / "model_comparison.csv", index=False)
    (args.out / "pca_comparison.json").write_text(json.dumps(pca, indent=2), encoding="utf8")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    for ax, metric, title in zip(axes, ["median_factor_distance_bp", "p95_factor_distance_bp"], ["Median change", "95th percentile change"]):
        table = frame.groupby(["pair", "model"])[metric].mean().unstack()
        table.plot.bar(ax=ax, rot=0, color=["#1479c9", "#888888", "#6247d6"])
        ax.set(title=title, ylabel="Sum of absolute factor changes (bp)", xlabel="")
    fig.suptitle("Same-input comparison, last 63 observations of 2025; mean across training windows")
    fig.savefig(args.out / "model_comparison.png", dpi=160)
    assert frame.loc[frame.model == "ols", "max_residual_change_bp"].max() == 0
    print("Review complete:", args.out, flush=True)


if __name__ == "__main__":
    main()
