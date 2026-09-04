"""Japan: Ministry of Finance English CSV, needs a browser UA.

The historical file sits under the historical/ subdirectory (requesting it at the
interest_rate/ root returns a 404 HTML page) and is spliced with the current-month
file by date, current month winning. Missing values are dashes; explanatory rows sit
at the tail.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from ..base import get_frame

ROOT = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate"
HISTORICAL_URL = f"{ROOT}/historical/jgbcme_all.csv"
CURRENT_URL = f"{ROOT}/jgbcme.csv"
TIMEOUT = 120
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TENOR_COL = {"short": "2Y", "long": "10Y"}


def _read(url: str) -> pd.DataFrame:
    response = requests.get(url, headers=UA, timeout=TIMEOUT)
    response.raise_for_status()
    if "<!DOCTYPE" in response.text[:200] or "<html" in response.text[:200].lower():
        raise RuntimeError(f"MoF returned HTML instead of CSV: {url}")
    # row 0 is a title; row 1 holds Date,1Y,2Y,...
    frame = pd.read_csv(io.StringIO(response.text), skiprows=1, na_values=["-", ""])
    frame = frame.rename(columns=lambda c: str(c).strip())
    if "Date" not in frame.columns:
        raise RuntimeError(f"MoF CSV has no Date column: {url}, got {list(frame.columns)[:6]}")
    index = pd.to_datetime(frame["Date"], format="%Y/%m/%d", errors="coerce")
    frame = frame.loc[index.notna()].set_index(index.dropna())  # drop trailing notes
    cols = {}
    for name, col in TENOR_COL.items():
        if col not in frame.columns:
            raise RuntimeError(f"MoF CSV has no {col} column: {url}")
        cols[name] = pd.to_numeric(frame[col], errors="coerce")
    return pd.DataFrame(cols).dropna(how="all")


def _fetch_raw() -> pd.DataFrame:
    history = _read(HISTORICAL_URL)
    current = _read(CURRENT_URL)
    combined = pd.concat([history, current])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def fetch() -> pd.DataFrame:
    return get_frame("foreign_jp_jgb", _fetch_raw)
