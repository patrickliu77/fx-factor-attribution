"""Assemble the factor panel per pair.

Levels are always aligned first, each by its own offset, to the pair's FX trading-day
index; transforms then run on that index, so every transform measures the change
between two adjacent FX trading days. Spread factors follow SPEC 3.7: align each leg by
its own offset, take the leg difference first, then first-difference.

Panels are assembled per pair with no global inner join, so the six countries' holidays
do not contaminate one another (SPEC 2.8).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import (
    HIGH_YIELD,
    LONG_SLOT,
    LOW_YIELD,
    PAIRS,
    PUBLICATION_LAG_LEGS,
    SHORT_SLOT,
    US_LEG,
    lasso_menu,
)
from ..data.alignment import (
    FOREIGN,
    USD_CLOSE,
    align_break_flags,
    align_to_index,
    offset_for,
)
from ..data.base import record

def _log_return(series: pd.Series) -> pd.Series:
    return np.log(series.where(series > 0)).diff()


def _carry_loo(returns: pd.DataFrame, pair: str) -> pd.Series:
    """Low-yield group minus high-yield group, each excluding the explained pair
    (SPEC 3.4).

    CAD and NOK belong to neither group; the factor then degenerates to the full carry.
    Grouping is static.
    """
    low = [p for p in LOW_YIELD if p != pair]
    high = [p for p in HIGH_YIELD if p != pair]
    return returns[low].mean(axis=1, skipna=True) - returns[high].mean(
        axis=1, skipna=True
    )


def _dollar_loo(returns: pd.DataFrame, pair: str) -> pd.Series:
    """Equal-weight mean of the other five pairs. Must strictly exclude the explained
    pair; anything else is tautological leakage."""
    others = [p for p in PAIRS if p != pair]
    return returns[others].mean(axis=1, skipna=True)


def factor_stale_names(factor: str) -> tuple[str, ...]:
    """Names for stale flags. Spreads must tell which leg is stale (2026-08-27
    ruling 3)."""
    if factor in (SHORT_SLOT, LONG_SLOT):
        return (f"{factor}.us", f"{factor}.foreign")
    return (factor,)


def _spread(
    pair: str,
    slot: str,
    us_level: pd.Series,
    foreign_level: pd.Series,
    breaks: pd.Series,
    index: pd.DatetimeIndex,
    overrides: dict | None,
) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Legs aligned by their own offsets, then differenced across legs, then
    first-differenced; sign is US minus foreign.

    US rates rising relative to foreign should push USD/XXX up, so a positive beta is
    the economically positive direction.
    """
    us_offset = offset_for(pair, USD_CLOSE, overrides)
    foreign_offset = offset_for(pair, FOREIGN, overrides)
    us_aligned, us_stale = align_to_index(us_level, index, us_offset)
    fx_aligned, fx_stale = align_to_index(foreign_level, index, foreign_offset)

    level = us_aligned - fx_aligned
    diff = level.diff()

    # conventions differ across a break day, so its difference has no economic meaning
    break_days = align_break_flags(breaks, index, foreign_offset)
    if bool(break_days.any()):
        diff = diff.mask(break_days)
    return diff.rename(slot), {
        f"{slot}.us": us_stale,
        f"{slot}.foreign": fx_stale,
    }


def build_pair_panel(pair: str, raw, overrides: dict | None = None) -> pd.DataFrame:
    """Return the pair's y and all candidate factors, with stale flag columns attached.

    overrides is used only by alignment diagnostics to temporarily shift one factor
    class's offset; normal runs pass None.
    """
    y_full = raw.fx_returns[pair]
    index = pd.DatetimeIndex(y_full.dropna().index)

    usd_offset = offset_for(pair, USD_CLOSE, overrides)
    columns: dict[str, pd.Series] = {}
    stale: dict[str, pd.Series] = {}

    def add_usd_close(name, level, transform, break_dates=None) -> None:
        aligned, is_stale = align_to_index(level, index, usd_offset)
        values = transform(aligned)
        if break_dates is not None:
            # conventions differ across a break day, so its difference has no economic
            # meaning (the HY OAS user/FRED splice day)
            flags = pd.Series(False, index=level.index)
            flags.loc[flags.index.isin(pd.DatetimeIndex(break_dates))] = True
            values = values.mask(align_break_flags(flags, index, usd_offset))
        columns[name] = values
        stale[name] = is_stale

    # FX-internal constructed factors are always same-day
    columns["DOLLAR_LOO"] = _dollar_loo(raw.fx_returns, pair).reindex(index)
    columns["CARRY_LOO"] = _carry_loo(raw.fx_returns, pair).reindex(index)

    # spreads
    foreign = raw.foreign[pair]
    us_short_id, us_long_id = US_LEG[pair]
    for slot, us_id, foreign_col, break_col in (
        (SHORT_SLOT, us_short_id, "short", "break_short"),
        (LONG_SLOT, us_long_id, "long", "break_long"),
    ):
        series, leg_stale = _spread(
            pair,
            slot,
            raw.us_yields[us_id],
            foreign[foreign_col],
            foreign[break_col],
            index,
            overrides,
        )
        columns[slot] = series
        stale.update(leg_stale)

    # risk and credit
    add_usd_close("dVIX", raw.vix, lambda s: s.diff())
    add_usd_close("dBAA10Y", raw.baa, lambda s: s.diff())
    if getattr(raw, "hy_oas", None) is not None:
        splice = getattr(raw, "hy_oas_splice", None)
        add_usd_close(
            "dHY_OAS",
            raw.hy_oas,
            lambda s: s.diff(),
            break_dates=[splice] if splice is not None else None,
        )

    hyg, hyg_stale = align_to_index(raw.etfs["HYG"], index, usd_offset)
    iei, iei_stale = align_to_index(raw.etfs["IEI"], index, usd_offset)
    columns["HY_EXCESS"] = _log_return(hyg) - _log_return(iei)
    stale["HY_EXCESS"] = hyg_stale | iei_stale

    # commodities and ETFs
    for name in ("WTI", "BRENT", "COPPER", "GOLD"):
        add_usd_close(name, raw.cmdty[name], _log_return)
    add_usd_close("EMB", raw.etfs["EMB"], _log_return)

    menu = lasso_menu(pair)
    frame = pd.DataFrame({"y": y_full.reindex(index), **{k: columns[k] for k in menu}})

    stale_cols = {}
    for factor in menu:
        for name in factor_stale_names(factor):
            if name in stale:
                age = stale[name].reindex(index).fillna(0).astype("int64")
                stale_cols[f"stale::{name}"] = age > 0
                stale_cols[f"stale_age::{name}"] = age

    # Staleness from a publication-lag leg will later be filled by real observations;
    # rows computed from it are marked provisional and may be recomputed and
    # overwritten once official data arrives (SPEC_phase2 4.1 route B).
    #
    # But one leg carries two kinds of staleness that must be kept apart: **tail**
    # staleness means "not published yet" and will be filled later; **interior**
    # staleness is a local holiday — the source never had that day and never will.
    # Only the former counts as provisional. The criterion: is the day later than the
    # leg's last real observation.
    provisional = pd.Series(False, index=index)
    for leg in PUBLICATION_LAG_LEGS.get(pair, ()):
        for slot in (SHORT_SLOT, LONG_SLOT):
            key = f"{slot}.{leg}"
            if key not in stale:
                continue
            age = stale[key].reindex(index).fillna(0)
            fresh = index[age == 0]
            if len(fresh) == 0:
                continue
            provisional |= (age > 0) & (index > fresh[-1])
    stale_cols["provisional"] = provisional
    frame = pd.concat([frame, pd.DataFrame(stale_cols, index=index)], axis=1)

    before = len(frame)
    frame = frame.dropna(subset=["y", *menu])

    # The data frontier is provisional too: the delivered panel's latest trading-day
    # row has no later observation corroborating its inputs — upstream may retract or
    # relabel that bar (exactly the 2026-08-31 incident). Marking it provisional keeps
    # it overwritable until the next trading day appears and a recompute confirms it,
    # at which point it freezes. Publication lag and the data frontier are two kinds of
    # "inputs not final" sharing one flag. Must be set after dropna: the frontier row
    # may be joined away by a missing factor, and setting the flag before the join
    # would land it on a row that does not exist.
    if len(frame):
        frame.loc[frame.index[-1], "provisional"] = True
    record(
        "pair_panel",
        pair=pair,
        factors=menu,
        rows_before_join=before,
        rows_after_join=len(frame),
        lost=before - len(frame),
        first=str(frame.index[0].date()) if len(frame) else None,
        last=str(frame.index[-1].date()) if len(frame) else None,
    )
    return frame
