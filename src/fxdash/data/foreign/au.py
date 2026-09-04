"""Australia: RBA F2, current CSV spliced with historical xls, needs a browser UA.

The current CSV has 11 metadata rows; row 10 (0-based) is the Series ID row. The
historical xls Data sheet has the same layout, row 10 being the Mnemonic row, with
data from 1995-01-03. The 2Y has a vacuum from 2013-05-20 to 08-30, treated as
whole-row missing with no carry forward (SPEC 2.5); the worst-case 7-day staleness
from the F2 Friday release is carried by the stale flag, not filled here.
"""

from __future__ import annotations

import csv
import io

import pandas as pd
import requests

from ..base import get_frame, record

CURRENT_URL = "https://www.rba.gov.au/statistics/tables/csv/f2-data.csv"
HISTORICAL_URL = "https://www.rba.gov.au/statistics/tables/xls-hist/f02dhist.xls"
MNEMONIC = {"short": "FCMYGBAG2D", "long": "FCMYGBAG10D"}
ID_ROW = 10  # Series ID / Mnemonic row
TIMEOUT = 120
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _pick(frame: pd.DataFrame, ids) -> pd.DataFrame:
    """frame columns correspond to ids by position; extract the two mnemonic columns
    and convert to a date index."""
    lookup = {str(v).strip(): i for i, v in enumerate(ids) if pd.notna(v)}
    index = pd.to_datetime(frame.iloc[:, 0], errors="coerce", format="mixed")
    keep = index.notna().to_numpy()
    columns = {}
    for name, mnemonic in MNEMONIC.items():
        if mnemonic not in lookup:
            raise RuntimeError(f"RBA {mnemonic} not found, got {sorted(lookup)[:8]}")
        values = pd.to_numeric(frame.iloc[:, lookup[mnemonic]], errors="coerce")
        columns[name] = values.to_numpy()[keep]
    out = pd.DataFrame(columns, index=pd.DatetimeIndex(index.to_numpy()[keep]))
    return out.dropna(how="all").sort_index()


def _read_current() -> pd.DataFrame:
    response = requests.get(CURRENT_URL, headers=UA, timeout=TIMEOUT)
    response.raise_for_status()
    # The title row has a single field while later rows have six; read_csv errors on
    # inconsistent column counts, so pad the rows first
    rows = list(csv.reader(io.StringIO(response.text)))
    width = max(len(r) for r in rows)
    raw = pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object)
    return _pick(raw.iloc[ID_ROW + 1 :], raw.iloc[ID_ROW])


def _read_historical() -> pd.DataFrame:
    content = requests.get(HISTORICAL_URL, headers=UA, timeout=TIMEOUT).content
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Data", header=None)
    return _pick(raw.iloc[ID_ROW + 1 :], raw.iloc[ID_ROW])


def _fetch_raw() -> pd.DataFrame:
    current = _read_current()
    historical = _read_historical()
    combined = pd.concat([historical, current])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()

    vacuum = combined.loc["2013-05-20":"2013-08-30", "short"]
    record(
        "rba_2y_vacuum",
        first="2013-05-20",
        last="2013-08-30",
        n_days=int(len(vacuum)),
        n_missing=int(vacuum.isna().sum()),
        action="whole-row missing, no carry forward",
    )
    return combined


def fetch() -> pd.DataFrame:
    return get_frame("foreign_au_rba", _fetch_raw)
