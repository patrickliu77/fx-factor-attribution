"""live idempotency and the provisional overwrite policy (SPEC_phase2 1.3 constraint 4).

The immutable-history boundary: non-provisional rows are never touched, provisional
rows are overwritten only when the input as of advances. The bit-identical-frozen-rows
item is the sister test of no_lookahead; together they draw the boundary.
"""

import json

import pandas as pd
import pytest

from fxdash.config import PROVISIONAL_AGE_LIMIT_DAYS
from fxdash.schedule.merge import merge_contract
from fxdash.schedule.modes import as_of_advanced
from fxdash.status import GREEN, YELLOW, build_status, stale_provisional


def _row(date, pair="USDAUD", provisional=False, r2=0.5, contribution=0.001):
    return {
        "date": pd.Timestamp(date),
        "pair": pair,
        "window": 126,
        "model": "ols",
        "betas": json.dumps({"d10Y_DIFF": 0.02}),
        "contributions": json.dumps({"d10Y_DIFF": contribution}),
        "r2_full": r2,
        "r2_exog": r2 - 0.1,
        "selected_factors": json.dumps(["d10Y_DIFF"]),
        "residual": 0.0002,
        "residual_z": 0.3,
        "stale_flags": json.dumps([]),
        "systematic": 0.0001,
        "exogenous": contribution,
        "y": 0.0013,
        "lambda": None,
        "provisional": provisional,
        "schema_version": "1.1.0",
    }


def _frame(rows):
    return pd.DataFrame(rows)


def test_provisional_row_is_overwritten_and_becomes_frozen():
    """Once official data lands, the provisional row is overwritten and turns frozen."""
    existing = _frame([_row("2026-08-24", provisional=True, r2=0.40, contribution=0.0)])
    incoming = _frame([_row("2026-08-24", provisional=False, r2=0.55, contribution=0.004)])

    result = merge_contract(existing, incoming, as_of_advanced=True)
    assert result.overwritten == 1
    row = result.frame.iloc[0]
    assert row["provisional"] is False or row["provisional"] == False  # noqa: E712
    assert row["r2_full"] == pytest.approx(0.55)

    # Once frozen, no later rerun may touch it again
    later = _frame([_row("2026-08-24", provisional=False, r2=0.99)])
    again = merge_contract(result.frame, later, as_of_advanced=True)
    assert again.overwritten == 0
    assert again.frozen_kept == 1
    assert again.frame.iloc[0]["r2_full"] == pytest.approx(0.55)


def test_frozen_rows_are_bit_identical_across_reruns():
    """Frozen rows stay bit-identical under any rerun (sister test of no_lookahead)."""
    existing = _frame(
        [_row(d, provisional=False, r2=0.5 + i / 100) for i, d in enumerate(
            ["2026-08-17", "2026-08-18", "2026-08-19"]
        )]
    )
    # Model changed, parameters changed, every value different -- history still untouchable
    incoming = _frame(
        [_row(d, provisional=False, r2=0.9) for d in
         ["2026-08-17", "2026-08-18", "2026-08-19"]]
    )
    result = merge_contract(existing, incoming, as_of_advanced=True)
    assert result.overwritten == 0
    assert result.frozen_kept == 3
    pd.testing.assert_frame_equal(
        result.frame.sort_values("date").reset_index(drop=True),
        existing.sort_values("date").reset_index(drop=True),
        check_like=True,
    )


def test_code_change_alone_never_overwrites_a_provisional_row():
    """No as-of advance means no legitimate reason to overwrite, even if the recompute
    differs (constraint 1)."""
    existing = _frame([_row("2026-08-24", provisional=True, r2=0.40)])
    incoming = _frame([_row("2026-08-24", provisional=True, r2=0.77)])
    result = merge_contract(existing, incoming, as_of_advanced=False)
    assert result.overwritten == 0
    assert result.provisional_kept == 1
    assert result.frame.iloc[0]["r2_full"] == pytest.approx(0.40)


def test_new_dates_are_appended_without_touching_history():
    existing = _frame([_row("2026-08-24", provisional=False, r2=0.4)])
    incoming = _frame([_row("2026-08-25", provisional=True, r2=0.6)])
    result = merge_contract(existing, incoming, as_of_advanced=False)
    assert result.appended == 1
    assert len(result.frame) == 2
    assert result.frame.set_index("date").loc["2026-08-24", "r2_full"] == pytest.approx(0.4)


def test_repeated_run_on_the_same_date_produces_no_duplicate_rows():
    existing = _frame([_row("2026-08-24", provisional=False)])
    for _ in range(3):
        existing = merge_contract(existing, _frame([_row("2026-08-24")]),
                                  as_of_advanced=True).frame
    assert len(existing) == 1


def test_overwrite_writes_an_audit_entry():
    """An overwrite must leave an audit trail, not just flash by in the log (constraint 2)."""
    existing = _frame([_row("2026-08-24", provisional=True, r2=0.40, contribution=0.0)])
    incoming = _frame([_row("2026-08-24", provisional=False, r2=0.55, contribution=0.004)])
    trigger = {"sources": {"USDAUD.foreign": {"before": "2026-08-19", "after": "2026-08-26"}}}

    result = merge_contract(existing, incoming, as_of_advanced=True, trigger=trigger)
    entry = result.audit[0]
    assert entry["date"] == "2026-08-24"
    assert entry["pair"] == "USDAUD"
    assert entry["provisional_before"] is True
    assert entry["provisional_after"] is False
    assert entry["trigger"] == trigger
    assert entry["r2_full_delta"] == pytest.approx(0.15)
    assert entry["contribution_d10Y_DIFF_delta"] == pytest.approx(0.004)


def test_rewrite_history_replaces_frozen_rows():
    """Explicit backfill recompute may rewrite frozen rows; this is the channel for
    intentional factor-set or schema changes."""
    existing = _frame([_row("2026-08-24", provisional=False, r2=0.40)])
    incoming = _frame([_row("2026-08-24", provisional=False, r2=0.77)])
    result = merge_contract(existing, incoming, as_of_advanced=False, rewrite_history=True)
    assert result.overwritten == 1
    assert result.frame.iloc[0]["r2_full"] == pytest.approx(0.77)


def test_rewrite_history_drops_superseded_rows():
    """When a factor-set change makes a whole day vanish, residue rows computed under
    the old convention must be dropped, not kept."""
    existing = _frame([
        _row("2026-08-24", provisional=False, r2=0.4),
        _row("2026-08-25", provisional=False, r2=0.4),  # gone under the new convention (break set to missing)
        _row("2026-08-26", provisional=False, r2=0.4),
    ])
    incoming = _frame([
        _row("2026-08-24", provisional=False, r2=0.6),
        _row("2026-08-26", provisional=False, r2=0.6),
    ])
    result = merge_contract(existing, incoming, as_of_advanced=False, rewrite_history=True)
    assert len(result.frame) == 2
    assert [str(d.date()) for d in result.frame["date"]] == ["2026-08-24", "2026-08-26"]


def test_rewrite_history_keeps_rows_outside_the_recomputed_range():
    existing = _frame([
        _row("2026-07-01", provisional=False, r2=0.3),
        _row("2026-08-24", provisional=False, r2=0.4),
    ])
    incoming = _frame([_row("2026-08-24", provisional=False, r2=0.6)])
    result = merge_contract(existing, incoming, as_of_advanced=False, rewrite_history=True)
    assert len(result.frame) == 2
    kept = result.frame.set_index("date").loc["2026-07-01", "r2_full"]
    assert kept == pytest.approx(0.3)


def test_as_of_advance_detection():
    now = {"USDAUD.foreign": "2026-08-26"}
    assert as_of_advanced(now, {"USDAUD.foreign": "2026-08-19"})[0] is True
    assert as_of_advanced(now, {"USDAUD.foreign": "2026-08-26"})[0] is False
    assert as_of_advanced(now, {})[0] is True  # first run


def test_overdue_provisional_turns_status_yellow():
    """Overage provisional is a tripwire; never wait indefinitely for the backfill
    (constraint 3)."""
    old = pd.Timestamp("2026-08-01")
    contract = _frame([_row(old, provisional=True)])
    today = old + pd.Timedelta(days=PROVISIONAL_AGE_LIMIT_DAYS + 5)

    overdue = stale_provisional(contract, today=today)
    assert len(overdue) == 1
    assert overdue[0]["pair"] == "USDAUD"

    status = build_status(contract, "live", {"contract": {}}, today=today)
    assert status["state"] == YELLOW
    assert any("provisional" in r for r in status["reasons"])


def test_recent_provisional_keeps_status_green():
    recent = pd.Timestamp("2026-08-24")
    contract = _frame([_row(recent, provisional=True)])
    status = build_status(
        contract, "live", {"contract": {}}, today=recent + pd.Timedelta(days=3)
    )
    assert status["state"] == GREEN
    assert status["provisional_rows"] == 1
