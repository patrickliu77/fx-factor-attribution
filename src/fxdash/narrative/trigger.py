"""Trigger criterion and fact set (SPEC_phase3 §1).

Pure functions: no network, no LLM calls, so they can be pinned down completely by
tests.

Two jobs are deliberately merged here: **the fact set injected into the prompt is at
the same time the whitelist for numeric verification** (SPEC_phase3 §5, check 3).
Numbers are formatted into strings in this one place only; the model receives those
strings and verification recognizes only those strings. Split the formatting across
two places and sooner or later you get "-192.7 bp in the prompt, -192.70 bp in the
verification table" -- the system contradicting itself.

The fact set carries **trend context** (explanatory power and residual level over the
last 21 and 252 days), which is not decoration. Without it, a model facing a large
residual has exactly one option: force an explanation out of that day's news. With
it, the model can state a different truth -- "this pair's explanatory power has been
degrading for a month; how much of today's 161 bp is the day's events and how much is
the factor set itself failing cannot be distinguished". The live acceptance run in
SPEC_phase3 §10 explicitly requires that kind of honesty.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import CONTRACT_DIR, DEFAULT_WINDOW
from ..robustness import N_FMT as ROBUST_FMT
from .. import robustness as RB

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- criterion
# Calibrated on measured data 2026-09-01; derivation and data in SPEC_phase3 §1.2-1.4.
# The absolute floor is in bp rather than "relative to the pair's own one-year
# median": under this project's z definition the latter is never binding (|z|>=2
# already implies |res| >= 2.97x the median), so writing it in would be self-deception.
TRIGGER_Z = 2.0
TRIGGER_RESIDUAL_BP = 50.0
MAX_PER_DAY = 3

# Median |residual| across all full-sample trigger days (measured 2026-09-01). Used
# as a magnitude calibration for the model: "routine reporting usually accompanies a
# move far smaller than this". It goes on the whitelist; otherwise the model quoting
# this calibration value would be flagged by numeric check 3 -- refusing to recognize
# a number you supplied yourself is the hardest kind of bug to track down.
TRIGGER_MEDIAN_RESIDUAL_BP = 104.2

DEFAULT_MODEL = "ols"

# The two trend-context windows: the last month and the last year
CONTEXT_SHORT = 21
CONTEXT_LONG = 252

# Canonical number formats. One copy site-wide: change here and both the prompt and
# verification change together.
BP_FMT = "{:+.1f} bp"
Z_FMT = "{:+.2f}"
R2_FMT = "{:.3f}"
PCT_FMT = "{:+.2f}%"
MAG_FMT = "{:.1f} bp"  # unsigned magnitude (medians and the like)


def fmt_bp(value):
    return None if value is None or not np.isfinite(value) else BP_FMT.format(value)


def fmt_mag(value):
    return None if value is None or not np.isfinite(value) else MAG_FMT.format(value)


def fmt_z(value):
    return None if value is None or not np.isfinite(value) else Z_FMT.format(value)


def fmt_r2(value):
    return None if value is None or not np.isfinite(value) else R2_FMT.format(value)


def fmt_pct(value):
    return None if value is None or not np.isfinite(value) else PCT_FMT.format(value)


def _num(value):
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _scale(value):
    v = _num(value)
    return None if v is None else v * 1e4


@dataclass
class Context:
    """Trend context. Answers "is today anomalous, or has this model been anomalous
    for a while"."""

    r2_full_median_short: float | None = None
    r2_full_median_long: float | None = None
    abs_residual_median_short_bp: float | None = None
    abs_residual_median_long_bp: float | None = None
    z_exceed_days_short: int | None = None
    short_window: int = CONTEXT_SHORT
    long_window: int = CONTEXT_LONG

    def rendered(self) -> dict:
        out = {
            "r2_full_median_recent": fmt_r2(self.r2_full_median_short),
            "r2_full_median_year": fmt_r2(self.r2_full_median_long),
            "abs_residual_median_recent": fmt_mag(self.abs_residual_median_short_bp),
            "abs_residual_median_year": fmt_mag(self.abs_residual_median_long_bp),
            "z_exceed_days_recent": (
                None if self.z_exceed_days_short is None
                else str(self.z_exceed_days_short)
            ),
            # Window lengths go on the whitelist too: when the model legitimately
            # says "the last 21 trading days", the number 21 must not be killed by
            # numeric check 3
            "context_window_recent": str(self.short_window),
            "context_window_year": str(self.long_window),
        }
        return {k: v for k, v in out.items() if v is not None}

    def to_dict(self) -> dict:
        return {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "r2_full_median_short": self.r2_full_median_short,
            "r2_full_median_long": self.r2_full_median_long,
            "abs_residual_median_short_bp": self.abs_residual_median_short_bp,
            "abs_residual_median_long_bp": self.abs_residual_median_long_bp,
            "z_exceed_days_short": self.z_exceed_days_short,
        }


@dataclass
class Fact:
    """Every statable fact about one pair on one day.

    `rendered` is **all** of the number strings the model may quote; any other number
    appearing in the body is a failure (SPEC_phase3 §5, check 3).
    """

    pair: str
    date: str
    window: int
    y_bp: float | None
    residual_bp: float | None
    residual_z: float | None
    systematic_bp: float | None
    exogenous_bp: float | None
    r2_full: float | None
    r2_exog: float | None
    top_factor: str | None
    contributions_bp: dict = field(default_factory=dict)
    provisional: bool = False
    context: Context = field(default_factory=Context)
    # Neutral facts about this pair's most recent trigger, attached by run from
    # outputs/narrative/. Deliberately carries no "same event or not" judgement:
    # make that call for the model and it will write up two triggers two weeks apart
    # with entirely different event kinds as a continuation.
    previous: dict = field(default_factory=dict)
    # Robustness state (SPEC_phase3 §12.6): state label + two N1 values + abstain flag
    # and abstain-run length. Neutral statements only, with no "therefore the reading
    # is untrustworthy" attached; the judgement is left to the model.
    # N2 is stored in the artifact along with the dict but does not enter the prompt
    # (the zero-cost switch reserved in §12.2)
    robustness: dict = field(default_factory=dict)

    @property
    def abs_z(self) -> float:
        return abs(self.residual_z) if self.residual_z is not None else 0.0

    @property
    def abs_residual_bp(self) -> float:
        return abs(self.residual_bp) if self.residual_bp is not None else 0.0

    def triggers(self) -> bool:
        """SPEC_phase3 §1.1: the two conditions are ANDed."""
        return self.abs_z >= TRIGGER_Z and self.abs_residual_bp >= TRIGGER_RESIDUAL_BP

    def rendered(self) -> dict:
        """The number strings fed to the prompt, which are also the numeric-check
        whitelist."""
        out = {
            "y": fmt_bp(self.y_bp),
            "residual": fmt_bp(self.residual_bp),
            "residual_z": fmt_z(self.residual_z),
            "systematic": fmt_bp(self.systematic_bp),
            "exogenous": fmt_bp(self.exogenous_bp),
            "r2_full": fmt_r2(self.r2_full),
            "r2_exog": fmt_r2(self.r2_exog),
            "y_pct": fmt_pct(self.y_bp / 100.0 if self.y_bp is not None else None),
            "window": str(self.window),
            "history_median_residual": fmt_mag(TRIGGER_MEDIAN_RESIDUAL_BP),
        }
        if self.previous.get("trading_days_ago") is not None:
            out["previous_trading_days_ago"] = str(self.previous["trading_days_ago"])
        rb = self.robustness or {}
        if rb.get("available"):
            if rb.get("d_ridge_n1") is not None:
                out["robustness_d_ridge"] = ROBUST_FMT.format(rb["d_ridge_n1"])
            if rb.get("d_lasso_n1") is not None:
                out["robustness_d_lasso"] = ROBUST_FMT.format(rb["d_lasso_n1"])
            if rb.get("abstain"):
                # A single abstain day and a 50-day abstain run are two different
                # facts (§12.4); give the model the count
                out["robustness_abstain_run"] = str(rb.get("abstain_run_days", 0))
        for name, value in self.contributions_bp.items():
            out[f"contribution.{name}"] = fmt_bp(value)
        out.update(self.context.rendered())
        return {k: v for k, v in out.items() if v is not None}

    def allowed_numbers(self) -> set[str]:
        """The whitelisted number **strings**, units and percent signs stripped, for
        verbatim comparison during verification."""
        return {
            text.replace(" bp", "").replace("%", "")
            for text in self.rendered().values()
        }

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "date": self.date,
            "window": self.window,
            "y_bp": self.y_bp,
            "residual_bp": self.residual_bp,
            "residual_z": self.residual_z,
            "systematic_bp": self.systematic_bp,
            "exogenous_bp": self.exogenous_bp,
            "r2_full": self.r2_full,
            "r2_exog": self.r2_exog,
            "top_factor": self.top_factor,
            "contributions_bp": self.contributions_bp,
            "provisional": self.provisional,
            "context": self.context.to_dict(),
            "previous": self.previous,
            "robustness": self.robustness,
            "rendered": self.rendered(),
        }


def _top_factor(contributions: dict) -> str | None:
    best, best_abs = None, 0.0
    for name, value in contributions.items():
        if value is None:
            continue
        if abs(value) > best_abs:
            best, best_abs = name, abs(value)
    return best


def _context(history: pd.DataFrame) -> Context:
    """history is this pair's block up to and including the current day, sorted by
    date ascending."""
    if history.empty:
        return Context()

    def med(column, n):
        tail = history[column].tail(n).dropna()
        return float(tail.median()) if len(tail) else None

    abs_res = history["residual"].abs() * 1e4
    short = abs_res.tail(CONTEXT_SHORT).dropna()
    long_ = abs_res.tail(CONTEXT_LONG).dropna()
    z_tail = history["residual_z"].abs().tail(CONTEXT_SHORT).dropna()

    return Context(
        r2_full_median_short=med("r2_full", CONTEXT_SHORT),
        r2_full_median_long=med("r2_full", CONTEXT_LONG),
        abs_residual_median_short_bp=float(short.median()) if len(short) else None,
        abs_residual_median_long_bp=float(long_.median()) if len(long_) else None,
        z_exceed_days_short=int((z_tail >= TRIGGER_Z).sum()) if len(z_tail) else None,
    )


def load_facts(
    date: str | None = None,
    contract_dir: Path | None = None,
    window: int = DEFAULT_WINDOW,
    model: str = DEFAULT_MODEL,
) -> tuple[str, list[Fact]]:
    """Read the contract, return (date, facts for every pair on that day). date=None
    takes the latest day.

    Read-only, open briefly and close. Never writes outputs/contract/.
    """
    root = Path(contract_dir) if contract_dir else CONTRACT_DIR
    parts = sorted(Path(root).glob("year=*/part.parquet"))
    if not parts:
        raise FileNotFoundError(f"contract is empty: {root}")
    frame = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    block = frame[(frame["window"] == window) & (frame["model"] == model)]
    if block.empty:
        raise ValueError(f"contract has no window={window} model={model}")

    target = pd.Timestamp(date) if date else block["date"].max()
    day = block[block["date"] == target]
    if day.empty:
        raise ValueError(f"contract has no day {target.date()}")

    facts = []
    for _, row in day.sort_values("pair").iterrows():
        pair = str(row["pair"])
        contributions = {
            k: (None if v is None else float(v) * 1e4)
            for k, v in json.loads(row["contributions"] or "{}").items()
        }
        history = (
            block[(block["pair"] == pair) & (block["date"] <= target)]
            .sort_values("date")
        )
        facts.append(Fact(
            pair=pair,
            date=target.strftime("%Y-%m-%d"),
            window=int(window),
            y_bp=_scale(row["y"]),
            residual_bp=_scale(row["residual"]),
            residual_z=_num(row["residual_z"]),
            systematic_bp=_scale(row["systematic"]),
            exogenous_bp=_scale(row["exogenous"]),
            r2_full=_num(row["r2_full"]),
            r2_exog=_num(row["r2_exog"]),
            top_factor=_top_factor(contributions),
            contributions_bp=contributions,
            provisional=bool(row.get("provisional", False)),
            context=_context(history),
            robustness=(RB.state_for_pair(frame, pair, target)
                        if window == RB.CANONICAL_WINDOW else {"available": False}),
        ))
    return target.strftime("%Y-%m-%d"), facts


def trading_days(
    contract_dir: Path | None = None,
    window: int = DEFAULT_WINDOW,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """The contract's own date index is the trading calendar, used for source-date
    verification (SPEC_phase3 §5, check 2).

    No separate calendar library: a day that made it into the contract is a trading
    day as far as this system is concerned.
    """
    root = Path(contract_dir) if contract_dir else CONTRACT_DIR
    parts = sorted(Path(root).glob("year=*/part.parquet"))
    frame = pd.concat([pd.read_parquet(p, columns=["date", "window", "model"])
                       for p in parts], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    block = frame[(frame["window"] == window) & (frame["model"] == model)]
    return sorted({d.strftime("%Y-%m-%d") for d in block["date"]})


def select_triggered(facts: list[Fact], max_per_day: int = MAX_PER_DAY) -> list[Fact]:
    """Pairs meeting the criterion, sorted by |z| descending, capped per day.

    The cap applies to generation only; the caller writes the full criterion result
    into the manifest (SPEC_phase3 §1.5), because "several pairs fired on the same
    day" is itself information and must not be erased by the cap.
    """
    hit = [f for f in facts if f.triggers()]
    hit.sort(key=lambda f: (-f.abs_z, f.pair))
    return hit[:max_per_day]


def trigger_report(facts: list[Fact], max_per_day: int = MAX_PER_DAY) -> dict:
    """Full picture of the criterion evaluation; goes into the manifest."""
    hit = [f for f in facts if f.triggers()]
    hit.sort(key=lambda f: (-f.abs_z, f.pair))
    chosen = hit[:max_per_day]
    return {
        "criterion": {
            "abs_z_min": TRIGGER_Z,
            "abs_residual_bp_min": TRIGGER_RESIDUAL_BP,
            "max_per_day": max_per_day,
        },
        "evaluated": len(facts),
        "triggered": [f.pair for f in hit],
        "selected": [f.pair for f in chosen],
        "capped": len(hit) - len(chosen),
    }
