from datetime import datetime

import pytest

from fxdash.narrative import morning as M, morning_dispatch as G, morning_health as H
from fxdash import operations as O
from test_morning import moment
from test_morning_acceptance import completed_day


@pytest.mark.parametrize("stamp,phase,state", [
    ("2026-01-08T13:49:00+00:00", "before_window", "idle"),
    ("2026-01-08T15:00:00+00:00", "after_window", "missed_window"),
    ("2026-07-08T14:00:00+00:00", "after_window", "missed_window"),
    ("2026-03-09T14:00:00+00:00", "after_window", "missed_window"),
    ("2026-11-02T15:00:00+00:00", "after_window", "missed_window"),
    ("2026-09-05T17:00:00+00:00", "weekend", "idle"),
])
def test_idle_scheduled_observation_never_fetches_or_fabricates(tmp_path, stamp, phase, state):
    when = datetime.fromisoformat(stamp)
    def forbidden(*args, **kwargs):
        pytest.fail("An idle gate must not prepare, finalize or publish")
    result = G.dispatch(tmp_path, tmp_path, clock=lambda: when, invocation_source="scheduled_task",
                        prepare_fn=forbidden, finalize_fn=forbidden, publisher=forbidden)
    assert result["state"] == state and result["phase"] == phase
    assert H.latest_observation(tmp_path, clock=lambda: when) == result
    assert not (tmp_path / "briefing/days").exists()
    assert len(list((tmp_path / "briefing/clock" / result["date"]).glob("*.json"))) == 1


def test_manual_idle_still_has_no_writes(tmp_path):
    before = set(tmp_path.rglob("*"))
    assert G.dispatch(tmp_path, tmp_path, clock=lambda: moment(20)) == {"state": "idle"}
    assert set(tmp_path.rglob("*")) == before


def test_successful_day_does_not_become_late_failure(tmp_path, synthetic_raw):
    folder = completed_day(tmp_path, synthetic_raw)
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    result = H.observe_idle(tmp_path, moment(15))
    assert result["state"] == "idle" and result["phase"] == "after_window"
    assert result["edition_state"] == "numbers_only" and result["push_state"] == "published"
    assert {p: p.read_bytes() for p in before} == before
    M.atomic_json(folder / "publish.json", {"state": "published", "edition_hash": "incorrect"})
    assert H.observe_idle(tmp_path, moment(15))["state"] == "missed_window"


def test_unreadable_edition_not_marked_delivered(tmp_path):
    root = tmp_path / "briefing/days/2026-01-08"
    M.atomic_json(root / "edition.json", {"state": "ready"})
    result = H.observe_idle(tmp_path, moment(15))
    assert result["state"] == "missed_window" and result["edition_state"] == "archive_unreadable"


def test_append_clock_history_cannot_count_as_formal_acceptance(tmp_path):
    from fxdash.narrative.acceptance import assess_day
    for _ in range(2):
        result = H.observe_idle(tmp_path, moment(15))
        H.save_observation(tmp_path, result)
    assert len(list((tmp_path / "briefing/clock/2026-01-08").glob("*.json"))) == 2
    assert not assess_day(tmp_path, "2026-01-08")["passed"]
    report = O.collect_report(tmp_path, start_date="2026-01-08", clock=lambda: moment(15))
    assert report["clock_observation"]["state"] == "missed_window"
    assert "结果码为 2" in O.render_report(report)


@pytest.mark.parametrize("bad", [
    {"observed_at":"invalid"}, {"observed_at":"2026-01-08T15:00:00"},
    {"observed_at":"2026-01-09T15:00:00+00:00"}, {"source":"manual"},
    {"phase":"weekend"}, {"date":"2026-01-07"},
])
def test_invalid_clock_record_visible_as_unreadable(tmp_path, bad):
    observation = dict(H.observe_idle(tmp_path, moment(15)), **bad)
    M.atomic_json(tmp_path / "briefing/clock/latest.json", observation)
    assert H.latest_observation(tmp_path, clock=lambda: moment(15))["state"] == "unreadable"


def test_check_is_read_only_and_missed_scheduled_invocation_returns_two(tmp_path, monkeypatch, capsys):
    before = set(tmp_path.rglob("*"))
    monkeypatch.setattr(M, "now_utc", lambda: moment(15))
    # dispatch's captured default clock is explicit here to keep production time out of tests.
    dispatch = G.dispatch
    monkeypatch.setattr(G, "dispatch", lambda output, repo, **kw: dispatch(output, repo, clock=lambda: moment(15), **kw))
    assert G.main(["--output-dir", str(tmp_path), "--check"]) == 0
    assert set(tmp_path.rglob("*")) == before
    assert G.main(["--output-dir", str(tmp_path), "--scheduled-task"]) == 2
    assert '"missed_window"' in capsys.readouterr().out


def test_active_gate_cannot_be_saved_as_idle(tmp_path):
    with pytest.raises(ValueError, match="requires_idle"):
        H.observe_idle(tmp_path, moment())
