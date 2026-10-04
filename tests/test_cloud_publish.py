import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

import pytest

from fxdash.cloud import publish as P
from fxdash.cloud.state import StateError
from fxdash.web import build as B
from test_build import site_app, no_network  # noqa: F401; synthetic app and network isolation

TOKEN = "ghp_" + "offlinefixture" * 3


@pytest.fixture(autouse=True)
def no_credentials_or_http(monkeypatch):
    monkeypatch.setattr(P, "known_secrets", lambda: ())
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("no external request"))


@pytest.fixture
def candidate(tmp_path):
    # File-validation cases need a valid export shape, not a fresh regression
    # dashboard per mutation. The actual builder is exercised separately below.
    path = tmp_path / "candidate"
    shutil.copytree(B.STATIC_DIR, path)
    meta = {"pairs": ["USDEUR"], "windows": [126], "models": ["ols"],
            "default_window": 126, "default_model": "ols", "data_version": "fixture-version",
            "date_range": {"first": "2026-01-07", "last": "2026-01-07"}, "model_revision": "fixture-model"}
    requests = {r: B.file_for(r) for r in B.request_set(meta)}
    for request, name in requests.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"as_of": "2026-01-07", "data_version": "fixture-version", "model_revision": "fixture-model"}
        params = parse_qs(urlsplit(request).query)
        payload.update({key: int(params[key][0]) if key == "window" else params[key][0]
                        for key in ("window", "model") if key in params})
        if request == "/meta":
            payload = meta
        elif request == "/status":
            payload["server"] = {"data_version": "fixture-version"}
        elif request.startswith("/overview?"):
            payload["summary"] = {key: payload[key] for key in ("as_of", "window", "model")}
        elif request.startswith("/pairs/") and "/series?" in request:
            payload.update(pair=urlsplit(request).path.split("/")[2], dates=["2026-01-07"])
        target.write_text(json.dumps(payload), encoding="utf-8")
    manifest = {"requests": requests, "files": sorted(requests.values()), "media_files": [],
                "built_at": "2026-01-08T17:00:00+00:00", "as_of": "2026-01-07",
                "data_version": "fixture-version", "model_revision": "fixture-model", "briefing": None}
    (path / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / ".nojekyll").write_bytes(b"")
    return path


def allow_local_transport(env):
    env = dict(env)
    index = int(env["GIT_CONFIG_COUNT"])
    env[f"GIT_CONFIG_KEY_{index}"], env[f"GIT_CONFIG_VALUE_{index}"] = "protocol.file.allow", "always"
    env["GIT_CONFIG_COUNT"] = str(index + 1)
    return env


class LocalRemote:
    """Real git commands, but the one fixed HTTPS remote maps to a private test repo."""
    def __init__(self, root):
        self.remote, self.seed = root / "remote.git", root / "old-checkout"
        self.seed.mkdir()
        self.calls, self.before_push = [], None
        env = allow_local_transport(P.git_environment())
        P.run_git(["init", "--bare", str(self.remote)], root, env)
        P.run_git(["init", "-q", "-b", "gh-pages"], self.seed, env)
        self.advance()

    def advance(self):
        env = allow_local_transport(P.git_environment())
        P.run_git(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "--allow-empty",
                   "-m", "old deployment"], self.seed, env)
        P.run_git(["push", str(self.remote), "HEAD:" + P.REF], self.seed, env)
        return P.run_git(["rev-parse", "HEAD"], self.seed, env)

    def head(self):
        return P.run_git(["ls-remote", str(self.remote), P.REF], self.seed,
                         allow_local_transport(P.git_environment())).split()[0]

    def __call__(self, args, cwd, env):
        self.calls.append(list(args))
        assert all(TOKEN not in arg for arg in args)
        if args[0] == "push" and self.before_push:
            self.before_push()
        mapped = [str(self.remote) if arg == P.REMOTE else arg for arg in args]
        # Tests must not perform any actual HTTPS git operation.
        assert all("https://" not in arg for arg in mapped)
        return P.run_git(mapped, cwd, allow_local_transport(env))


def prepared(candidate, tmp_path):
    report = P.inspect(candidate)
    return P.stage(candidate, tmp_path / "staged", expected_sha256=report["sha256"])


def test_existing_builder_produces_an_accepted_export(site_app, tmp_path):
    path = tmp_path / "actual-build"
    manifest = B.build(path, app=site_app[1])
    report = P.inspect(path)
    staged = P.stage(path, tmp_path / "actual-stage", expected_sha256=report["sha256"])
    assert report["files"] > len(manifest["files"]) and staged.sha256 == report["sha256"]


@pytest.fixture
def built_candidate(tmp_path, monkeypatch, request):
    """An actual isolated export with a frozen edition and synthetic voice bytes."""
    from fxdash.web import market
    from fxdash.narrative import audio_briefing
    from test_audio_briefing import saved, prepare
    from test_morning import moment

    # Short, owned TEMP paths also support Windows installations without
    # long-path support: media paths contain a full 64-character edition hash.
    with TemporaryDirectory(prefix="fxp-") as folder:
        root = Path(folder) / "pipeline"
        root.mkdir()
        cache = Path(folder) / "empty-cache"
        monkeypatch.setattr(market, "CACHE_DIR", cache)
        monkeypatch.setattr(audio_briefing, "backend", lambda: "windows")
        edition, _, _ = saved(root)
        if getattr(request, "param", True):
            prepare(root, edition)
        path = Path(folder) / "candidate"
        B.build(path, app=B.create_app(root, cache_dir=cache), now=moment(17, 2))
        assert P.inspect(path)["sha256"]
        yield path


@pytest.mark.parametrize("mutation", [
    "manifest_version", "manifest_as_of", "manifest_model", "meta_version", "status_version", "status_model",
    "overview_version", "overview_as_of", "overview_summary", "overview_model", "weekly_as_of", "daily_window",
    "comparison_model", "pair_identity", "series_as_of", "drivers_version", "briefing_identity", "audio_bytes",
    "audio_hash", "audio_identity", "overview_runtime",
])
def test_actual_export_identity_mismatch_blocks_review_and_staging(built_candidate, tmp_path, monkeypatch, mutation):
    path = built_candidate
    manifest = json.loads((path / "build.json").read_bytes())
    name = "build.json"
    if mutation.startswith("meta_"):
        name = "api/meta.json"
    elif mutation.startswith("status_"):
        name = "api/status.json"
    elif mutation.startswith("overview_"):
        name = B.file_for("/overview?window=126&model=ols")
    elif mutation == "weekly_as_of":
        name = B.file_for("/attribution/weekly?window=126&model=ols")
    elif mutation == "daily_window":
        name = B.file_for("/narrative/daily?window=126&model=ols")
    elif mutation == "comparison_model":
        name = B.file_for("/research/comparison?window=126")
    elif mutation in {"pair_identity", "series_as_of"}:
        name = B.file_for("/pairs/USDEUR/series?window=126&model=ols&observations=252")
    elif mutation in {"drivers_version", "audio_hash", "audio_identity"}:
        name = "api/news.json"
    value = json.loads((path / name).read_bytes())
    if mutation == "audio_bytes":
        audio = next(item for item in manifest["media_files"] if item.endswith("/en.mp3"))
        (path / audio).write_bytes(b"ID3" + b"corrupted-fixture-bytes" * 100)
    else:
        if mutation == "status_version":
            value["server"]["data_version"] = "different-version"
        elif mutation == "drivers_version":
            value["drivers"]["data_version"] = "different-version"
        elif mutation.endswith("_version"):
            value["data_version"] = "different-version"
        elif mutation in {"manifest_model", "status_model", "comparison_model"}:
            value["model_revision"] = "different-model"
        elif mutation.endswith("_as_of") and mutation != "series_as_of":
            value["as_of"] = "2027-01-01"
        elif mutation == "series_as_of":
            value["dates"][-1] = "2027-01-01"
        elif mutation == "overview_summary":
            value["summary"]["as_of"] = "2027-01-01"
        elif mutation == "overview_model":
            value["model"] = "ridge"
        elif mutation == "overview_runtime":
            value["status_digest"]["runtime"]["attribution_as_of"] = "2027-01-01"
        elif mutation == "daily_window":
            value["window"] = 252
        elif mutation == "pair_identity":
            value["pair"] = "USDJPY"
        elif mutation == "briefing_identity":
            value["briefing"]["edition_hash"] = "0" * 64
        elif mutation == "audio_hash":
            value["briefing"]["audio"]["languages"]["en"]["audio_sha256"] = "0" * 64
        elif mutation == "audio_identity":
            value["briefing"]["audio"]["languages"]["en"]["url"] = next(
                item for item in manifest["media_files"] if item.endswith("/zh.mp3"))
        (path / name).write_text(json.dumps(value), encoding="utf-8")

    monkeypatch.setattr(P, "push", lambda *a, **kw: pytest.fail("An invalid candidate must not reach push"))
    monkeypatch.setattr("fxdash.narrative.subscriptions.Provider",
                        lambda *a, **kw: pytest.fail("An invalid candidate must not send mail"))
    with pytest.raises(StateError, match="publication_identity_mismatch"):
        P.inspect(path)
    with pytest.raises(StateError, match="publication_identity_mismatch"):
        P.stage(path, tmp_path / "rejected-stage", expected_sha256="0" * 64,
                runner=lambda *a: pytest.fail("An invalid candidate must not run Git"))
    assert not (tmp_path / "rejected-stage").exists()


@pytest.mark.parametrize("built_candidate", [False], indirect=True)
def test_actual_text_only_edition_remains_publishable(built_candidate):
    manifest = json.loads((built_candidate / "build.json").read_bytes())
    assert manifest["briefing"] and manifest["media_files"] == []
    assert P.inspect(built_candidate)["sha256"]


def _historical_audio(path):
    import hashlib
    manifest = json.loads((path / "build.json").read_bytes())
    news = json.loads((path / "api/news.json").read_bytes())
    historical = {"mode": "edition", "date": "2025-12-31", "edition_hash": "a" * 64,
                  "data_version": "older-frozen-version", "audio": {"languages": {}}}
    selected = next(item for item in manifest["media_files"] if item.endswith("/en.mp3"))
    media = f'media/briefing/edition/{historical["date"]}/{historical["edition_hash"]}/audio-v1/en.mp3'
    target = path / media
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((path / selected).read_bytes())
    historical["audio"]["languages"]["en"] = {
        "state": "ready", "url": media, "audio_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    news["briefing_archive"]["history"].append(historical)
    # Older voice files retained by the builder need not be selected by a
    # current edition; their allowlisted paths remain valid export members.
    retained = media.replace("audio-v1", "audio-v2")
    (path / retained).parent.mkdir(parents=True, exist_ok=True)
    (path / retained).write_bytes(target.read_bytes())
    manifest["media_files"] = sorted(manifest["media_files"] + [media, retained])
    (path / "api/news.json").write_text(json.dumps(news), encoding="utf-8")
    (path / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return target


def test_frozen_history_and_unselected_old_voice_files_remain_publishable(built_candidate):
    _historical_audio(built_candidate)
    assert P.inspect(built_candidate)["sha256"]


def test_corrupted_ready_historical_audio_blocks_publication(built_candidate):
    historical = _historical_audio(built_candidate)
    historical.write_bytes(b"corrupted historical fixture")
    with pytest.raises(StateError, match="publication_identity_mismatch"):
        P.inspect(built_candidate)


def test_valid_build_is_reviewable_and_staged_without_changing_source(candidate, tmp_path):
    report = P.inspect(candidate)
    before = {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob("*") if p.is_file()}
    staged = prepared(candidate, tmp_path)
    assert staged.sha256 == report["sha256"]
    assert len(staged.commit) == 40 and (staged.directory / ".git").is_dir()
    assert before == {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob("*") if p.is_file()}
    assert not (candidate / ".git").exists()
    assert P.run_git(["rev-list", "--parents", "-n", "1", "HEAD"], staged.directory, P.git_environment()) == staged.commit


@pytest.mark.parametrize("name", [".env", "outputs/subscriptions/config.json", "data/cache/prices.parquet",
    "api/private.json", ".git/config", "notes/interview.html", "private.sqlite3"])
def test_private_or_unexpected_files_are_never_staged(candidate, tmp_path, name):
    path = candidate / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"private")
    with pytest.raises(StateError):
        P.stage(candidate, tmp_path / "staged", expected_sha256="a" * 64)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("name", ["app.js", "index.html", "fonts.css", "figures/pipeline-en.svg"])
def test_frontend_assets_must_match_the_reviewed_source_tree(candidate, name):
    (candidate / name).write_bytes(b"unexpected asset")
    with pytest.raises(StateError, match="publication_allowlist_mismatch"):
        P.inspect(candidate)


def test_manifest_cannot_authorize_an_extra_private_api_response(candidate):
    path = candidate / "build.json"
    manifest = json.loads(path.read_bytes())
    manifest["requests"]["/subscribers"] = "api/subscribers.json"
    manifest["files"].append("api/subscribers.json")
    (candidate / "api/subscribers.json").write_bytes(b'{"private":"fixture"}')
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(StateError, match="publication_allowlist_mismatch"):
        P.inspect(candidate)


def test_known_credential_in_an_otherwise_valid_api_file_blocks_publication(candidate):
    secret = b"synthetic-test-secret-value"
    path = candidate / "api/news.json"
    value = json.loads(path.read_bytes())
    value["unsafe"] = secret.decode()
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(StateError, match="secret_in_publication_refused"):
        P.inspect(candidate, secrets=(secret,))


def test_existing_stage_or_changed_review_hash_is_never_overwritten(candidate, tmp_path):
    folder = tmp_path / "existing"
    folder.mkdir()
    marker = folder / "keep.txt"
    marker.write_bytes(b"keep me")
    sha = P.inspect(candidate)["sha256"]
    with pytest.raises(StateError, match="publication_requires_new_directory"):
        P.stage(candidate, folder, expected_sha256=sha)
    assert marker.read_bytes() == b"keep me"
    with pytest.raises(StateError, match="publication_changed_since_review"):
        P.stage(candidate, tmp_path / "new", expected_sha256="0" * 64)
    assert not (tmp_path / "new").exists()


def test_git_environment_drops_ambient_credentials_tracing_hooks_and_askpass(monkeypatch):
    for key in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_ASKPASS", "GIT_DIR", "GIT_CONFIG_COUNT",
                "GIT_CONFIG_VALUE_99", "GCM_TRACE", "GEMINI_API_KEY", "BREVO_API_KEY", "FXDASH_BLOB_ACCESS_TOKEN"):
        monkeypatch.setenv(key, "sensitive-fixture")
    env = P.git_environment(TOKEN)
    assert "sensitive-fixture" not in str(env)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    settings = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert settings["http.followRedirects"] == "false"
    assert settings["credential.helper"] == "" and settings["protocol.allow"] == "never"
    assert settings["core.attributesFile"] == os.devnull


def test_real_git_push_uses_explicit_lease_and_still_requires_public_verification(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    remote = LocalRemote(tmp_path)
    old = remote.head()
    result = P.push(staged, old, token_provider=lambda: TOKEN, runner=remote)
    assert remote.head() == staged.commit
    assert result["state"] == "pushed" and result["public_verified"] is False
    push = [c for c in remote.calls if c[0] == "push"]
    assert len(push) == 1 and f"--force-with-lease={P.REF}:{old}" in push[0]
    assert "--force" not in push[0] and P.REMOTE in push[0]
    # The token and authorization header are never stored in the staged repo.
    encoded = base64.b64encode(("x-access-token:" + TOKEN).encode())
    for file in (staged.directory / ".git").rglob("*"):
        if file.is_file():
            assert TOKEN.encode() not in file.read_bytes() and encoded not in file.read_bytes()


def test_a_remote_advance_before_push_is_not_overwritten(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    remote = LocalRemote(tmp_path)
    old = remote.head()
    new = remote.advance()
    with pytest.raises(StateError, match="publication_remote_changed"):
        P.push(staged, old, token_provider=lambda: TOKEN, runner=remote)
    assert remote.head() == new and not any(c[0] == "push" for c in remote.calls)


def test_a_remote_advance_between_probe_and_push_is_rejected_by_git(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    remote = LocalRemote(tmp_path)
    old = remote.head()
    remote.before_push = remote.advance
    with pytest.raises(StateError, match="git_operation_unconfirmed"):
        P.push(staged, old, token_provider=lambda: TOKEN, runner=remote)
    assert remote.head() not in {old, staged.commit}
    assert len([c for c in remote.calls if c[0] == "push"]) == 1


def test_lost_push_response_is_not_automatically_retried(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    remote = LocalRemote(tmp_path)
    def lost_reply(args, cwd, env):
        result = remote(args, cwd, env)
        if args[0] == "push":
            raise TimeoutError("private credential-containing transport error")
        return result
    with pytest.raises(StateError, match="publication_push_unconfirmed") as error:
        P.push(staged, remote.head(), token_provider=lambda: TOKEN, runner=lost_reply)
    assert "private credential" not in str(error.value)
    assert remote.head() == staged.commit
    assert len([c for c in remote.calls if c[0] == "push"]) == 1


@pytest.mark.parametrize("token", ["", "*", "ghs_" + "x" * 40, "token with spaces", TOKEN + "\n"])
def test_missing_or_default_actions_tokens_cannot_publish(candidate, tmp_path, token):
    staged = prepared(candidate, tmp_path)
    with pytest.raises(StateError, match="publication_pat_required"):
        P.push(staged, "a" * 40, token_provider=lambda: token,
               runner=lambda *a: pytest.fail("No network before token check"))


def test_staged_file_mutation_blocks_all_network_operations(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    path = staged.directory / "api/news.json"
    value = json.loads(path.read_bytes())
    value["changed"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(StateError, match="publication_changed_since_review"):
        P.push(staged, "a" * 40, token_provider=lambda: TOKEN,
               runner=lambda *a: pytest.fail("No network after changed files"))


def test_push_requires_an_explicit_remote_head(candidate, tmp_path):
    staged = prepared(candidate, tmp_path)
    with pytest.raises(StateError, match="expected_publication_head_required"):
        P.push(staged, None, token_provider=lambda: pytest.fail("missing lease"))


def test_cli_cannot_push_even_if_a_publish_token_is_present(candidate, monkeypatch, capsys):
    monkeypatch.setenv("FXDASH_PUBLISH_TOKEN", TOKEN)
    monkeypatch.setattr(P, "push", lambda *a, **k: pytest.fail("CLI is local-only"))
    assert P.main(["--candidate", str(candidate)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pushed"] is False and result["network_called"] is False
    assert TOKEN not in str(result)
