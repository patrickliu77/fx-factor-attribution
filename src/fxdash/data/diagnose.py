"""Alignment diagnostics: for each pair and factor class, run the t-1, t, t+1
three-position regressions (SPEC 1.3).

12 diagnostics = 6 pairs x 2 classes (usd_close, foreign leg).

key_var is chosen per class: the foreign-leg diagnostic uses d10Y_DIFF (its foreign
leg is exactly what moves, and it is the quantity SPEC 10.3 uses when reporting
USDCAD); the usd_close diagnostic uses dVIX. d10Y_DIFF is not used for the usd_close
class because only its US leg moves there while the foreign leg is frozen, making the
coefficient mixed; dVIX is the variable driven purely by that class.

evidence rule (SPEC 1.4): if the R² gap between best and second-best is under 1.5
percentage points, or the key_var t-value ranking disagrees with the R² ranking, mark
thin, otherwise decisive. The SPEC does not state which variable key_var is, nor
whether "ranking" compares the whole sequence or only the best position; the
2026-08-27 rebuild enumerated six readings, and only "key_var per class, compare the
best position only" reproduced item by item the 7 thin / 5 decisive recorded by the
original implementation (EUR both classes; JPY/CAD/NOK/AUD/MXN foreign leg), so this
reading was adopted. All 12 frozen offsets agree under every reading, so the choice
does not affect offsets. thin only affects re-check priority on data-source changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ..config import ALIGNMENT_DIR, FOREIGN_TENOR, LONG_SLOT, OFFSETS, PAIRS, US_LEG
from .alignment import FOREIGN, USD_CLOSE, write_profile
from .base import record

POSITIONS = [(1, "t-1"), (0, "t"), (-1, "t+1")]
KEY_VAR_BY_CLASS = {USD_CLOSE: "dVIX", FOREIGN: LONG_SLOT}
THIN_R2_GAP_PP = 1.5  # percentage points


def key_var_for(factor_class: str) -> str:
    return KEY_VAR_BY_CLASS[factor_class]


def _fit(
    frame: pd.DataFrame, factors: list[str], key_var: str
) -> tuple[float, float, float]:
    design = sm.add_constant(frame[factors], has_constant="add")
    model = sm.OLS(frame["y"], design).fit()
    return (
        float(model.rsquared),
        float(model.tvalues.get(key_var, np.nan)),
        float(model.params.get(key_var, np.nan)),
    )


def diagnose_pair_class(pair, factor_class, raw, extra_factors=None):
    """Sweep one factor class's offset over {t-1, t, t+1}, other classes kept frozen.

    extra_factors serves the confirmatory diagnostic of a new series: add it to the
    design matrix and sweep again.
    """
    from ..factors.build import build_pair_panel
    from ..config import baseline_factors

    factors = baseline_factors(pair) + list(extra_factors or [])
    key_var = key_var_for(factor_class)
    rows = []
    scatter: dict[str, pd.DataFrame] = {}
    for shift, label in POSITIONS:
        overrides = {pair: dict(OFFSETS[pair])}
        overrides[pair][factor_class] = shift
        frame = build_pair_panel(pair, raw, overrides=overrides)
        r2, tval, beta = _fit(frame, factors, key_var)
        scatter[label] = frame[[key_var, "y"]].copy()
        rows.append(
            {
                "alignment": label,
                "offset": shift,
                "n": int(len(frame)),
                "r2": round(r2, 4),
                "t_key": round(tval, 2),
                "beta_key": round(beta, 6),
                # The sign flip in the foreign-leg diagnostic is the SPEC 10.3
                # reference point; keep the long-end coefficient on file as well
                "t_d10Y_DIFF": round(tval, 2) if key_var == LONG_SLOT else None,
            }
        )

    table = pd.DataFrame(rows)
    best = table.loc[int(table["r2"].idxmax())]
    ordered = table.sort_values("r2", ascending=False)
    r2_gap_pp = float((ordered.iloc[0]["r2"] - ordered.iloc[1]["r2"]) * 100)

    # Compare the best position only: is the highest-R² alignment also the one with
    # the highest |t|
    best_by_r2 = str(ordered.iloc[0]["alignment"])
    best_by_t = str(table.loc[table["t_key"].abs().idxmax(), "alignment"])
    consistent = best_by_r2 == best_by_t

    thin_reasons = []
    if r2_gap_pp < THIN_R2_GAP_PP:
        thin_reasons.append(f"R² gap {r2_gap_pp:.2f} pp, under {THIN_R2_GAP_PP}")
    if not consistent:
        thin_reasons.append(
            f"best |t| position {best_by_t} for {key_var} disagrees with best R² position {best_by_r2}"
        )

    short_tenor, long_tenor = FOREIGN_TENOR[pair]
    us_short, us_long = US_LEG[pair]
    entry = {
        "pair": pair,
        "factor_class": factor_class,
        "chosen_offset": int(best["offset"]),
        "chosen_alignment": best["alignment"],
        "frozen_offset": int(OFFSETS[pair][factor_class]),
        "matches_frozen": int(best["offset"]) == int(OFFSETS[pair][factor_class]),
        "evidence": "thin" if thin_reasons else "decisive",
        "evidence_reasons": thin_reasons,
        "r2_gap_pp": round(r2_gap_pp, 2),
        "key_var": key_var,
        "positions": rows,
        "tenor": {
            "foreign_short": short_tenor,
            "foreign_long": long_tenor,
            "us_short": us_short,
            "us_long": us_long,
        },
    }
    return entry, scatter


def plot_triptych(entry: dict, scatter: dict[str, pd.DataFrame]) -> str:
    """Three-panel scatter with fitted lines, annotated with the multivariate R² and
    t-values (CLAUDE.md 21 / SPEC 1.3)."""
    from ..report.style import label, setup_matplotlib

    setup_matplotlib()
    import matplotlib.pyplot as plt

    key_var = entry["key_var"]
    ALIGNMENT_DIR.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.0), sharey=True)
    for ax, position in zip(axes, entry["positions"], strict=True):
        position_label = position["alignment"]
        data = scatter[position_label].dropna()
        x, y = data[key_var].to_numpy(), data["y"].to_numpy()
        chosen = position_label == entry["chosen_alignment"]
        ax.scatter(x, y, s=4, alpha=0.25, color="#1f77b4" if chosen else "#999999")
        if len(x) > 2 and np.ptp(x) > 0:
            slope, intercept = np.polyfit(x, y, 1)
            grid = np.linspace(x.min(), x.max(), 50)
            ax.plot(grid, slope * grid + intercept, color="#d62728", lw=1.6)
        mark = label("  (chosen)", "  (chosen)") if chosen else ""
        r2_text = label("multivariate R²", "multivariate R²")
        ax.set_title(
            f"{position['alignment']}{mark}\n"
            f"{r2_text}={position['r2']:.4f}  t({key_var})={position['t_key']}",
            fontsize=10,
        )
        ax.set_xlabel(key_var, fontsize=9)
        ax.axhline(0, color="#cccccc", lw=0.6)
        ax.axvline(0, color="#cccccc", lw=0.6)
    axes[0].set_ylabel(f"{entry['pair']} log return", fontsize=9)
    title = label("alignment diagnostic", "alignment diagnostic")
    frozen = label("frozen offset", "frozen offset")
    fig.suptitle(
        f"{entry['pair']} · {entry['factor_class']} {title} · "
        f"{frozen} {entry['frozen_offset']:+d} · {entry['evidence']}",
        fontsize=11,
    )
    fig.tight_layout()
    path = ALIGNMENT_DIR / f"align_{entry['pair']}_{entry['factor_class']}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return str(path.name)


def run_diagnostics(raw, pairs=None, plot=True) -> tuple[list[dict], list[dict]]:
    """Run all 12 diagnostics; return (entries, mismatches)."""
    entries, mismatches = [], []
    for pair in pairs or PAIRS:
        for factor_class in (USD_CLOSE, FOREIGN):
            entry, scatter = diagnose_pair_class(pair, factor_class, raw)
            if plot:
                entry["figure"] = plot_triptych(entry, scatter)
            entries.append(entry)
            record(
                "alignment_diagnostic",
                pair=pair,
                factor_class=factor_class,
                chosen=entry["chosen_offset"],
                frozen=entry["frozen_offset"],
                matches=entry["matches_frozen"],
                evidence=entry["evidence"],
                r2_gap_pp=entry["r2_gap_pp"],
            )
            if not entry["matches_frozen"]:
                mismatches.append(entry)
    return entries, mismatches


def diagnose_new_series(series_name: str, raw, factor_class=USD_CLOSE, pairs=None):
    """Confirmatory alignment diagnostic when a new series is onboarded
    (SPEC_phase2 4.2).

    Add the new series to the design matrix and rerun the three alignment positions to
    confirm it does not move the class's best position. **Record only, never change
    frozen offsets**: a new series inherits its class's frozen offset; this step
    confirms, it does not re-decide.
    """
    from ..config import baseline_factors, lasso_menu

    entries = []
    for pair in pairs or PAIRS:
        if series_name not in lasso_menu(pair):
            # this pair's menu does not include the series (e.g. AUD has no dHY_OAS);
            # nothing to confirm
            record("confirmatory_alignment_skipped", series=series_name, pair=pair)
            continue
        design = baseline_factors(pair) + [series_name]
        entry, _ = diagnose_pair_class(pair, factor_class, raw, extra_factors=[series_name])
        entry["confirmatory_for"] = series_name
        entry["design"] = design
        entry["frozen_unchanged"] = True
        entries.append(entry)
        record(
            "confirmatory_alignment",
            series=series_name,
            pair=pair,
            factor_class=factor_class,
            chosen=entry["chosen_offset"],
            frozen=entry["frozen_offset"],
            matches=entry["matches_frozen"],
        )
    return entries


def summarise(entries: list[dict]) -> dict:
    thin = [f"{e['pair']}/{e['factor_class']}" for e in entries if e["evidence"] == "thin"]
    return {
        "n_diagnostics": len(entries),
        "n_thin": len(thin),
        "n_decisive": len(entries) - len(thin),
        "thin_items": thin,
        "n_mismatch": sum(1 for e in entries if not e["matches_frozen"]),
    }


def persist(entries: list[dict]) -> dict:
    return write_profile(entries, extra={"summary": summarise(entries)})
