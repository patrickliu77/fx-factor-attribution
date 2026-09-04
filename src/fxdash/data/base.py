"""Fetch foundation: three-tier fallback chain, parquet cache, validation log.

Fetch priority is fixed: online, then data/cache/, then data/user/ (CLAUDE.md 8).
Every fallback is noted in the validation log with the file name and last date, so
whether a given day's numbers were fresh can be judged after the fact.
"""

from __future__ import annotations

import json
import logging
import time

import pandas as pd

from ..config import CACHE_DIR, USER_DIR

log = logging.getLogger(__name__)

RETRIES = 3
BACKOFF_BASE = 2.0

# Validation log. run.py dumps it to outputs/ at the end.
_RECORDS: list[dict] = []


def record(event: str, **fields) -> None:
    """Append one validation-log entry."""
    entry = {"event": event, **fields}
    _RECORDS.append(entry)
    log.info("%s %s", event, json.dumps(fields, ensure_ascii=False, default=str))


def records() -> list[dict]:
    return list(_RECORDS)


def reset_records() -> None:
    _RECORDS.clear()


def dump_records(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in _RECORDS:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def cache_path(name: str):
    """Cache file name. '=' and '^' are unsafe in Windows file names."""
    safe = name.replace("=", "_").replace("^", "").replace("/", "_")
    return CACHE_DIR / f"{safe}.parquet"


def _last_date(obj) -> str:
    if obj is None or len(obj) == 0:
        return "empty"
    return str(pd.Timestamp(obj.index[-1]).date())


def _read_cache(name: str):
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # a corrupt cache must not fail the whole run
        log.warning("cache read failed %s: %s", path.name, exc)
        return None


def _write_cache(name: str, frame: pd.DataFrame) -> None:
    path = cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path)
    except Exception as exc:
        log.warning("cache write failed %s: %s", path.name, exc)


def get_frame(name: str, fetcher, user_loader=None) -> pd.DataFrame:
    """Fetch one DataFrame through the three-tier fallback chain; index is a
    DatetimeIndex.

    fetcher and user_loader are zero-argument callables. The cache is refreshed only
    on a successful online fetch.
    """
    last_exc = None
    for attempt in range(RETRIES):
        try:
            frame = fetcher()
            if frame is None or len(frame) == 0:
                raise RuntimeError("empty response")
            frame = _normalise(frame)
            _write_cache(name, frame)
            record("fetch_online", series=name, rows=len(frame), last=_last_date(frame))
            return frame
        except Exception as exc:
            last_exc = exc
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_BASE * (attempt + 1))
    log.warning("online fetch failed %s: %s", name, last_exc)

    cached = _read_cache(name)
    if cached is not None and len(cached):
        cached = _normalise(cached)
        record(
            "fallback_cache",
            series=name,
            file=cache_path(name).name,
            last=_last_date(cached),
            reason=str(last_exc)[:200],
        )
        return cached

    if user_loader is not None:
        try:
            frame = _normalise(user_loader())
            if len(frame):
                record(
                    "fallback_user",
                    series=name,
                    file=getattr(user_loader, "source_name", "data/user/"),
                    last=_last_date(frame),
                    reason=str(last_exc)[:200],
                )
                return frame
        except Exception as exc:
            log.warning("data/user/ fallback failed %s: %s", name, exc)

    raise RuntimeError(f"all three fallback tiers failed: {name}: {last_exc}")


def get_series(name: str, fetcher, user_loader=None) -> pd.Series:
    """Fetch a single-column series. fetcher may return a Series or a one-column
    DataFrame."""
    frame = get_frame(name, fetcher, user_loader)
    if frame.shape[1] != 1:
        raise ValueError(f"{name} expected a single column, got {list(frame.columns)}")
    return frame.iloc[:, 0].rename(name)


def _normalise(obj) -> pd.DataFrame:
    """Normalise to a date-ascending DataFrame without duplicates or all-NaN rows."""
    frame = obj.to_frame() if isinstance(obj, pd.Series) else pd.DataFrame(obj)
    frame.index = pd.to_datetime(frame.index).normalize()
    frame.index.name = "date"
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    return frame.dropna(how="all")


def load_user_csv(filename: str, date_col=0, value_col=1, name=None):
    """Build a loader reading one data/user/ file, for the third tier of get_series."""

    def loader():
        path = USER_DIR / filename
        raw = pd.read_csv(path, na_values=[".", "-", ""])
        series = pd.Series(
            pd.to_numeric(raw.iloc[:, value_col], errors="coerce").values,
            index=pd.to_datetime(raw.iloc[:, date_col]),
            name=name or filename,
        )
        return series.dropna()

    loader.source_name = f"data/user/{filename}"
    return loader
