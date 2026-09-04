"""yfinance source: FX, commodities, ETFs.

FX is converted to the uniform USD/XXX convention where a rising value means a
stronger USD (CLAUDE.md 3), and validated with full-sample median assertions
(CLAUDE.md 4).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..config import CMDTY_TICKERS, DIRECTION_RANGES, ETF_TICKERS, FX_TICKERS, START
from .base import get_series, record

log = logging.getLogger(__name__)

# Daily bar roll time (US Eastern). FX and CME commodity daily bars roll at 17:00 ET,
# US equity ETFs close at 16:00; uniformly use 17:00 plus a 15-minute buffer to decide
# the "last closed bar".
BAR_CLOSE_HOUR_ET = 17
BAR_CLOSE_BUFFER_MIN = 15


def last_closed_bar_date(now=None) -> pd.Timestamp:
    """Largest bar date acceptable at the current moment (tz-naive date).

    yfinance hands out the daily bar that has **not yet closed**, and the date it
    carries can change after the fact: the 2026-08-30 (Sunday) 20:30 ET batch run got
    a bar stamped 08-31 — Monday's unfinished bar 3.5 hours into the session, later
    withdrawn upstream in its entirety (08-31 was a UK bank holiday) — and the
    contract consequently froze 45 rows belonging to a nonexistent date. The
    criterion must be the clock, not the data itself: a bar stamped D closes at the
    earliest at 17:00 ET on day D, so while now < 17:15 ET on day D that bar cannot
    have closed and is always dropped.
    """
    now_et = (
        pd.Timestamp.now(tz="America/New_York")
        if now is None
        else pd.Timestamp(now).tz_convert("America/New_York")
    )
    cutoff = now_et.normalize()
    closed_today = (now_et.hour, now_et.minute) >= (
        BAR_CLOSE_HOUR_ET,
        BAR_CLOSE_BUFFER_MIN,
    )
    if not closed_today:
        cutoff -= pd.Timedelta(days=1)
    return cutoff.tz_localize(None)


def _download(ticker: str, auto_adjust: bool) -> pd.Series:
    import yfinance

    frame = yfinance.download(
        ticker,
        start=START,
        auto_adjust=auto_adjust,
        progress=False,
        actions=False,
        threads=False,
    )
    if frame is None or len(frame) == 0:
        raise RuntimeError(f"yfinance empty response: {ticker}")
    if isinstance(frame.columns, pd.MultiIndex):
        # even a single ticker can come back with two column levels (Price, Ticker)
        frame = frame.xs(ticker, axis=1, level=-1)
    if "Close" not in frame.columns:
        raise RuntimeError(f"yfinance has no Close column: {ticker}, got {list(frame.columns)}")
    series = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(series) == 0:
        raise RuntimeError(f"yfinance Close all missing: {ticker}")

    # Drop unclosed bars (criterion: last_closed_bar_date). All yfinance sources
    # filter at this single entry point, so the cache only ever holds closed data.
    index = pd.to_datetime(series.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    # keep is a boolean ndarray (return type of a DatetimeIndex comparison), used
    # directly as a mask
    keep = (index.normalize() <= last_closed_bar_date()).astype(bool)
    dropped = int((~keep).sum())
    if dropped:
        record(
            "unclosed_bar_dropped",
            ticker=ticker,
            n=dropped,
            dates=[str(d.date()) for d in index[~keep][-3:]],
        )
        series = series[keep]
    if len(series) == 0:
        raise RuntimeError(f"yfinance all bars unclosed: {ticker}")
    return series.rename(ticker)


def fetch_fx_closes() -> dict[str, pd.Series]:
    """USD/XXX close levels for the six pairs."""
    closes: dict[str, pd.Series] = {}
    for ticker, (pair, invert) in FX_TICKERS.items():
        series = get_series(ticker, lambda t=ticker: _download(t, auto_adjust=False))
        if invert:
            series = 1.0 / series
        closes[pair] = series.rename(pair)

    for pair, series in closes.items():
        low, high = DIRECTION_RANGES[pair]
        median = float(series.median())
        ok = bool(low <= median <= high)
        record(
            "direction_check",
            pair=pair,
            median=round(median, 4),
            low=low,
            high=high,
            passed=ok,
        )
        if not ok:
            raise AssertionError(
                f"direction check failed: {pair} full-sample median {median:.4f} outside [{low}, {high}]; "
                "quote direction or inversion is wrong"
            )
    return closes


def fetch_commodity_closes() -> dict[str, pd.Series]:
    """Commodity closes. Blank the negative WTI prices of 2020-04, otherwise the log
    return blows up."""
    out: dict[str, pd.Series] = {}
    for ticker, name in CMDTY_TICKERS.items():
        series = get_series(ticker, lambda t=ticker: _download(t, auto_adjust=False))
        bad = series[series <= 0]
        if len(bad):
            record(
                "nonpositive_price",
                series=name,
                n=len(bad),
                dates=[str(d.date()) for d in bad.index],
            )
            series = series.where(series > 0)
        out[name] = series.rename(name)
    return out


def fetch_etf_closes() -> dict[str, pd.Series]:
    """ETFs use adjusted closes: EMB and HYG are bond ETFs where distributions are
    not negligible."""
    return {
        name: get_series(
            f"{ticker}_adj", lambda t=ticker: _download(t, auto_adjust=True)
        ).rename(name)
        for ticker, name in ETF_TICKERS.items()
    }


def fetch_vix_fallback() -> pd.Series:
    """Fallback path when FRED VIXCLS is unavailable."""
    return _download("^VIX", auto_adjust=False).rename("VIXCLS")


def log_returns(series: pd.Series) -> pd.Series:
    """Price to daily log return (CLAUDE.md 5)."""
    return np.log(series.where(series > 0)).diff()
