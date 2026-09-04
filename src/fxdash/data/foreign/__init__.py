"""Six-country official daily government bond yields.

Endpoints, series keys and format pitfalls are documented at the top of each country
module. Unofficial daily sources must never serve as the primary source (CLAUDE.md 9).
Every fetcher returns a DataFrame with columns fixed as short/long, in percentage points.
"""

from __future__ import annotations

import pandas as pd

from . import au, ca, de, jp, mx, no

FETCHERS = {
    "USDEUR": de.fetch,
    "USDJPY": jp.fetch,
    "USDCAD": ca.fetch,
    "USDNOK": no.fetch,
    "USDAUD": au.fetch,
    "USDMXN": mx.fetch,
}


def fetch_foreign_yields(pair: str) -> pd.DataFrame:
    """Return the short/long yields plus the break_short/break_long break flags.

    A break flag means that day's level comes from a different convention than the
    previous day's (Norway splice day, Mexico bond-switch day); its diff has no
    economic meaning and must be blanked when building spread factors.
    """
    frame = FETCHERS[pair]()
    missing = {"short", "long"} - set(frame.columns)
    if missing:
        raise RuntimeError(f"{pair} foreign leg missing columns {sorted(missing)}")
    for col in ("break_short", "break_long"):
        if col in frame.columns:
            frame[col] = frame[col].fillna(0).astype(bool)
        else:
            frame[col] = False
    return frame[["short", "long", "break_short", "break_long"]]


__all__ = ["fetch_foreign_yields", "FETCHERS", "au", "ca", "de", "jp", "mx", "no"]
