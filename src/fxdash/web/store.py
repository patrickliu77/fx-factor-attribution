"""Data snapshot and hot reload (SPEC_web §1).

At startup read every year-partitioned contract parquet in full, parse the four
JSON string columns (betas / contributions / selected_factors / stale_flags)
once on the server, organise them into columnar blocks keyed by
(pair, window, model) held in memory, then drop the original DataFrame. The
request path does zero parsing and zero filtering.

This is where the "never harm the pipeline" rule lands: fully in-memory
snapshots, read handles opened and closed immediately. On Windows a long-lived
parquet handle would give the 19:30 pipeline a PermissionError on write --
downstream must never interfere with the pipeline.

Hot reload uses status.json as the commit marker (it is close to the last file
the pipeline writes): mtime advances -> settle -> reread only if two signatures
agree -> swap the reference atomically only if the new snapshot passes
acceptance; any failure keeps serving the old snapshot. Better stale than 500.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import robustness as RB
from .market import MarketData

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 30  # stat status.json only once this many seconds have passed
SETTLE_S = 10  # settle after mtime advances, to dodge the write window
SIGNATURE_GAP_S = 5  # gap between the two signatures
RETRY_AFTER_S = 60  # retry interval after a failed reload
KNOWN_SCHEMAS = {"1.0.0", "1.1.0"}

# matches FX_INTERNAL_FACTORS on the engine side; hardcoded rather than imported
# from the engine (web imports only config, and these two names are contract
# semantics, frozen in SPEC_phase1)
SYSTEMATIC_FACTORS = ("DOLLAR_LOO", "CARRY_LOO")

WATCHED_JSON = ("status.json", "run_manifest.json", "coverage.json")


class SnapshotError(RuntimeError):
    """New snapshot failed to build or failed acceptance. Caller keeps the old one."""


class Combo:
    """Columnar block for one (pair, window, model) combo. All arrays are the
    same length and sorted by ascending date."""

    __slots__ = (
        "pair", "window", "model", "dates", "y", "residual", "residual_z",
        "r2_full", "r2_exog", "systematic", "exogenous", "provisional",
        "lam", "factors", "contributions", "betas", "selected", "stale_events",
    )

    def __init__(self, pair, window, model, block: pd.DataFrame):
        self.pair, self.window, self.model = pair, window, model
        block = block.sort_values("date")
        self.dates = [d.strftime("%Y-%m-%d") for d in block["date"]]

        def col(name):
            return block[name].to_numpy(dtype=float)

        self.y = col("y")
        self.residual = col("residual")
        self.residual_z = col("residual_z")
        self.r2_full = col("r2_full")
        self.r2_exog = col("r2_exog")
        self.systematic = col("systematic")
        self.exogenous = col("exogenous")
        self.lam = col("lambda")
        self.provisional = (
            block["provisional"].fillna(False).astype(bool).to_numpy()
            if "provisional" in block
            else np.zeros(len(block), dtype=bool)
        )

        # the JSON string columns are parsed once, here. Key order is factor
        # order (a contract guarantee); take the key order of this combo's last row
        parsed_contrib = [json.loads(s or "{}") for s in block["contributions"]]
        parsed_betas = [json.loads(s or "{}") for s in block["betas"]]
        self.factors = list(parsed_contrib[-1].keys()) if parsed_contrib else []

        n = len(block)
        self.contributions = {
            f: np.array(
                [_nan_if_none(row.get(f)) for row in parsed_contrib], dtype=float
            )
            for f in self.factors
        }
        self.betas = {
            f: np.array([_nan_if_none(row.get(f)) for row in parsed_betas], dtype=float)
            for f in self.factors
        }

        if model == "lasso":
            chosen = [set(json.loads(s or "[]")) for s in block["selected_factors"]]
            self.selected = {
                f: np.array([1 if f in c else 0 for c in chosen], dtype=int)
                for f in self.factors
            }
        else:
            self.selected = None

        # stale uses a sparse event table: the vast majority of days are empty
        self.stale_events = []
        for i, s in enumerate(block["stale_flags"]):
            flags = json.loads(s or "[]")
            if flags:
                self.stale_events.append({"date": self.dates[i], "flags": flags})


def _nan_if_none(value):
    return np.nan if value is None else float(value)


def clean(value):
    """Sanitise NaN/inf to None -- starlette 500s on NaN (SPEC_web §2)."""
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def clean_list(array) -> list:
    return [clean(v) for v in array]


class Snapshot:
    """One immutable data snapshot. Derived caches hang off the instance, so
    swapping the reference invalidates all of them at once."""

    def __init__(self, output_dir: Path, cache_dir: Path | None = None):
        self.output_dir = Path(output_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.loaded_at = pd.Timestamp.now().isoformat(timespec="seconds")

        parts = sorted(self.output_dir.glob("contract/year=*/part.parquet"))
        if not parts:
            raise SnapshotError(f"contract is empty: {self.output_dir}")
        # open and close fast: release each handle as soon as it is read
        frame = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        frame["date"] = pd.to_datetime(frame["date"])

        schemas = set(frame["schema_version"].dropna().unique())
        unknown = schemas - KNOWN_SCHEMAS
        if unknown:
            raise SnapshotError(f"unknown contract schema: {sorted(unknown)}")

        self.rows = int(len(frame))
        self.schema_version = max(schemas) if schemas else None
        self.date_first = frame["date"].min().strftime("%Y-%m-%d")
        self.date_last = frame["date"].max().strftime("%Y-%m-%d")

        self.combos: dict[tuple, Combo] = {}
        for (pair, window, model), block in frame.groupby(
            ["pair", "window", "model"], observed=True
        ):
            self.combos[(pair, int(window), model)] = Combo(
                pair, int(window), model, block
            )

        self.pairs = sorted({k[0] for k in self.combos})
        self.windows = sorted({k[1] for k in self.combos})
        self.models = sorted({k[2] for k in self.combos})

        # robustness badge (SPEC_phase3 §12). This is the documented exception to
        # rule three (the only new maths is summation): the state machine is
        # computed by the shared module fxdash.robustness, web invents no maths of
        # its own and does not import the attribution engine. With insufficient
        # data report available=False honestly, never fake agreement
        self.robustness = {pair: self._pair_robustness(pair) for pair in self.pairs}

        self.status = self._read_json("status.json")
        self.manifest = self._read_json("run_manifest.json")
        self.coverage = self._read_json("coverage.json")
        self.pca = self._read_pca()
        # market data serves only the ticker and the trend chart; if it cannot be
        # read, degrade it wholesale and never drag down the attribution snapshot
        try:
            self.market = MarketData(self.cache_dir)
        except Exception as exc:
            log.warning(
                "market layer unavailable, ticker and trend chart fall back to "
                "placeholders: %s", exc)
            self.market = None

        signature = files_signature(self.output_dir)
        digest = hashlib.sha1(repr(signature).encode()).hexdigest()[:8]
        generated = (self.status or {}).get("generated_at", self.loaded_at)
        self.data_version = f"{generated}-{digest}"

        self._validate()

    def _pair_robustness(self, pair: str) -> dict:
        combos = {m: self.combos.get((pair, RB.CANONICAL_WINDOW, m))
                  for m in RB.MODELS_NEEDED}
        if any(c is None for c in combos.values()):
            return {"available": False}
        frames = []
        for m, c in combos.items():
            frames.append(pd.DataFrame({
                ("systematic", m): c.systematic,
                ("exogenous", m): c.exogenous,
                ("residual", m): c.residual,
                ("y", m): c.y,
            }, index=pd.to_datetime(c.dates)))
        # products of the same contract's inner join, so dates should already
        # agree; still intersect defensively
        piv = pd.concat(frames, axis=1, join="inner").sort_index()
        piv.columns = pd.MultiIndex.from_tuples(piv.columns)
        try:
            return RB.state_at(RB.compute_pair(piv))
        except Exception as exc:
            log.warning("robustness computation failed %s: %s", pair, exc)
            return {"available": False}

    def _read_json(self, name: str) -> dict:
        path = self.output_dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SnapshotError(f"{name} is unparseable: {exc}") from exc

    def _read_pca(self) -> pd.DataFrame | None:
        path = self.output_dir / "pca_monitor.parquet"
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"])
        return frame.sort_values("date")

    def _validate(self) -> None:
        """Snapshot acceptance: reject the whole thing on failure and keep the old
        snapshot (SPEC_web §1)."""
        if not self.combos:
            raise SnapshotError("no combos at all")
        # combo coverage on the last day must not be partial: every combo that
        # ever appeared should have data
        empty = [k for k, c in self.combos.items() if len(c.dates) == 0]
        if empty:
            raise SnapshotError(f"empty combos: {empty[:5]}")

    def combo(self, pair: str, window: int, model: str) -> Combo | None:
        return self.combos.get((pair, window, model))


def files_signature(output_dir: Path) -> tuple:
    """(relative path, mtime_ns, size) signature over every watched file, used
    for the stability check and the version number."""
    entries = []
    for p in sorted(output_dir.glob("contract/year=*/part.parquet")):
        st = p.stat()
        entries.append((p.name + "/" + p.parent.name, st.st_mtime_ns, st.st_size))
    for name in (*WATCHED_JSON, "pca_monitor.parquet"):
        p = output_dir / name
        if p.exists():
            st = p.stat()
            entries.append((name, st.st_mtime_ns, st.st_size))
    return tuple(entries)


class DataStore:
    """Holds the current snapshot and owns hot reload. The request path does a
    single attribute read, so it is naturally lock-free."""

    def __init__(self, output_dir: Path, settle_s: float = SETTLE_S,
                 signature_gap_s: float = SIGNATURE_GAP_S,
                 cache_dir: Path | None = None):
        self.output_dir = Path(output_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.settle_s = settle_s
        self.signature_gap_s = signature_gap_s
        # if startup fails the service should fail to come up, and that is right
        self.snapshot = Snapshot(self.output_dir, self.cache_dir)
        self.reload_state = "fresh"
        self._status_mtime = self._stat_status()
        self._last_check = time.monotonic()
        self._next_retry = 0.0
        self._lock = threading.Lock()

    def _stat_status(self) -> int:
        path = self.output_dir / "status.json"
        return path.stat().st_mtime_ns if path.exists() else 0

    def current(self) -> Snapshot:
        """Request entry point. Cheap check of the commit marker, trigger a reload
        when needed."""
        now = time.monotonic()
        if now - self._last_check >= CHECK_INTERVAL_S:
            self._last_check = now
            mtime = self._stat_status()
            stale_retry = self.reload_state == "stale_retrying" and now >= self._next_retry
            if mtime != self._status_mtime or stale_retry:
                # single-threaded reload; requests that miss the lock just use
                # the old snapshot
                if self._lock.acquire(blocking=False):
                    try:
                        self._reload(mtime)
                    finally:
                        self._lock.release()
        return self.snapshot

    def _reload(self, status_mtime: int) -> None:
        try:
            time.sleep(self.settle_s)  # dodge the 19:30-19:45 write window
            first = files_signature(self.output_dir)
            time.sleep(self.signature_gap_s)
            second = files_signature(self.output_dir)
            if first != second:
                # still being written. Try again on the next check
                self.reload_state = "stale_retrying"
                self._next_retry = time.monotonic() + RETRY_AFTER_S
                return

            candidate = Snapshot(self.output_dir, self.cache_dir)
            # row count must not regress: the pipeline's own overwrite discipline,
            # reused on the web side with the same intuition
            if candidate.rows < self.snapshot.rows:
                raise SnapshotError(
                    f"row count regressed {self.snapshot.rows} -> {candidate.rows}"
                )
            self.snapshot = candidate  # atomic reference swap
            self._status_mtime = status_mtime
            self.reload_state = "fresh"
            log.info("snapshot updated: %s", candidate.data_version)
        except Exception as exc:
            # keep serving the old snapshot. Better stale than 500
            self.reload_state = "stale_retrying"
            self._next_retry = time.monotonic() + RETRY_AFTER_S
            log.warning("reload failed, still serving the old snapshot: %s", exc)
