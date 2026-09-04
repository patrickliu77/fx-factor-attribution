"""Daily overview page (SPEC_phase2 section 5)."""

import json

import pandas as pd
import pytest

from fxdash.config import DEFAULT_WINDOW, PAIRS
from fxdash.report.overview import build_overview, write_overview


def _row(pair, date="2026-08-28", provisional=False, z=0.4, stale=()):
    return {
        "date": pd.Timestamp(date),
        "pair": pair,
        "window": DEFAULT_WINDOW,
        "model": "ols",
        "betas": json.dumps({"d10Y_DIFF": 0.02}),
        "contributions": json.dumps({"d10Y_DIFF": 0.0004}),
        "r2_full": 0.55,
        "r2_exog": 0.21,
        "selected_factors": json.dumps(["d10Y_DIFF"]),
        "residual": 0.0003,
        "residual_z": z,
        "stale_flags": json.dumps(list(stale)),
        "systematic": 0.0006,
        "exogenous": 0.0004,
        "y": 0.0013,
        "lambda": None,
        "provisional": provisional,
        "schema_version": "1.1.0",
    }


@pytest.fixture
def contract():
    return pd.DataFrame([_row(p, z=0.5 + i) for i, p in enumerate(PAIRS)])


@pytest.fixture
def status():
    return {
        "state": "green",
        "mode": "live",
        "generated_at": "2026-08-28 19:40:00",
        "contract_last_date": "2026-08-28",
        "rows": 225225,
        "provisional_rows": 54,
        "overdue_provisional": [],
        "health_findings": [],
        "reasons": [],
        "source_as_of": {"USDAUD.foreign": "2026-08-19"},
        "schema_version": "1.1.0",
        "heartbeat": {
            "state": "green",
            "last_live_success": "2026-08-28 19:40:00",
            "age_hours": 0.3,
            "note": "scheduler healthy",
        },
    }


@pytest.fixture
def manifest():
    return {
        "merge": {"frozen_kept": 225162, "overwritten": 0},
        "provisional_overwrites": [],
        "coverage": {
            p: {"first": "2010-01-06", "last": "2026-08-28", "rows": 4331}
            for p in PAIRS
        },
    }


def test_page_is_self_contained(contract, status, manifest):
    html = build_overview(contract, status, manifest)
    assert html.startswith("<!doctype html>")
    assert "http://" not in html and "https://" not in html
    assert "<style>" in html  # styles inlined, no external files


def test_every_pair_appears_with_its_numbers(contract, status, manifest):
    html = build_overview(contract, status, manifest)
    for pair in PAIRS:
        assert pair in html
        assert f'href="{pair}.html"' in html  # entry links to the six pair pages


def test_status_pill_reflects_state(contract, status, manifest):
    assert 'class="pill ok"' in build_overview(contract, status, manifest)
    assert 'class="pill warn"' in build_overview(
        contract, {**status, "state": "yellow"}, manifest
    )
    assert 'class="pill crit"' in build_overview(
        contract, {**status, "state": "red"}, manifest
    )


def test_no_alerts_says_so_plainly(contract, status, manifest):
    assert "当前无告警" in build_overview(contract, status, manifest)


def test_alerts_are_rendered_with_their_action(contract, status, manifest):
    noisy = {
        **status,
        "state": "yellow",
        "health_findings": [
            {
                "check": "r2_relative_low",
                "pair": "USDNOK",
                "state": "onset",
                "action": "check factor construction, data alignment and differencing first",
            }
        ],
    }
    html = build_overview(contract, noisy, manifest)
    assert "r2_relative_low" in html
    assert "check factor construction" in html


def test_overdue_provisional_is_surfaced(contract, status, manifest):
    overdue = {
        **status,
        "state": "yellow",
        "overdue_provisional": [
            {
                "pair": "USDAUD",
                "oldest_provisional": "2026-07-01",
                "age_days": 58,
                "limit_days": 21,
                "note": "official source may have stopped publishing or changed "
                "its interface; do not wait indefinitely for the backfill",
            }
        ],
    }
    html = build_overview(contract, overdue, manifest)
    assert "provisional 超龄" in html
    assert "58" in html


def test_provisional_pair_is_chipped(contract, status, manifest):
    marked = contract.copy()
    marked.loc[marked["pair"] == "USDAUD", "provisional"] = True
    html = build_overview(marked, status, manifest)
    assert "provisional</span>" in html


def test_residual_ranking_is_sorted_by_magnitude(contract, status, manifest):
    html = build_overview(contract, status, manifest)
    ranking = html.split("残差 z 排行")[1].split("</ul>")[0]
    # read the order off the page, not from PAIRS
    order = sorted(PAIRS, key=ranking.index)
    magnitudes = [
        abs(float(contract.set_index("pair").loc[p, "residual_z"])) for p in order
    ]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert order[0] == "USDMXN"  # the largest z in the constructed data


def test_missing_pair_does_not_break_the_page(status, manifest):
    partial = pd.DataFrame([_row(p) for p in PAIRS[:3]])
    html = build_overview(partial, status, manifest)
    assert "尚无记录" in html
    for pair in PAIRS:
        assert pair in html


def test_each_pair_shows_its_own_latest_day(status, manifest):
    """Holiday calendars differ across the six pairs; last days are naturally
    ragged, so never slice at the global max date."""
    rows = [_row(p, date="2026-08-28") for p in PAIRS[:-1]]
    rows.append(_row(PAIRS[-1], date="2026-08-26"))  # this pair lags two days
    html = build_overview(pd.DataFrame(rows), status, manifest)

    assert "尚无记录" not in html  # all six pairs should show numbers
    for pair in PAIRS:
        assert f'class="pair">{pair}</span>' in html


def test_a_lagging_pair_is_marked_with_its_date(status, manifest):
    rows = [_row(p, date="2026-08-28") for p in PAIRS[:-1]]
    rows.append(_row(PAIRS[-1], date="2026-08-26"))
    html = build_overview(pd.DataFrame(rows), status, manifest)
    assert "2026-08-26</span>" in html  # the lagging date is flagged explicitly


def test_theme_tokens_cover_the_unstamped_default(contract, status, manifest):
    """The unstamped default theme must get every token, or the page renders
    half light, half dark."""
    html = build_overview(contract, status, manifest)
    base = html.split(":root{")[1].split("}")[0]
    for token in ("--ground", "--surface", "--ink", "--accent", "--ok", "--crit"):
        assert token in base


def test_write_overview_lands_next_to_the_pair_pages(contract, status, manifest):
    import fxdash.report.overview as module

    path = write_overview(contract, status, manifest)
    assert path.endswith("index.html")
    assert (module.REPORT_DIR / "index.html").read_text(
        encoding="utf-8").startswith("<!doctype")


def test_direction_line_shows_each_bucket_sign(contract, status, manifest):
    """Bars drawn by absolute value lose the sign; the direction line restores it.

    All same sign, mutual cancellation, and residual opposing are three very
    different days; the pattern itself is diagnostic.
    """
    signed = contract.copy()
    row = signed["pair"] == "USDEUR"
    signed.loc[row, "systematic"] = 0.0006   # +
    signed.loc[row, "exogenous"] = -0.0004   # −
    signed.loc[row, "residual"] = 0.0
    html = build_overview(signed, status, manifest)
    card = html.split('class="pair">USDEUR')[1].split("</article>")[0]
    assert "方向" in card
    assert "系统性+" in card and "外生−" in card and "残差0" in card


def test_frozen_cell_only_appears_in_live_mode(contract, status, manifest):
    """In a --rewrite-history backfill it is 0; showing that would be read as
    history having been fully rewritten."""
    live = build_overview(contract, {**status, "mode": "live"}, manifest)
    assert "冻结保留" in live

    backfill = build_overview(contract, {**status, "mode": "backfill"}, manifest)
    assert "冻结保留" not in backfill


def test_heartbeat_band_reports_last_live_and_age(contract, status, manifest):
    html = build_overview(contract, status, manifest)
    assert "最近一次成功 live" in html
    assert "2026-08-28 19:40:00" in html
    assert "距今" in html
    assert 'class="pulse ok"' in html


def test_stalled_scheduler_is_visible_on_the_page(contract, status, manifest):
    """A stalled scheduler is the most dangerous failure mode of an unattended
    system; the page must say so."""
    stalled = {
        **status,
        "state": "yellow",
        "heartbeat": {
            "state": "yellow",
            "last_live_success": "2026-08-25 19:40:00",
            "age_hours": 72.3,
            "note": "suspected scheduler stall: 72 hours without a successful live run",
        },
    }
    html = build_overview(contract, stalled, manifest)
    assert 'class="pulse warn"' in html
    assert "suspected scheduler stall" in html
    assert "天前" in html  # humanise() output, part of the Chinese report page UI


def test_missing_heartbeat_does_not_break_the_page(contract, status, manifest):
    html = build_overview(contract, {k: v for k, v in status.items()
                                     if k != "heartbeat"}, manifest)
    assert 'class="pulse ok"' in html


def test_header_carries_generation_time_and_schema(contract, status, manifest):
    """Makes it traceable later which build of the outputs was read."""
    html = build_overview(contract, status, manifest)
    head = html.split('class="sub"')[1].split("</p>")[0]
    assert "2026-08-28 19:40:00" in head
    assert "schema" in head and "1.1.0" in head


def test_page_carries_its_own_freshness_check(contract, status, manifest):
    """The page must be able to check its own freshness.

    status.json and this page are written only by successful runs; once a run
    fails, the page freezes on the last green -- exactly the scenario the
    heartbeat guards against, yet cannot guard for itself. This actually
    happened on 2026-08-29: the task was killed by StopOnIdleEnd, the
    heartbeat computed yellow, while the page still showed green.
    """
    html = build_overview(contract, status, manifest)
    assert 'id="fresh"' in html
    assert 'data-generated="2026-08-28 19:40:00"' in html
    assert 'data-warn=' in html and 'data-crit=' in html
    assert 'id="statusPill"' in html
    assert "疑似调度停摆" in html          # display copy inside the script
    assert "<script>" in html


def test_freshness_thresholds_come_from_config(contract, status, manifest):
    from fxdash.config import HEARTBEAT_CRIT_HOURS, HEARTBEAT_WARN_HOURS

    html = build_overview(contract, status, manifest)
    assert f'data-warn="{HEARTBEAT_WARN_HOURS}"' in html
    assert f'data-crit="{HEARTBEAT_CRIT_HOURS}"' in html


def test_freshness_falls_back_to_config_when_heartbeat_absent(contract, status, manifest):
    from fxdash.config import HEARTBEAT_WARN_HOURS

    bare = {k: v for k, v in status.items() if k != "heartbeat"}
    html = build_overview(contract, bare, manifest)
    assert f'data-warn="{HEARTBEAT_WARN_HOURS}"' in html
