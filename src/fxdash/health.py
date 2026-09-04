"""Runtime health checks (PLAN Phase 1's three checks + SPEC_phase2 section 2 upgrade).

Dual mode: backfill does end-of-period summary statistics only; live evaluates daily
and goes into the manifest.

The two threshold layers are ANDed (SPEC_phase2 2.2). AND rather than OR follows from
the second layer's semantics: its job is to suppress alerts when the whole market drops
together, not to add another alert path of its own. Parameters were measured on a
16.2-year sample; the system as a whole starts about 0.99 alerts per year.

The absolute floor is kept as the last line of defense, but under live it likewise
requires 10 consecutive trading days to trigger: AUD has r2_full above 0.75 on 20% of
days, so daily triggering would fire constantly, and the LOO leakage this line is meant
to catch is persistent by nature anyway.

An alert fires once at the state transition and is presented as an ongoing state
afterwards (SPEC_phase2 2.3).

Monitoring discipline (SPEC_phase2 2.5): any alert that stays in a triggered state
long-term under normal operation is a broken criterion, not a system anomaly, and the
criterion must be fixed. Each criterion's measured trigger frequency is recorded in the
comments here; recheck them whenever a criterion changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import (
    CROSS_PAIR_Z_THRESHOLD,
    EXTRA_FACTORS,
    HEALTH_PERSIST_DAYS,
    HEALTH_QUANTILE,
    HEALTH_QUANTILE_WINDOW,
    HEALTH_R2_HIGH,
    HEALTH_R2_LOW,
    baseline_factors,
)
from .data.base import record

COMMODITY_FACTORS = {"WTI", "BRENT", "COPPER", "GOLD"}
COMMODITY_INCREMENTAL_CAP = 0.15
BACKFILL_SUMMARY_FRACTION = 0.5  # fraction threshold for the backfill summary


def _sustained(condition: pd.Series, days: int) -> pd.Series:
    """Only `days` consecutive days of the condition count as a trigger."""
    flags = condition.fillna(False).astype(bool)
    return flags.rolling(days).sum() >= days


def _onsets(fired: pd.Series) -> pd.Series:
    """Fire once at the state transition; no daily re-alerting."""
    return fired & ~fired.shift(1, fill_value=False)


def relative_r2_alerts(r2_panel: pd.DataFrame) -> dict[str, pd.Series]:
    """Two layers ANDed: below own rolling one-year quantile, and clearly lagging the
    other pairs.

    r2_panel columns are pairs, rows are dates. The quantile uses shift(1), strictly up
    to the previous day, same convention as attribution.
    """
    cross_z = r2_panel.sub(r2_panel.mean(axis=1), axis=0).div(
        r2_panel.std(axis=1), axis=0
    )
    out = {}
    for pair in r2_panel.columns:
        series = r2_panel[pair]
        threshold = series.rolling(HEALTH_QUANTILE_WINDOW).quantile(
            HEALTH_QUANTILE
        ).shift(1)
        own = series < threshold
        cross = cross_z[pair] < CROSS_PAIR_Z_THRESHOLD
        out[pair] = _sustained(own & cross, HEALTH_PERSIST_DAYS)
    return out


def absolute_floor_alerts(r2_panel: pd.DataFrame) -> dict[str, dict[str, pd.Series]]:
    """Absolute floor; under live it likewise requires 10 consecutive trading days.

    Only the lower bound acts on the rolling R² here. The 75% ceiling is not here —
    see check_absolute_ceiling.
    """
    return {
        pair: {
            "r2_below_floor": _sustained(
                r2_panel[pair] < HEALTH_R2_LOW, HEALTH_PERSIST_DAYS
            ),
        }
        for pair in r2_panel.columns
    }


def check_absolute_ceiling(pair: str, panel: pd.DataFrame) -> dict | None:
    """Last line of defense against tautological leakage, applied to the **full-sample
    R²**, not the rolling in-window R².

    Fixed 2026-08-27 per the SPEC_phase2 2.5 monitoring discipline. The original
    implementation put the 75% line on the rolling in-window R², and it stayed lit in
    practice: AUD triggered on 15.8% of trading days, longest streak 190 days and
    triggering at the time; NOK 9.7%. That is not a system anomaly but a wrong
    criterion — when PLAN set this line it meant the full-sample diagnostic convention
    of "daily R² above 75%", while the rolling in-window R² is naturally higher: only
    126 observations, seven or eight parameters, and an in-sample fit. Using one number
    to police two conventions is a category error.

    More fundamentally, a single absolute value is not equidistant across pairs:
    rolling means are AUD 0.61 and MXN 0.40, so the same 0.75 means AUD alerts at a
    0.14 gap while MXN needs 0.35 — completely different strictness.

    On the full-sample convention the six pairs' full-sample R² runs 0.40 to 0.61, all
    with headroom to 0.75, while real LOO leakage would push it far beyond 0.75. The
    line thus returns to its intended role as a last line of defense: silent in normal
    times, certain to fire when things go wrong.
    """
    from .config import baseline_factors as _baseline

    model = sm.OLS(
        panel["y"], sm.add_constant(panel[_baseline(pair)], has_constant="add")
    ).fit()
    r2 = float(model.rsquared)
    if r2 <= HEALTH_R2_HIGH:
        return None
    return {
        "check": "r2_above_ceiling",
        "pair": pair,
        "full_sample_r2": round(r2, 4),
        "threshold": HEALTH_R2_HIGH,
        "action": ACTIONS["r2_above_ceiling"],
    }


ACTIONS = {
    "r2_relative_low": "check factor construction, data alignment and differencing first",
    "r2_below_floor": "check factor construction, data alignment and differencing first (absolute floor)",
    "r2_above_ceiling": "check that leave one out strictly excludes the explained pair; beware tautological leakage",
    "commodity_incremental_r2_high": "check the time alignment of the oil price series first",
}


def evaluate(r2_panel: pd.DataFrame, mode: str, as_of=None) -> list[dict]:
    """live evaluates the day's state daily; backfill gives end-of-period summary
    only."""
    if r2_panel.empty:
        return []
    relative = relative_r2_alerts(r2_panel)
    absolute = absolute_floor_alerts(r2_panel)

    findings = []
    if mode == "backfill":
        for pair in r2_panel.columns:
            for check, fired in (
                ("r2_relative_low", relative[pair]),
                *absolute[pair].items(),
            ):
                onsets = int(_onsets(fired).sum())
                if onsets:
                    findings.append(
                        {
                            "check": check,
                            "pair": pair,
                            "mode": mode,
                            "onsets": onsets,
                            "days_fired": int(fired.fillna(False).sum()),
                            "action": ACTIONS[check],
                        }
                    )
        return findings

    # live: look only at the latest day's state, distinguishing "just triggered today"
    # from "in an ongoing triggered state"
    day = pd.Timestamp(as_of) if as_of is not None else r2_panel.index[-1]
    for pair in r2_panel.columns:
        for check, fired in (
            ("r2_relative_low", relative[pair]),
            *absolute[pair].items(),
        ):
            if day not in fired.index or not bool(fired.loc[day]):
                continue
            onset = bool(_onsets(fired).loc[day])
            findings.append(
                {
                    "check": check,
                    "pair": pair,
                    "mode": mode,
                    "date": str(day.date()),
                    "state": "onset" if onset else "ongoing",
                    "action": ACTIONS[check],
                }
            )
    return findings


def check_commodity_incremental(pair: str, panel: pd.DataFrame) -> dict | None:
    """Abnormally large commodity incremental R² usually means a time-alignment
    problem in the oil price series."""
    factors = baseline_factors(pair)
    commodity = [f for f in EXTRA_FACTORS[pair] if f in COMMODITY_FACTORS]
    if not commodity:
        return None
    without = [f for f in factors if f not in commodity]

    full = sm.OLS(panel["y"], sm.add_constant(panel[factors], has_constant="add")).fit()
    base = sm.OLS(panel["y"], sm.add_constant(panel[without], has_constant="add")).fit()
    incremental = float(full.rsquared - base.rsquared)
    if incremental <= COMMODITY_INCREMENTAL_CAP:
        return None
    return {
        "check": "commodity_incremental_r2_high",
        "pair": pair,
        "factors": commodity,
        "incremental_r2": round(incremental, 4),
        "threshold": COMMODITY_INCREMENTAL_CAP,
        "action": ACTIONS["commodity_incremental_r2_high"],
    }


def run_health_checks(
    r2_panel: pd.DataFrame, panels: dict[str, pd.DataFrame], mode: str, as_of=None
) -> tuple[list[dict], list[dict]]:
    """Return (summary, current).

    summary is the product of this mode: backfill gives end-of-period summary
    statistics, live gives the day's state. current is always "what is triggering as
    of the last day"; the status colour looks only at it.

    The two must stay separate, or backfill's 16 years of historical statistics would
    be treated as current alerts and status would stay yellow forever — exactly the
    kind of dead alert the SPEC_phase2 2.5 monitoring discipline forbids.
    """
    summary = evaluate(r2_panel, mode, as_of)
    current = (
        summary
        if mode == "live"
        else evaluate(r2_panel, "live", as_of=as_of or (
            r2_panel.index[-1] if len(r2_panel) else None
        ))
    )

    # The two full-sample-convention checks: commodity incremental R² and the last
    # line of defense against tautological leakage. Both are "current state", so they
    # go into both summary and current.
    for pair, panel in panels.items():
        for finding in (
            check_commodity_incremental(pair, panel),
            check_absolute_ceiling(pair, panel),
        ):
            if finding:
                summary.append({**finding, "mode": mode})
                current.append({**finding, "mode": mode})

    for finding in summary:
        record("health_check", **finding)
    return summary, current
