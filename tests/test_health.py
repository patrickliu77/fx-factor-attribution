"""Health check upgrade: dual mode, two relative-threshold layers ANDed, transition
alerts (SPEC_phase2 section 2)."""

import numpy as np
import pandas as pd
import pytest

from fxdash.config import (
    CROSS_PAIR_Z_THRESHOLD,
    HEALTH_PERSIST_DAYS,
    HEALTH_QUANTILE,
    HEALTH_R2_HIGH,
    HEALTH_R2_LOW,
    PAIRS,
)
from fxdash.factors.build import build_pair_panel
from fxdash.health import (
    absolute_floor_alerts,
    check_absolute_ceiling,
    check_commodity_incremental,
    evaluate,
    relative_r2_alerts,
    run_health_checks,
)


def _panel(n=600, base=0.5, seed=0):
    """r2 panel of the six pairs, all healthy by default."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {p: base + rng.normal(0, 0.02, n) for p in PAIRS}, index=index
    )


def test_healthy_panel_raises_nothing():
    panel = _panel()
    assert evaluate(panel, "backfill") == []
    assert evaluate(panel, "live") == []


def test_market_wide_drop_is_suppressed():
    """A market-wide drop is normal in a crisis; the second layer's job is to suppress
    it, not alert pair by pair."""
    panel = _panel()
    panel.iloc[-60:] -= 0.35  # all six pairs drop together
    findings = evaluate(panel, "live")
    assert [f for f in findings if f["check"] == "r2_relative_low"] == []


def test_single_pair_falling_behind_alerts():
    """Alert only when a single pair lags the other five."""
    panel = _panel()
    panel.iloc[-20:, panel.columns.get_loc("USDNOK")] -= 0.35
    findings = evaluate(panel, "live")
    flagged = [f for f in findings if f["check"] == "r2_relative_low"]
    assert len(flagged) == 1
    assert flagged[0]["pair"] == "USDNOK"


def test_a_sustained_shift_stops_alerting_once_it_becomes_the_new_normal():
    """The quantile is relative to own history; a sustained low level is absorbed by
    the distribution and the alert goes quiet.

    Not a defect but a property the monitoring discipline (SPEC_phase2 2.5) requires:
    an alert must be able to return to green. The relative criterion answers "is this
    pair worse than its own last year"; after a regime change the answer should indeed
    be no. Genuinely persistent level problems are caught by the absolute floor.
    """
    panel = _panel(n=800)
    column = panel.columns.get_loc("USDNOK")
    panel.iloc[-400:, column] -= 0.35  # dropped and never came back
    fired = relative_r2_alerts(panel)["USDNOK"]

    assert fired.any()  # alerted when it first dropped
    assert not bool(fired.iloc[-1])  # but did not keep ringing


def test_a_brief_dip_does_not_alert():
    """Anything lasting under 10 trading days does not trigger."""
    panel = _panel()
    panel.iloc[-4:, panel.columns.get_loc("USDNOK")] -= 0.35
    assert [f for f in evaluate(panel, "live") if f["check"] == "r2_relative_low"] == []


def test_alert_fires_once_on_transition_then_reports_ongoing():
    """The alert fires once at the transition, then shows as an ongoing state
    (SPEC_phase2 2.3)."""
    panel = _panel()
    panel.iloc[-60:, panel.columns.get_loc("USDNOK")] -= 0.35
    fired = relative_r2_alerts(panel)["USDNOK"]
    onset_day = fired[fired].index[0]

    at_onset = evaluate(panel, "live", as_of=onset_day)
    assert any(f["state"] == "onset" for f in at_onset if f["pair"] == "USDNOK")

    later = fired[fired].index[10]
    at_later = evaluate(panel, "live", as_of=later)
    assert any(f["state"] == "ongoing" for f in at_later if f["pair"] == "USDNOK")


def test_backfill_reports_summary_not_daily_state():
    """backfill gives end-of-period summary statistics only, no daily output
    (SPEC_phase2 2.1)."""
    panel = _panel()
    panel.iloc[-60:, panel.columns.get_loc("USDNOK")] -= 0.35
    findings = evaluate(panel, "backfill")
    flagged = [f for f in findings if f["check"] == "r2_relative_low"]
    assert len(flagged) == 1
    assert "onsets" in flagged[0] and "days_fired" in flagged[0]
    assert "state" not in flagged[0]


def test_rolling_r2_does_not_carry_the_ceiling_any_more():
    """The 75% ceiling moved to the full-sample convention. A high rolling R² no
    longer triggers; otherwise AUD would stay lit long-term."""
    panel = _panel(base=0.5)
    panel.iloc[-HEALTH_PERSIST_DAYS:, panel.columns.get_loc("USDAUD")] = (
        HEALTH_R2_HIGH + 0.1
    )
    assert "r2_above_ceiling" not in absolute_floor_alerts(panel)["USDAUD"]
    assert [f for f in evaluate(panel, "live") if f["check"] == "r2_above_ceiling"] == []


def test_full_sample_ceiling_stays_silent_on_a_healthy_panel(synthetic_raw):
    """Silent in normal times: a realistically scaled panel must not trip the last
    line of defense."""
    for pair in PAIRS:
        panel = build_pair_panel(pair, synthetic_raw)
        assert check_absolute_ceiling(pair, panel) is None


def test_full_sample_ceiling_fires_on_a_tautological_panel(synthetic_raw):
    """Certain to fire when things go wrong: build y as a linear combination of the
    factors, simulating LOO leakage from not excluding self."""
    panel = build_pair_panel("USDEUR", synthetic_raw).copy()
    panel["y"] = panel["DOLLAR_LOO"] * 1.0 + panel["dVIX"] * 1e-6
    finding = check_absolute_ceiling("USDEUR", panel)
    assert finding is not None
    assert finding["full_sample_r2"] > HEALTH_R2_HIGH
    assert "leave one out" in finding["action"]


def test_absolute_floor_low_triggers_when_sustained():
    panel = _panel()
    panel.iloc[-HEALTH_PERSIST_DAYS:, panel.columns.get_loc("USDEUR")] = (
        HEALTH_R2_LOW - 0.02
    )
    findings = evaluate(panel, "live")
    assert any(f["check"] == "r2_below_floor" for f in findings)


def test_commodity_check_skips_pairs_without_a_commodity_factor(synthetic_raw):
    panel = build_pair_panel("USDEUR", synthetic_raw)
    assert check_commodity_incremental("USDEUR", panel) is None


def test_commodity_check_fires_when_oil_explains_too_much(synthetic_raw):
    panel = build_pair_panel("USDCAD", synthetic_raw).copy()
    panel["y"] = 0.8 * panel["WTI"] + 0.0001 * panel["DOLLAR_LOO"]
    finding = check_commodity_incremental("USDCAD", panel)
    assert finding is not None
    assert finding["factors"] == ["WTI"]
    assert "time alignment" in finding["action"]


def test_commodity_check_quiet_on_realistic_panel(synthetic_raw):
    panel = build_pair_panel("USDCAD", synthetic_raw)
    assert check_commodity_incremental("USDCAD", panel) is None


def test_run_health_checks_is_quiet_on_a_clean_run(synthetic_raw):
    panels = {p: build_pair_panel(p, synthetic_raw) for p in PAIRS}
    summary, current = run_health_checks(_panel(), panels, "live")
    assert summary == [] and current == []


def test_backfill_summary_never_drives_status_colour():
    """Having triggered in history does not mean trouble now. summary and current must
    stay separate, or status stays yellow forever."""
    panel = _panel(n=800)
    column = panel.columns.get_loc("USDNOK")
    panel.iloc[300:360, column] -= 0.35  # triggered long ago, long since recovered

    summary, current = run_health_checks(panel, {}, "backfill")
    assert any(f["check"] == "r2_relative_low" for f in summary)
    assert current == []


def test_thresholds_are_the_finalised_ones():
    # before changing these numbers, rerun the measured trigger frequencies
    # (SPEC_phase2 2.5 monitoring discipline)
    assert HEALTH_QUANTILE == 0.10
    assert CROSS_PAIR_Z_THRESHOLD == -1.5
    assert HEALTH_PERSIST_DAYS == 10
    assert (HEALTH_R2_LOW, HEALTH_R2_HIGH) == (0.10, 0.75)
