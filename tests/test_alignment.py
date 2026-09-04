"""Alignment utilities: recovering a planted offset, carry-forward cap, stale flag
moving with the value, break mapping."""

import numpy as np
import pandas as pd

from fxdash.data.alignment import align_break_flags, align_to_index


def bdays(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def test_zero_offset_is_identity_on_observed_days():
    index = bdays(10)
    series = pd.Series(np.arange(10.0), index=index)
    values, stale = align_to_index(series, index, offset=0)
    pd.testing.assert_series_equal(values, series, check_names=False)
    assert not stale.any()


def test_offset_one_takes_previous_trading_day():
    """Offset +1 means factor day d maps to FX day d+1, i.e. the day uses the
    previous trading day's value."""
    index = bdays(10)
    series = pd.Series(np.arange(10.0), index=index)
    values, _ = align_to_index(series, index, offset=1)
    assert np.isnan(values.iloc[0])
    assert (values.iloc[1:].to_numpy() == np.arange(9.0)).all()


def test_negative_offset_takes_next_trading_day():
    index = bdays(10)
    series = pd.Series(np.arange(10.0), index=index)
    values, _ = align_to_index(series, index, offset=-1)
    assert (values.iloc[:-1].to_numpy() == np.arange(1.0, 10.0)).all()
    assert np.isnan(values.iloc[-1])


def test_recovers_a_planted_offset():
    """Construct y to depend strictly on x's previous-day value; among the three
    positions t-1 must have the highest correlation."""
    index = bdays(300)
    rng = np.random.default_rng(7)
    x = pd.Series(rng.normal(size=300), index=index)
    y = pd.Series(np.r_[np.nan, 2.0 * x.to_numpy()[:-1]], index=index)

    scores = {}
    for offset, label in ((1, "t-1"), (0, "t"), (-1, "t+1")):
        aligned, _ = align_to_index(x, index, offset)
        joined = pd.concat([y.rename("y"), aligned.rename("x")], axis=1).dropna()
        scores[label] = abs(joined["y"].corr(joined["x"]))
    assert max(scores, key=scores.get) == "t-1"
    assert scores["t-1"] > 0.99


def test_forward_fill_within_limit_reports_age():
    """Age is the number of trading days since the last real observation; 0 means an
    observation that day."""
    index = bdays(10)
    sparse = pd.Series([1.0, np.nan, np.nan, 4.0], index=index[:4])
    values, age = align_to_index(sparse, index[:4], offset=0, max_stale=3)
    assert values.tolist() == [1.0, 1.0, 1.0, 4.0]
    assert age.tolist() == [0, 1, 2, 0]


def test_no_fill_beyond_limit_leaves_hole():
    """Beyond the cap is a vacuum: whole-row missing, no carry forward (the
    Australian 2Y 2013 stretch)."""
    index = bdays(10)
    sparse = pd.Series([1.0], index=index[:1])
    values, stale = align_to_index(sparse, index, offset=0, max_stale=3)
    assert values.notna().sum() == 4  # observation day plus three carried-forward days
    assert values.iloc[4:].isna().all()
    assert stale.iloc[1:4].all()


def test_stale_age_shifts_with_the_value():
    """If the age did not move with the value, reports would pin the staleness on the
    wrong dates."""
    index = bdays(5)
    sparse = pd.Series([1.0, np.nan, 3.0, 4.0, 5.0], index=index)
    values, age = align_to_index(sparse, index, offset=1)
    assert age.tolist() == [0, 0, 1, 0, 0]
    assert values.iloc[2] == 1.0  # day 2 uses the value carried forward from day 1


def test_age_grows_across_a_publication_gap():
    """An RBA F2-style publication lag stays stale for several days running; the age
    must accumulate day by day."""
    index = bdays(8)
    sparse = pd.Series([1.0, 9.0], index=[index[0], index[6]])
    _, age = align_to_index(sparse, index, offset=0, max_stale=7)
    assert age.tolist() == [0, 1, 2, 3, 4, 5, 0, 1]


def test_empty_series_yields_all_nan():
    index = bdays(5)
    values, stale = align_to_index(pd.Series(dtype=float), index, offset=1)
    assert values.isna().all()
    assert not stale.any()


def test_break_flags_map_and_shift():
    index = bdays(5)
    flags = pd.Series([False, True, False, False, False], index=index)
    assert align_break_flags(flags, index, offset=0).tolist() == [
        False, True, False, False, False,
    ]
    assert align_break_flags(flags, index, offset=1).tolist() == [
        False, False, True, False, False,
    ]
