import copy
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

import pytest

from fxdash.cloud import briefing as B, journal as J, pipeline as P
from fxdash.cloud.state import StateError, read_artifact
from fxdash.narrative import morning as M, catchup as C, briefing_archive as A
from test_cloud_runtime import make_store, restart, claim
from test_morning import packet, moment, note

DAY = "2026-01-08"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def fail(*a, **k):
        pytest.fail("Cloud briefing tests must not call a real provider")
    monkeypatch.setattr("requests.sessions.Session.request", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", fail)
    monkeypatch.setattr("fxdash.web.drivers.collect", fail)


def setup(tmp_path, monkeypatch, *, stamp=None, use_model=False):
    now = [stamp or moment(16, 0)]
    p = packet(tmp_path / "source", now[0])
    p["pairs"] = [dict(p["pairs"][0], pair=pair) for pair in sorted(C.PAIRS)]
    store = make_store(tmp_path)
    journal = restart(store)
    ports = B.BriefingPorts(journal, DAY, tmp_path / "saved", clock=lambda: now[0], use_model=use_model)
    calls = []
    def collect(snapshot, **kw):
        calls.append("collect")
        return copy.deepcopy(p)
    monkeypatch.setattr("fxdash.web.drivers.collect", collect)
    monkeypatch.setattr("fxdash.web.store.Snapshot", lambda root: SimpleNamespace(date_last=p["as_of"], manifest={}))
    return SimpleNamespace(p=p, now=now, store=store, journal=journal, ports=ports, calls=calls)


def run(s):
    # Quant, audio and build are explicitly synthetic in this integration test;
    # news/recap use the real production packet, note and edition implementations.
    stages = {name: lambda context: b"synthetic-stage" for name in P.STAGES}
    stages.update(news=s.ports.news, recap=s.ports.recap)
    result = P.run(s.journal, DAY, b"synthetic-private-seed", P.Ports(stages), clock=lambda: s.now[0])
    return B.decode(read_artifact(s.store, result["artifacts"]["recap"]))


def saved(s, *, mode="catchup", complete=True, frozen=False):
    root = s.ports.output_dir / "briefing" / ("days" if mode == "edition" else "catchup") / DAY
    M.atomic_json(root / "packet.json", s.p)
    M.atomic_json(root / ("prepare.claim" if mode == "edition" else "generation.claim"),
                  {"started_at": s.now[0].isoformat(), "packet_hash": M.digest(s.p)})
    if complete:
        M.atomic_json(root / "draft.json", {"packet_hash": M.digest(s.p), "notes": []})
    if frozen:
        edition = M.compose_edition(s.p, [], moment=s.now[0], mode=mode)
        edition.update(scheduled=mode == "edition", late_publication=mode == "catchup")
        if mode == "catchup":
            edition.update(morning_target=M.cutoff(s.now[0]).isoformat(), target_cutoff=s.p["fetched_at"])
        M.atomic_json(root / "edition.json", edition)
    return root


@pytest.mark.parametrize("stamp,expected", [(moment(13, 55), "edition"), (moment(14, 1), "catchup"),
                                          (moment(16, 0), "catchup")])
def test_real_composition_from_collected_evidence_without_model(tmp_path, monkeypatch, stamp, expected):
    s = setup(tmp_path, monkeypatch, stamp=stamp)
    result = run(s)
    edition = result["edition"]
    assert A.valid_edition(edition, DAY, mode=expected)
    assert edition["scheduled"] is (expected == "edition")
    assert edition["evidence"]["fetched_at"] == s.p["fetched_at"]
    assert edition["packet_hash"] == M.digest(edition["evidence"])
    assert A.public_copy(edition)["recap"]["text"]["en"]
    assert s.calls == ["collect"] and not list(s.ports.output_dir.rglob("edition.json"))
    assert not any(k.startswith(("publish/", "email-")) for k in s.journal._read()[1]["claims"])


def test_paid_model_budget_and_durable_claim_before_constructor(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, use_model=True)
    attempts = []
    class Client:
        def __init__(self, **kwargs):
            assert claim(s.store)["state"] == "pending"
            assert kwargs == {"timeout": 45, "max_requests": 3, "max_attempts": 1}
        def complete(self, system, user, schema):
            attempts.append(json.loads(user))
            return note(s.p["pairs"][0])
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", Client)
    result = run(s)
    assert len(attempts) == 3 and result["edition"]["state"] == "ready"
    assert result["edition"]["notes"]
    # A fresh worker with the same durable journal reuses both artifacts.
    s.journal = restart(s.store)
    s.ports = B.BriefingPorts(s.journal, DAY, s.ports.output_dir, clock=lambda: s.now[0], use_model=True)
    assert run(s) == result and len(attempts) == 3 and s.calls == ["collect"]


@pytest.mark.parametrize("mode", ["edition", "catchup"])
@pytest.mark.parametrize("frozen", [False, True])
def test_existing_generation_is_reused_without_collection_or_model(tmp_path, monkeypatch, mode, frozen):
    s = setup(tmp_path, monkeypatch, stamp=moment(13, 55) if mode == "edition" else None, use_model=True)
    root = saved(s, mode=mode, frozen=frozen)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    s.now[0] = moment(16, 0)
    result = run(s)
    assert not s.calls and result["edition"]["mode"] == mode
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    if frozen:
        assert result["edition"] == json.loads(before["edition.json"])


@pytest.mark.parametrize("damage", ["missing_draft", "bad_hash", "bad_json", "missing_packet", "future_claim", "broken_edition"])
def test_uncertain_legacy_never_generates_again(tmp_path, monkeypatch, damage):
    s = setup(tmp_path, monkeypatch, use_model=True)
    root = saved(s, complete=damage != "missing_draft")
    if damage == "bad_hash":
        M.atomic_json(root / "draft.json", {"packet_hash": "0" * 64, "notes": []})
    if damage == "bad_json":
        (root / "draft.json").write_bytes(b"broken")
    if damage == "missing_packet":
        (root / "packet.json").unlink()
    if damage == "future_claim":
        M.atomic_json(root / "generation.claim", {"packet_hash": M.digest(s.p), "started_at": moment(17, 0).isoformat()})
    if damage == "broken_edition":
        M.atomic_json(root / "edition.json", {})
    with pytest.raises(StateError, match="operation_needs_review"):
        run(s)
    assert not s.calls
    assert claim(s.store, f"news/{DAY}/edition")["state"] == "review_required"


@pytest.mark.parametrize("damage", ["five_pairs", "duplicate_pair", "stale_day", "future_news", "old_source", "all_news_failed"])
def test_invalid_evidence_blocks_before_model(tmp_path, monkeypatch, damage):
    s = setup(tmp_path, monkeypatch, use_model=True)
    if damage == "five_pairs":
        s.p["pairs"].pop()
    if damage == "duplicate_pair":
        s.p["pairs"][-1]["pair"] = s.p["pairs"][0]["pair"]
    if damage == "stale_day":
        s.p["as_of"] = "2026-01-06"
    if damage == "future_news":
        s.p["fetched_at"] = moment(17, 0).isoformat()
    if damage == "old_source":
        next(iter(s.p["slates"].values()))["items"][0]["observed_at"] = moment(13, 0).isoformat()
    if damage == "all_news_failed":
        for slate in s.p["slates"].values():
            slate["error"] = "unavailable"
    with pytest.raises(StateError, match="operation_needs_review"):
        run(s)
    assert f"recap/{DAY}/edition" not in s.journal._read()[1]["claims"]


def test_unclaimed_port_and_changed_context_cannot_call_external_services(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch)
    context = {k: b"test" for k in ("seed", "inputs", "quant")}
    with s.journal.session():
        with pytest.raises(StateError, match="briefing_claim_required"):
            s.ports.news(context)
        fingerprint = M.digest({k: hashlib.sha256(v).hexdigest() for k, v in context.items()})
        with pytest.raises(StateError):
            s.journal.run_once("news", DAY, "edition", fingerprint,
                lambda: s.ports.news(dict(context, seed=b"changed")))
    assert not s.calls


def test_interrupting_model_leaves_claim_that_blocks_replay(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, use_model=True)
    def interrupted(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(s)
    assert claim(s.store)["state"] == "pending"
    with pytest.raises(StateError, match="operation_needs_review"):
        run(s)
    assert s.calls == ["collect"]


def test_late_finish_does_not_relabel_actual_morning_evidence(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, stamp=moment(13, 55), use_model=True)
    class Client:
        def __init__(self, **kwargs):
            pass
        def complete(self, *a):
            s.now[0] = moment(14, 10)
            return note(s.p["pairs"][0])
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", Client)
    edition = run(s)["edition"]
    assert edition["mode"] == "edition" and edition["late_publication"]
    assert edition["news_observed_by"] == moment(13, 55).isoformat(timespec="seconds")


def test_collection_crossing_cutoff_is_catchup(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, stamp=moment(13, 55))
    def collect(*a, **kw):
        s.now[0] = moment(14, 5)
        p = copy.deepcopy(s.p)
        p["fetched_at"] = s.now[0].isoformat()
        return p
    monkeypatch.setattr("fxdash.web.drivers.collect", collect)
    edition = run(s)["edition"]
    assert edition["mode"] == "catchup" and edition["scheduled"] is False
    assert edition["target_cutoff"] == moment(14, 5).isoformat()


def test_day_rollover_stops_subsequent_model_calls(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, use_model=True)
    calls = []
    class Client:
        def __init__(self, **kw):
            pass
        def complete(self, *a):
            calls.append(1)
            s.now[0] = datetime.fromisoformat("2026-01-09T05:00:00+00:00")
            return note(s.p["pairs"][0])
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", Client)
    with pytest.raises(StateError, match="operation_needs_review"):
        run(s)
    assert len(calls) == 1 and claim(s.store)["state"] == "review_required"


def test_no_second_call_after_ownership_check_fails(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, use_model=True)
    calls = []
    original = s.ports._claimed
    def check(stage, context):
        if calls and stage == "recap":
            raise StateError("journal_owner_changed")
        return original(stage, context)
    class Client:
        def __init__(self, **kw):
            pass
        def complete(self, *a):
            calls.append(1)
            return note(s.p["pairs"][0])
    monkeypatch.setattr(s.ports, "_claimed", check)
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", Client)
    run(s)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'x' * (B.MAX_PACKET + 1)],
                         ids=["array", "duplicate-key", "non-finite", "oversized"])
def test_private_artifact_parser_rejects_invalid_data(body):
    with pytest.raises(StateError, match="invalid_briefing_artifact"):
        B.decode(body)


@pytest.mark.parametrize("stamp,expected", [
    ("2026-07-08T12:55:00+00:00", "edition"), ("2026-07-08T13:01:00+00:00", "catchup"),
    ("2026-01-08T13:55:00+00:00", "edition"), ("2026-01-08T14:01:00+00:00", "catchup"),
])
def test_packet_boundary_tracks_new_york_dst(tmp_path, stamp, expected):
    when = datetime.fromisoformat(stamp)
    p = packet(tmp_path, when)
    p["as_of"] = M.previous_session(when.date())
    p["pairs"] = [dict(p["pairs"][0], pair=pair, date=p["as_of"]) for pair in C.PAIRS]
    assert B.packet_mode(p, when, when.date().isoformat()) == expected
