"""Synthetic-data fixtures. Unit tests never go online; outputs/ is isolated wholesale."""

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

import fxdash.config as _config
from fxdash.config import PAIRS, US_LEG
from fxdash.data.panel import RawData

# Import every module that binds a path constant under outputs/, so the
# isolation fixture below can discover and redirect all of them. A module
# imported lazily after the fixture ran would keep the real path.
import fxdash.attribution.contract  # noqa: F401  (OUTPUT_DIR, CONTRACT_DIR)
import fxdash.coverage              # noqa: F401  (COVERAGE_PATH)
import fxdash.heartbeat             # noqa: F401  (HEARTBEAT_PATH)
import fxdash.narrative.store       # noqa: F401  (NARRATIVE_DIR)
import fxdash.report.build          # noqa: F401  (REPORT_DIR)
import fxdash.report.overview       # noqa: F401  (REPORT_DIR)
import fxdash.run                   # noqa: F401  (OUTPUT_DIR)
import fxdash.schedule.modes        # noqa: F401  (AS_OF_PATH)
import fxdash.status                # noqa: F401  (STATUS_PATH)
import fxdash.web.newsfeed          # noqa: F401  (NARRATIVE_DIR)

# Captured at import time, before any test patches config.
REAL_OUTPUT_ROOT = pathlib.Path(_config.OUTPUT_DIR).resolve()


@pytest.fixture(autouse=True)
def isolated_outputs(tmp_path, monkeypatch):
    """Redirect every fxdash module-level Path under outputs/ into tmp_path.

    CLAUDE.md rule 23: tests isolate outputs/ wholesale. Discovery beats
    enumeration here; per-constant stubbing is how the dump_records hole
    happened, because a stub list is always one write path short of the
    real set. Anything a module binds under the real outputs/ is remapped,
    including constants added after this fixture was written.
    """
    fake_root = tmp_path / "outputs"
    fake_root.mkdir(exist_ok=True)
    for name, module in list(sys.modules.items()):
        if not (name == "fxdash" or name.startswith("fxdash.")):
            continue
        for attr, value in list(vars(module).items()):
            if not isinstance(value, pathlib.Path):
                continue
            try:
                relative = value.resolve().relative_to(REAL_OUTPUT_ROOT)
            except ValueError:
                continue
            target = fake_root if str(relative) == "." else fake_root / relative
            monkeypatch.setattr(module, attr, target)
    return fake_root


def bdays(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture
def rng():
    return np.random.default_rng(20260827)


@pytest.fixture
def synthetic_raw(rng):
    """Small six-pair panel with every factor present; values random, structure real."""
    index = bdays(400)
    n = len(index)

    fx_levels, returns = {}, {}
    for i, pair in enumerate(PAIRS):
        step = rng.normal(0, 0.005, n)
        level = 100.0 * np.exp(np.cumsum(step)) * (1 + 0.1 * i)
        fx_levels[pair] = pd.Series(level, index=index, name=pair)
        returns[pair] = pd.Series(np.r_[np.nan, np.diff(np.log(level))], index=index)
    fx_returns = pd.DataFrame(returns)

    def level_series(base, scale):
        return pd.Series(
            base + np.cumsum(rng.normal(0, scale, n)), index=index
        )

    us_ids = sorted({sid for pair in PAIRS for sid in US_LEG[pair]})
    foreign = {}
    for pair in PAIRS:
        frame = pd.DataFrame(
            {"short": level_series(1.5, 0.02), "long": level_series(2.5, 0.03)},
            index=index,
        )
        frame["break_short"] = False
        frame["break_long"] = False
        foreign[pair] = frame

    # The dHY_OAS splice date sits mid-sample, for the break-to-missing tests
    hy_oas = pd.Series(4.5 + np.cumsum(rng.normal(0, 0.03, n)), index=index)

    return RawData(
        hy_oas=hy_oas,
        hy_oas_splice=index[300],
        fx_levels=fx_levels,
        fx_returns=fx_returns,
        cmdty={
            name: pd.Series(
                50 * np.exp(np.cumsum(rng.normal(0, 0.02, n))), index=index
            )
            for name in ("WTI", "BRENT", "COPPER", "GOLD")
        },
        etfs={
            name: pd.Series(
                90 * np.exp(np.cumsum(rng.normal(0, 0.006, n))), index=index
            )
            for name in ("EMB", "HYG", "IEI")
        },
        us_yields={sid: level_series(2.0, 0.03) for sid in us_ids},
        vix=level_series(18.0, 0.5),
        baa=level_series(2.2, 0.02),
        foreign=foreign,
    )
