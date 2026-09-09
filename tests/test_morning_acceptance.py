from datetime import datetime, timedelta

from fxdash.narrative import acceptance as A, morning as M, morning_dispatch as G
from fxdash.data.vintages import capture_raw
from test_morning import packet, moment


def completed_day(root, synthetic_raw, source="scheduled_task"):
    day = "2026-01-08"
    p = packet(root)
    p["pairs"] = [dict(p["pairs"][0], pair=pair) for pair in
                  ("USDEUR", "USDJPY", "USDCAD", "USDNOK", "USDAUD", "USDMXN")]
    p["input_archive"] = capture_raw(synthetic_raw, root, started_at=moment()-timedelta(hours=10),
                                    clock=lambda: moment()-timedelta(hours=9), source_records=[], mode="live")
    folder = root / "briefing" / "days" / day

    def prepare(*args, **kwargs):
        M.atomic_json(folder / "packet.json", p)
        return {"state": "prepared", "date": day}

    G.dispatch(root, root, clock=lambda: moment(), prepare_fn=prepare, invocation_source=source)
    G.dispatch(root, root, clock=lambda: moment(14, 0), publisher=lambda repo: None, invocation_source=source)
    return folder


def test_real_artifact_chain_and_degraded_text_counted_separately(tmp_path, synthetic_raw):
    completed_day(tmp_path, synthetic_raw)
    result = A.assess_day(tmp_path, "2026-01-08")
    assert result["passed"], result
    assert not result["context_included"]
    assert result["public_pages_delivery"] == "not_checked"


def test_manual_editions_do_not_pass_scheduled_acceptance(tmp_path, synthetic_raw):
    completed_day(tmp_path, synthetic_raw, source="manual")
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["passed"]
    assert not result["checks"]["scheduled_preparation"]


def test_receipt_or_packet_tampering_fails(tmp_path, synthetic_raw):
    folder = completed_day(tmp_path, synthetic_raw)
    receipt = M.read_json(folder / "publish.json")
    receipt["edition_hash"] = "wrong"
    M.atomic_json(folder / "publish.json", receipt)
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["checks"]["matching_push"]
    payload = M.read_json(folder / "packet.json")
    payload["fetched_at"] = moment(14, 1).isoformat()
    M.atomic_json(folder / "packet.json", payload)
    result = A.assess_day(tmp_path, "2026-01-08")
    assert not result["checks"]["eligible_pre_cutoff_inputs"]
    assert not result["checks"]["packet_hash_matches"]


def test_prospective_period_does_not_fabricate_missing_days(tmp_path):
    before = set(tmp_path.rglob("*"))
    result = A.assess(tmp_path, start_date="2026-09-08", clock=lambda: datetime.fromisoformat("2026-09-07T20:00:00+00:00"))
    assert result["state"] == "collecting" and result["days"] == []
    assert set(tmp_path.rglob("*")) == before
    later = A.assess(tmp_path, start_date="2026-09-08", clock=lambda: datetime.fromisoformat("2026-09-14T20:00:00+00:00"))
    assert len(later["days"]) == 5
    assert later["consecutive_passes"] == 0


def test_inflight_before_deadline_is_not_counted(tmp_path):
    result = A.assess(tmp_path, start_date="2026-01-08", clock=lambda: moment(14, 4))
    assert result["days"] == []


def test_interrupted_dispatch_leaves_durable_attempt(tmp_path):
    def fail(*args, **kwargs):
        raise KeyboardInterrupt()
    import pytest
    with pytest.raises(KeyboardInterrupt):
        G.dispatch(tmp_path, tmp_path, clock=lambda: moment(), prepare_fn=fail)
    files = list((tmp_path / "briefing/days/2026-01-08/dispatch").glob("*.json"))
    assert len(files) == 2
    assert {M.read_json(f)["state"] for f in files} == {"started", "exception"}
