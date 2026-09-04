"""Five dedicated tests for the MX10Y_DERIVED guardrail (SPEC 2.6 finalised version:
subtract the basis before counting)."""

import numpy as np
import pandas as pd
import pytest

from fxdash.data.foreign.mx import (
    BASIS_MIN_MONTHS,
    FAIL_CONSECUTIVE_MONTHS,
    RAW_ALARM_BP,
    RESIDUAL_THRESHOLD_BP,
    monthly_guardrail,
)


def _series(monthly_values, start="2015-01"):
    """Spread monthly values across daily frequency so the guardrail can take
    monthly means."""
    months = pd.period_range(start, periods=len(monthly_values), freq="M")
    index, values = [], []
    for month, value in zip(months, monthly_values, strict=True):
        for day in pd.date_range(month.start_time, month.end_time, freq="D")[:5]:
            index.append(day)
            values.append(value)
    return pd.Series(values, index=pd.DatetimeIndex(index))


def _official(monthly_values, start="2015-01"):
    months = pd.period_range(start, periods=len(monthly_values), freq="M")
    return pd.Series(monthly_values, index=months, dtype=float)


def test_basis_is_median_of_trailing_available_months():
    """Basis is the median of available monthly deviations up to last month, current
    month excluded; undefined below the minimum month count."""
    n = 14
    derived = _series([8.0] * n)
    official = _official([7.8] * n)  # a constant +20bp primary-secondary basis
    table, _ = monthly_guardrail(derived, official)

    assert table["basis_bp"].iloc[:BASIS_MIN_MONTHS].isna().all()
    later = table["basis_bp"].iloc[BASIS_MIN_MONTHS:].astype(float)
    np.testing.assert_allclose(later.to_numpy(), 20.0, atol=1e-6)


def test_constant_basis_is_absorbed_not_flagged():
    """A constant primary-secondary basis leaves zero residual once subtracted; no
    breach should be flagged."""
    n = 24
    table, verdict = monthly_guardrail(_series([8.0] * n), _official([7.5] * n))
    assert verdict["failed"] is False
    assert verdict["n_over_15"] == 0
    # the raw 50bp deviation persists throughout, but that is basis, not failure
    np.testing.assert_allclose(table["raw_dev_bp"].astype(float).to_numpy(), 50.0, atol=1e-6)


def test_guardrail_acts_on_residual_not_raw_deviation():
    """With a 50bp basis, a month at 60bp raw deviation keeps only a 10bp residual,
    under the 15bp threshold."""
    n = 20
    derived = [8.0] * n
    official = [7.5] * n
    derived[15] = 8.10  # that month: raw deviation 60bp, residual 10bp
    table, verdict = monthly_guardrail(_series(derived), _official(official))

    row = table.iloc[15]
    assert row["raw_dev_bp"] == pytest.approx(60.0, abs=1e-6)
    assert row["resid_dev_bp"] == pytest.approx(10.0, abs=1e-6)
    assert bool(row["over_15"]) is False
    assert verdict["failed"] is False


def test_official_na_months_are_skipped_without_resetting_the_streak():
    """Official N/E months are skipped without resetting: one missing month in the
    middle must not break the streak."""
    n = 24
    derived = [8.0] * n
    official = [8.0] * n
    for i in range(12, 19):  # seven consecutive available months deviating 30bp
        derived[i] = 8.30
    official[15] = np.nan  # one of them has the official value missing

    official_series = _official(official).dropna()
    _, verdict = monthly_guardrail(_series(derived), official_series)

    assert verdict["n_available"] == n - 1
    # the missing month is skipped rather than resetting; the remaining six
    # available months still constitute failure
    assert verdict["max_consecutive_over_15"] >= FAIL_CONSECUTIVE_MONTHS
    assert verdict["failed"] is True


def test_six_consecutive_over_threshold_fails_and_five_does_not():
    n = 26
    base_official = [8.0] * n

    def run(n_bad):
        derived = [8.0] * n
        for i in range(14, 14 + n_bad):
            derived[i] = 8.30  # residual 30bp, over 15bp
        return monthly_guardrail(_series(derived), _official(base_official))[1]

    five = run(FAIL_CONSECUTIVE_MONTHS - 1)
    six = run(FAIL_CONSECUTIVE_MONTHS)
    assert five["failed"] is False
    assert five["max_consecutive_over_15"] == FAIL_CONSECUTIVE_MONTHS - 1
    assert six["failed"] is True
    assert six["fail_month"] is not None


def test_single_month_over_50bp_alarms_immediately():
    n = 20
    derived = [8.0] * n
    derived[17] = 8.60  # raw deviation 60bp
    table, verdict = monthly_guardrail(_series(derived), _official([8.0] * n))

    assert verdict["n_over_50"] == 1
    assert bool(table.iloc[17]["over_50"]) is True
    assert abs(float(table.iloc[17]["raw_dev_bp"])) > RAW_ALARM_BP
    # immediate alarm and streak counting are independent lines; one month over 50
    # is not derivation failure
    assert verdict["failed"] is False


def test_threshold_constants_match_spec():
    assert RESIDUAL_THRESHOLD_BP == 15.0
    assert RAW_ALARM_BP == 50.0
    assert FAIL_CONSECUTIVE_MONTHS == 6
    assert BASIS_MIN_MONTHS == 6
