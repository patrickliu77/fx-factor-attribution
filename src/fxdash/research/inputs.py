"""Read existing files into an auditable snapshot, without fetchers or cache writes."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..data.alignment import USD_CLOSE, align_to_index, offset_for
from ..data.panel import RawData, _fx_return_panel
from ..factors.build import build_pair_panel

SCHEMA = "attribution-research-inputs-1"
FOREIGN_CACHE = dict(zip(config.PAIRS, (
    "de_bund", "jp_jgb", "ca_boc", "no_nb", "au_rba", "mx_banxico")))


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


class FileReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sources = {}

    def frame(self, relative, *, csv=False):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("input path escapes repository")
        payload = path.read_bytes()
        self.sources[relative] = {"sha256": digest(payload), "bytes": len(payload)}
        frame = (pd.read_csv(io.BytesIO(payload), index_col=0, parse_dates=True)
                 if csv else pd.read_parquet(io.BytesIO(payload)))
        frame.index = pd.to_datetime(frame.index)
        if frame.index.tz is not None or not frame.index.equals(frame.index.normalize()):
            raise ValueError(f"expected timezone-naive daily index: {relative}")
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f"duplicate or unsorted dates: {relative}")
        if frame.empty:
            raise ValueError(f"empty input: {relative}")
        self.sources[relative].update(first=str(frame.index[0].date()), last=str(frame.index[-1].date()))
        return frame


def load_raw_offline(repo, end):
    reader = FileReader(repo)
    end = pd.Timestamp(end)
    if pd.isna(end) or end.tz is not None or end != end.normalize():
        raise ValueError("end must be a timezone-naive date")

    def series(name):
        safe = name.replace("=", "_").replace("^", "").replace("/", "_")
        frame = reader.frame(f"data/cache/{safe}.parquet")
        if frame.shape[1] != 1:
            raise ValueError(f"expected one column: {name}")
        return pd.to_numeric(frame.iloc[:, 0], errors="coerce").loc[:end]

    # The archive covers the pilot. Never extrapolate it through missing online OAS.
    hy = reader.frame(f"data/user/{config.HY_OAS_USER_FILE}", csv=True)
    if hy.shape[1] != 1:
        raise ValueError("expected one HY OAS column")
    hy = pd.to_numeric(hy.iloc[:, 0], errors="coerce").dropna()
    if hy.empty or end > hy.index[-1] or end >= pd.Timestamp(config.HY_OAS_SPLICE_DATE):
        raise ValueError("No offline HY OAS tail for requested end; choose a date within the user archive, before the splice.")
    hy = hy.loc[:end]
    fx = {}
    for ticker, (pair, invert) in config.FX_TICKERS.items():
        price = series(ticker)
        price = (1 / price if invert else price).rename(pair)
        lo, hi = config.DIRECTION_RANGES[pair]
        if not lo <= price.median() <= hi:
            raise ValueError(f"FX quote direction check failed: {pair}")
        fx[pair] = price
    commodities = {}
    for ticker, name in config.CMDTY_TICKERS.items():
        price = series(ticker)
        commodities[name] = price.where(price > 0).rename(name)
    foreign = {}
    for pair, name in FOREIGN_CACHE.items():
        frame = reader.frame(f"data/cache/foreign_{name}.parquet").loc[:end].copy()
        if not {"short", "long"}.issubset(frame.columns):
            raise ValueError(f"missing foreign yield legs: {pair}")
        for column in ("break_short", "break_long"):
            frame[column] = frame[column].fillna(0).astype(bool) if column in frame else False
        foreign[pair] = frame
    raw = RawData(
        fx_levels=fx, fx_returns=_fx_return_panel(fx), cmdty=commodities,
        etfs={name: series(ticker + "_adj") for ticker, name in config.ETF_TICKERS.items()},
        us_yields={name: series(name) for name in sorted({v for pair in config.US_LEG.values() for v in pair})},
        vix=series("VIXCLS"), baa=series("BAA10Y"), foreign=foreign, hy_oas=hy,
    )
    return raw, reader.sources


def prepare_panel(raw, pair):
    """Match every arm on one pair calendar; no monkeypatch of production menus."""
    frame = build_pair_panel(pair, raw).copy()
    factors = list(config.lasso_menu(pair))
    original = len(frame)
    alternative = {"USDCAD": "BRENT", "USDNOK": "WTI"}.get(pair)
    if alternative:
        # Alignment must happen on the ORIGINAL FX index, before production dropna.
        index = pd.DatetimeIndex(raw.fx_returns[pair].dropna().index)
        aligned, age = align_to_index(raw.cmdty[alternative], index, offset_for(pair, USD_CLOSE))
        frame[alternative] = np.log(aligned.where(aligned > 0)).diff().reindex(frame.index)
        frame[f"stale::{alternative}"] = age.reindex(frame.index).gt(0)
        factors.append(alternative)
    finite = np.isfinite(frame[["y", *factors]].to_numpy(float)).all(axis=1)
    frame = frame.loc[finite].copy()
    if len(frame):
        frame.loc[frame.index[-1], "provisional"] = True
    observed = int(raw.fx_returns[pair].notna().sum())
    audit = {"raw_fx_observations": observed, "production_complete_rows": original,
             "production_join_loss": observed-original, "alternative_oil_join_loss": original-len(frame),
             "common_complete_rows": len(frame), "columns_checked": factors,
             "first": str(frame.index[0].date()) if len(frame) else None,
             "last": str(frame.index[-1].date()) if len(frame) else None,
             "provisional": int(frame.provisional.sum())}
    if frame.empty:
        raise ValueError(f"no complete research panel for {pair}")
    return frame, audit


def save_snapshot(destination, panels, metadata):
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    hashes = {}
    for pair, panel in panels.items():
        if pair not in config.PAIRS:
            raise ValueError("unknown pair in snapshot")
        path = destination / f"{pair}.parquet"
        panel.to_parquet(path)
        hashes[pair] = digest(path.read_bytes())
    value = {**metadata, "schema": SCHEMA, "panel_hashes": hashes}
    write_json(destination / "manifest.json", value)
    return value


def read_snapshot(source, end):
    source = Path(source)
    metadata = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA or metadata.get("model_revision") != config.MODEL_REVISION:
        raise ValueError("snapshot schema or model revision mismatch")
    if metadata.get("end") != pd.Timestamp(end).strftime("%Y-%m-%d"):
        raise ValueError("snapshot cutoff differs from requested end")
    if not isinstance(metadata.get("panel_hashes"), dict) or not metadata["panel_hashes"]:
        raise ValueError("snapshot must contain at least one panel")
    panels = {}
    for pair, expected in metadata["panel_hashes"].items():
        if pair not in config.PAIRS:
            raise ValueError("unknown pair in snapshot")
        path = (source / f"{pair}.parquet").resolve()
        if not path.is_relative_to(source.resolve()):
            raise ValueError("snapshot file escapes its directory")
        payload = path.read_bytes()
        if digest(payload) != expected:
            raise ValueError(f"snapshot hash mismatch: {pair}")
        panels[pair] = pd.read_parquet(io.BytesIO(payload))
    return panels, metadata


def reserve_output(out, repo):
    out, repo = Path(out).resolve(), Path(repo).resolve()
    research_root = (repo / "outputs" / "research").resolve()
    # Inside this repo, writes are confined to new research run directories.
    if repo.is_relative_to(out) or (out.is_relative_to(repo) and
            (not out.is_relative_to(research_root) or out == research_root)):
        raise ValueError("output must be a new directory under outputs/research or outside the repository")
    out.mkdir(parents=True, exist_ok=False)
    return out


def protected_hashes(repo):
    """Evidence that offline runs left production code, data and outputs unchanged."""
    repo = Path(repo).resolve()
    result = {}
    for folder in ("data", "outputs", "src/fxdash"):
        for path in sorted((repo / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(repo).as_posix()
            if rel.startswith(("outputs/research/", "src/fxdash/research/")):
                continue
            result[rel] = digest(path.read_bytes())
    return result
