"""Independent reference tests for fold-local scaling and intercept handling."""

import numpy as np
import pytest
from sklearn.linear_model import Lasso, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from fxdash.models import lasso, ridge
from fxdash.models.rolling import _fit_once
from fxdash.models.validation import prepare_fold


@pytest.fixture
def shifting_sample():
    rng = np.random.default_rng(47)
    x = rng.normal(size=(100, 3))
    x[25:] *= np.array([6, 0.3, 2])
    x[50:] += np.array([5, -3, 8])
    y = 7 + x @ np.array([0.2, -1.1, 0.7]) + rng.normal(0, 0.3, 100)
    return x, y


@pytest.mark.parametrize("module,cls", [(ridge, Ridge), (lasso, Lasso)])
def test_cv_matches_independent_fold_fitted_sklearn_pipeline(shifting_sample, module, cls):
    x, y = shifting_sample
    grid = np.array([0.0001, 0.03, 0.7, 5.0])
    expected = np.zeros(len(grid))
    for train, test in ridge.time_series_splits(len(y)):
        for i, alpha in enumerate(grid):
            kwargs = {"max_iter": 20000, "tol": 1e-7} if cls is Lasso else {}
            model = make_pipeline(StandardScaler(), cls(alpha=alpha, **kwargs))
            model.fit(x[train], y[train])
            expected[i] += np.mean((y[test] - model.predict(x[test])) ** 2)
    np.testing.assert_allclose(module._cv_error(x, y, grid), expected, rtol=1e-9, atol=1e-10)


def test_future_rows_cannot_change_training_preprocessing(shifting_sample):
    x, y = shifting_sample
    train, test = ridge.time_series_splits(len(y))[0]
    before = prepare_fold(x, y, train, test)
    changed_x, changed_y = x.copy(), y.copy()
    changed_x[25:] += 1000
    changed_y[25:] -= 100
    after = prepare_fold(changed_x, changed_y, train, test)
    np.testing.assert_array_equal(before[0], after[0])
    np.testing.assert_array_equal(before[1], after[1])
    assert not np.array_equal(before[2], after[2])


@pytest.mark.parametrize("module", [ridge, lasso])
def test_rolling_passes_raw_training_window_to_penalty_selection(shifting_sample, module, monkeypatch):
    x, y = shifting_sample
    calls = []
    def select(raw_x, raw_y, tag):
        calls.append((raw_x.copy(), raw_y.copy()))
        return 0.03
    monkeypatch.setattr(module, "select_lambda", select)
    _fit_once(x, y, getattr(module, "solve_" + module.__name__.rsplit(".", 1)[1]), {}, True)
    np.testing.assert_array_equal(calls[0][0], x)
    np.testing.assert_array_equal(calls[0][1], y)


def test_constant_training_column_is_finite():
    x = np.column_stack([np.ones(12), np.arange(12)])
    arrays = prepare_fold(x, np.arange(12.0) + 5, slice(0, 6), slice(6, 12))
    assert all(np.isfinite(a).all() for a in arrays)
    np.testing.assert_array_equal(arrays[0][:, 0], 0)
