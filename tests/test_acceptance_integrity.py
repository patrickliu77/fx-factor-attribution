import pytest

from fxdash.narrative import acceptance as A, morning as M
from test_morning_acceptance import completed_day


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(finished_at=None),
    lambda r: r.update(finished_at="2026-01-08T13:00:00+00:00"),
    lambda r: r.update(action="prepare"),
    lambda r: r.update(date="2026-01-09"),
    lambda r: r.update(run_id=[]),
])
def test_malformed_finish_cannot_pass_or_crash(tmp_path, synthetic_raw, mutation):
    folder = completed_day(tmp_path, synthetic_raw)
    path = next(p for p in (folder / "dispatch").glob("*.finish.json") if M.read_json(p)["action"] == "publish")
    value = M.read_json(path)
    mutation(value)
    M.atomic_json(path, value)
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["passed"]
    assert result["record_issues"]


def test_unreadable_and_orphan_records_are_visible(tmp_path, synthetic_raw):
    folder = completed_day(tmp_path, synthetic_raw)
    (folder / "dispatch" / "broken.start.json").write_text("broken", encoding="utf-8")
    M.atomic_json(folder / "dispatch" / "orphan.finish.json", {"run_id": "orphan", "state": "published"})
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["passed"]
    assert len(result["record_issues"]) == 2


@pytest.mark.parametrize("field,value", [("pairs", 7), ("slates", []), ("pairs", [{"pair": []}])])
def test_malformed_packet_shape_reports_failure(tmp_path, synthetic_raw, field, value):
    folder = completed_day(tmp_path, synthetic_raw)
    packet = M.read_json(folder / "packet.json")
    packet[field] = value
    M.atomic_json(folder / "packet.json", packet)
    assert not A.assess_day(tmp_path, "2026-01-08")["passed"]


@pytest.mark.parametrize("action,finished,check", [
    ("prepare", "2026-01-08T14:01:00+00:00", "scheduled_preparation"),
    ("publish", "2026-01-08T14:06:00+00:00", "scheduled_publication"),
])
def test_late_completion_does_not_pass_from_start_time_alone(tmp_path, synthetic_raw, action, finished, check):
    folder = completed_day(tmp_path, synthetic_raw)
    path = next(p for p in (folder / "dispatch").glob("*.finish.json") if M.read_json(p)["action"] == action)
    value = M.read_json(path)
    value["finished_at"] = finished
    M.atomic_json(path, value)
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["passed"] and not result["checks"][check]
