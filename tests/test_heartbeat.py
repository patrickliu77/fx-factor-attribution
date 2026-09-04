"""Scheduler heartbeat (SPEC_phase2 1.4 and section 5).

The most dangerous failure mode of an unattended system is going quiet: the scheduled
task stops running one day, the page keeps showing yesterday's content, the freshness
table still has values, and nothing anywhere says "it did not run today".
"""

import json

import pandas as pd
import pytest

from fxdash import heartbeat, status as status_mod
from fxdash.config import HEARTBEAT_CRIT_HOURS, HEARTBEAT_WARN_HOURS
from fxdash.status import GREEN, RED, YELLOW, build_status


NOW = pd.Timestamp("2026-08-28 20:00:00")


def _contract(date="2026-08-28"):
    return pd.DataFrame(
        [{"date": pd.Timestamp(date), "pair": "USDEUR", "provisional": False}]
    )


def test_only_live_runs_beat():
    assert heartbeat.beat("backfill", when=NOW) is None
    assert not heartbeat.HEARTBEAT_PATH.exists()
    assert heartbeat.beat("live", when=NOW) is not None
    assert heartbeat.HEARTBEAT_PATH.exists()


def test_fresh_beat_is_green():
    heartbeat.beat("live", when=NOW)
    pulse = heartbeat.assess(now=NOW + pd.Timedelta(hours=2))
    assert pulse["state"] == GREEN
    assert pulse["age_hours"] == pytest.approx(2.0)


def test_no_record_does_not_alarm():
    """A freshly installed repo should not start out red."""
    pulse = heartbeat.assess(now=NOW)
    assert pulse["state"] == GREEN
    assert pulse["last_live_success"] is None
    assert "no live run" in pulse["note"]


def test_stale_beyond_warn_turns_yellow():
    heartbeat.beat("live", when=NOW)
    pulse = heartbeat.assess(now=NOW + pd.Timedelta(hours=HEARTBEAT_WARN_HOURS + 1))
    assert pulse["state"] == YELLOW
    assert "suspected scheduler stall" in pulse["note"]


def test_stale_beyond_crit_turns_red():
    heartbeat.beat("live", when=NOW)
    pulse = heartbeat.assess(now=NOW + pd.Timedelta(hours=HEARTBEAT_CRIT_HOURS + 1))
    assert pulse["state"] == RED


def test_just_inside_the_warn_window_stays_green():
    heartbeat.beat("live", when=NOW)
    pulse = heartbeat.assess(now=NOW + pd.Timedelta(hours=HEARTBEAT_WARN_HOURS - 1))
    assert pulse["state"] == GREEN


def test_stale_manifest_timestamp_turns_status_yellow():
    """Forge a stale heartbeat; status must turn yellow -- the acceptance criterion."""
    heartbeat.beat("live", when=NOW)
    later = NOW + pd.Timedelta(hours=HEARTBEAT_WARN_HOURS + 3)
    status = build_status(_contract(), "live", {"contract": {}}, today=later)

    assert status["state"] == YELLOW
    assert any("suspected scheduler stall" in r for r in status["reasons"])
    assert status["heartbeat"]["state"] == YELLOW


def test_dead_scheduler_turns_status_red():
    heartbeat.beat("live", when=NOW)
    later = NOW + pd.Timedelta(hours=HEARTBEAT_CRIT_HOURS + 5)
    status = build_status(_contract(), "live", {"contract": {}}, today=later)
    assert status["state"] == RED


def test_healthy_pulse_keeps_status_green():
    heartbeat.beat("live", when=NOW)
    status = build_status(
        _contract(), "live", {"contract": {}}, today=NOW + pd.Timedelta(hours=3)
    )
    assert status["state"] == GREEN


def test_corrupt_heartbeat_file_is_survivable():
    heartbeat.HEARTBEAT_PATH.write_text("{ not json", encoding="utf-8")
    assert heartbeat.assess(now=NOW)["state"] == GREEN


def test_humanise_reads_naturally():
    assert heartbeat.humanise(None) == "—"
    assert "分钟前" in heartbeat.humanise(0.5)
    assert "小时前" in heartbeat.humanise(9.0)
    assert "天前" in heartbeat.humanise(96.0)


def test_thresholds_are_the_ruled_ones():
    assert HEARTBEAT_WARN_HOURS == 26
    assert HEARTBEAT_CRIT_HOURS == 72


def test_failed_run_writes_a_red_status(monkeypatch):
    """A failed run must leave a red status; the page must not stay on last time's green."""
    import fxdash.status as st
    from fxdash.run import main_guarded

    def boom(argv=None):
        raise RuntimeError("simulated fetch failure")

    monkeypatch.setattr("fxdash.run.main", boom)
    with pytest.raises(RuntimeError, match="simulated fetch failure"):
        main_guarded([])

    written = json.loads(st.STATUS_PATH.read_text(encoding="utf-8"))
    assert written["state"] == RED
    assert any("simulated fetch failure" in r for r in written["reasons"])


def test_guard_reraises_the_original_error(tmp_path, monkeypatch):
    """When the fallback status write fails, the original exception must not be masked."""
    import fxdash.status as st
    from fxdash.run import main_guarded

    monkeypatch.setattr(st, "STATUS_PATH", tmp_path / "nonexistent" / "x" / "s.json")
    monkeypatch.setattr("fxdash.run.main", lambda argv=None: (_ for _ in ()).throw(
        ValueError("original error")))
    with pytest.raises(ValueError, match="original error"):
        main_guarded([])
