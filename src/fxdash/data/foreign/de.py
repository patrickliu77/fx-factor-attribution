"""Germany: Bundesbank SDMX CSV, no auth.

Svensson-estimated spot yield curve. Holiday rows carry a single dot in the value
column and "No value available" in the comment column; drop the whole row rather
than filling zero.
"""

from __future__ import annotations

import io
import re

import pandas as pd
import requests

from ..base import get_frame

BASE = "https://api.statistiken.bundesbank.de/rest/download/BBSIS"
TENOR_KEY = {"short": "R02XX", "long": "R10XX"}  # 2Y / 10Y
TIMEOUT = 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}),([^,]*)")


def _fetch_one(tenor_key: str) -> pd.Series:
    url = f"{BASE}/D.I.ZST.ZI.EUR.S1311.B.A604.{tenor_key}.R.A.A._Z._Z.A"
    response = requests.get(
        url, params={"format": "csv", "lang": "en"}, headers=UA, timeout=TIMEOUT
    )
    response.raise_for_status()
    dates, values = [], []
    for line in io.StringIO(response.text):
        match = _ROW.match(line.lstrip("﻿"))
        if not match:
            continue  # two header rows
        raw = match.group(2).strip()
        if raw in ("", "."):
            continue  # holiday placeholder
        dates.append(match.group(1))
        values.append(raw)
    if not dates:
        raise RuntimeError(f"Bundesbank no data rows: {tenor_key}")
    series = pd.Series(
        pd.to_numeric(values, errors="coerce"), index=pd.to_datetime(dates)
    ).dropna()
    return series


def _fetch_raw() -> pd.DataFrame:
    cols = {name: _fetch_one(key) for name, key in TENOR_KEY.items()}
    return pd.DataFrame(cols)


def fetch() -> pd.DataFrame:
    return get_frame("foreign_de_bund", _fetch_raw)
