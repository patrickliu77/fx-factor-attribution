"""Unclosed-bar criterion and orphan-row purge (the three defence lines of the
2026-08-31 incident).

Incident: at 20:30 ET on 2026-08-30 (Sunday), yfinance handed the batch run an
unclosed bar stamped 08-31 (Monday's session, 3.5 hours in); the system accepted it
wholesale and froze 45 rows. Upstream later withdrew the 08-31 bar entirely (UK bank
holiday), leaving 45 orphan rows in the contract on a nonexistent date, which the
freeze rule made impossible to correct or delete.

Three defence lines: the clock criterion rejects unclosed bars; the data frontier is
marked provisional (test_factors); purge_beyond removes invalidated rows beyond the
recompute window.
"""

import json

import pandas as pd
import pytest

from fxdash.data.yf_source import last_closed_bar_date
from fxdash.schedule.merge import merge_contract
from fxdash.schedule.modes import source_as_of


def _et(text):
    return pd.Timestamp(text, tz="America/New_York")


class TestLastClosedBarDate:
    """The criterion must be the clock, not the data: a bar stamped D closes at the
    earliest at 17:00 ET on day D."""

    def test_sunday_evening_rejects_mondays_bar(self):
        # 2026-08-30 Sunday 20:30 ET: the incident scene. A bar stamped 08-31 must
        # be rejected
        cutoff = last_closed_bar_date(_et("2026-08-30 20:30"))
        assert cutoff == pd.Timestamp("2026-08-30")
        assert pd.Timestamp("2026-08-31") > cutoff  # rejected
        assert pd.Timestamp("2026-08-28") <= cutoff  # Friday's bar is kept

    def test_monday_evening_rejects_tuesdays_bar(self):
        cutoff = last_closed_bar_date(_et("2026-08-31 20:30"))
        assert cutoff == pd.Timestamp("2026-08-31")
        assert pd.Timestamp("2026-09-01") > cutoff

    def test_before_close_todays_bar_is_rejected(self):
        cutoff = last_closed_bar_date(_et("2026-08-31 16:59"))
        assert cutoff == pd.Timestamp("2026-08-30")

    def test_buffer_minutes_matter(self):
        # Between 17:00 and 17:15 the same-day bar is still rejected, leaving a
        # buffer for close data to land
        assert last_closed_bar_date(_et("2026-08-31 17:10")) == pd.Timestamp("2026-08-30")
        assert last_closed_bar_date(_et("2026-08-31 17:20")) == pd.Timestamp("2026-08-31")

    def test_other_timezone_input_converts(self):
        # 21:30 CT = 22:30 ET; the same-day bar has closed
        cutoff = last_closed_bar_date(
            pd.Timestamp("2026-08-31 21:30", tz="America/Chicago")
        )
        assert cutoff == pd.Timestamp("2026-08-31")


def _row(date, pair="USDEUR", provisional=False, r2=0.5):
    return {
        "date": pd.Timestamp(date), "pair": pair, "window": 126, "model": "ols",
        "betas": json.dumps({}), "contributions": json.dumps({}),
        "r2_full": r2, "r2_exog": r2 - 0.1, "selected_factors": json.dumps([]),
        "residual": 0.0, "residual_z": 0.0, "stale_flags": json.dumps([]),
        "systematic": 0.0, "exogenous": 0.0, "y": 0.001, "lambda": None,
        "provisional": provisional, "schema_version": "1.1.0",
    }


class TestPurgeBeyond:
    """Orphan rows fall beyond the recompute window; purge_beyond extends the purge
    to the existing last date."""

    def test_orphans_beyond_incoming_max_are_dropped(self):
        # 08-31 and 09-01 are orphans (dates withdrawn upstream); incoming only
        # reaches 08-28
        existing = pd.DataFrame(
            [_row("2026-08-28"), _row("2026-08-31"), _row("2026-09-01")]
        )
        incoming = pd.DataFrame([_row("2026-08-28", r2=0.6)])
        result = merge_contract(
            existing, incoming, as_of_advanced=False,
            rewrite_history=True, purge_beyond=True,
        )
        assert [str(d.date()) for d in result.frame["date"]] == ["2026-08-28"]

    def test_without_purge_beyond_orphans_survive(self):
        # This was the behaviour during the incident: orphan rows outside the
        # recompute window could not be removed
        existing = pd.DataFrame([_row("2026-08-28"), _row("2026-08-31")])
        incoming = pd.DataFrame([_row("2026-08-28", r2=0.6)])
        result = merge_contract(
            existing, incoming, as_of_advanced=False, rewrite_history=True,
        )
        assert len(result.frame) == 2

    def test_partial_backfill_must_not_purge_the_future(self):
        # A partial backfill (explicit --end) must never enable purge_beyond -- that
        # is guaranteed by run.py; here we pin that merge itself keeps history beyond
        # the window when the flag is off
        existing = pd.DataFrame([_row("2015-06-30"), _row("2026-08-28")])
        incoming = pd.DataFrame([_row("2015-06-30", r2=0.9)])
        result = merge_contract(
            existing, incoming, as_of_advanced=False, rewrite_history=True,
        )
        dates = sorted(str(d.date()) for d in result.frame["date"])
        assert dates == ["2015-06-30", "2026-08-28"]


def test_source_as_of_includes_fx_frontier(synthetic_raw):
    """The FX frontier enters as_of: frontier provisional rows rely on it to unlock
    overwrite the next day."""
    out = source_as_of(synthetic_raw)
    for pair in synthetic_raw.fx_levels:
        assert f"{pair}.fx" in out
    # the publication-lag leg is still present
    assert "USDAUD.foreign" in out


def test_download_filters_unclosed_bars(monkeypatch):
    """The filter must actually take effect inside _download.

    Lesson from the evening of 2026-08-31: the criterion function itself was correct,
    but the one line applying it raised AttributeError and every online fetch
    silently fell back to the stale cache -- exactly the silent degradation of most
    concern. This test exercises the real _download path and does not mock the
    filter itself.
    """
    import sys

    import fxdash.data.yf_source as yf

    future = pd.Timestamp.now().normalize() + pd.Timedelta(days=5)
    frame = pd.DataFrame(
        {"Close": [1.0, 2.0, 3.0]},
        index=pd.DatetimeIndex(
            [pd.Timestamp("2026-08-27"), pd.Timestamp("2026-08-28"), future]
        ),
    )

    class _FakeYF:
        @staticmethod
        def download(*args, **kwargs):
            return frame.copy()

    monkeypatch.setitem(sys.modules, "yfinance", _FakeYF)
    series = yf._download("TEST=X", auto_adjust=False)
    assert future not in series.index
    assert len(series) == 2
    assert list(series.values) == [1.0, 2.0]
