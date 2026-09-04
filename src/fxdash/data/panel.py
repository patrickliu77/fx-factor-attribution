"""Raw series loading and the FX return panel.

The FX return panel is built on the union of the six pairs' dates: carry close levels
forward first, then take log returns, then blank the returns on carried-forward days.
A currency on holiday thus does not contribute a fake zero return to DOLLAR_LOO that
day; it simply drops out of that day's mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import MAX_STALE_BDAYS, PAIRS, START, US_LEG
from . import fred_source, hy_oas, yf_source
from .base import record
from .foreign import fetch_foreign_yields


@dataclass
class RawData:
    fx_levels: dict[str, pd.Series]
    fx_returns: pd.DataFrame
    cmdty: dict[str, pd.Series]
    etfs: dict[str, pd.Series]
    us_yields: dict[str, pd.Series]
    vix: pd.Series
    baa: pd.Series
    foreign: dict[str, pd.DataFrame] = field(default_factory=dict)
    # Spliced HY OAS and its splice date; the diff on the splice day has no economic
    # meaning and must be blanked
    hy_oas: pd.Series | None = None
    hy_oas_splice: pd.Timestamp | None = None


def _fx_return_panel(levels: dict[str, pd.Series]) -> pd.DataFrame:
    union = pd.DatetimeIndex(sorted(set().union(*(s.index for s in levels.values()))))
    union = union[union >= pd.Timestamp(START)]
    columns = {}
    for pair, series in levels.items():
        on_union = series.reindex(union)
        observed = on_union.notna()
        filled = on_union.ffill(limit=MAX_STALE_BDAYS)
        returns = np.log(filled.where(filled > 0)).diff()
        # no real trade on a carried-forward day: blank the return rather than log zero
        columns[pair] = returns.where(observed)
    panel = pd.DataFrame(columns)[list(levels)]
    record(
        "fx_return_panel",
        n_days=len(panel),
        first=str(panel.index[0].date()),
        last=str(panel.index[-1].date()),
        per_pair_obs={p: int(panel[p].notna().sum()) for p in panel.columns},
    )
    return panel


def load_raw() -> RawData:
    """Fetch all raw series in one pass."""
    fx_levels = yf_source.fetch_fx_closes()
    cmdty = yf_source.fetch_commodity_closes()
    etfs = yf_source.fetch_etf_closes()
    vix = fred_source.get_vix()
    baa = fred_source.get_fred("BAA10Y")
    us_yields = fred_source.get_us_yields(
        [sid for pair in PAIRS for sid in US_LEG[pair]]
    )
    foreign = {pair: fetch_foreign_yields(pair) for pair in PAIRS}
    hy_series, hy_splice = hy_oas.build()
    return RawData(
        fx_levels=fx_levels,
        fx_returns=_fx_return_panel(fx_levels),
        cmdty=cmdty,
        etfs=etfs,
        us_yields=us_yields,
        vix=vix,
        baa=baa,
        foreign=foreign,
        hy_oas=hy_series,
        hy_oas_splice=hy_splice,
    )
