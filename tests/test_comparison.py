from types import SimpleNamespace

import numpy as np
import pytest

from fxdash.web.comparison import compare_pair, report


def snapshot(n=4):
    combos = {}
    for name, e in (("ols", 1), ("ridge", 2), ("lasso", 3)):
        combos[name] = SimpleNamespace(
            dates=[f"{i:04d}" for i in range(n)], y=np.full(n, 0.001),
            residual=np.full(n, e * 0.0001), provisional=np.zeros(n, dtype=bool),
            contributions={"A": np.full(n, (10-e)*0.0001)}, factors=["A"],
            betas={"A": np.arange(n, dtype=float)},
            selected={"A": np.arange(n) % 2} if name == "lasso" else None,
        )
    s = SimpleNamespace(combo=lambda p, w, m: combos.get(m), pairs=["TEST"],
                        date_last="0003", data_version="test", status={})
    return s, combos


def test_exact_metrics_and_selection():
    s, _ = snapshot()
    sample = compare_pair(s, "TEST", 126)["samples"]["recent"]
    assert sample["observations"] == 4
    assert sample["zero_mae_bp"] == pytest.approx(10)
    ols, ridge, lasso = sample["models"]
    assert ols["mae_bp"] == pytest.approx(1)
    assert ridge["rmse_bp"] == pytest.approx(2)
    assert ridge["allocation_l1_vs_ols_bp"] == pytest.approx(1)
    assert lasso["mse_relative_to_zero"] == pytest.approx(.09)
    assert lasso["selection"]["switch_fraction"] == 1
    assert lasso["selection"]["frequency"]["A"] == .5
    assert lasso["median_absolute_beta_change"]["A"] == 1


def test_final_finite_intersection_is_shared_across_models():
    s, c = snapshot()
    c["ols"].provisional[0] = True
    c["ridge"].residual[1] = np.nan
    c["lasso"].contributions["A"][2] = np.nan
    row = compare_pair(s, "TEST", 63)
    assert row["samples"]["all"]["observations"] == 1
    assert row["samples"]["all"]["start"] == "0003"
    assert row["samples"]["all"]["models"][2]["selection"]["switch_fraction"] is None


def test_no_sample_and_missing_model_degrade():
    s, c = snapshot()
    c["ridge"].provisional[:] = True
    assert compare_pair(s, "TEST", 126)["reason"] == "no_matched_final_rows"
    del c["lasso"]
    assert compare_pair(s, "TEST", 126)["reason"] == "requires_three_models"


def test_latest_252_and_revision_and_cache():
    s, _ = snapshot(300)
    r = report(s, 126)
    assert r is report(s, 126)
    row = r["pairs"][0]
    assert row["samples"]["recent"]["observations"] == 252
    assert row["samples"]["all"]["observations"] == 300
    assert "not forecast" in " ".join(r["notes"])


def test_mismatched_target_is_not_silently_compared():
    s, c = snapshot()
    c["ridge"].y[1] = .01
    with pytest.raises(ValueError, match="realised returns"):
        compare_pair(s, "TEST", 126)
