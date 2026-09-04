"""Finalized parameter registry: the single place the code reads its fixed parameters
from. Each value was settled in the project's design notes before it landed here, and
a change starts there, not in this file."""

from pathlib import Path

# ---------------------------------------------------------------- paths
# config.py -> fxdash -> src -> repo root; no hardcoded absolute paths (CLAUDE.md 11)
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
USER_DIR = DATA_DIR / "user"
OUTPUT_DIR = REPO_ROOT / "outputs"
ALIGNMENT_DIR = OUTPUT_DIR / "alignment"
CONTRACT_DIR = OUTPUT_DIR / "contract"
REPORT_DIR = OUTPUT_DIR / "reports"


def display_path(path) -> str:
    """Repo-relative, forward-slash form of a path for console and log lines.

    An absolute path carries no information in a log and changes from machine to
    machine; it is also the one thing that can drag non-ASCII characters into a
    console log that is otherwise pure ASCII. Paths outside the repository fall
    back to their absolute form."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


START = "2010-01-01"

# The history range is pinned explicitly, never inherited from whatever the data
# happens to deliver (SPEC_phase2 1.7). The original implementation silently lost
# ~579 trading days, 14% of the sample, with nobody noticing; the tolerances below
# exist for exactly that kind of silent truncation.
BACKFILL_START = "2010-01-01"
PANEL_START_TOLERANCE_DAYS = 10
# Tolerance for row-count comparison. yfinance FX close timestamps shift across time
# zones, so the still-open bar for the current day comes and goes; a jitter of one or
# two rows across runs is normal. The tolerance must absorb that jitter while staying
# far below what it is meant to catch -- the original implementation was short 579
# days. A later start date gets zero tolerance: that is a precise signal.
COVERAGE_ROW_TOLERANCE = 5

# ---------------------------------------------------------------- pair
PAIRS = ["USDEUR", "USDJPY", "USDCAD", "USDNOK", "USDAUD", "USDMXN"]

# ticker -> (pair, invert or not). EURUSD=X and AUDUSD=X quote the other way (CLAUDE.md 3)
FX_TICKERS = {
    "EURUSD=X": ("USDEUR", True),
    "JPY=X": ("USDJPY", False),
    "CAD=X": ("USDCAD", False),
    "NOK=X": ("USDNOK", False),
    "AUDUSD=X": ("USDAUD", True),
    "MXN=X": ("USDMXN", False),
}

# Full-sample median must fall inside the range, else the quote direction is flipped
# (CLAUDE.md 4)
DIRECTION_RANGES = {
    "USDEUR": (0.6, 1.05),
    "USDJPY": (80, 200),
    "USDCAD": (0.9, 1.6),
    "USDNOK": (4, 13),
    "USDAUD": (1.0, 2.0),
    "USDMXN": (10, 25),
}

CMDTY_TICKERS = {"CL=F": "WTI", "BZ=F": "BRENT", "HG=F": "COPPER", "GC=F": "GOLD"}
ETF_TICKERS = {"EMB": "EMB", "HYG": "HYG", "IEI": "IEI"}

# ---------------------------------------------------------------- differential legs
# The US leg matches the foreign leg's actual tenor, so the differential does not mix
# in term-structure slope (SPEC 2.3). Slot names stay d2Y_DIFF/d10Y_DIFF; the actual
# tenors are recorded in the alignment profile (approved 2026-08-05).
US_LEG = {
    "USDEUR": ("DGS2", "DGS10"),
    "USDJPY": ("DGS2", "DGS10"),
    "USDCAD": ("DGS2", "DGS10"),
    "USDNOK": ("DGS3", "DGS10"),
    "USDAUD": ("DGS2", "DGS10"),
    "USDMXN": ("DGS1", "DGS10"),
}

# pair -> (short-end tenor label, long-end tenor label), so the alignment profile can
# record the actual tenors
FOREIGN_TENOR = {
    "USDEUR": ("2Y", "10Y"),
    "USDJPY": ("2Y", "10Y"),
    "USDCAD": ("2Y", "10Y"),
    "USDNOK": ("3Y", "10Y"),  # Norway has no daily 2Y
    "USDAUD": ("2Y", "10Y"),
    "USDMXN": ("1Y", "10Y"),  # short end is 364-day Cetes; long end derived
}

SHORT_SLOT = "d2Y_DIFF"
LONG_SLOT = "d10Y_DIFF"

# ---------------------------------------------------------------- offsets
# Frozen (SPEC 1.2). +1 means factor day d maps to FX day d+1.
# Diagnostics rerun only on a data-source change; a rebuild must reproduce this table
# and stop on any mismatch (2026-08-27 ruling 4).
OFFSETS = {
    "USDEUR": {"usd_close": 1, "foreign": 0},
    "USDJPY": {"usd_close": 1, "foreign": 1},
    "USDCAD": {"usd_close": 1, "foreign": 1},
    "USDNOK": {"usd_close": 1, "foreign": 1},
    "USDAUD": {"usd_close": 1, "foreign": 1},
    "USDMXN": {"usd_close": 0, "foreign": 0},
}

# FX-internal constructed factors are always same-day (SPEC 1.2)
FX_INTERNAL_FACTORS = ("DOLLAR_LOO", "CARRY_LOO")

# Carry-forward cap. RBA F2 publishes on Fridays with at worst 7 days of staleness,
# which this absorbs; beyond the cap the value counts as a vacuum and the whole row
# goes missing instead of being carried forward (SPEC 2.5, AU 2Y 2013-05-20 to 08-30).
MAX_STALE_BDAYS = 7

# Publication-lag sources: staleness on these legs means "not published yet", not "no
# trading that day"; a real observation will arrive later, so rows computed from them
# are provisional and, once official data lands, may and must be recomputed and
# overwritten (SPEC_phase2 4.1 route B / CLAUDE.md 9). Staleness on every other source
# comes from holidays and is never filled in. RBA F2, published Fridays with up to
# 7 days of lag, is currently the only case.
PUBLICATION_LAG_LEGS = {"USDAUD": ("foreign",)}

# Age cap for provisional rows (calendar days). F2's normal lag stays within 8 days;
# 21 days is a deliberately loose tripwire: beyond it the official source may have
# stopped publishing or changed its interface, so status turns yellow instead of
# waiting indefinitely for the backfill.
PROVISIONAL_AGE_LIMIT_DAYS = 21

# Heartbeat: how long since the last successful live run. The most dangerous failure
# mode of an unattended system is not an error but the scheduled task quietly no
# longer running -- the page keeps showing yesterday's content and everything looks
# fine. A daily task normally fires every ~24 hours; 26 leaves margin for weekends and
# DST switches, 72 means it has not run for days in a row.
HEARTBEAT_WARN_HOURS = 26
HEARTBEAT_CRIT_HOURS = 72

# ---------------------------------------------------------------- factor library
BASE_FACTORS = ["DOLLAR_LOO", "CARRY_LOO", SHORT_SLOT, LONG_SLOT, "dVIX"]

# Benchmarks are pinned; the engine does not choose between WTI and Brent (SPEC 3.2)
EXTRA_FACTORS = {
    "USDEUR": [],
    "USDJPY": ["GOLD"],
    "USDCAD": ["WTI"],
    "USDNOK": ["BRENT"],
    "USDAUD": ["COPPER", "GOLD"],
    "USDMXN": ["EMB"],
}

# Lasso menu only, never the OLS/Ridge design matrix (SPEC 3.3).
# dHY_OAS replaced dBAA10Y from Phase 2 on (SPEC_phase2 4.2). The full-history
# OAS is the series FRED had clipped to a rolling three years, which is what
# forced the BAA10Y proxy in the first place; once the full history arrived the
# proxy retired and left every menu. dBAA10Y exists today only as history: an
# unavailable series is never stood in for by a different one (CLAUDE.md rule
# 10), acquisition failures go through the three-step fallback chain and
# nothing else. The AUD menu stays HY_EXCESS-only (eight-factor cap).
OPTIONAL_FACTORS = {
    "USDEUR": ["HY_EXCESS", "dHY_OAS"],
    "USDJPY": ["HY_EXCESS", "dHY_OAS"],
    "USDCAD": ["HY_EXCESS", "dHY_OAS"],
    "USDNOK": ["HY_EXCESS", "dHY_OAS"],
    "USDAUD": ["HY_EXCESS"],
    "USDMXN": ["HY_EXCESS", "dHY_OAS"],
}

# dHY_OAS splice: full history from the user file (1996-12-31 to 2026-02-06) joined to
# FRED's rolling three-year window. The splice date is fixed, it does not move as the
# FRED window rolls; the diff on the splice day is set to missing.
HY_OAS_USER_FILE = "fred_BAMLH0A0HYM2.csv"
HY_OAS_SPLICE_DATE = "2026-02-07"
HY_OAS_MEDIAN_RANGE = (3.0, 8.0)  # direction check, measured 4.54
# Overlap measured bit-identical; on clear disagreement stop and report
HY_OAS_OVERLAP_MAX_GAP_BP = 1.0
HY_OAS_OVERLAP_MIN_CORR = 0.999

MAX_FACTORS_PER_PAIR = 8  # hard cap (CLAUDE.md 19)

# CARRY_LOO groups are static. CAD and NOK sit in neither group and use the full
# carry (SPEC 3.4)
LOW_YIELD = ["USDJPY", "USDEUR"]
HIGH_YIELD = ["USDMXN", "USDAUD"]


def baseline_factors(pair):
    """Design-matrix columns for OLS and Ridge."""
    return BASE_FACTORS + EXTRA_FACTORS[pair]


def lasso_menu(pair):
    """Lasso candidate set: baseline plus optional factors."""
    return baseline_factors(pair) + OPTIONAL_FACTORS[pair]


def exogenous_factors(names):
    """The exog subset drops only the two FX-internal constructed factors, never any
    exogenous factor (2026-08-27 ruling 1)."""
    return [n for n in names if n not in FX_INTERNAL_FACTORS]


# ---------------------------------------------------------------- engine
WINDOWS = [63, 126, 252]
DEFAULT_WINDOW = 126
MODELS = ["ols", "ridge", "lasso"]

LAMBDA_GRID_LOG10 = (-4.0, 4.0)
LAMBDA_GRID_POINTS = 25
LAMBDA_REFIT_EVERY = 21  # trading days
CV_SPLITS = 3

PCA_N_COMPONENTS = 2
PCA_SIGN_REFERENCE_PAIR = "USDCAD"  # PC2 sign: USDCAD loading positive
# pc2_carry is the legacy line, kept until the projection R² goes live (SPEC_phase2 3.1).
# carry_projection_r2 is the new one: projection R² of CARRY onto span{PC2, PC3},
# rotation invariant.
PCA_CORR_WARN = {"pc1_dollar": 0.9, "pc2_carry": 0.6, "carry_projection_r2": 0.5}
PCA_MONITOR_SCHEMA_VERSION_NOTE = (
    "1.1.0 adds the carry_projection_r2 column (additive, backward compatible)"
)

RESIDUAL_Z_WINDOW = 126

# Reference lines for section 8 of the USDMXN report page: the 15bp guardrail
# threshold and the 50bp immediate-alert line
MX_GUARDRAIL_LINES = (15.0, 50.0)

# ---------------------------------------------------------------- health checks
# Absolute floors stay as the last line of defense (SPEC_phase2 2.3). live likewise
# requires 10 consecutive trading days: measured counts of days with r2_full above
# 0.75 are AUD 842, NOK 489, CAD 139, EUR 155 -- a per-day trigger would fire daily.
HEALTH_R2_LOW = 0.10
HEALTH_R2_HIGH = 0.75

# The two relative-threshold layers are ANDed (SPEC_phase2 2.2, confirmation gate D2).
# Measured on 16.2 years of sample: ~0.99 alert onsets per year system-wide, longest
# single episode 94 trading days. Re-measure those frequencies before changing these
# numbers (SPEC_phase2 2.5 monitoring discipline: an alert that stays lit means the
# criterion is wrong, not that the system is broken).
HEALTH_QUANTILE = 0.10
HEALTH_QUANTILE_WINDOW = 252
CROSS_PAIR_Z_THRESHOLD = -1.5
HEALTH_PERSIST_DAYS = 10

# Literature benchmark bands, for reports (PLAN part 6, Phase 0 target ranges, daily)
LITERATURE_BANDS_DAILY = {
    "USDEUR": (0.20, 0.45),
    "USDJPY": (0.20, 0.45),
    "USDCAD": (0.20, 0.50),
    "USDNOK": (0.20, 0.50),
    "USDAUD": (0.20, 0.50),
    "USDMXN": (0.10, 0.35),
}

# ---------------------------------------------------------------- outputs
# 1.1.0: adds the provisional column (backward-compatible addition; downstream reads
# legacy data with a default of False)
CONTRACT_SCHEMA_VERSION = "1.1.0"
PCA_MONITOR_SCHEMA_VERSION = "1.1.0"

# SPEC 10.1 reference benchmark: mean rolling R² of the full OLS model, 126-day window
BENCHMARK_R2_MEAN = {
    "USDAUD": 0.62,
    "USDNOK": 0.61,
    "USDCAD": 0.59,
    "USDEUR": 0.52,
    "USDJPY": 0.45,
    "USDMXN": 0.40,
}
BENCHMARK_R2_TOL = 0.05
# Delivery date of the original implementation. The R² comparison must truncate here
# to stay same-period comparable with it (2026-08-27 ruling 5)
BENCHMARK_AS_OF = "2026-08-06"
