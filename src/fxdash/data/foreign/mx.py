"""Mexico: Banxico SIE API, needs BANXICO_TOKEN.

Mexico has no daily government-bond YTM series. The short end uses Cetes 364-day
directly (SF45473). The long end MX10Y_DERIVED is backed out from the three daily
series of the CF300 price vector: SF45455 dirty price, SF45478 coupon, SF45430 days
to maturity (SPEC 2.6). The vector tracks the benchmark bond of the "7 to 10 year"
bucket rather than a strict 10-year point, so it is named MX10Y_DERIVED instead of
MX10Y (CLAUDE.md 10) and is continuously validated by the monthly deviation guardrail
against SF30057.

The Bonos M pricing convention is a 182-day coupon period with interest on 182/360:
    i = r * 182 / 360                     per-period discount rate, r annualised yield
    C = c * 182 / 360                     per-period coupon per 100 face, c coupon rate in percent
    P = sum_k C / (1+i)^(d_k/182) + 100 / (1+i)^(N/182)
where N is days to maturity and coupon dates are d_k = N - 182*(K-k), K = ceil(N/182).
P is the dirty price, so no accrued interest needs to be subtracted.
"""

from __future__ import annotations

import math
import os

import numpy as np
import pandas as pd
import requests
from scipy.optimize import brentq

from ...config import START
from ..base import get_frame, record

SIE = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"
CETES_364 = "SF45473"  # short end, Cetes 364-day yield
DIRTY_PRICE = "SF45455"  # Bonos 7 a 10 años dirty price
COUPON = "SF45478"  # Bonos 7 a 10 años coupon
PLAZO = "SF45430"  # Bonos 7 a 10 años days to maturity
OFFICIAL_10Y_MONTHLY = "SF30057"  # cross-check counterparty, primary-market auction monthly mean

COUPON_PERIOD_DAYS = 182
DAY_COUNT = 360
TIMEOUT = 60

# Event footnotes for the circuit breaker's historical hit months (SPEC_phase2 3.3).
# Footnotes are background annotations; the mechanism is explained by the intra-month
# sampling difference between primary and secondary markets, unrelated to derivation
# quality: none of the three months had a benchmark switch, all had unusually wide
# intra-month derived ranges (66 to 97bp), and the deviation was negative every time.
EVENT_FOOTNOTES = {
    "2011-10": "European debt crisis escalated; EFSF enlarged in late October; sharp risk-asset rally.",
    "2013-06": "Taper tantrum; June 19 FOMC; EM bond selloff.",
    "2023-11": (
        "Global bond rally in November, starting with the November 1 FOMC and the "
        "quarterly refunding announcement; the below-expectations US October CPI "
        "on the 14th accelerated the move."
    ),
}
FOOTNOTE_COMMON_NOTE = (
    "Footnotes are background annotations; the mechanism is explained by the "
    "intra-month sampling difference between primary and secondary markets, "
    "unrelated to derivation quality."
)

# Guardrail parameters (SPEC 2.6 finalised version)
BASIS_LOOKBACK_MONTHS = 12
BASIS_MIN_MONTHS = 6
RESIDUAL_THRESHOLD_BP = 15.0
RAW_ALARM_BP = 50.0
FAIL_CONSECUTIVE_MONTHS = 6


def _token() -> str:
    token = os.environ.get("BANXICO_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BANXICO_TOKEN not set; cannot fetch from Banxico SIE")
    return token


def fetch_sie(series_ids, start: str = START, end: str | None = None) -> pd.DataFrame:
    """Fetch a set of SIE series. Banxico records missing values as N/E; numbers
    carry thousands-separator commas."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    ids = ",".join(series_ids)
    response = requests.get(
        f"{SIE}/{ids}/datos/{start}/{end}",
        headers={"Bmx-Token": _token()},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Banxico SIE request failed: {ids}, HTTP {response.status_code}")
    columns = {}
    for series in response.json()["bmx"]["series"]:
        rows = series.get("datos", [])
        if not rows:
            continue
        values = pd.to_numeric(
            [str(r["dato"]).replace(",", "") for r in rows], errors="coerce"
        )
        columns[series["idSerie"]] = pd.Series(
            values, index=pd.to_datetime([r["fecha"] for r in rows], format="%d/%m/%Y")
        )
    if not columns:
        raise RuntimeError(f"Banxico SIE empty response: {ids}")
    return pd.DataFrame(columns).sort_index()


def bond_price(annual_yield: float, coupon_pct: float, days_to_maturity: float) -> float:
    """Dirty price per 100 face under the Bonos M convention."""
    if days_to_maturity <= 0:
        raise ValueError(f"days to maturity must be positive, got {days_to_maturity}")
    period_rate = annual_yield * COUPON_PERIOD_DAYS / DAY_COUNT
    coupon = coupon_pct * COUPON_PERIOD_DAYS / DAY_COUNT
    n_coupons = math.ceil(days_to_maturity / COUPON_PERIOD_DAYS)
    price = 0.0
    for k in range(1, n_coupons + 1):
        days = days_to_maturity - COUPON_PERIOD_DAYS * (n_coupons - k)
        price += coupon / (1.0 + period_rate) ** (days / COUPON_PERIOD_DAYS)
    price += 100.0 / (1.0 + period_rate) ** (days_to_maturity / COUPON_PERIOD_DAYS)
    return price


def solve_ytm(
    dirty_price: float, coupon_pct: float, days_to_maturity: float
) -> float:
    """Back out the annualised yield from the dirty price, returned in percent.
    Price is monotone decreasing in yield; use Brent."""
    if not np.isfinite([dirty_price, coupon_pct, days_to_maturity]).all():
        return float("nan")
    if dirty_price <= 0 or days_to_maturity <= 0:
        return float("nan")

    def objective(rate):
        return bond_price(rate, coupon_pct, days_to_maturity) - dirty_price

    low, high = 1e-8, 2.0
    if objective(low) < 0 or objective(high) > 0:
        return float("nan")  # price outside the solvable bracket
    return brentq(objective, low, high, xtol=1e-12, rtol=1e-14) * 100.0


def derive_mx10y(frame: pd.DataFrame) -> pd.DataFrame:
    """Solve the YTM day by day and flag benchmark-switch days for diff blanking."""
    needed = [DIRTY_PRICE, COUPON, PLAZO]
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise RuntimeError(f"MX10Y derivation missing input series {missing}")
    data = frame[needed].dropna()

    ytm = pd.Series(
        [
            solve_ytm(p, c, n)
            for p, c, n in zip(
                data[DIRTY_PRICE], data[COUPON], data[PLAZO], strict=True
            )
        ],
        index=data.index,
        name="long",
    )

    # Days to maturity normally decreases day by day; a jump up means the benchmark
    # bond switched and that day's level is not comparable
    plazo_change = data[PLAZO].diff()
    switch = plazo_change > 0
    record(
        "mx_benchmark_switch",
        n_switch_days=int(switch.sum()),
        first=str(data.index[0].date()),
        last=str(data.index[-1].date()),
        n_unsolved=int(ytm.isna().sum()),
        action="blank the diff on switch days",
    )
    out = pd.DataFrame({"long": ytm})
    out["break_long"] = switch.reindex(out.index).fillna(False).astype(int)
    return out


def _fetch_raw() -> pd.DataFrame:
    vectors = fetch_sie([DIRTY_PRICE, COUPON, PLAZO])
    derived = derive_mx10y(vectors)
    cetes = fetch_sie([CETES_364])[CETES_364].rename("short")
    frame = pd.concat([cetes, derived], axis=1).sort_index()
    frame["break_short"] = 0
    return frame


def fetch() -> pd.DataFrame:
    frame = get_frame("foreign_mx_banxico", _fetch_raw)
    for col in ("break_short", "break_long"):
        if col not in frame:
            frame[col] = 0
    return frame


# ------------------------------------------------------------------ guardrail
def monthly_guardrail(
    derived_long: pd.Series, official: pd.Series | None = None
) -> tuple[pd.DataFrame, dict]:
    """Monthly cross-check against SF30057 (SPEC 2.6 finalised version: subtract the
    basis before counting).

    SF30057 is a primary-market auction monthly mean while the derived side is a
    secondary-market full-month mean, so a primary-secondary basis exists between
    them; the basis mechanism exists precisely to absorb it, and breaches are judged
    only after the basis is subtracted.

    official=None fetches SF30057 live; tests pass a monthly series to stay offline.
    """
    if official is None:
        official = fetch_sie([OFFICIAL_10Y_MONTHLY])[OFFICIAL_10Y_MONTHLY]
    official = official.copy()
    if not isinstance(official.index, pd.PeriodIndex):
        official.index = pd.DatetimeIndex(official.index).to_period("M")
    derived_monthly = derived_long.groupby(derived_long.index.to_period("M")).mean()

    months = derived_monthly.index.sort_values()
    rows = []
    for month in months:
        off = float(official.get(month, np.nan))
        der = float(derived_monthly.loc[month])
        raw_bp = (der - off) * 100 if np.isfinite(off) else np.nan
        rows.append(
            {
                "month": str(month),
                "derived": round(der, 4),
                "official": round(off, 4) if np.isfinite(off) else None,
                "raw_dev_bp": round(raw_bp, 2) if np.isfinite(raw_bp) else None,
                "available": bool(np.isfinite(raw_bp)),
            }
        )
    table = pd.DataFrame(rows)

    # Basis: median of the trailing 12 available monthly deviations up to last month;
    # all of them when fewer than 12, minimum 6, current month excluded
    basis, residual, over15, over50 = [], [], [], []
    history: list[float] = []
    for row in table.itertuples():
        if len(history) >= BASIS_MIN_MONTHS:
            current_basis = float(
                np.median(history[-BASIS_LOOKBACK_MONTHS:])
            )
        else:
            current_basis = np.nan
        resid = (
            row.raw_dev_bp - current_basis
            if row.available and np.isfinite(current_basis)
            else np.nan
        )
        basis.append(round(current_basis, 2) if np.isfinite(current_basis) else None)
        residual.append(round(resid, 2) if np.isfinite(resid) else None)
        over15.append(bool(np.isfinite(resid) and abs(resid) > RESIDUAL_THRESHOLD_BP))
        over50.append(
            bool(row.available and abs(row.raw_dev_bp) > RAW_ALARM_BP)
        )
        if row.available:
            history.append(row.raw_dev_bp)

    table["basis_bp"] = basis
    table["resid_dev_bp"] = residual
    table["over_15"] = over15
    table["over_50"] = over50

    # Reverse breach during basis reversion: a single-month breach whose residual has
    # the opposite sign to the basis signals the basis reverting, not derivation
    # failure. The report page demotes it to a light-toned note labelled "basis
    # reversion period" - no red, and it does not count toward the consecutive streak
    # (SPEC_phase2 3.2).
    reverse = []
    for row in table.itertuples():
        is_reverse = bool(
            row.over_15
            and row.basis_bp is not None
            and row.resid_dev_bp is not None
            and row.basis_bp != 0
            and (row.resid_dev_bp > 0) != (row.basis_bp > 0)
        )
        reverse.append(is_reverse)
    table["reverse_breach"] = reverse
    # reverse breaches do not count toward the streak
    table["counts_toward_streak"] = table["over_15"] & ~table["reverse_breach"]
    table["footnote"] = table["month"].map(EVENT_FOOTNOTES).fillna("")

    # Streaks count available months only; official N/E months are skipped without
    # resetting
    streak = max_streak = 0
    fail_month = None
    for row in table.itertuples():
        if not row.available or row.resid_dev_bp is None:
            continue
        if row.counts_toward_streak:
            streak += 1
            max_streak = max(max_streak, streak)
            if streak >= FAIL_CONSECUTIVE_MONTHS and fail_month is None:
                fail_month = row.month
        else:
            streak = 0

    verdict = {
        "failed": fail_month is not None,
        "fail_month": fail_month,
        "max_consecutive_over_15": max_streak,
        "n_months": int(len(table)),
        "n_available": int(table["available"].sum()),
        "n_over_15": int(table["over_15"].sum()),
        "n_over_50": int(table["over_50"].sum()),
        "n_reverse_breach": int(table["reverse_breach"].sum()),
    }
    record("mx_guardrail", **verdict)
    if verdict["n_over_50"]:
        record(
            "mx_guardrail_alarm_50bp",
            months=[r.month for r in table.itertuples() if r.over_50],
        )
    return table, verdict
