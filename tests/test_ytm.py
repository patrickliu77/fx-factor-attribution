"""YTM solver for MX10Y_DERIVED, validated against hand-computed known answers
(SPEC 2.6 / 8).

Under the Bonos M convention, when days to maturity is an exact multiple of 182, a
dirty price at par gives an annualised yield exactly equal to the coupon rate. The
identity is hand-checkable: per-period discount rate i = r*182/360, per-period coupon
C = c*182/360; the par condition i = C/100 gives r = c/100 directly.
"""

import math

import pytest

from fxdash.data.foreign.mx import COUPON_PERIOD_DAYS, DAY_COUNT, bond_price, solve_ytm


def test_par_bond_yields_coupon_rate():
    # 10 full coupon periods, dirty price 100: yield must equal the 8% coupon exactly
    assert solve_ytm(100.0, 8.0, 10 * COUPON_PERIOD_DAYS) == pytest.approx(8.0, abs=1e-9)


def test_par_bond_single_period():
    assert solve_ytm(100.0, 8.0, COUPON_PERIOD_DAYS) == pytest.approx(8.0, abs=1e-9)


def test_single_period_closed_form():
    # with one period left it is hand-computable: i = (100 + C) / P - 1, r = i * 360 / 182
    coupon_pct, price = 8.0, 102.0
    coupon = coupon_pct * COUPON_PERIOD_DAYS / DAY_COUNT
    expected = ((100.0 + coupon) / price - 1.0) * DAY_COUNT / COUPON_PERIOD_DAYS * 100.0
    assert expected == pytest.approx(3.96460, abs=1e-4)  # hand-computed reference value
    assert solve_ytm(price, coupon_pct, COUPON_PERIOD_DAYS) == pytest.approx(
        expected, abs=1e-9
    )


def test_price_function_round_trip():
    # for arbitrary parameters, plugging the solved yield back into the pricing
    # function must recover the original price
    for price, coupon_pct, days in [
        (96.546651, 8.0, 3466),  # actual observation 2026-08-26
        (105.25, 7.5, 2500),
        (88.0, 5.5, 3600),
    ]:
        rate = solve_ytm(price, coupon_pct, days)
        assert math.isfinite(rate)
        assert bond_price(rate / 100.0, coupon_pct, days) == pytest.approx(price, abs=1e-8)


def test_price_is_monotone_decreasing_in_yield():
    prices = [bond_price(r, 8.0, 3466) for r in (0.05, 0.07, 0.09, 0.11)]
    assert prices == sorted(prices, reverse=True)


def test_discount_premium_direction():
    # a discount bond yields above the coupon, a premium bond below
    assert solve_ytm(95.0, 8.0, 10 * COUPON_PERIOD_DAYS) > 8.0
    assert solve_ytm(105.0, 8.0, 10 * COUPON_PERIOD_DAYS) < 8.0


def test_invalid_inputs_return_nan():
    assert math.isnan(solve_ytm(float("nan"), 8.0, 1820))
    assert math.isnan(solve_ytm(100.0, 8.0, 0))
    assert math.isnan(solve_ytm(-1.0, 8.0, 1820))


def test_bond_price_rejects_nonpositive_maturity():
    with pytest.raises(ValueError):
        bond_price(0.08, 8.0, 0)
