import shutil
from datetime import timedelta

import pytest

from fxdash.narrative import morning as M, catchup as C, usage_acceptance as U
from fxdash.data.vintages import capture_raw
from test_catchup import inputs, options
from test_morning import moment


def delivered(root, raw, source="scheduled_task", publisher=None):
    p = inputs(root)
    ref = capture_raw(raw, root, started_at=moment()-timedelta(hours=10),
                      clock=lambda: moment()-timedelta(hours=9), source_records=[], mode="live")
    settings = options(p)
    factory = settings["snapshot_factory"]
    def snapshot(path):
        value = factory(path)
        value.manifest = {"input_archive": ref}
        return value
    settings["snapshot_factory"] = snapshot
    if publisher:
        settings["publisher"] = publisher
    C.run(root, root, invocation_source=source, **settings)
    return root / "briefing/catchup/2026-01-08", settings


def observe(root, stamp=None):
    return U.assess(root, clock=lambda: stamp or moment(17, 0))


def test_no_calls_missing_days_weekends_and_shared_locks_are_not_failures(tmp_path):
    M.atomic_json(tmp_path / "briefing/clock/latest.json", {"state": "missed_window"})
    (tmp_path / "briefing/days/2026-01-08").mkdir(parents=True)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = observe(tmp_path, moment(17, 0, "2026-02-09"))
    assert report["state"] == "no_activity" and report["days"] == []
    assert "required_consecutive_weekdays" not in report
    assert {p: p.read_bytes() for p in before} == before


def test_afternoon_automatic_delivery_is_valid_and_future_gaps_do_not_reset(tmp_path, synthetic_raw):
    root, _ = delivered(tmp_path, synthetic_raw)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = observe(tmp_path, moment(17, 0, "2026-02-09"))
    assert report["delivered_days"] == report["automated_days"] == 1
    assert report["observed_days"] == 1 and report["state"] == "delivered"
    assert report["days"][0]["context"]["state"] == "numbers_only"
    assert report["days"][0]["public_pages_delivery"] == "not_checked"
    assert {p: p.read_bytes() for p in before} == before


def test_later_scheduled_check_does_not_turn_manual_generation_into_automatic(tmp_path, synthetic_raw):
    _, settings = delivered(tmp_path, synthetic_raw, source="manual")
    settings["clock"] = lambda: moment(16, 15)
    assert C.run(tmp_path, tmp_path, invocation_source="scheduled_task", **settings)["state"] == "already_available"
    report = observe(tmp_path)
    assert report["delivered_days"] == 1 and report["automated_days"] == 0


def test_old_archives_are_delivery_evidence_without_fabricated_provenance(tmp_path, synthetic_raw):
    root, _ = delivered(tmp_path, synthetic_raw)
    legacy = tmp_path / "legacy/briefing/catchup/2026-01-08"
    for name in ("packet.json", "edition.json", "publish.json", "status.json"):
        M.atomic_json(legacy / name, M.read_json(root / name))
    shutil.copytree(tmp_path / "input_archive", tmp_path / "legacy/input_archive")
    row = observe(tmp_path / "legacy")["days"][0]
    assert row["passed"] and row["automation"] == "not_proven"
    assert not row["record_issues"]


@pytest.mark.parametrize("file,field,value,check", [
    ("publish.json", "edition_hash", "wrong", "matching_push"),
    ("publish.json", "finished_at", "2026-01-08T15:00:00+00:00", "ordered_publication"),
    ("edition.json", "generated_at", "2026-01-08T19:00:00+00:00", "actual_edition_date"),
    ("packet.json", "as_of", "2026-01-01", "current_complete_inputs"),
    ("packet.json", "input_archive", {}, "quant_input_archive_verified"),
])
def test_delivery_integrity_is_not_relaxed(tmp_path, synthetic_raw, file, field, value, check):
    root, _ = delivered(tmp_path, synthetic_raw)
    body = M.read_json(root / file)
    body[field] = value
    M.atomic_json(root / file, body)
    row = observe(tmp_path)["days"][0]
    assert not row["passed"] and not row["checks"][check] and row["state"] == "attention"


def test_generation_failure_is_separate_from_valid_numeric_delivery(tmp_path, synthetic_raw):
    root, _ = delivered(tmp_path, synthetic_raw)
    body = M.read_json(root / "edition.json")
    body["warnings"].append("generation_requests_failed")
    M.atomic_json(root / "edition.json", body)
    push = M.read_json(root / "publish.json")
    push["edition_hash"] = M.digest(body)
    M.atomic_json(root / "publish.json", push)
    report = observe(tmp_path)
    assert report["delivered_days"] == 1 and report["generation_issue_days"] == 1
    assert report["days"][0]["context"]["state"] == "unavailable"


@pytest.mark.parametrize("state,expected", [("waiting_for_attribution", "waiting"),
    ("waiting_for_news", "waiting"), ("catchup_failed", "attention"), ("preparing", "completion_unconfirmed")])
def test_actual_waits_failures_and_uncertain_completion_are_distinct(tmp_path, state, expected):
    M.atomic_json(tmp_path / "briefing/catchup/2026-01-08/status.json", {"state": state})
    row = observe(tmp_path)["days"][0]
    assert row["state"] == expected and not row["passed"]


def test_bad_or_unfinished_invocations_do_not_silently_prove_success(tmp_path, synthetic_raw):
    root, _ = delivered(tmp_path, synthetic_raw)
    finish = next((root / "invocations").glob("*.finish.json"))
    row = M.read_json(finish)
    row["source"] = "manual"
    M.atomic_json(finish, row)
    result = observe(tmp_path)["days"][0]
    assert result["record_issues"] and not result["passed"]
    assert result["automation"] == "not_proven"


def test_failed_push_is_visible_with_no_fresh_generation_on_retry(tmp_path, synthetic_raw):
    root, settings = delivered(tmp_path, synthetic_raw,
                               publisher=lambda _: (_ for _ in ()).throw(RuntimeError("private")))
    assert observe(tmp_path)["state"] == "attention"
    original = (root / "edition.json").read_bytes()
    settings.update(publisher=lambda _: None, collector=lambda *a, **k: pytest.fail("No new fetch"),
                    clock=lambda: moment(16, 15))
    C.run(tmp_path, tmp_path, invocation_source="scheduled_task", **settings)
    row = observe(tmp_path)["days"][0]
    assert row["passed"] and "publish_failed" in row["failures"]
    assert row["automation"] == "confirmed"
    assert (root / "edition.json").read_bytes() == original


@pytest.mark.parametrize("error,expected", [("GenerationError", True), ("RuntimeError", True),
                                           ("numeric_assertion", False), ("no_sources", False)])
def test_legacy_draft_errors_are_read_without_changing_the_edition(tmp_path, synthetic_raw, error, expected):
    root, _ = delivered(tmp_path, synthetic_raw)
    original = {p: p.read_bytes() for p in (root / "edition.json", root / "publish.json")}
    draft = M.read_json(root / "draft.json")
    draft["notes"] = [{"attempted": True, "published": False, "errors": [error]}]
    M.atomic_json(root / "draft.json", draft)
    row = observe(tmp_path)["days"][0]
    assert row["passed"] and row["context"]["generation_failed"] is expected
    assert row["context"]["draft_evidence"] == "verified"
    assert all(p.read_bytes() == before for p, before in original.items())
    draft["packet_hash"] = "unrelated"
    M.atomic_json(root / "draft.json", draft)
    quality = observe(tmp_path)["days"][0]["context"]
    assert not quality["generation_failed"] and quality["draft_evidence"] == "unreadable_or_mismatched"


@pytest.mark.parametrize("bad", [{}, {"state": []}, {"state": "invented"}])
def test_corrupt_status_remains_visible_without_crashing_the_audit(tmp_path, bad):
    M.atomic_json(tmp_path / "briefing/catchup/2026-01-08/status.json", bad)
    row = observe(tmp_path)["days"][0]
    assert row["state"] == "attention" and row["record_issues"]


@pytest.mark.parametrize("which,field", [("start", "source"), ("finish", "state")])
def test_malformed_ledger_values_do_not_crash_or_pass(tmp_path, synthetic_raw, which, field):
    root, _ = delivered(tmp_path, synthetic_raw)
    path = next((root / "invocations").glob(f"*.{which}.json"))
    record = M.read_json(path)
    record[field] = []
    M.atomic_json(path, record)
    row = observe(tmp_path)["days"][0]
    assert not row["passed"] and row["record_issues"]


def test_failed_morning_preparation_is_not_hidden_as_inactivity(tmp_path):
    M.atomic_json(tmp_path / "briefing/days/2026-01-08/prepare.json", {"state": "prepare_failed"})
    M.atomic_json(tmp_path / "briefing/days/2026-01-08/prepare.claim", {"started_at": moment().isoformat()})
    row = observe(tmp_path)["days"][0]
    assert row["state"] == "attention" and "prepare_failed" in row["failures"]
