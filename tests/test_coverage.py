"""Data coverage discipline (SPEC_phase2 1.7).

The original implementation lost 14% of the sample with nobody noticing; these two
gates exist because of that.
"""

import json

import pandas as pd
import pytest

from fxdash import coverage
from fxdash.config import (
    BACKFILL_START,
    COVERAGE_ROW_TOLERANCE,
    PANEL_START_TOLERANCE_DAYS,
)
from fxdash.coverage import CoverageError


def _panel(first, rows=100):
    index = pd.bdate_range(first, periods=rows)
    return pd.DataFrame({"y": range(rows)}, index=index)


def test_start_within_tolerance_passes():
    coverage.check_start({"USDEUR": _panel(BACKFILL_START)})


def test_start_slightly_late_is_tolerated():
    late = pd.bdate_range(BACKFILL_START, periods=PANEL_START_TOLERANCE_DAYS)[-1]
    coverage.check_start({"USDEUR": _panel(late)})


def test_start_far_late_is_a_red_light():
    """The upstream-history-silently-truncated case."""
    with pytest.raises(CoverageError, match="silently truncated"):
        coverage.check_start({"USDEUR": _panel("2012-04-25")})


def test_empty_panel_is_a_red_light():
    with pytest.raises(CoverageError, match="panel is empty"):
        coverage.check_start({"USDEUR": _panel(BACKFILL_START, rows=0)})


def test_first_run_writes_a_baseline():
    panels = {"USDEUR": _panel(BACKFILL_START, 200)}
    current = coverage.enforce(panels)
    assert current["USDEUR"]["rows"] == 200
    saved = json.loads(coverage.COVERAGE_PATH.read_text(encoding="utf-8"))
    assert saved["backfill_start"] == BACKFILL_START
    assert saved["pairs"]["USDEUR"]["rows"] == 200


def test_growing_history_is_fine():
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 200)})
    grown = coverage.enforce({"USDEUR": _panel(BACKFILL_START, 260)})
    assert grown["USDEUR"]["rows"] == 260


def test_row_count_shrinking_is_a_red_light():
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 200)})
    with pytest.raises(CoverageError, match="rows_decreased"):
        coverage.enforce({"USDEUR": _panel(BACKFILL_START, 150)})


def test_tail_jitter_within_tolerance_does_not_fire():
    """yfinance's unfinished intraday bar makes the tail come and go; a jitter of one
    or two rows must not alert."""
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 200)})
    current = coverage.enforce(
        {"USDEUR": _panel(BACKFILL_START, 200 - COVERAGE_ROW_TOLERANCE)}
    )
    assert current["USDEUR"]["rows"] == 200 - COVERAGE_ROW_TOLERANCE


def test_a_real_truncation_still_fires_despite_the_tolerance():
    """The tolerance is far smaller than what it is meant to catch: the original
    implementation was short 579 days."""
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 4300)})
    with pytest.raises(CoverageError, match="rows_decreased"):
        coverage.enforce({"USDEUR": _panel(BACKFILL_START, 4300 - 579)})


def test_start_moving_later_is_a_red_light():
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 300)})
    later = pd.bdate_range(BACKFILL_START, periods=6)[-1]
    with pytest.raises(CoverageError, match="start_moved_later"):
        coverage.enforce({"USDEUR": _panel(later, 300)})


def test_shrink_can_be_allowed_explicitly():
    """Use --allow-coverage-shrink for an intentional shrink, still leaving a trace."""
    coverage.enforce({"USDEUR": _panel(BACKFILL_START, 200)})
    current = coverage.enforce({"USDEUR": _panel(BACKFILL_START, 150)}, strict=False)
    assert current["USDEUR"]["rows"] == 150


def test_backfill_start_is_pinned():
    assert BACKFILL_START == "2010-01-01"
