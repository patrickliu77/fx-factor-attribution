import pandas as pd
import pytest

from fxdash.data import vintage_audit as A, vintages as V
from test_vintages import NOW, capture


def test_changes_separate_growth_revision_missing_and_dtype():
    idx = pd.date_range("2026-09-01", periods=4)
    left = pd.DataFrame({"a": [1., 2., None, 4.], "b": [1., 2., 3., 4.]}, index=idx)
    right = left.copy()
    right.loc[idx[1], "a"] = 2.5
    right.loc[idx[2], "a"] = 3.
    right.loc[idx[3], "b"] = None
    right = right.drop(idx[0])
    right.loc[pd.Timestamp("2026-09-05")] = [5., 5.]
    before = left.copy(deep=True)
    result = A.table_changes(left, right)
    assert result["added_dates"] == result["removed_dates"] == 1
    assert result["revised_cells"] == result["filled_cells"] == result["became_missing_cells"] == 1
    pd.testing.assert_frame_equal(left, before)


def test_equal_nan_and_column_changes():
    x = pd.DataFrame({"x": [None, 1.]}, index=pd.date_range("2026-09-01", periods=2))
    assert not A.table_changes(x, x.copy())["changed"]
    result = A.table_changes(x, x.rename(columns={"x": "y"}))
    assert result["added_columns"] == ["y"] and result["removed_columns"] == ["x"]


def test_inventory_baseline_is_not_engine_history(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    pd.DataFrame({"x": [1.]}, index=pd.date_range("2020-01-01", periods=1)).to_parquet(cache / "x.parquet")
    V.capture_cache(cache, tmp_path, clock=lambda: NOW)
    result = A.audit_archive(tmp_path)
    assert result["state"] == "cache_only"
    assert result["comparison"]["state"] == "not_enough_comparable_captures"
    assert result["verified_records"][0]["observed_at"].startswith("2026-09-07")


def test_raw_revision_exact_and_bounded_examples(tmp_path, synthetic_raw):
    first = capture(synthetic_raw, tmp_path)
    synthetic_raw.hy_oas.iloc[-20:] += .1
    second = capture(synthetic_raw, tmp_path)
    result = A.compare_captures(tmp_path / first["manifest"], tmp_path / second["manifest"])
    assert result["changed_tables"] == 1
    assert result["tables"]["hy_oas"]["revised_cells"] == 20
    assert len(result["tables"]["hy_oas"]["examples"]) == 12


def test_corrupt_newest_file_is_not_hidden_by_an_older_good_one(tmp_path, synthetic_raw):
    capture(synthetic_raw, tmp_path)
    path = tmp_path / "input_archive/captures/9999-invalid.json"
    path.write_text("not json")
    result = A.audit_archive(tmp_path)
    assert result["state"] == "incomplete"
    assert len(result["issues"]) == 1
    assert result["verified_records"]


def test_different_capture_kinds_never_compared(tmp_path, synthetic_raw):
    first = capture(synthetic_raw, tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    synthetic_raw.fx_returns.to_parquet(cache / "fx.parquet")
    baseline = V.capture_cache(cache, tmp_path, clock=lambda: NOW)
    with pytest.raises(ValueError, match="different_capture_kinds"):
        A.compare_captures(tmp_path / first["manifest"], tmp_path / baseline["manifest"])
