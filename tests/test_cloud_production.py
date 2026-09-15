"""Offline production wiring: real files, composers, builder, Git and sender.

The quant subprocess boundary and all provider transports are injected. Separate
engine tests cover regressions; this suite does not claim a live data rehearsal.
"""
import copy
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest

from fxdash import config
from fxdash.cloud import audio, workspace as W, runtime as R, ports as F, reconcile as X
from fxdash.cloud import snapshot as S, journal as J, pipeline as P, worker
from fxdash.cloud.briefing import encode, decode
from fxdash.cloud.state import SQLiteStore, StateError, Conflict, read_artifact, save_artifact
from fxdash.narrative import morning as M, audio_briefing as A, audio_script
from fxdash.web import build as B
from test_web import _write_fixture, _write_cache, _row, DATES, EMPTY_RSS
from test_morning import moment, packet, note
from test_audio_briefing import saved, prepare
from test_subscriptions import configure
from test_cloud_publish import LocalRemote, TOKEN, candidate  # noqa: F401
from test_cloud_snapshot import seed as minimal_seed

DAY = "2026-01-08"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*a, **kw):
        pytest.fail("Production wiring tests must not call real providers")
    monkeypatch.setattr("requests.sessions.Session.request", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", fail)
    monkeypatch.setattr("fxdash.narrative.azure_speech.render", fail)
    monkeypatch.setattr("fxdash.web.headlines._fetch", lambda q: EMPTY_RSS)
    monkeypatch.setattr("fxdash.web.market._fetch_dxy", lambda: None)
    monkeypatch.setattr(S, "known_secrets", lambda: ())
    monkeypatch.setattr("fxdash.cloud.publish.known_secrets", lambda: ())


def seed(root):
    out = root / "outputs"
    _write_fixture(out)
    rows = [dict(_row(day, pair, i, model=model, provisional=i == len(DATES)), window=window,
                 schema_version=config.CONTRACT_SCHEMA_VERSION)
            for i, day in enumerate(DATES, 1) for pair in config.PAIRS
            for window in config.WINDOWS for model in config.MODELS]
    frame = pd.DataFrame(rows)
    for year, block in frame.groupby(frame.date.dt.year):
        block.to_parquet(out / f"contract/year={year}/part.parquet", index=False)
    manifest = M.read_json(out / "run_manifest.json")
    manifest["model_revision"] = config.MODEL_REVISION
    M.atomic_json(out / "run_manifest.json", manifest)
    M.atomic_json(out / "source_as_of.json", {p + ".fx": DATES[-1] for p in config.PAIRS})
    M.atomic_json(out / "alignment/profile.json", {"frozen": True, "entries": [
        {"pair": p, "factor_class": g, "frozen_offset": o, "chosen_offset": o}
        for p, groups in config.OFFSETS.items() for g, o in groups.items()]})
    path = root / "data/user/fred_BAMLH0A0HYM2.csv"
    path.parent.mkdir(parents=True)
    path.write_text("DATE,VALUE\n2026-01-07,4.0\n")
    _write_cache(root / "data/cache")
    configure(out)
    assert R.preflight.inspect(root)["state"] == "ready_for_shadow"
    return W.bundle(root)


def artifact(tmp_path):
    _, edition, _ = saved(tmp_path)
    return encode({"schema": "cloud-briefing-v1", "edition": edition})


def renderer(script, target, lang, *, script_version):
    assert script_version == "audio-v5" and script.read_text(encoding="utf-8")
    target.write_bytes(b"ID3" + b"x" * 4000)
    return {"duration_seconds": 60, "engine": "azure-neural-speech", "voice": "Test " + lang}


def harness(tmp_path, monkeypatch):
    now = [moment(17, 2)]
    data = seed(tmp_path / "seed")
    store = SQLiteStore(tmp_path / "state.sqlite3", create=True)
    R.bootstrap(store, data, tmp_path)
    p = packet(tmp_path / "evidence", now[0])
    p["pairs"] = [dict(p["pairs"][0], pair=pair) for pair in config.PAIRS]
    calls, public, available = [], {}, [True]
    def collect(snapshot, **kwargs):
        assert len(snapshot.combos) == 54 and snapshot.output_dir != config.OUTPUT_DIR
        calls.append("news")
        return copy.deepcopy(p)
    monkeypatch.setattr("fxdash.web.drivers.collect", collect)
    class Client:
        def __init__(self, **kw):
            assert kw == {"timeout": 45, "max_requests": 3, "max_attempts": 1}
        def complete(self, *a):
            calls.append("model")
            return note(p["pairs"][0])
    monkeypatch.setattr("fxdash.narrative.client.GeminiClient", Client)
    def render(*args, **kw):
        calls.append("audio-" + args[2])
        return renderer(*args, **kw)
    def execute(root, stage, **kw):
        calls.append(stage)
        assert (root / W.MARKER).is_file()
        assert root != config.REPO_ROOT and root / "outputs" != config.OUTPUT_DIR
        if stage == "quant":
            # Deliberately synthetic boundary: existing numerical engine suite
            # covers the actual regressions. No live market downloads here.
            M.atomic_json(root / "outputs/heartbeat.json", {"state": "synthetic", "n": calls.count("quant")})
        else:
            B.build(root / "site", output_dir=root / "outputs", cache_dir=root / "data/cache", now=now[0])
            public.update({f.relative_to(root / "site").as_posix(): f.read_bytes()
                           for f in (root / "site").rglob("*") if f.is_file()})
    def fetch(url, limit):
        calls.append("probe")
        if not available[0]:
            raise TimeoutError("private remote message")
        return public[url]
    class Provider:
        def create(self, value):
            assert J.Journal(store)._read()[1]["owner"] is not None
            calls.append("create")
            return len(calls)
        def send(self, identity):
            calls.append("send")
    remote = LocalRemote(tmp_path)
    def factory(journal, day, parent, **kw):
        return F.ProductionPorts(journal, day, parent, **kw, worker=execute, renderer=render,
            provider_factory=Provider, fetcher=fetch, git_runner=remote, publish_token=lambda: TOKEN)
    return SimpleNamespace(store=store, data=data, calls=calls, public=public, available=available,
        now=now, factory=factory, remote=remote)


def go(s, tmp_path, **kw):
    # Independent connection/controller every invocation, as on a new runner.
    return R.run_day(SQLiteStore(s.store.path), tmp_path, clock=lambda: s.now[0],
                     run_id=123, run_attempt=1, port_factory=s.factory, **kw)


def controller(tmp_path, monkeypatch):
    # Policy/recovery tests do not need to rebuild the full dashboard. Preflight
    # and real production wiring are tested separately with all 54 combinations.
    minimal_seed(tmp_path / "seed")
    data = W.bundle(tmp_path / "seed")
    monkeypatch.setattr(R.preflight, "inspect", lambda root: {"state": "ready_for_shadow"})
    store = SQLiteStore(tmp_path / "state.sqlite3", create=True)
    R.bootstrap(store, data, tmp_path)
    calls, now = [], [moment(17, 2)]
    def stage(name):
        def action(context):
            calls.append(name)
            return data
        return action
    def factory(*a, **kw):
        return SimpleNamespace(ports=lambda: P.Ports({s: stage(s) for s in P.STAGES}))
    return SimpleNamespace(store=store, data=data, calls=calls, now=now, factory=factory)


def test_complete_shadow_real_builder_without_paid_or_outward_effects(tmp_path, monkeypatch):
    s = harness(tmp_path, monkeypatch)
    head = s.remote.head()
    for _ in range(2):
        assert go(s, tmp_path)["state"] == "shadow_ready"
    # The builder also collects its independent, unpaid current-news board.
    assert s.calls == ["quant", "news", "build", "news"]
    assert s.remote.head() == head and R.health(s.store, clock=lambda: s.now[0])["state"] == "shadow_only"
    _, state = R.read_head(s.store)
    assert state["latest_seed"] != hashlib.sha256(s.data).hexdigest()
    assert len(J.Journal(s.store)._read()[1]["claims"]) == 7
    assert not list(tmp_path.glob("fxc-*")) and not list(tmp_path.glob("fxpub-*"))


def test_end_to_end_mocked_delivery_resume_preserves_model_audio_and_send_claims(tmp_path, monkeypatch):
    s = harness(tmp_path, monkeypatch)
    R.authorize_delivery(s.store, s.data, tmp_path, local_sender_stopped=True, clock=lambda: s.now[0])
    s.available[0] = False
    assert go(s, tmp_path, mode="delivery", model=True, speech=True)["state"] == "waiting_for_public_verification"
    assert "send" not in s.calls and s.calls.count("model") == 3
    reviewed = copy.deepcopy(s.public)
    s.available[0] = True
    s.now[0] += timedelta(minutes=2)
    for _ in range(2):
        assert go(s, tmp_path, mode="delivery", model=True, speech=True)["state"] == "submitted"
    assert s.calls.count("audio-en") == s.calls.count("audio-zh") == 1
    assert s.calls.count("quant") == s.calls.count("build") == 1 and s.calls.count("news") == 2
    assert s.calls.count("create") == s.calls.count("send") == 2
    assert len([c for c in s.remote.calls if c[0] == "push"]) == 1
    assert s.public == reviewed and s.store.read("probes/" + DAY) is not None
    assert R.health(s.store, clock=lambda: s.now[0])["state"] == "submitted"
    _, head = R.read_head(s.store)
    with W.restored(tmp_path, read_artifact(s.store, head["latest_seed"])) as root:
        from fxdash.narrative.briefing_archive import dashboard
        brief = dashboard(root / "outputs")["current"]
        assert brief["audio"]["state"] == "ready"
        assert brief["recap"]["text"]["en"]
    assert not any("subscriptions" in k for k in s.public)


@pytest.mark.parametrize("field,value", [("mode", "delivery"), ("model", True), ("speech", True)])
def test_same_day_policy_changes_cannot_regenerate(tmp_path, monkeypatch, field, value):
    s = controller(tmp_path, monkeypatch)
    go(s, tmp_path)
    before = list(s.calls)
    with pytest.raises(StateError):
        go(s, tmp_path, **{field: value})
    assert s.calls == before


def test_failed_news_keeps_quant_checkpoint_for_the_next_day(tmp_path, monkeypatch):
    s = harness(tmp_path, monkeypatch)
    def fail(*a, **kw):
        raise TimeoutError("provider-secret-text")
    monkeypatch.setattr("fxdash.web.drivers.collect", fail)
    for _ in range(2):
        with pytest.raises(StateError, match="cloud_run_needs_review"):
            go(s, tmp_path)
    _, head = R.read_head(s.store)
    quant = J.Journal(s.store)._read()[1]["claims"][f"quant/{DAY}/edition"]["receipt"]["artifact_sha256"]
    assert head["latest_seed"] == quant and s.calls == ["quant"]
    assert b"provider-secret" not in s.store.read(J.KEY).body


def test_bootstrap_and_cutover_fail_closed(tmp_path, monkeypatch):
    s = controller(tmp_path, monkeypatch)
    with pytest.raises(StateError, match="cloud_already_initialized"):
        R.bootstrap(s.store, s.data, tmp_path)
    with pytest.raises(StateError, match="stop_attestation"):
        R.authorize_delivery(s.store, s.data, tmp_path)
    go(s, tmp_path)
    with pytest.raises(StateError, match="fresh_run_day"):
        R.authorize_delivery(s.store, s.data, tmp_path, local_sender_stopped=True, clock=lambda: s.now[0])
    assert R.read_head(s.store)[1]["delivery_authorized"] is False


def test_independent_health_rejects_missing_run_and_forged_summary(tmp_path, monkeypatch):
    s = controller(tmp_path, monkeypatch)
    R.authorize_delivery(s.store, s.data, tmp_path, local_sender_stopped=True, clock=lambda: s.now[0])
    assert R.health(s.store, clock=lambda: moment(13))["state"] == "not_due"
    assert R.health(s.store, clock=lambda: moment(date="2026-01-10"))["state"] == "not_due"
    assert R.health(s.store, clock=lambda: s.now[0])["last_run_state"] == "missing_run"
    s.store.compare_and_swap("runs/" + DAY, None, encode({"date": DAY, "state": "submitted", "mode": "delivery"}))
    assert R.health(s.store, clock=lambda: s.now[0])["state"] == "attention_required"


def test_audio_preserves_existing_recordings_and_editions(tmp_path):
    value = artifact(tmp_path)
    _, brief = audio.install(tmp_path, value)
    for lang in ("en", "zh"):
        assert audio.ensure_language(tmp_path, value, lang, enabled=True, renderer=renderer,
            clock=lambda: moment(17), gate=lambda: None)["state"] == "ready"
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for lang in ("en", "zh"):
        audio.ensure_language(tmp_path, value, lang, enabled=True,
            renderer=lambda *a, **kw: pytest.fail("must not synthesize twice"),
            clock=lambda: moment(18), gate=lambda: None)
    assert all(p.read_bytes() == body for p, body in before.items())
    assert A.inspect(tmp_path, brief)["state"] == "ready"


@pytest.mark.parametrize("kind", ["generating", "failed", "orphan", "corrupt_ready", "future"])
def test_partial_or_uncertain_audio_never_repeats_request(tmp_path, kind):
    value = artifact(tmp_path)
    _, brief = audio.install(tmp_path, value)
    root = A.sidecar(tmp_path, brief, "audio-v5")
    if kind in {"corrupt_ready", "future"}:
        audio.ensure_language(tmp_path, value, "en", enabled=True, renderer=renderer,
                              clock=lambda: moment(17), gate=lambda: None)
        if kind == "corrupt_ready":
            (root / "en.mp3").write_bytes(b"corrupt")
    else:
        root.mkdir(parents=True, exist_ok=True)
        if kind == "orphan":
            (root / "en.mp3").write_bytes(b"unfinished")
        else:
            M.atomic_json(root / "en.json", dict(A.identity(brief), language="en", script_version="audio-v5", state=kind))
    with pytest.raises(StateError, match="legacy_audio_needs_review"):
        audio.ensure_language(tmp_path, value, "en", enabled=True,
            renderer=lambda *a, **kw: pytest.fail("never replay uncertain synthesis"),
            clock=lambda: moment(16 if kind == "future" else 18), gate=lambda: None)


def test_legacy_audio_reuse_does_not_upgrade_or_mix_versions(tmp_path):
    path, edition, _ = saved(tmp_path)
    prepare(tmp_path, path)
    value = encode({"schema": "cloud-briefing-v1", "edition": edition})
    for lang in ("en", "zh"):
        assert audio.ensure_language(tmp_path, value, lang, enabled=True,
            renderer=lambda *a, **kw: pytest.fail("no silent upgrade"), clock=lambda: moment(18),
            gate=lambda: None)["engine"] == "windows-system-speech"
    _, brief = audio.install(tmp_path, value)
    (A.sidecar(tmp_path, brief) / "zh.json").unlink()  # test-owned interrupted legacy attachment
    for suffix in (".txt", ".mp3"):
        (A.sidecar(tmp_path, brief) / ("zh" + suffix)).unlink()
    with pytest.raises(StateError, match="legacy_audio_version"):
        audio.ensure_language(tmp_path, value, "zh", enabled=True, renderer=renderer,
                              clock=lambda: moment(18), gate=lambda: None)


def test_conflicting_frozen_packet_and_unclaimed_ports_block(tmp_path):
    value = artifact(tmp_path)
    (tmp_path / "briefing/catchup/2026-01-08/packet.json").write_bytes(b"{}")
    with pytest.raises(StateError, match="frozen_edition_conflict"):
        audio.install(tmp_path, value)
    store = SQLiteStore(tmp_path / "state.sqlite3", create=True)
    J.Journal.initialize(store)
    journal = J.Journal(store)
    with journal.session():
        ports = F.ProductionPorts(journal, DAY, tmp_path, clock=lambda: moment(17))
        with pytest.raises(StateError, match="claim_required"):
            ports.inputs({"seed": b"seed"})


def test_disposable_source_copy_and_environment_are_isolated(tmp_path, monkeypatch):
    data = seed(tmp_path / "seed")
    for name in S.SECRET_NAMES:
        monkeypatch.setenv(name, "test-secret-" + name)
    monkeypatch.setenv("GIT_TRACE", "1")
    with W.restored(tmp_path, data, code=True) as root:
        assert (root / "src/fxdash/run.py").is_file() and (root / "src/fxdash/web/static/app.js").is_file()
        assert not (root / ".git").exists() and not (root / ".env").exists() and not (root / "notes").exists()
        for stage in ("quant", "build"):
            env = W.worker_environment(root, stage)
            assert all(k not in env for k in S.SECRET_NAMES if stage != "quant" or k not in {"FRED_API_KEY", "BANXICO_TOKEN"})
            assert env["PYTHONPATH"] == str(root / "src") and "GIT_TRACE" not in env
        seen = []
        def fake_run(args, **kw):
            seen.append((args, kw))
            return SimpleNamespace(returncode=0)
        monkeypatch.setattr(subprocess, "run", fake_run)
        W.execute(root, "quant", replay_capture="input_archive/captures/test.json")
        assert seen[0][1]["cwd"] == root and seen[0][1]["timeout"] == 7200
        if W.os.name == "nt":
            assert seen[0][1]["creationflags"] == subprocess.CREATE_NO_WINDOW
        with pytest.raises(StateError):
            W.execute(root, "quant", replay_capture="../other.json")
    assert not root.exists()


def test_worker_refuses_live_repository_and_modified_copied_code(tmp_path, monkeypatch):
    with pytest.raises(StateError, match="disposable_copy"):
        worker.check_workspace()
    data = seed(tmp_path / "seed")
    with W.restored(tmp_path, data, code=True) as root:
        monkeypatch.setattr(config, "REPO_ROOT", root)
        monkeypatch.chdir(root)
        assert worker.check_workspace() == root
        seen = []
        monkeypatch.setattr("fxdash.run.main", lambda args: seen.append(args) or 0)
        assert worker.main(["--stage", "quant"]) == 0
        assert seen == [["--mode", "live", "--skip-report"]]
        (root / "src/fxdash/run.py").write_text("changed")
        with pytest.raises(StateError, match="source_changed"):
            worker.check_workspace()
        monkeypatch.chdir(tmp_path)


def test_public_bundle_roundtrip_and_no_overwrite(candidate, tmp_path):
    body, report = W.public_bundle(candidate)
    assert W.restore_public(body, tmp_path / "restored")["sha256"] == report["sha256"]
    with pytest.raises(StateError, match="new_directory"):
        W.restore_public(body, tmp_path / "restored")


@pytest.mark.parametrize("name", ["../escape", "/escape", "CON.json", "a./b.json", "a\\b.json", "api/x.json:stream"])
def test_public_archive_path_attacks(tmp_path, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        info = zipfile.ZipInfo()
        info.filename = name  # Preserve malicious bytes even on Windows.
        z.writestr(info, "bad")
    with pytest.raises(StateError, match="invalid_public_bundle"):
        W.restore_public(buffer.getvalue(), tmp_path / "target")
    assert not (tmp_path / "target").exists()


def abandoned(tmp_path, monkeypatch):
    s = controller(tmp_path, monkeypatch)
    go(s, tmp_path)
    row, head = R.read_head(s.store)
    owner = head["active"]["owner"]
    jrow, value = J.Journal(s.store)._read()
    value["owner"] = owner
    value["claims"][f"recap/{DAY}/edition"]["state"] = "pending"
    s.store.compare_and_swap(J.KEY, jrow.version, J.encode(value))
    report = X.inspect(s.store)
    return s, report


def test_audited_recovery_releases_only_owner_and_preserves_uncertain_claims(tmp_path, monkeypatch):
    s, report = abandoned(tmp_path, monkeypatch)
    claims = J.Journal(s.store)._read()[1]["claims"]
    result = X.release(s.store, expected_sha256=report["journal_sha256"], owner=report["owner"],
        run_id=123, run_attempt=1, scheduler_stopped=True, proof=lambda i, a: (i, a) == (123, 1))
    assert result["claims_preserved"] and result["external_actions_retried"] is False
    assert J.Journal(s.store)._read()[1]["claims"] == claims and X.inspect(s.store)["state"] == "unowned"
    with pytest.raises(StateError):
        go(s, tmp_path)


@pytest.mark.parametrize("change", ["sha", "owner", "run_id", "run_attempt", "attestation", "proof"])
def test_recovery_cannot_release_wrong_or_running_worker(tmp_path, monkeypatch, change):
    s, report = abandoned(tmp_path, monkeypatch)
    kw = dict(expected_sha256=report["journal_sha256"], owner=report["owner"], run_id=123,
              run_attempt=1, scheduler_stopped=True, proof=lambda *a: True)
    kw.update({"sha": {"expected_sha256": "0" * 64}, "owner": {"owner": "0" * 32},
        "run_id": {"run_id": 124}, "run_attempt": {"run_attempt": 2},
        "attestation": {"scheduler_stopped": False}, "proof": {"proof": lambda *a: False}}[change])
    before = s.store.read(J.KEY).body
    with pytest.raises(StateError):
        X.release(s.store, **kw)
    assert s.store.read(J.KEY).body == before


def test_cloud_workflow_gates_and_secret_scope():
    root = Path(__file__).resolve().parents[1]
    daily = (root / ".github/workflows/cloud-production.yml").read_text()
    watch = (root / ".github/workflows/cloud-watchdog.yml").read_text()
    for text in (daily, watch):
        assert "vars.FXDASH_CLOUD_ENABLED == 'true'" in text and "refs/heads/main" in text
        assert "timezone: America/New_York" in text and "cancel-in-progress: false" in text
        assert "persist-credentials: false" in text and "upload-artifact" not in text
        assert "pull_request" not in text and "environment: fx-cloud-production" in text
    assert "default: shadow" in daily and daily.count("default: false") == 2
    assert "runtime health" in watch and "secrets." not in watch


def test_local_operator_cannot_run_production_or_dump_credentials(monkeypatch, capsys):
    monkeypatch.setattr(R, "azure_store", lambda **kw: pytest.fail("must not contact Azure"))
    assert R.main(["run", "--local-operator"]) == 2
    assert "local_operator_cannot" in capsys.readouterr().out
    assert R.main(["check"]) in {0, 2}
    assert json.loads(capsys.readouterr().out)["network_called"] is False


@pytest.mark.parametrize("status,attempt,repository,path,accepted", [
    ("completed", 1, X.REPOSITORY, ".github/workflows/cloud-production.yml", True),
    ("in_progress", 1, X.REPOSITORY, ".github/workflows/cloud-production.yml", False),
    ("completed", 2, X.REPOSITORY, ".github/workflows/cloud-production.yml", False),
    ("completed", 1, "other/repo", ".github/workflows/cloud-production.yml", False),
    ("completed", 1, X.REPOSITORY, ".github/workflows/other.yml", False),
])
def test_recovery_verifies_fixed_github_run_and_attempt(monkeypatch, status, attempt, repository, path, accepted):
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, limit):
            yield json.dumps({"id": 123, "run_attempt": attempt, "status": status, "head_branch": "main",
                "repository": {"full_name": repository}, "path": path}).encode()
    def request(session, method, url, **kw):
        assert session.trust_env is False
        assert url == f"https://api.github.com/repos/{X.REPOSITORY}/actions/runs/123"
        assert kw["allow_redirects"] is False and "Authorization" not in kw["headers"]
        return Response()
    monkeypatch.setattr("requests.sessions.Session.request", request)
    assert X.completed_run(123, 1) is accepted


def test_recovery_conditional_write_cannot_erase_a_concurrent_update(tmp_path, monkeypatch):
    s, report = abandoned(tmp_path, monkeypatch)
    def race(*a):
        row, value = J.Journal(s.store)._read()
        value["claims"][f"recap/{DAY}/edition"]["state"] = "review_required"
        s.store.compare_and_swap(J.KEY, row.version, J.encode(value))
        return True
    with pytest.raises(Conflict):
        X.release(s.store, expected_sha256=report["journal_sha256"], owner=report["owner"],
            run_id=123, run_attempt=1, scheduler_stopped=True, proof=race)
    assert X.inspect(s.store)["state"] == "owned"
    assert X.inspect(s.store)["claims"][f"recap/{DAY}/edition"] == "review_required"


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(schema=True), lambda v: v.update(latest_seed="bad"),
    lambda v: v.update(delivery_authorized=True), lambda v: v["active"].update(owner="bad"),
    lambda v: v["active"].update(run_attempt=True), lambda v: v["active"]["policy"].update(model="true"),
])
def test_corrupt_runtime_control_is_never_reinitialized(tmp_path, monkeypatch, mutate):
    s = controller(tmp_path, monkeypatch)
    go(s, tmp_path)
    row, value = R.read_head(s.store)
    mutate(value)
    s.store.compare_and_swap(R.HEAD, row.version, encode(value))
    before = s.store.read(R.HEAD).body
    with pytest.raises(StateError):
        go(s, tmp_path)
    assert s.store.read(R.HEAD).body == before


def test_new_day_starts_from_promoted_seed_and_never_mails_backlog(tmp_path, monkeypatch):
    s = controller(tmp_path, monkeypatch)
    go(s, tmp_path)
    latest = R.read_head(s.store)[1]["latest_seed"]
    s.now[0] += timedelta(days=1)
    assert go(s, tmp_path)["state"] == "shadow_ready"
    active = R.read_head(s.store)[1]["active"]
    assert active["day"] == "2026-01-09" and active["seed_sha256"] == latest
    assert len(J.Journal(s.store)._read()[1]["claims"]) == 14
    before = list(s.calls)
    s.now[0] += timedelta(days=1)
    assert go(s, tmp_path)["state"] == "outside_run_day" and s.calls == before


def test_provider_configuration_check_is_offline_and_masks_values(monkeypatch):
    for k in S.SECRET_NAMES:
        monkeypatch.delenv(k, raising=False)
    for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID"):
        monkeypatch.setenv(name, "12345678-1234-1234-1234-123456789abc")
    monkeypatch.setenv("FXDASH_STORAGE_ACCOUNT", "testaccount")
    monkeypatch.setenv("FXDASH_STORAGE_CONTAINER", "private-state")
    assert R.check_settings()["state"] == "configured"
    assert R.check_settings(model=True)["state"] == "configuration_required"
    monkeypatch.setenv("FXDASH_PUBLISH_TOKEN", "ghs_unsupported-token")
    assert R.check_settings(delivery=True)["state"] == "configuration_required"
    monkeypatch.setenv("AZURE_TENANT_ID", "invalid")
    report = R.check_settings()
    assert report["state"] == "configuration_required" and report["network_called"] is False
    assert "invalid" not in json.dumps(report) and "ghs_unsupported" not in json.dumps(report)


def test_raw_calendar_sources_survive_private_roundtrip(tmp_path):
    minimal_seed(tmp_path / "seed")
    path = tmp_path / "seed/outputs/calendar/2026-01-08/sources/bls.ics"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    with W.restored(tmp_path, W.bundle(tmp_path / "seed")) as root:
        assert (root / path.relative_to(tmp_path / "seed")).read_bytes() == path.read_bytes()
    with pytest.raises(S.SnapshotError):
        S.relative_path("outputs/subscriptions/not-calendar.ics")
