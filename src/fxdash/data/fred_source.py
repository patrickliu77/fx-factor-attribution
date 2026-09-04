"""FRED source: US yields, VIX, BAA10Y.

With FRED_API_KEY the official API is used, otherwise fall back to the fredgraph CSV
unauthenticated export. The key is read only from the environment variable and never
enters any error message, to avoid leaking via logs or tracebacks (CLAUDE.md 11).
"""

from __future__ import annotations

import io
import logging
import os

import pandas as pd
import requests

from ..config import START
from .base import get_series
from .yf_source import fetch_vix_fallback

log = logging.getLogger(__name__)

TIMEOUT = 30
# The unauthenticated export clamps ICE BofA licensed series to the last three years;
# full-history series must validate their start date
FULL_HISTORY_GUARD = {"BAA10Y": "2011-01-01"}


def _api_key() -> str:
    return os.environ.get("FRED_API_KEY", "").strip()


def fetch_fred(series_id: str) -> pd.Series:
    key = _api_key()
    if key:
        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "observation_start": START,
            },
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            # no URL or params here, so the key cannot appear in the exception
            raise RuntimeError(f"FRED API request failed: {series_id}, HTTP {response.status_code}")
        obs = response.json()["observations"]
        series = pd.Series(
            [o["value"] for o in obs],
            index=pd.to_datetime([o["date"] for o in obs]),
            name=series_id,
        )
        series = pd.to_numeric(series, errors="coerce")
    else:
        response = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": series_id, "cosd": START},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        raw = pd.read_csv(io.StringIO(response.text), na_values=".")
        series = pd.Series(
            pd.to_numeric(raw.iloc[:, 1], errors="coerce").values,
            index=pd.to_datetime(raw.iloc[:, 0]),
            name=series_id,
        )

    series = series.dropna()
    series = series[series.index >= pd.Timestamp(START)]
    if len(series) == 0:
        raise RuntimeError(f"FRED empty response: {series_id}")

    guard = FULL_HISTORY_GUARD.get(series_id)
    if guard and series.index[0] > pd.Timestamp(guard):
        raise RuntimeError(
            f"{series_id} starts {series.index[0].date()}, later than {guard}; not full history, stopping per convention."
        )
    return series


def get_fred(series_id: str) -> pd.Series:
    return get_series(series_id, lambda: fetch_fred(series_id))


def get_vix() -> pd.Series:
    """VIXCLS first; when unavailable fall back to yfinance ^VIX, recorded by name."""
    return get_series("VIXCLS", lambda: fetch_fred("VIXCLS"), user_loader=fetch_vix_fallback)


def get_us_yields(series_ids) -> dict[str, pd.Series]:
    return {sid: get_fred(sid) for sid in sorted(set(series_ids))}
