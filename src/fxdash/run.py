"""Single-command entry point: python -m fxdash.run --start 2010-01-01

Pipeline: load raw series, run alignment diagnostics and check the frozen offsets,
assemble the factor panel per pair, run rolling estimation and attribution over
{63,126,252} x {ols,ridge,lasso}, write the contract and the reports.

The alignment offsets are the foundation of engine-output correctness, so they are a
mid-pipeline sentinel: the frozen table is checked the moment diagnostics finish, and
on any mismatch we stop and report without entering the rest of the pipeline
(2026-08-27 ruling 4).
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from .attribution.contract import build_contract, read_contract, write_contract
from .attribution.engine import attribute, identity_error
from .config import (
    ALIGNMENT_DIR,
    BENCHMARK_AS_OF,
    BENCHMARK_R2_MEAN,
    BENCHMARK_R2_TOL,
    DEFAULT_WINDOW,
    MODELS,
    MODEL_REVISION,
    OUTPUT_DIR,
    PAIRS,
    START,
    WINDOWS,
    baseline_factors,
    display_path,
    lasso_menu,
)
from .coverage import enforce as enforce_coverage
from .data import diagnose, panel as panel_mod
from .data.alignment import read_profile
from .data.base import dump_records, record, records
from .factors.build import build_pair_panel
from .health import run_health_checks
from . import heartbeat
from .models.pca_monitor import run_monitor
from .models.rolling import rolling_fit
from .models.revision import require_compatible_history
from .schedule.merge import merge_contract
from .schedule.modes import (
    RunMode,
    as_of_advanced,
    load_previous_as_of,
    save_as_of,
    source_as_of,
)
from .status import build_status, write_status


class OffsetMismatch(RuntimeError):
    """Frozen offsets do not match. No retuning; stop immediately."""


def design_for(pair: str, model: str) -> list[str]:
    """Optional factors enter the Lasso menu only, never the OLS/Ridge design matrix
    (SPEC 3.3)."""
    return lasso_menu(pair) if model == "lasso" else baseline_factors(pair)


def check_offsets(raw, rediagnose: bool) -> dict:
    profile = None if rediagnose else read_profile()
    if profile is None:
        entries, mismatches = diagnose.run_diagnostics(raw)
        profile = diagnose.persist(entries)
        if mismatches:
            detail = ", ".join(
                f"{m['pair']}/{m['factor_class']}: diagnostics chose "
                f"{m['chosen_offset']:+d}, frozen {m['frozen_offset']:+d}"
                for m in mismatches
            )
            raise OffsetMismatch(
                f"Alignment diagnostics disagree with the frozen offset table of "
                f"SPEC 1.2 ({len(mismatches)} entries): {detail}. "
                "Per convention, stop and report; do not retune."
            )
    summary = profile.get("summary", {})
    record("offset_check", **summary)
    return profile


def benchmark_report(r2_by_pair: dict[str, pd.Series]) -> dict:
    """SPEC 10.1 comparison. Must truncate at the original implementation's delivery
    date to stay same-period comparable; the full sample is reported for reference."""
    as_of = pd.Timestamp(BENCHMARK_AS_OF)
    rows = []
    for pair, series in r2_by_pair.items():
        same_period = series[series.index <= as_of]
        expected = BENCHMARK_R2_MEAN[pair]
        actual = float(same_period.mean()) if len(same_period) else float("nan")
        rows.append(
            {
                "pair": pair,
                "expected": expected,
                "as_of_2026_08_06": round(actual, 4),
                "deviation": round(actual - expected, 4),
                "within_tol": bool(abs(actual - expected) <= BENCHMARK_R2_TOL),
                "full_sample": round(float(series.mean()), 4),
                "n_as_of": int(len(same_period)),
                "n_full": int(len(series)),
            }
        )
    table = pd.DataFrame(rows).sort_values("as_of_2026_08_06", ascending=False)
    expected_order = sorted(BENCHMARK_R2_MEAN, key=BENCHMARK_R2_MEAN.get, reverse=True)

    # The ranking only binds between pairs whose expected values differ by more than
    # the tolerance (SPEC 10.1, 2026-08-27 revision). CAD and NOK differ by only 0.02,
    # far below their own year-to-year variation, so their order is not constrained.
    actual = {r["pair"]: r["as_of_2026_08_06"] for r in rows}
    violations = [
        f"{a} should rank above {b}"
        for i, a in enumerate(expected_order)
        for b in expected_order[i + 1 :]
        if BENCHMARK_R2_MEAN[a] - BENCHMARK_R2_MEAN[b] > BENCHMARK_R2_TOL
        and actual[a] <= actual[b]
    ]
    verdict = {
        "tolerance": BENCHMARK_R2_TOL,
        "all_within_tol": bool(table["within_tol"].all()),
        "rank_expected": expected_order,
        "rank_actual": list(table["pair"]),
        "rank_violations": violations,
        "rank_ok": not violations,
        "table": table.to_dict(orient="records"),
    }
    record("benchmark_r2", **{k: v for k, v in verdict.items() if k != "table"})
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fxdash.run")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in RunMode],
        default=RunMode.BACKFILL.value,
        help="backfill: historical refill; live: daily increment (idempotent, "
        "see SPEC_phase2 1.3)",
    )
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=None, help="end date; defaults to the latest available")
    parser.add_argument("--pairs", nargs="*", default=PAIRS)
    parser.add_argument("--windows", nargs="*", type=int, default=WINDOWS)
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--rediagnose", action="store_true", help="rerun alignment diagnostics")
    parser.add_argument("--skip-report", action="store_true")
    parser.add_argument(
        "--allow-coverage-shrink",
        action="store_true",
        help="allow the history range to shrink versus the previous run. Use only "
        "when the shrink is confirmed intentional (SPEC_phase2 1.7)",
    )
    parser.add_argument(
        "--rewrite-history",
        action="store_true",
        help="explicit backfill recompute, allowed to rewrite frozen rows. Use only"
        " on an intentional factor-set, schema or model change; same rank as"
        " --allow-coverage-shrink (SPEC_phase2 1.3 constraint 1)",
    )
    args = parser.parse_args(argv)
    mode = RunMode(args.mode)
    # Validate before doing any work; do not let a ten-minute recompute finish only to
    # discover the flag combination was invalid
    if args.rewrite_history and mode is not RunMode.BACKFILL:
        parser.error("--rewrite-history is only allowed together with --mode backfill")

    require_compatible_history(
        OUTPUT_DIR, rewrite_history=args.rewrite_history,
        complete=(pd.Timestamp(args.start) <= pd.Timestamp(START) and args.end is None
                  and set(args.pairs) == set(PAIRS)
                  and set(args.windows) == set(WINDOWS)
                  and set(args.models) == set(MODELS)),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ALIGNMENT_DIR.mkdir(parents=True, exist_ok=True)

    import time as _time

    _t0 = _time.perf_counter()

    def _stage(label):
        print(f"  [{_time.perf_counter() - _t0:6.0f}s] {label}")

    print("Loading raw series ...")
    raw = panel_mod.load_raw()
    _stage("load complete")

    print("Checking frozen offsets ...")
    check_offsets(raw, args.rediagnose)
    _stage("offset check complete")

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end) if args.end else None
    panels, frames, r2_by_pair = {}, [], {}

    for pair in args.pairs:
        frame = build_pair_panel(pair, raw)
        frame = frame[frame.index >= start]
        if end is not None:
            frame = frame[frame.index <= end]
        panels[pair] = frame
        print(f"  {pair}: {len(frame)} rows {frame.index[0].date()}..{frame.index[-1].date()}")

    coverage = enforce_coverage(panels, strict=not args.allow_coverage_shrink)

    for pair in args.pairs:
        frame = panels[pair]
        for window in args.windows:
            for model in args.models:
                factors = design_for(pair, model)
                rolling = rolling_fit(frame, pair, window, model, factors)
                result = attribute(frame, rolling)
                error = identity_error(result)
                if error > 1e-12:
                    raise RuntimeError(
                        f"attribution identity not closed {pair}/w{window}/{model}: "
                        f"max error {error:.3e}"
                    )
                frames.append(build_contract(pair, window, model, result))
                if window == DEFAULT_WINDOW and model == "ols":
                    r2_by_pair[pair] = pd.Series(result.r2_full, index=result.dates)
            print(f"  {pair} w{window} done")

    # Health checks: backfill does an end-of-period summary only, live evaluates per
    # day (SPEC_phase2 2.1). The status color looks only at current (what is firing as
    # of the last day), never at the historical summary.
    _stage("rolling estimation and attribution complete")
    r2_panel = pd.DataFrame(r2_by_pair).sort_index()
    findings, current_findings = run_health_checks(r2_panel, panels, mode.value)
    _stage("health checks complete")

    incoming = pd.concat(frames, ignore_index=True)

    # Idempotent merge: non-provisional rows are never touched; provisional rows are
    # overwritten only when the input as of advances
    current_as_of = source_as_of(raw)
    advanced, moved = as_of_advanced(current_as_of, load_previous_as_of())
    try:
        existing = read_contract()
    except FileNotFoundError:
        existing = None
    merged = merge_contract(
        existing,
        incoming,
        as_of_advanced=advanced,
        trigger={"sources": moved},
        rewrite_history=args.rewrite_history,
        # On a full-range recompute the purge range extends to the existing last date,
        # sweeping out invalidated rows past the recompute range (the 2026-08-31
        # orphan rows sat exactly there); a partial backfill must never enable this
        purge_beyond=args.rewrite_history and args.end is None,
    )
    _stage("idempotent merge complete")
    contract = merged.frame
    summary = write_contract(contract)
    _stage("contract written to disk")
    save_as_of(current_as_of)
    print(f"contract: {summary}")
    print(f"merge: {merged.summary()}")
    if merged.audit:
        print(f"provisional overwrite of {len(merged.audit)} rows, audit recorded in manifest")

    monitor = pd.concat(
        [run_monitor(raw.fx_returns, window) for window in args.windows],
        ignore_index=True,
    )
    monitor.to_parquet(OUTPUT_DIR / "pca_monitor.parquet", index=False)

    benchmark = benchmark_report(r2_by_pair)
    print("\nSPEC 10.1 comparison (as of 2026-08-06, same-period comparable with the original):")
    print(pd.DataFrame(benchmark["table"]).to_string(index=False))
    print(f"  all within +/-{BENCHMARK_R2_TOL}: {benchmark['all_within_tol']}")
    print(
        f"  ranking (binding only for pairs with expected gap >{BENCHMARK_R2_TOL}): "
        f"{'pass' if benchmark['rank_ok'] else benchmark['rank_violations']}"
    )

    if findings:
        print("\nHealth check findings:")
        for finding in findings:
            print("  ", finding)

    reports = []
    if not args.skip_report:
        from .report.build import build_all_reports

        reports = build_all_reports(contract, panels, monitor, raw)
        print(f"\nreports: {len(reports)}")

    manifest = {
        "mode": mode.value,
        "model_revision": MODEL_REVISION,
        "start": str(start.date()),
        "end": str(contract["date"].max().date()),
        "pairs": args.pairs,
        "windows": args.windows,
        "models": args.models,
        "contract": summary,
        "benchmark": benchmark,
        "health_findings": findings,
        "health_current": current_findings,
        "coverage": coverage,
        "source_as_of": current_as_of,
        "as_of_advanced": advanced,
        "as_of_moved": moved,
        "merge": merged.summary(),
        # The overwrite audit is the backfill's paper trail; it must live in the
        # manifest, not just the log (SPEC_phase2 1.3 constraint 2)
        "provisional_overwrites": merged.audit,
        # Both override flags leave a record on every use; they must not become
        # habitual flags added out of hand
        "coverage_shrink_allowed": bool(args.allow_coverage_shrink),
        "rewrite_history_allowed": bool(args.rewrite_history),
    }
    (OUTPUT_DIR / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    # Heartbeat before status: this live run has already succeeded up to this point,
    # and this run is what the page should see
    heartbeat.beat(mode.value)
    status = write_status(build_status(contract, mode.value, manifest, current_findings))
    print(f"status: {status['state']}  {status['reasons'] or 'no alerts'}")

    # Overview page last: it reads only contract and status, so both must already be
    # on disk
    if not args.skip_report:
        from .report.overview import write_overview

        print(f"overview page: {display_path(write_overview(contract, status, manifest))}")

    dump_records(OUTPUT_DIR / "validation_log.jsonl")
    _stage("all done")
    return 0


def main_guarded(argv=None) -> int:
    """Write failures into status too; never leave the page stuck on last time's green.

    On a failed run, status.json and the overview page would simply not be rewritten,
    so the page keeps showing yesterday's green -- exactly the scenario the heartbeat
    guards against, yet cannot stop for itself. This writes a red status on the
    exception path.

    Note it cannot save the case where the process is killed externally (e.g. the
    Ctrl+C sent by Task Scheduler's StopOnIdleEnd); nothing gets written then. The
    fallback there is the overview page's own freshness self-check script.
    """
    try:
        return main(argv)
    except BaseException as exc:  # KeyboardInterrupt must leave a trace too
        try:
            from .status import build_status, write_status

            write_status(
                build_status(
                    pd.DataFrame(columns=["date", "provisional"]),
                    "unknown",
                    {"failed": f"{type(exc).__name__}: {str(exc)[:300]}"},
                )
            )
            dump_records(OUTPUT_DIR / "validation_log.jsonl")
        except Exception:
            pass  # if the fallback status write itself fails, do not mask the original error
        raise


if __name__ == "__main__":
    sys.exit(main_guarded())
