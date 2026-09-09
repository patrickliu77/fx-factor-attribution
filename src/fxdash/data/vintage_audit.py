"""Read-only archive inspection and exact changes between comparable captures."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .vintages import read_capture


def _frame(value):
    return value.to_frame() if isinstance(value, pd.Series) else value


def table_changes(before, after):
    left, right = _frame(before), _frame(after)
    if not left.index.is_unique or not right.index.is_unique or not left.columns.is_unique or not right.columns.is_unique:
        raise ValueError("non_unique_axes")
    dates = left.index.intersection(right.index)
    columns = left.columns.intersection(right.columns)
    a, b = left.loc[dates, columns], right.loc[dates, columns]
    revised = a.notna() & b.notna() & a.ne(b)
    filled = a.isna() & b.notna()
    missing = a.notna() & b.isna()
    examples = []
    for kind, mask in (("revised", revised), ("filled", filled), ("became_missing", missing)):
        for row, col in zip(*mask.to_numpy().nonzero()):
            if len(examples) >= 12:
                break
            old, new = a.iloc[row, col], b.iloc[row, col]
            examples.append({"kind": kind, "date": str(dates[row].date()), "column": str(columns[col]),
                             "before": None if pd.isna(old) else str(old), "after": None if pd.isna(new) else str(new)})
    result = {"added_dates": len(right.index.difference(left.index)),
              "removed_dates": len(left.index.difference(right.index)),
              "added_columns": list(map(str, right.columns.difference(left.columns))),
              "removed_columns": list(map(str, left.columns.difference(right.columns))),
              "revised_cells": int(revised.sum().sum()), "filled_cells": int(filled.sum().sum()),
              "became_missing_cells": int(missing.sum().sum()),
              "dtype_changes": [str(c) for c in columns if str(left[c].dtype) != str(right[c].dtype)],
              "examples": examples}
    result["changed"] = any(result[k] for k in result if k != "examples")
    return result


def compare_captures(before_path, after_path):
    before, left = read_capture(before_path)
    after, right = read_capture(after_path)
    if before["kind"] != after["kind"]:
        raise ValueError("different_capture_kinds")
    if datetime.fromisoformat(before["observed_at"]) > datetime.fromisoformat(after["observed_at"]):
        raise ValueError("capture_order_reversed")
    changes = {}
    for key in sorted(left.keys() | right.keys()):
        if key not in left or key not in right:
            changes[key] = {"state": "added_table" if key not in left else "removed_table", "changed": True}
        else:
            changes[key] = {"state": "compared", **table_changes(left[key], right[key])}
    return {"state": "compared", "kind": before["kind"],
            "before": Path(before_path).name, "after": Path(after_path).name,
            "before_observed_at": before["observed_at"], "after_observed_at": after["observed_at"],
            "changed_tables": sum(r["changed"] for r in changes.values()), "tables": changes,
            "interpretation": "Input snapshots differ; this does not imply a frozen attribution row was changed."}


def audit_archive(output_dir, *, limit=20):
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("positive_archive_limit_required")
    root = Path(output_dir) / "input_archive" / "captures"
    paths = sorted(root.glob("*.json"), reverse=True)
    records, issues = [], []
    for path in paths[:limit]:
        try:
            capture, tables = read_capture(path)
            stamp = datetime.fromisoformat(capture["observed_at"])
            if not stamp.tzinfo or capture["kind"] not in ("engine_inputs", "cache_baseline"):
                raise ValueError("invalid_capture_metadata")
            records.append({"file": path.name, "kind": capture["kind"], "observed_at": capture["observed_at"],
                            "tables": len(tables), "first_data_date": min((entry["first_date"] for entry in capture["tables"].values() if entry["first_date"]), default=None),
                            "last_data_date": max((entry["last_date"] for entry in capture["tables"].values() if entry["last_date"]), default=None)})
        except Exception as exc:
            # Error type only: never reproduce corrupt input or provider secrets.
            issues.append({"file": path.name, "reason": "capture_unreadable", "error_type": type(exc).__name__})
    records.sort(key=lambda row: datetime.fromisoformat(row["observed_at"]), reverse=True)
    engine = [r for r in records if r["kind"] == "engine_inputs"]
    baselines = [r for r in records if r["kind"] == "cache_baseline"]
    comparison = {"state": "not_enough_comparable_captures"}
    comparable = engine if engine else baselines
    if len(comparable) >= 2:
        try:
            comparison = compare_captures(root / comparable[1]["file"], root / comparable[0]["file"])
        except Exception as exc:
            comparison = {"state": "comparison_failed", "error_type": type(exc).__name__}
    return {"state": "incomplete" if issues else "engine_inputs_available" if engine else "cache_only" if records else "missing",
            "total_capture_files": len(paths), "inspection_limit": limit, "checked_files": min(len(paths), limit),
            "verified_records": records, "issues": issues, "comparison": comparison,
            "scope": "Only the most recent capture files within the inspection limit have been verified."}
