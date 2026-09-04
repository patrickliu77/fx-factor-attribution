"""Data coverage discipline (SPEC_phase2 1.7).

The historical range must be pinned explicitly and verified on every run, never
inherited from whatever the data happened to give. Phase 1's original contract was
short about 579 trading days — 14% of the sample — versus the same-convention expected
value, and nobody noticed; this module is the two gates against that kind of silent
truncation: a start assertion and a cross-run comparison.
"""

from __future__ import annotations

import json

import pandas as pd

from .config import (
    BACKFILL_START,
    COVERAGE_ROW_TOLERANCE,
    OUTPUT_DIR,
    PANEL_START_TOLERANCE_DAYS,
)
from .data.base import record

COVERAGE_PATH = OUTPUT_DIR / "coverage.json"


class CoverageError(RuntimeError):
    """Historical range does not match expectations. Red light, stop; never continue
    silently."""


def describe(panels: dict[str, pd.DataFrame]) -> dict:
    return {
        pair: {
            "first": str(frame.index[0].date()),
            "last": str(frame.index[-1].date()),
            "rows": int(len(frame)),
        }
        for pair, frame in panels.items()
        if len(frame)
    }


def check_start(panels: dict[str, pd.DataFrame]) -> None:
    """Each pair's panel start must fall within tolerance after BACKFILL_START."""
    floor = pd.Timestamp(BACKFILL_START)
    offenders = []
    for pair, frame in panels.items():
        if not len(frame):
            offenders.append(f"{pair}: panel is empty")
            continue
        # count trading days, not calendar days, so New Year holidays do not misfire
        lag = len(pd.bdate_range(floor, frame.index[0])) - 1
        if lag > PANEL_START_TOLERANCE_DAYS:
            offenders.append(
                f"{pair}: start {frame.index[0].date()} is {lag} trading days later than {floor.date()}"
            )
    if offenders:
        raise CoverageError(
            "panel start beyond tolerance; history may have been silently truncated upstream (SPEC_phase2 1.7): "
            + "; ".join(offenders)
        )


def compare_with_previous(current: dict) -> list[dict]:
    """Compare with the previous run; an unexpected shrink of the historical range is
    a red light."""
    if not COVERAGE_PATH.exists():
        record("coverage_baseline", pairs=len(current))
        return []

    previous = json.loads(COVERAGE_PATH.read_text(encoding="utf-8")).get("pairs", {})
    shrunk = []
    for pair, now in current.items():
        before = previous.get(pair)
        if not before:
            continue
        # a later start or fewer rows both count as shrinking; the tail advancing is
        # normal
        if pd.Timestamp(now["first"]) > pd.Timestamp(before["first"]):
            shrunk.append(
                {
                    "pair": pair,
                    "kind": "start_moved_later",
                    "before": before["first"],
                    "after": now["first"],
                }
            )
        # row count carries a tolerance: the day's unfinished bar makes the tail come
        # and go, and a jitter of one or two rows is normal
        if before["rows"] - now["rows"] > COVERAGE_ROW_TOLERANCE:
            shrunk.append(
                {
                    "pair": pair,
                    "kind": "rows_decreased",
                    "before": before["rows"],
                    "after": now["rows"],
                    "tolerance": COVERAGE_ROW_TOLERANCE,
                }
            )
    return shrunk


def enforce(panels: dict[str, pd.DataFrame], strict: bool = True) -> dict:
    """Run both gates and persist this run's coverage. Returns this run's coverage
    description."""
    check_start(panels)
    current = describe(panels)
    shrunk = compare_with_previous(current)
    if shrunk:
        record("coverage_shrunk", items=shrunk)
        if strict:
            raise CoverageError(
                "historical range shrank versus the previous run (SPEC_phase2 1.7): "
                + "; ".join(
                    f"{s['pair']} {s['kind']} {s['before']} -> {s['after']}" for s in shrunk
                )
            )

    COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_PATH.write_text(
        json.dumps(
            {"backfill_start": BACKFILL_START, "pairs": current},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    record("coverage", backfill_start=BACKFILL_START, pairs=current)
    return current
