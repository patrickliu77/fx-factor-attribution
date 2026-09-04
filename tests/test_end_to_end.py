"""End to end: one pipeline produces the contract and every report (SPEC 8).

Runs the full pipeline on synthetic data, no network. The real-data end to end is
carried by python -m fxdash.run.
"""

import json

import pandas as pd
import pytest

from fxdash.attribution import contract as contract_mod
from fxdash.attribution.contract import build_contract
from fxdash.attribution.engine import attribute
from fxdash.config import MODELS, PAIRS, baseline_factors, lasso_menu
from fxdash.factors.build import build_pair_panel
from fxdash.models.pca_monitor import run_monitor
from fxdash.models.rolling import rolling_fit
from fxdash.report import build as report_mod

WINDOWS = [60, 90]


@pytest.fixture
def pipeline(synthetic_raw, isolated_outputs):
    # Paths come from the global isolation fixture; no per-constant stubs.

    panels, frames = {}, []
    for pair in PAIRS:
        panel = build_pair_panel(pair, synthetic_raw)
        panels[pair] = panel
        for window in WINDOWS:
            for model in MODELS:
                factors = lasso_menu(pair) if model == "lasso" else baseline_factors(pair)
                result = attribute(panel, rolling_fit(panel, pair, window, model, factors))
                frames.append(build_contract(pair, window, model, result))

    contract = pd.concat(frames, ignore_index=True)
    summary = contract_mod.write_contract(contract)
    monitor = run_monitor(synthetic_raw.fx_returns, WINDOWS[0])
    return contract, summary, panels, monitor, synthetic_raw, isolated_outputs


def test_contract_has_all_combinations(pipeline):
    contract, summary, *_ = pipeline
    assert summary["combos"] == len(PAIRS) * len(WINDOWS) * len(MODELS)
    assert set(contract["pair"]) == set(PAIRS)
    assert set(contract["model"]) == set(MODELS)
    assert set(contract["window"]) == set(WINDOWS)


def test_contract_columns_match_the_schema(pipeline):
    contract, *_ = pipeline
    assert list(contract.columns) == contract_mod.COLUMNS
    assert contract["schema_version"].nunique() == 1


def test_contract_round_trips_through_parquet(pipeline):
    contract, summary, *_ = pipeline
    reloaded = contract_mod.read_contract()
    assert len(reloaded) == len(contract) == summary["rows"]
    assert list(reloaded.columns) == contract_mod.COLUMNS


def test_contract_is_partitioned_by_year(pipeline):
    contract, summary, _, _, _, out_root = pipeline
    years = sorted(contract["date"].dt.year.unique())
    for year in years:
        assert (out_root / "contract" / f"year={year}" / "part.parquet").exists()
    assert summary["partitions"] == len(years)


def test_nested_fields_are_valid_json(pipeline):
    contract, *_ = pipeline
    row = contract.iloc[0]
    betas = json.loads(row["betas"])
    contributions = json.loads(row["contributions"])
    assert set(betas) == set(contributions)
    assert isinstance(json.loads(row["selected_factors"]), list)
    assert isinstance(json.loads(row["stale_flags"]), list)


def test_identity_holds_in_the_written_contract(pipeline):
    """The identity must survive the write to disk, not just hold in memory."""
    contract, *_ = pipeline
    sample = contract.sample(min(400, len(contract)), random_state=0)
    for _, row in sample.iterrows():
        total = sum(v for v in json.loads(row["contributions"]).values() if v is not None)
        assert abs(total + row["residual"] - row["y"]) < 1e-12


def test_latest_snapshot_is_written(pipeline):
    contract, _, _, _, _, out_root = pipeline
    snapshot = json.loads((out_root / "contract_latest.json").read_text(encoding="utf-8"))
    assert snapshot["date"] == str(contract["date"].max().date())
    assert snapshot["n_records"] == len(snapshot["records"]) > 0


def test_all_six_reports_are_written_and_self_contained(pipeline):
    _, _, panels, monitor, raw, out_root = pipeline
    paths = report_mod.build_all_reports(
        pipeline[0], panels, monitor, raw
    )
    assert len(paths) == len(PAIRS)
    for pair in PAIRS:
        html = (out_root / "reports" / f"{pair}.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert "data:image/png;base64," in html  # figures embedded, no external files
        assert "http://" not in html and "https://" not in html
        assert pair in html
