"""Norway: Norges Bank, static synthetic xlsx spliced with the generic SDMX API.

Norway has no daily 2Y; the short slot uses 3Y (SPEC 2.2, with DGS3 on the US leg
accordingly). The synthetic series stopped updating 2021-06-30 and generic starts
2019-01-02; the splice date is 2019-01-02, whose diff is blanked and logged, with the
overlap's level gap and diff correlation kept on file (SPEC 2.4). When the query
window holds no data the API returns 404 rather than an empty 200; treat that as a
normal empty result.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import requests

from ..base import get_frame, record

GENERIC_BASE = "https://data.norges-bank.no/api/data/GOVT_GENERIC_RATES"
SYNTHETIC_URL = (
    "https://www.norges-bank.no/contentassets/f851e16643634e72a63b13967e0e463e/"
    "government-bonds-synthetic-business-day.xlsx"
)
TENOR = {"short": ("3Y", "3 years"), "long": ("10Y", "10 years")}
SPLICE_DATE = pd.Timestamp("2019-01-02")
SYNTHETIC_END = pd.Timestamp("2021-06-30")
TIMEOUT = 120
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch_generic(tenor: str) -> pd.Series:
    response = requests.get(
        f"{GENERIC_BASE}/B.{tenor}.GBON",
        params={"format": "csv", "startPeriod": "1980-01-01"},
        headers=UA,
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return pd.Series(dtype=float)  # no data comes back as 404, not an error
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text), sep=";")
    if "TIME_PERIOD" not in frame.columns:
        raise RuntimeError(f"Norges generic unexpected columns: {list(frame.columns)[:6]}")
    series = pd.Series(
        pd.to_numeric(frame["OBS_VALUE"], errors="coerce").values,
        index=pd.to_datetime(frame["TIME_PERIOD"]),
    )
    return series.dropna().sort_index()


def _fetch_synthetic(content: bytes, sheet: str) -> pd.Series:
    frame = pd.read_excel(
        io.BytesIO(content),
        sheet_name=sheet,
        header=None,
        skiprows=6,
        usecols=[1, 2],
        names=["date", "rate"],
    )
    index = pd.to_datetime(frame["date"], errors="coerce")
    keep = index.notna().to_numpy()
    series = pd.Series(
        pd.to_numeric(frame["rate"], errors="coerce").to_numpy()[keep],
        index=pd.DatetimeIndex(index.to_numpy()[keep]),
    )
    return series.dropna().sort_index()


def _splice(synthetic: pd.Series, generic: pd.Series, label: str) -> pd.Series:
    overlap_index = synthetic.index.intersection(generic.index)
    if len(overlap_index):
        lhs, rhs = synthetic.loc[overlap_index], generic.loc[overlap_index]
        level_gap_bp = float((rhs - lhs).abs().mean() * 100)
        diff_corr = float(lhs.diff().corr(rhs.diff()))
        record(
            "norway_splice_overlap",
            tenor=label,
            n=len(overlap_index),
            first=str(overlap_index[0].date()),
            last=str(overlap_index[-1].date()),
            level_gap_bp=round(level_gap_bp, 2),
            diff_corr=round(diff_corr, 4),
        )
    head = synthetic[synthetic.index < SPLICE_DATE]
    tail = generic[generic.index >= SPLICE_DATE]
    if len(tail) == 0:
        raise RuntimeError(f"Norges generic segment empty: {label}")
    return pd.concat([head, tail]).sort_index()


def _fetch_raw() -> pd.DataFrame:
    content = requests.get(SYNTHETIC_URL, headers=UA, timeout=TIMEOUT).content
    columns = {}
    for name, (tenor, sheet) in TENOR.items():
        synthetic = _fetch_synthetic(content, sheet)
        if len(synthetic) and synthetic.index[-1] > SYNTHETIC_END:
            record(
                "norway_synthetic_extended",
                tenor=tenor,
                last=str(synthetic.index[-1].date()),
                note="synthetic stopped updating 2021-06-30; later data requires review",
            )
        columns[name] = _splice(synthetic, _fetch_generic(tenor), tenor)

    frame = pd.DataFrame(columns).sort_index()
    # The two sides of the splice date come from different conventions, so the diff
    # that day is meaningless. Store int; convert back to bool after the parquet
    # round trip
    is_splice = (frame.index == SPLICE_DATE).astype(int)
    frame["break_short"] = is_splice
    frame["break_long"] = is_splice
    if bool(is_splice.any()):
        record("norway_splice_break", date=str(SPLICE_DATE.date()), action="blank the diff")
    else:
        record(
            "norway_splice_break_absent",
            date=str(SPLICE_DATE.date()),
            note="splice date absent from the final index; nothing to blank",
        )
    return frame


def fetch() -> pd.DataFrame:
    frame = get_frame("foreign_no_nb", _fetch_raw)
    for col in ("break_short", "break_long"):
        frame[col] = frame[col].fillna(0).astype(bool) if col in frame else False
    return frame
