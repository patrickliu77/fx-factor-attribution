"""The benchmark comparison criterion of SPEC 10.1 (revised 2026-08-27).

The only criterion is per-pair deviation within ±0.05; ordering is binding only
between pairs whose expected values are more than 0.05 apart. CAD and NOK expected
values differ by just 0.02, far below their own annual variation, so no order is
required between them.
"""

import pandas as pd
import pytest

from fxdash.config import BENCHMARK_R2_MEAN, BENCHMARK_R2_TOL
from fxdash.run import benchmark_report


def _series(value: float, n: int = 300) -> pd.Series:
    index = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series(value, index=index)


def _report(values: dict[str, float]):
    return benchmark_report({pair: _series(v) for pair, v in values.items()})


def test_exact_benchmarks_pass_everything():
    verdict = _report(dict(BENCHMARK_R2_MEAN))
    assert verdict["all_within_tol"] is True
    assert verdict["rank_ok"] is True


def test_cad_nok_swap_is_allowed():
    """Expected values differ by only 0.02; a swapped order is not a violation."""
    values = dict(BENCHMARK_R2_MEAN)
    values["USDCAD"], values["USDNOK"] = 0.5855, 0.5695  # measured in this rebuild
    verdict = _report(values)
    assert verdict["all_within_tol"] is True
    assert verdict["rank_ok"] is True
    assert verdict["rank_violations"] == []
    # the order really is opposite to expected, but the criterion no longer objects
    assert verdict["rank_actual"].index("USDCAD") < verdict["rank_actual"].index("USDNOK")
    assert verdict["rank_expected"].index("USDNOK") < verdict["rank_expected"].index("USDCAD")


def test_wide_gap_inversion_is_still_a_violation():
    """AUD and MXN expected values are 0.22 apart; an inversion must be reported."""
    values = dict(BENCHMARK_R2_MEAN)
    values["USDAUD"], values["USDMXN"] = 0.40, 0.62
    verdict = _report(values)
    assert verdict["rank_ok"] is False
    assert any("USDAUD" in v and "USDMXN" in v for v in verdict["rank_violations"])


def test_deviation_beyond_tolerance_is_flagged():
    values = dict(BENCHMARK_R2_MEAN)
    values["USDNOK"] = BENCHMARK_R2_MEAN["USDNOK"] - BENCHMARK_R2_TOL - 0.01
    verdict = _report(values)
    assert verdict["all_within_tol"] is False
    row = next(r for r in verdict["table"] if r["pair"] == "USDNOK")
    assert row["within_tol"] is False


def test_nok_actual_deviation_is_inside_tolerance():
    """This rebuild's NOK deviation is -0.0405, at the edge but inside."""
    values = dict(BENCHMARK_R2_MEAN)
    values["USDNOK"] = 0.5695
    verdict = _report(values)
    row = next(r for r in verdict["table"] if r["pair"] == "USDNOK")
    assert row["deviation"] == pytest.approx(-0.0405, abs=1e-4)
    assert row["within_tol"] is True
    assert verdict["all_within_tol"] is True
