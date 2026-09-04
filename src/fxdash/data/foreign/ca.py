"""Canada: Bank of Canada Valet JSON, no auth. No missing values since 2001; the
cleanest of the six."""

from __future__ import annotations

import pandas as pd
import requests

from ...config import START
from ..base import get_frame

BASE = "https://www.bankofcanada.ca/valet/observations"
SERIES = {"short": "BD.CDN.2YR.DQ.YLD", "long": "BD.CDN.10YR.DQ.YLD"}
TIMEOUT = 60


def _fetch_one(series_id: str) -> pd.Series:
    response = requests.get(
        f"{BASE}/{series_id}/json", params={"start_date": START}, timeout=TIMEOUT
    )
    response.raise_for_status()
    observations = response.json().get("observations", [])
    if not observations:
        raise RuntimeError(f"BoC Valet empty response: {series_id}")
    dates = [o["d"] for o in observations]
    values = [o.get(series_id, {}).get("v") for o in observations]
    return pd.Series(
        pd.to_numeric(values, errors="coerce"), index=pd.to_datetime(dates)
    ).dropna()


def _fetch_raw() -> pd.DataFrame:
    return pd.DataFrame({name: _fetch_one(sid) for name, sid in SERIES.items()})


def fetch() -> pd.DataFrame:
    return get_frame("foreign_ca_boc", _fetch_raw)
