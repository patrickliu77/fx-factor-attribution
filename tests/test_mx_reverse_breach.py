"""MX guardrail reverse-breach handling and event footnotes (SPEC_phase2 3.2, 3.3)."""

import numpy as np
import pandas as pd
import pytest

from fxdash.data.foreign.mx import (
    EVENT_FOOTNOTES,
    FAIL_CONSECUTIVE_MONTHS,
    FOOTNOTE_COMMON_NOTE,
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


def test_reverse_breach_is_flagged_and_excluded_from_the_streak():
    """A reverse breach during basis reversion is the basis reverting, not derivation
    failure; it does not count toward the streak."""
    n = 40
    derived = [8.0] * n
    official = [8.0] * n
    # first build a stretch of stable positive basis so the basis median turns positive
    for i in range(0, 20):
        derived[i] = 8.40
    # then push the residual far the other way: positive basis, negative residual
    for i in range(20, 20 + FAIL_CONSECUTIVE_MONTHS + 2):
        derived[i] = 8.00

    table, verdict = monthly_guardrail(_series(derived), _official(official))
    flagged = table[table["reverse_breach"]]
    assert len(flagged) > 0
    # reverse breaches are excluded from the streak, so no derivation failure
    assert (~flagged["counts_toward_streak"]).all()
    assert verdict["n_reverse_breach"] == len(flagged)
    assert verdict["failed"] is False


def test_same_direction_breach_still_counts_and_can_fail():
    """Sustained breaches in the basis direction still count; the reverse rule must
    not hollow out the guardrail."""
    n = 30
    derived = [8.0] * n
    for i in range(14, 14 + FAIL_CONSECUTIVE_MONTHS):
        derived[i] = 8.30
    _, verdict = monthly_guardrail(_series(derived), _official([8.0] * n))
    assert verdict["failed"] is True


def test_reverse_breach_requires_an_actual_breach():
    """Months without a 15bp breach must not be flagged as reverse breaches."""
    n = 24
    table, _ = monthly_guardrail(_series([8.0] * n), _official([7.5] * n))
    assert not table["reverse_breach"].any()
    assert table["over_15"].sum() == 0


def test_event_footnotes_carry_the_ruled_wording():
    assert EVENT_FOOTNOTES["2011-10"].startswith("European debt crisis escalated")
    assert "EFSF" in EVENT_FOOTNOTES["2011-10"]
    assert "Taper tantrum" in EVENT_FOOTNOTES["2013-06"]
    assert "June 19 FOMC" in EVENT_FOOTNOTES["2013-06"]
    november = EVENT_FOOTNOTES["2023-11"]
    assert "November 1 FOMC and the quarterly refunding announcement" in november
    assert "below-expectations US October CPI on the 14th" in november
    assert set(EVENT_FOOTNOTES) == {"2011-10", "2013-06", "2023-11"}


def test_common_note_keeps_the_mechanism_separate_from_the_event():
    assert "background annotations" in FOOTNOTE_COMMON_NOTE
    assert "intra-month sampling difference between primary and secondary markets" in FOOTNOTE_COMMON_NOTE
    assert "unrelated to derivation quality" in FOOTNOTE_COMMON_NOTE


def test_footnotes_attach_to_the_right_months():
    months = pd.period_range("2011-08", "2011-12", freq="M")
    derived = _series([8.0] * len(months), start="2011-08")
    table, _ = monthly_guardrail(derived, _official([8.0] * len(months), start="2011-08"))
    row = table[table["month"] == "2011-10"].iloc[0]
    assert row["footnote"] == EVENT_FOOTNOTES["2011-10"]
    other = table[table["month"] == "2011-09"].iloc[0]
    assert other["footnote"] == ""
