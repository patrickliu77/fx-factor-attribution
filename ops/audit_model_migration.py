"""Read-only checks and a comparison report for a full model-history migration.

The report measures replacement differences, including any source revisions.
It does not estimate out-of-sample performance improvement.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fxdash.config import CONTRACT_SCHEMA_VERSION, MODEL_REVISION, PCA_MONITOR_SCHEMA_VERSION

KEY = ["date", "pair", "window", "model"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def load_contract(root):
    parts = sorted((Path(root) / "contract").glob("year=*/part.parquet"))
    if not parts:
        raise ValueError("Contract has no partitions")
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True).sort_values(KEY).reset_index(drop=True)


def compare_contracts(before, after):
    if before.duplicated(KEY).any() or after.duplicated(KEY).any():
        raise ValueError("Duplicate contract keys")
    old = before.sort_values(KEY).reset_index(drop=True)
    new = after.sort_values(KEY).reset_index(drop=True)
    if not old[KEY].equals(new[KEY]):
        raise ValueError("Contract key coverage changed; investigate before promotion")
    numeric = new[["y", "systematic", "exogenous", "residual"]].to_numpy()
    if not np.isfinite(numeric).all():
        raise ValueError("Non-finite return or grouped contribution")
    group_error = float(np.abs(numeric[:, 1:].sum(axis=1) - numeric[:, 0]).max())
    rows, factor_error = [], 0.0
    for (pair, window, model), new_group in new.groupby(["pair", "window", "model"]):
        old_group = old.loc[new_group.index]
        old_values = [json.loads(v) for v in old_group.contributions]
        new_values = [json.loads(v) for v in new_group.contributions]
        distance, sums = [], []
        for left, right in zip(old_values, new_values):
            if left.keys() != right.keys():
                raise ValueError("Factor menu changed")
            if any(v is None or not np.isfinite(v) for v in right.values()):
                raise ValueError("Non-finite factor contribution")
            distance.append(sum(abs(right[k] - left[k]) for k in right) * 1e4)
            sums.append(sum(right.values()))
        error = np.asarray(sums) + new_group.residual.to_numpy() - new_group.y.to_numpy()
        factor_error = max(factor_error, float(np.abs(error).max()))
        old_selected = [set(json.loads(v)) for v in old_group.selected_factors]
        new_selected = [set(json.loads(v)) for v in new_group.selected_factors]
        delta = (new_group.residual - old_group.residual).abs() * 1e4
        rows.append({
            "pair": pair, "window": int(window), "model": model, "rows": len(new_group),
            "first": str(new_group.date.min().date()), "last": str(new_group.date.max().date()),
            "median_factor_distance_bp": float(np.median(distance)),
            "p95_factor_distance_bp": float(np.quantile(distance, .95)),
            "max_residual_change_bp": float(delta.max()),
            "changed_rows_above_1e_8_bp": int((np.asarray(distance) > 1e-8).sum()),
            "selection_changed_days": sum(a != b for a, b in zip(old_selected, new_selected)),
            "max_return_change_bp": float((new_group.y - old_group.y).abs().max() * 1e4),
        })
    if max(group_error, factor_error) > 1e-12:
        raise ValueError("Attribution identity failed")
    return pd.DataFrame(rows), {"group_identity_max": group_error, "factor_identity_max": factor_error}


def audit(backup, candidate, report):
    backup, candidate, report = Path(backup), Path(candidate), Path(report)
    if report.exists():
        raise ValueError("Choose a new report directory")
    manifest = json.loads((backup / "backup_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if digest(backup / item["path"]) != item["sha256"]:
            raise ValueError("Backup changed: " + item["path"])
    for tree in ("alignment", "narrative"):
        old_files = {p.relative_to(backup / "outputs" / tree): digest(p)
                     for p in (backup / "outputs" / tree).rglob("*") if p.is_file()}
        new_files = {p.relative_to(candidate / "outputs" / tree): digest(p)
                     for p in (candidate / "outputs" / tree).rglob("*") if p.is_file()}
        if old_files != new_files:
            raise ValueError("Frozen archive changed: " + tree)
    before, after = load_contract(backup / "outputs"), load_contract(candidate / "outputs")
    table, identity = compare_contracts(before, after)
    if set(after.schema_version) != {CONTRACT_SCHEMA_VERSION}:
        raise ValueError("Mixed contract calculation versions")
    old_pca = pd.read_parquet(backup / "outputs/pca_monitor.parquet").sort_values(["window", "date"]).reset_index(drop=True)
    new_pca = pd.read_parquet(candidate / "outputs/pca_monitor.parquet").sort_values(["window", "date"]).reset_index(drop=True)
    if not old_pca[["date", "window"]].equals(new_pca[["date", "window"]]):
        raise ValueError("PCA coverage changed")
    if set(new_pca.schema_version) != {PCA_MONITOR_SCHEMA_VERSION}:
        raise ValueError("Mixed PCA calculation versions")
    pca = []
    for window, group in new_pca.groupby("window"):
        old_group = old_pca.loc[group.index]
        delta = group.carry_projection_r2 - old_group.carry_projection_r2
        pca.append({"window": int(window), "rows": len(group),
                    "median_projection_change": float(delta.median()),
                    "max_abs_projection_change": float(delta.abs().max()),
                    "changed_warning_days": int((group.warn_flags != old_group.warn_flags).sum())})
    status = json.loads((candidate / "outputs/status.json").read_text(encoding="utf-8"))
    run = json.loads((candidate / "outputs/run_manifest.json").read_text(encoding="utf-8"))
    if status["state"] == "red" or status.get("model_revision") != MODEL_REVISION:
        raise ValueError("Candidate status/revision failed")
    if not run["rewrite_history_allowed"] or run["coverage_shrink_allowed"]:
        raise ValueError("Unexpected migration flags")
    changed_inputs = [item["path"] for item in manifest["files"] if item["path"].startswith("data/")
                      and digest(candidate / item["path"]) != item["sha256"]]
    summary = {"model_revision": MODEL_REVISION, "schema_version": CONTRACT_SCHEMA_VERSION,
               "rows_before": len(before), "rows_after": len(after),
               "combos": len(table), "keys_identical": True, **identity, "pca": pca,
               "status": status["state"], "changed_input_files": changed_inputs,
               "archives_unchanged": ["alignment", "narrative"],
               "comparison_basis": "Replacement difference against saved history, including source revisions"}
    report.mkdir(parents=True)
    table.to_csv(report / "contract_comparison.csv", index=False)
    (report / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    for ax, metric, title in zip(axes, ["median_factor_distance_bp", "p95_factor_distance_bp"],
                                 ["Median replacement change", "95th percentile replacement change"]):
        table.groupby(["pair", "model"])[metric].mean().unstack().plot.bar(
            ax=ax, rot=0, color=["#1479c9", "#888888", "#6247d6"])
        ax.set(title=title, ylabel="Sum of absolute factor changes (bp)", xlabel="")
    fig.suptitle("Full history; mean across training windows; source revisions included")
    fig.savefig(report / "contract_comparison.png", dpi=160)
    plt.close(fig)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    audit(args.backup, args.candidate, args.report)
