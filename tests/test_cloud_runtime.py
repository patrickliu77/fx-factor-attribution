from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import shutil
import threading

import pytest

from fxdash.cloud import state as S, journal as J, pipeline as P, delivery as D
from fxdash.narrative import morning as M
from test_subscriptions import setup
from test_morning import moment


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Cloud runtime tests must not call any real provider")
    monkeypatch.setattr("requests.request", fail)
    monkeypatch.setattr("requests.get", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)


def make_store(tmp_path):
    store = S.SQLiteStore(tmp_path / "private.sqlite3", create=True)
    J.Journal.initialize(store)
    return store


def restart(store, **kwargs):
    kwargs.setdefault("clock", lambda: moment(17, 2))
    return J.Journal(S.SQLiteStore(store.path), **kwargs)


def digest(body=b"input"):
    return hashlib.sha256(body).hexdigest()


def claim(store, key="recap/2026-01-08/edition"):
    return json.loads(store.read(J.KEY).body)["claims"][key]


def test_simulator_requires_explicit_creation_and_never_reinitializes(tmp_path):
    path = tmp_path / "private.sqlite3"
    with pytest.raises(S.StateError):
        S.SQLiteStore(path)
    assert not path.exists()
    store = S.SQLiteStore(path, create=True)
    with pytest.raises(S.StateError, match="journal_missing"):
        with J.Journal(store).session():
            pytest.fail("missing journal")
    J.Journal.initialize(store)
    with pytest.raises(S.Conflict):
        J.Journal.initialize(store)
    with pytest.raises(S.StateError):
        S.SQLiteStore(path, create=True)
    assert J.Journal(store)._read()[1]["claims"] == {}


@pytest.mark.parametrize("key", ["../a", "/a", "a//b", "a/../b", "A", "https://x", "a\\b", ""])
def test_simulator_keys_are_restricted(tmp_path, key):
    store = make_store(tmp_path)
    with pytest.raises(S.StateError, match="invalid_state_key"):
        store.read(key)
    with pytest.raises(S.StateError, match="invalid_state_key"):
        store.compare_and_swap(key, None, b"body")


def test_atomic_compare_and_swap_across_independent_connections(tmp_path):
    store = make_store(tmp_path)
    first = store.compare_and_swap("test/value", None, b"old")
    barrier = threading.Barrier(2)

    def replace_once(body):
        other = S.SQLiteStore(store.path)
        barrier.wait(timeout=10)
        try:
            other.compare_and_swap("test/value", first.version, body)
            return "ok"
        except S.Conflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(replace_once, [b"a", b"b"]))
    assert sorted(results) == ["conflict", "ok"]
    assert store.read("test/value").body in {b"a", b"b"}


def test_artifacts_are_immutable_and_corruption_does_not_trigger_regeneration(tmp_path):
    store = make_store(tmp_path)
    sha = S.save_artifact(store, b"original")
    assert S.save_artifact(store, b"original") == sha
    assert S.read_artifact(store, sha) == b"original"
    row = store.read("artifacts/" + sha)
    store.compare_and_swap("artifacts/" + sha, row.version, b"corrupt")
    with pytest.raises(S.StateError, match="artifact_corrupt"):
        S.save_artifact(store, b"original")
    with pytest.raises(S.StateError, match="artifact_missing_or_corrupt"):
        S.read_artifact(store, sha)


def test_claim_precedes_callback_and_restart_returns_saved_receipt(tmp_path):
    store = make_store(tmp_path)
    expected = {"artifact_sha256": digest(b"recap")}

    def action():
        assert claim(store)["state"] == "pending"
        return expected

    with restart(store).session() as journal:
        assert journal.run_once("recap", "2026-01-08", "edition", digest(), action) == expected
    with restart(store).session() as journal:
        assert journal.run_once("recap", "2026-01-08", "edition", digest(),
                                lambda: pytest.fail("must not repeat")) == expected
        with pytest.raises(S.StateError, match="operation_input_changed"):
            journal.run_once("recap", "2026-01-08", "edition", digest(b"changed"), action)


def test_only_one_owner_and_no_automatic_clock_based_takeover(tmp_path):
    store = make_store(tmp_path)
    with restart(store).session() as first:
        with pytest.raises(S.StateError, match="owner_busy"):
            with restart(store, clock=lambda: moment(17, 0) + timedelta(days=10)).session():
                pytest.fail("a live owner cannot be stolen")
        assert first._owned()[1]["owner"] == first.owner
    with restart(store).session():
        pass


def test_abandoned_owner_requires_operator_reconciliation(tmp_path):
    store = make_store(tmp_path)
    row = store.read(J.KEY)
    value = json.loads(row.body)
    value["owner"] = "a" * 32
    store.compare_and_swap(J.KEY, row.version, J.encode(value))
    with pytest.raises(S.StateError, match="owner_busy"):
        with restart(store).session():
            pytest.fail("abandoned lock must not be silently deleted")


@pytest.mark.parametrize("phase", ["claim", "completion"])
def test_storage_write_failure_blocks_or_preserves_uncertain_action(tmp_path, phase):
    store = make_store(tmp_path)
    calls = []

    class FailingStore:
        def read(self, key):
            return store.read(key)

        def compare_and_swap(self, key, expected, body):
            value = json.loads(body)
            row = value.get("claims", {}).get("recap/2026-01-08/edition", {})
            target = "pending" if phase == "claim" else "succeeded"
            if row.get("state") == target and value.get("owner"):
                raise S.StateError("state_write_failed")
            return store.compare_and_swap(key, expected, body)

    with pytest.raises(S.StateError):
        with J.Journal(FailingStore()).session() as journal:
            journal.run_once("recap", "2026-01-08", "edition", digest(),
                             lambda: calls.append(1) or {"artifact_sha256": digest(b"result")})
    assert len(calls) == (0 if phase == "claim" else 1)
    if phase == "completion":
        with restart(store).session() as journal:
            with pytest.raises(S.StateError, match="operation_needs_review"):
                journal.run_once("recap", "2026-01-08", "edition", digest(), lambda: pytest.fail("uncertain"))


@pytest.mark.parametrize("error", [TimeoutError, KeyboardInterrupt])
def test_interrupted_callback_never_repeats_or_persists_exception_text(tmp_path, error):
    store = make_store(tmp_path)

    def fail():
        raise error("private provider response with secret")

    with pytest.raises((S.StateError, KeyboardInterrupt)):
        with restart(store).session() as journal:
            journal.run_once("recap", "2026-01-08", "edition", digest(), fail)
    with restart(store).session() as journal:
        with pytest.raises(S.StateError, match="operation_needs_review"):
            journal.run_once("recap", "2026-01-08", "edition", digest(), lambda: pytest.fail("repeat"))
    assert b"private provider" not in store.read(J.KEY).body
    assert claim(store)["state"] == ("pending" if error == KeyboardInterrupt else "review_required")


@pytest.mark.parametrize("receipt", [{"artifact_sha256": "bad"}, {"campaign_id": True},
                                      {"error": "secret"}, {"submitted": True}, None])
def test_operation_receipts_are_strict_and_do_not_store_provider_bodies(tmp_path, receipt):
    store = make_store(tmp_path)
    with restart(store).session() as journal:
        with pytest.raises(S.StateError, match="operation_needs_review"):
            journal.run_once("recap", "2026-01-08", "edition", digest(), lambda: receipt)
    assert b"secret" not in store.read(J.KEY).body


def test_corrupt_journal_is_not_treated_as_an_empty_history(tmp_path):
    store = make_store(tmp_path)
    old = store.read(J.KEY)
    store.compare_and_swap(J.KEY, old.version, b"{}")
    with pytest.raises(S.StateError, match="journal_missing_or_corrupt"):
        with restart(store).session():
            pytest.fail("do not reset the journal")


def test_actual_email_sender_uses_durable_receipts_across_fresh_runners(tmp_path):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "seed")
    calls = []

    class Provider:
        def create(self, payload):
            calls.append(("create", payload["name"]))
            return len(calls)

        def send(self, identity):
            # The send claim is already visible through a second store connection.
            rows = json.loads(S.SQLiteStore(store.path).read(J.KEY).body)["claims"]
            assert any(k.startswith("email-send/") and r["state"] == "pending" for k, r in rows.items())
            calls.append(("send", identity))

    for runner in ("worker1", "worker2"):
        root = tmp_path / runner
        shutil.copytree(tmp_path / "seed", root)
        with restart(store).session() as journal:
            result = D.deliver_guarded(root, brief, verified, journal,
                                       provider_factory=Provider, clock=lambda: moment(17, 2))
        assert result["state"] == "submitted"
    assert len(calls) == 4  # One create and one send for each language, across both hosts.
    serialized = store.read(J.KEY).body
    assert b"htmlContent" not in serialized and b"Example sender" not in serialized


@pytest.mark.parametrize("phase", ["create", "send"])
def test_ambiguous_email_on_one_host_is_not_retried_on_another(tmp_path, phase):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "seed")
    calls = []

    class Provider:
        def create(self, payload):
            calls.append("create")
            if phase == "create":
                raise TimeoutError("secret provider reply")
            return len(calls)

        def send(self, identity):
            calls.append("send")
            raise TimeoutError("secret provider reply")

    for runner in ("worker1", "worker2"):
        root = tmp_path / runner
        shutil.copytree(tmp_path / "seed", root)
        with restart(store).session() as journal:
            result = D.deliver_guarded(root, brief, verified, journal,
                                       provider_factory=Provider, clock=lambda: moment(17, 2))
        assert result["state"] == "attention_required"
    assert len(calls) == (2 if phase == "create" else 4)
    assert b"secret" not in store.read(J.KEY).body


def test_changed_recipient_configuration_does_not_create_another_daily_campaign(tmp_path):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "seed")
    calls = []

    class Provider:
        def create(self, value):
            calls.append(1)
            return len(calls)

        def send(self, identity):
            pass

    for n in range(2):
        root = tmp_path / str(n)
        shutil.copytree(tmp_path / "seed", root)
        if n:
            settings = D.S.config(root)
            settings["lists"] = {"en": 21, "zh": 22}
            M.atomic_json(root / "subscriptions/config.json", settings)
        with restart(store).session() as journal:
            result = D.deliver_guarded(root, brief, verified, journal,
                                       provider_factory=Provider, clock=lambda: moment(17, 2))
        assert result["state"] == ("submitted" if n == 0 else "attention_required")
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["unverified", "stale", "old_day", "disabled"])
def test_existing_email_gates_run_before_any_cloud_claim_or_provider(tmp_path, change):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "worker")
    now = moment(17, 2)
    if change == "unverified":
        verified["state"] = "pending"
    if change == "stale":
        now += timedelta(hours=1)
    if change == "old_day":
        now += timedelta(days=1)
    if change == "disabled":
        M.atomic_json(tmp_path / "worker/subscriptions/config.json", {"enabled": False})
    with restart(store).session() as journal:
        result = D.deliver_guarded(tmp_path / "worker", brief, verified, journal,
                                   provider_factory=lambda: pytest.fail("provider gate"), clock=lambda: now)
    assert result["state"] != "submitted"
    assert J.Journal(store)._read()[1]["claims"] == {}


@pytest.mark.parametrize("delta", [timedelta(minutes=16), timedelta(days=1)])
def test_each_provider_send_rechecks_probe_age_and_delivery_date(tmp_path, delta):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "worker")
    now = [moment(17, 2)]
    calls = []

    class Provider:
        def create(self, value):
            calls.append("create")
            now[0] += delta
            return 7

        def send(self, identity):
            pytest.fail("the gate expired while creating the campaign")

    with restart(store).session() as journal:
        result = D.deliver_guarded(tmp_path / "worker", brief, verified, journal,
                                   provider_factory=Provider, clock=lambda: now[0])
    assert result["state"] == "attention_required" and calls == ["create"]


def ports(calls, *, verify=True):
    def stage(name):
        def execute(context):
            calls.append(name)
            return (name + ":" + ",".join(context)).encode()
        return execute
    def email(context, journal):
        calls.append("email")
        for n, lang in enumerate(("en", "zh"), start=1):
            journal.run_once("email-create", "2026-01-08", lang, digest(lang.encode()), lambda: {"campaign_id": n})
            journal.run_once("email-send", "2026-01-08", lang, digest(lang.encode()), lambda: {"submitted": True})
        return {"state": "submitted"}
    return P.Ports({name: stage(name) for name in P.STAGES}, publish=stage("publish"),
                   verify=lambda context: calls.append("verify") or verify,
                   email=email)


def test_shadow_prepares_all_stages_without_any_outward_delivery(tmp_path):
    store = make_store(tmp_path)
    calls = []
    hooks = ports(calls)
    for _ in range(2):
        result = P.run(restart(store), "2026-01-08", b"seed", hooks, clock=lambda: moment(17, 2))
        assert result["state"] == "shadow_ready" and result["email_sent"] is False
        assert len(result["artifacts"]) == 8
    assert calls == list(P.STAGES)


def test_pending_public_verification_can_retry_without_regeneration_or_republishing(tmp_path):
    store = make_store(tmp_path)
    calls = []
    first = P.run(restart(store), "2026-01-08", b"seed", ports(calls, verify=False),
                  mode="delivery", clock=lambda: moment(17, 2))
    assert first["state"] == "waiting_for_public_verification" and "email" not in calls
    second = P.run(restart(store), "2026-01-08", b"seed", ports(calls),
                   mode="delivery", clock=lambda: moment(17, 3))
    assert second["state"] == "submitted"
    assert calls == list(P.STAGES) + ["publish", "verify", "verify", "email"]


def test_pipeline_and_real_email_adapter_resume_together_without_repeat_send(tmp_path):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "seed")
    calls, sends = [], []

    class Provider:
        def create(self, value):
            sends.append("create")
            return len(sends)

        def send(self, identity):
            sends.append("send")

    for worker in ("one", "two"):
        root = tmp_path / worker
        shutil.copytree(tmp_path / "seed", root)
        hooks = replace(ports(calls), email=lambda context, journal: D.deliver_guarded(
            root, brief, verified, journal, provider_factory=Provider, clock=lambda: moment(17, 2)))
        assert P.run(restart(store), "2026-01-08", b"seed", hooks,
                     mode="delivery", clock=lambda: moment(17, 2))["state"] == "submitted"
    assert len(sends) == 4 and calls.count("publish") == 1 and calls.count("verify") == 2


def test_pipeline_missing_saved_artifact_blocks_without_repaying(tmp_path):
    store = make_store(tmp_path)
    calls = []
    hooks = ports(calls)
    result = P.run(restart(store), "2026-01-08", b"seed", hooks, clock=lambda: moment(17, 2))
    sha = result["artifacts"]["recap"]
    row = store.read("artifacts/" + sha)
    store.compare_and_swap("artifacts/" + sha, row.version, b"corrupt")
    with pytest.raises(S.StateError, match="artifact_missing_or_corrupt"):
        P.run(restart(store), "2026-01-08", b"seed", hooks, clock=lambda: moment(17, 3))
    assert calls == list(P.STAGES)


@pytest.mark.parametrize("stamp", ["2026-01-10T17:00:00+00:00", "2026-01-09T17:00:00+00:00"])
def test_pipeline_never_runs_historical_backlog_or_weekend(tmp_path, stamp):
    store = make_store(tmp_path)
    calls = []
    assert P.run(restart(store), "2026-01-08", b"seed", ports(calls),
                 clock=lambda: datetime.fromisoformat(stamp))["state"] == "outside_run_day"
    assert calls == []


@pytest.mark.parametrize("day,stamp,allowed", [
    ("2026-01-08", "2026-01-08T13:59:00Z", False),
    ("2026-01-08", "2026-01-08T14:00:00Z", True),
    ("2026-07-08", "2026-07-08T12:59:00Z", False),
    ("2026-07-08", "2026-07-08T13:00:00Z", True),
])
def test_delivery_clock_handles_new_york_standard_and_daylight_time(day, stamp, allowed):
    assert P.delivery_day(day, datetime.fromisoformat(stamp)) is allowed


def test_pipeline_does_not_publish_before_nine(tmp_path):
    store = make_store(tmp_path)
    calls = []
    result = P.run(restart(store), "2026-01-08", b"seed", ports(calls), mode="delivery",
                   clock=lambda: moment(13, 55))
    assert result["state"] == "waiting_for_delivery_window" and calls == list(P.STAGES)


def test_day_rollover_during_public_probe_prevents_email(tmp_path):
    store = make_store(tmp_path)
    calls = []
    now = [moment(17, 2)]

    def probe(context):
        now[0] += timedelta(days=1)
        return True

    hooks = replace(ports(calls), verify=probe)
    result = P.run(restart(store), "2026-01-08", b"seed", hooks, mode="delivery", clock=lambda: now[0])
    assert result["state"] == "outside_delivery_day" and "email" not in calls


def test_live_adapters_must_be_explicit_no_environment_can_enable_delivery(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    calls = []
    monkeypatch.setenv("FXDASH_CLOUD_ENABLE", "true")
    hooks = replace(ports(calls), publish=None)
    with pytest.raises(S.StateError, match="invalid_pipeline_ports"):
        P.run(restart(store), "2026-01-08", b"seed", hooks, mode="delivery", clock=lambda: moment(17, 2))
    assert calls == []


def test_local_pre_cloud_submissions_are_imported_before_restarting_from_an_old_seed(tmp_path):
    store = make_store(tmp_path)
    brief, verified = setup(tmp_path / "seed")
    calls = []

    class Provider:
        def create(self, value):
            calls.append("create")
            return len(calls)
        def send(self, identity):
            calls.append("send")

    # Simulate the existing local sender before migration, then transfer its
    # receipt to the journal. The next worker starts from a seed without receipts.
    assert D.S.deliver(tmp_path / "seed", brief, verified, provider_factory=Provider,
                       clock=lambda: moment(17, 2))["state"] == "submitted"
    for worker in ("seed", "fresh"):
        root = tmp_path / worker
        if worker == "fresh":
            shutil.copytree(tmp_path / "seed", root, ignore=shutil.ignore_patterns("deliveries"))
        with restart(store).session() as journal:
            assert D.deliver_guarded(root, brief, verified, journal,
                                     provider_factory=lambda: pytest.fail("already sent locally"),
                                     clock=lambda: moment(17, 2))["state"] == "submitted"
            assert journal.email_submitted("2026-01-08")
    assert len(calls) == 4


@pytest.mark.parametrize("legacy", [{}, {"state": "creating"}, {"state": "submitting", "campaign_id": 9},
                                    {"state": "review_required"}, {"state": "submitted", "campaign_id": True}])
def test_incomplete_legacy_receipts_block_instead_of_authorizing_a_new_send(tmp_path, legacy):
    store = make_store(tmp_path)
    with restart(store).session() as journal:
        journal.remember_local_delivery("2026-01-08", "en", legacy)
    with restart(store).session() as journal:
        with pytest.raises(S.StateError):
            journal.run_once("email-create", "2026-01-08", "en", digest(), lambda: pytest.fail("blocked legacy"))
    assert claim(store, "email-create/2026-01-08/en")["state"] == "review_required"


def test_false_success_from_email_port_cannot_report_pipeline_submission(tmp_path):
    store = make_store(tmp_path)
    hooks = replace(ports([]), email=lambda context, journal: {"state": "submitted"})
    with pytest.raises(S.StateError, match="durable_email_receipts_missing"):
        P.run(restart(store), "2026-01-08", b"seed", hooks, mode="delivery", clock=lambda: moment(17, 2))


def test_public_probe_exception_is_safe_and_never_calls_email(tmp_path):
    store = make_store(tmp_path)
    calls = []
    def fail(context):
        raise RuntimeError("private provider reply")
    result = P.run(restart(store), "2026-01-08", b"seed", replace(ports(calls), verify=fail),
                   mode="delivery", clock=lambda: moment(17, 2))
    assert result["state"] == "public_probe_unavailable" and "email" not in calls
    assert "private provider" not in str(result)


def test_duplicate_json_keys_cannot_reset_saved_ownership(tmp_path):
    store = make_store(tmp_path)
    row = store.read(J.KEY)
    store.compare_and_swap(J.KEY, row.version, b'{"schema":1,"claims":{},"owner":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","owner":null}')
    with pytest.raises(S.StateError, match="journal_missing_or_corrupt"):
        with restart(store).session():
            pytest.fail("duplicate owner must be rejected")
