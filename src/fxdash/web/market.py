"""Market layer: **display-only price levels** for the ticker and the trend
charts (SPEC_web §2.1).

Kept completely separate from the attribution numbers. contract holds only log
returns and the attribution decomposition, no price levels, while the ticker and
the six trend charts need exactly those levels, so this read-only adapter over
data/cache/ fills the gap.

Three rules hold:

- **Read-only**. Never write data/, never import the engine's data/ modules (the
  cache path rule is restated here).
- **Open and close fast**. pandas releases the handle as soon as the read is
  done, so the 19:30 pipeline never hits a PermissionError on write.
- **Never enters attribution**. No number this module produces takes part in, or
  corrects, contract's attribution conclusions; every number on the attribution
  side of the page still comes from contract.

When the cache is unavailable, degrade wholesale to available=False; the
frontend shows a placeholder and nothing is raised.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, FX_TICKERS, PAIRS

log = logging.getLogger(__name__)

# ICE dollar index. It is not in data/cache (DXY is not a factor of this model,
# PLAN replaces it with DOLLAR_LOO), so this is the market layer's only network
# fetch, and it serves display only.
DXY_SYMBOL = "DX-Y.NYB"
DXY_TTL_S = 30 * 60
# an item whose last bar trails the session by more than this many days is
# dropped entirely. The upstream site occasionally misses a day (DXY really has
# no 2026-08-28 bar); one missing day still displays. Trailing by a week means
# the source is dead, and broadcasting its stale value as "today's change" would
# be a lie.
STALE_LIMIT_DAYS = 7
_dxy_lock = threading.Lock()
_dxy_cache = {"series": None, "at": 0.0}

# display board. These were not picked to pad it out: oil, copper, gold, VIX and
# the US 10Y are all registered factors of this model (oil for NOK/CAD, copper
# for AUD, VIX is the risk leg, DGS10 is the US side of the rate-differential
# leg), so what scrolls on the ticker is exactly what drives the six
# decompositions below.
# (cache_name, code, label, kind, digits)
BOARD = [
    ("CL=F", "WTI", "WTI Crude", "px", 2),
    ("BZ=F", "BRENT", "Brent Crude", "px", 2),
    ("GC=F", "GOLD", "Gold", "px", 2),
    ("HG=F", "COPPER", "Copper", "px", 4),
    ("VIXCLS", "VIX", "VIX", "px", 2),
    ("DGS10", "US10Y", "US 10Y", "yield", 2),
]


def _fetch_dxy():
    """Fetch the DXY daily series. On failure return None, the board simply has
    one item fewer, and nothing is raised.

    Carries a 30-minute in-memory TTL: snapshot rebuilds (startup + the nightly
    hot reload) pass through here and should not hit the network every time.
    **Nothing is written to disk**; data/ stays read-only to the web layer.
    """
    now = time.monotonic()
    with _dxy_lock:
        cached = _dxy_cache["series"]
        if cached is not None and now - _dxy_cache["at"] < DXY_TTL_S:
            return cached
    try:
        import yfinance as yf
        frame = yf.download(DXY_SYMBOL, period="10y", interval="1d",
                            progress=False, auto_adjust=False, threads=False)
        if frame is None or frame.empty:
            raise ValueError("empty frame")
        close = frame["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        close = close.dropna().astype(float)
        if len(close) < 2:
            raise ValueError("too short")
    except Exception as exc:
        log.warning("DXY fetch failed, the board skips this item: %s", exc)
        with _dxy_lock:
            return _dxy_cache["series"]  # keep using the old value if there is one
    with _dxy_lock:
        _dxy_cache["series"] = close
        _dxy_cache["at"] = now
    return close


PAIR_LABEL = {
    "USDEUR": "USD/EUR", "USDJPY": "USD/JPY", "USDCAD": "USD/CAD",
    "USDNOK": "USD/NOK", "USDAUD": "USD/AUD", "USDMXN": "USD/MXN",
}

# chart time range -> number of trading-day rows. None means full history; ytd
# is sliced by calendar year separately.
RANGES = {
    "1d": 2, "5d": 5, "1m": 21, "6m": 126,
    "ytd": None, "1y": 252, "5y": 1260, "max": None,
}
INTRADAY_RANGES = {"1d"}  # daily data cannot draw intraday, frontend shows a placeholder


def _nyse_holidays(years) -> set:
    """Federal holidays minus Columbus Day and Veterans Day (the market is open
    on both), approximating the NYSE calendar."""
    try:
        from pandas.tseries.holiday import USFederalHolidayCalendar
    except Exception:
        return set()
    cal = USFederalHolidayCalendar()
    lo, hi = f"{min(years)}-01-01", f"{max(years)}-12-31"
    named = cal.holidays(start=lo, end=hi, return_name=True)
    return {
        pd.Timestamp(day).normalize()
        for day, name in named.items()
        if name not in ("Columbus Day", "Veterans Day")
    }


def is_trading_day(now=None) -> bool:
    """Whether today (US Eastern) is a trading day. Weekends and US market
    holidays are both no (the ticker is hidden on non-trading days)."""
    now_et = (pd.Timestamp.now(tz="America/New_York") if now is None
              else pd.Timestamp(now).tz_convert("America/New_York"))
    today = now_et.normalize().tz_localize(None)
    if today.weekday() >= 5:
        return False
    return today not in _nyse_holidays([today.year])


def _daily_index(series):
    """Normalise a time index from any source to tz-naive midnight dates.

    Yahoo's timestamps carry a timezone and a time of day. Skipping the
    normalisation has two consequences, both of which really happened: today's
    bar is judged later than session midnight and dropped entirely (so DXY runs
    permanently a day behind everything else); and comparing a tz-aware index
    with a naive Timestamp raises, so one broken source wipes out the whole board.
    """
    idx = pd.to_datetime(series.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out = pd.Series(series.to_numpy(dtype=float), index=idx.normalize())
    return out[~out.index.duplicated(keep="last")].sort_index()


def _finite(value):
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


class MarketData:
    """Price-level snapshot. A failed build means available=False and the
    frontend takes the placeholder branch."""

    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.levels = {}
        self.board = []
        self.session_date = None

        for ticker, (pair, invert) in FX_TICKERS.items():
            series = self._read(ticker)
            if series is None:
                continue
            if invert:
                series = 1.0 / series.replace(0.0, np.nan).dropna()
            self.levels[pair] = series

        self.available = bool(self.levels)
        if not self.available:
            return

        # align the whole board to FX's last trading day. Without this the board
        # mixes dates: a commodity cache may hold an unclosed bar that the
        # pipeline itself (last_closed_bar_date) already rejected, and taking the
        # last value blindly would broadcast that bar's move as the day's change.
        # One session, one date.
        session = max(s.index[-1] for s in self.levels.values())
        self.session_date = session.strftime("%Y-%m-%d")

        for pair in PAIRS:
            if pair in self.levels:
                self.board.append(self._quote(
                    PAIR_LABEL.get(pair, pair), pair, self.levels[pair],
                    kind="fx", digits=4, pair=pair, asof=session,
                ))
        dxy = self._quote("Dollar Index", "DXY", _fetch_dxy(),
                          kind="index", digits=2, asof=session)
        if dxy is not None:
            self.board.append(dxy)
        for name, code, label, kind, digits in BOARD:
            series = self._read(name)
            quote = self._quote(label, code, series, kind=kind, digits=digits,
                                asof=session)
            if quote is not None:
                self.board.append(quote)

    def _read(self, name: str):
        safe = name.replace("=", "_").replace("^", "").replace("/", "_")
        path = self.cache_dir / (safe + ".parquet")
        if not path.exists():
            return None
        try:
            frame = pd.read_parquet(path)  # open and close fast
        except Exception as exc:
            log.warning("market cache unreadable %s: %s", path.name, exc)
            return None
        if frame.empty or not len(frame.columns):
            return None
        series = frame.iloc[:, 0].astype(float)
        series.index = pd.to_datetime(series.index)
        series = series[np.isfinite(series.to_numpy(dtype=float))]
        return series.sort_index() if len(series) >= 2 else None

    @staticmethod
    def _quote(label, code, series, *, kind, digits, pair=None, asof=None):
        """One quote. A broken item just means one item fewer, never a broken board."""
        if series is None or not len(series):
            return None
        try:
            series = _daily_index(series)
            if asof is not None:
                series = series[series.index <= asof]
        except Exception as exc:
            log.warning("board item %s unavailable: %s", code, exc)
            return None
        if len(series) < 2:
            return None
        if asof is not None and (asof - series.index[-1]).days > STALE_LIMIT_DAYS:
            log.warning("board item %s trails the session by more than %d days, hidden",
                        code, STALE_LIMIT_DAYS)
            return None
        last, prev = float(series.iloc[-1]), float(series.iloc[-2])
        if kind == "yield":
            # bp for a yield is the level difference times 100, not the return of a yield
            chg_bp = (last - prev) * 100.0
            chg_pct = ((last / prev) - 1.0) * 100.0 if prev else None
        else:
            chg_pct = ((last / prev) - 1.0) * 100.0 if prev else None
            chg_bp = chg_pct * 100.0 if chg_pct is not None else None
        return {
            "code": code, "label": label, "pair": pair, "kind": kind,
            "digits": digits,
            "last": _finite(last),
            "prev": _finite(prev),
            "chg_bp": _finite(chg_bp),
            "chg_pct": _finite(chg_pct),
            "direction": 0 if last == prev else (1 if last > prev else -1),
            "date": series.index[-1].strftime("%Y-%m-%d"),
        }

    def series(self, pair: str, range_key: str) -> dict:
        series = self.levels.get(pair)
        if series is None:
            return {"available": False, "reason": "no_cache"}
        if range_key in INTRADAY_RANGES:
            return {"available": False, "reason": "intraday_pending",
                    "range": range_key}

        if range_key == "ytd":
            year = series.index[-1].year
            block = series[series.index >= pd.Timestamp(year=year, month=1, day=1)]
        else:
            n = RANGES.get(range_key)
            block = series if n is None else series.iloc[-min(n, len(series)):]
        if len(block) < 2:
            return {"available": False, "reason": "too_short", "range": range_key}

        values = block.to_numpy(dtype=float)
        first, last = float(values[0]), float(values[-1])
        return {
            "available": True,
            "pair": pair,
            "label": PAIR_LABEL.get(pair, pair),
            "range": range_key,
            "dates": [d.strftime("%Y-%m-%d") for d in block.index],
            "values": [_finite(v) for v in values],
            "first": _finite(first),
            "last": _finite(last),
            "min": _finite(values.min()),
            "max": _finite(values.max()),
            "chg_pct": _finite(((last / first) - 1.0) * 100.0) if first else None,
            "chg_bp": _finite(((last / first) - 1.0) * 10000.0) if first else None,
            "direction": 0 if last == first else (1 if last > first else -1),
            "digits": 4,
        }

    def ticker(self, now=None) -> dict:
        return {
            "available": self.available,
            "trading_day": is_trading_day(now),
            "session_date": self.session_date,
            "items": self.board,
        }
