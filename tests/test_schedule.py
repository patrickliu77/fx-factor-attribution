"""Run modes and gap backfill (SPEC_phase2 1.1, 1.3, 6)."""

import pandas as pd
import pytest

from fxdash.schedule.modes import RunMode, missing_dates, resolve_range


def _dates(*days):
    return pd.DatetimeIndex([pd.Timestamp(d) for d in days])


def test_no_gap_when_contract_is_current():
    panel = _dates("2026-08-24", "2026-08-25", "2026-08-26")
    assert len(missing_dates(panel, panel)) == 0


def test_gap_after_an_outage_is_detected():
    """After days of downtime, the first live run must recognize which dates to backfill."""
    have = _dates("2026-08-17", "2026-08-18")
    want = _dates("2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21")
    missing = missing_dates(have, want)
    assert [str(d.date()) for d in missing] == ["2026-08-19", "2026-08-20", "2026-08-21"]


def test_interior_gap_is_detected_not_just_the_tail():
    have = _dates("2026-08-17", "2026-08-21")
    want = _dates("2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21")
    missing = missing_dates(have, want)
    assert [str(d.date()) for d in missing] == ["2026-08-18", "2026-08-19", "2026-08-20"]


def test_missing_dates_is_order_and_duplicate_safe():
    have = _dates("2026-08-18", "2026-08-17", "2026-08-17")
    want = _dates("2026-08-19", "2026-08-17", "2026-08-18", "2026-08-19")
    missing = missing_dates(have, want)
    assert [str(d.date()) for d in missing] == ["2026-08-19"]
    assert missing.is_monotonic_increasing


def test_empty_contract_wants_everything():
    want = _dates("2026-08-17", "2026-08-18")
    assert len(missing_dates(_dates(), want)) == 2


def test_backfill_honours_the_explicit_range():
    start, end = resolve_range(RunMode.BACKFILL, "2010-01-01", "2020-12-31",
                               contract_last="2026-08-20")
    assert start == pd.Timestamp("2010-01-01")
    assert end == pd.Timestamp("2020-12-31")


def test_live_without_history_falls_back_to_the_start():
    start, end = resolve_range(RunMode.LIVE, "2010-01-01", None, contract_last=None)
    assert start == pd.Timestamp("2010-01-01")
    assert end is None


def test_live_resumes_from_existing_history():
    """live resumes from the last date of existing history so provisional rows can be
    overwritten."""
    start, _ = resolve_range(RunMode.LIVE, "2026-08-20", None,
                             contract_last="2026-08-18")
    assert start == pd.Timestamp("2026-08-18")


def test_run_mode_values_are_the_documented_flags():
    assert RunMode.BACKFILL.value == "backfill"
    assert RunMode.LIVE.value == "live"
    assert RunMode("live") is RunMode.LIVE
