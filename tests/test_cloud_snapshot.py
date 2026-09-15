import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest

from fxdash.cloud import snapshot as S
from fxdash.cloud import preflight as P


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    monkeypatch.setattr(S, "known_secrets", lambda: ())


def seed(root):
    files = {
        "data/cache/FX.parquet": b"cache fixture",
        "data/user/fred_BAMLH0A0HYM2.csv": b"DATE,VALUE\n2020-01-01,4.0\n",
        "outputs/alignment/profile.json": b'{"summary":{"frozen":true}}',
        "outputs/status.json": b'{"state":"green"}',
        "outputs/run_manifest.json": b'{"model_revision":"test"}',
        "outputs/source_as_of.json": b'{"USDAUD.fx":"2026-09-11"}',
        "outputs/contract/year=2026/part.parquet": b"contract fixture",
        "outputs/subscriptions/config.json": b'{"enabled":false,"sender_footer":"private footer"}',
        "outputs/subscriptions/deliveries/2026-09-14/en.json": b'{"state":"submitted","campaign_id":42}',
        "outputs/briefing/catchup/2026-09-14/generation.claim": b'{"packet_hash":"saved"}',
        "outputs/briefing/audio/catchup/2026-09-14/hash/audio-v5/en.mp3": b"audio fixture",
    }
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return files


def forged_bundle(path, files, *, entries=None, total=None):
    items = entries if entries is not None else [
        {"path": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in files.items()
    ]
    manifest = {"schema": S.SCHEMA, "files": items,
                "total_bytes": total if total is not None else sum(i["size"] for i in items)}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(S.MANIFEST, json.dumps(manifest))
        for name, body in files.items():
            archive.writestr(name, body)


def test_bundle_round_trip_preserves_claims_and_private_settings(tmp_path):
    root = tmp_path / "original"
    files = seed(root)
    bundle = tmp_path / "seed.zip"
    report = S.pack(root, bundle)
    assert report["files"] == len(files)
    assert report["uploaded"] is False
    target = tmp_path / "restored"
    restored = S.restore(bundle, target)
    assert restored["network_called"] is False
    assert S.inventory(root) == S.inventory(target)
    for name, body in files.items():
        assert (target / name).read_bytes() == body


def test_only_operational_allowlist_is_bundled(tmp_path):
    seed(tmp_path)
    for name in (".env", "notes/interview.html", "src/fxdash/run.py", ".github/workflows/a.yml",
                 "outputs/logs/worker.log", "outputs/cloud/private.zip",
                 "outputs/briefing/a.lock", "outputs/briefing/a.tmp",
                 "outputs/briefing/render-random/en.txt",
                 "data/user/not-approved.csv"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE", encoding="utf-8")
    names = {i["path"] for i in S.inventory(tmp_path)["files"]}
    assert names == set(seed(tmp_path))


@pytest.mark.parametrize("name", [
    "../outputs/status.json", "/outputs/status.json", "C:/outputs/status.json",
    "outputs/../src/run.py", "outputs/briefing/../../.env", "outputs/briefing/a\\b.json",
    "outputs/briefing//a.json", "outputs/briefing/./a.json", "outputs/briefing/a.json:stream",
    "outputs/briefing/NUL.json", "outputs/briefing/COM1.json", "outputs/briefing/a./b.json",
    ".github/workflows/send.yml", "src/fxdash/run.py", "notes/private.txt",
    "outputs/briefing/run.py", "outputs/briefing/render-inflight/en.txt",
])
def test_untrusted_bundle_paths_are_refused(tmp_path, name):
    bundle = tmp_path / "bad.zip"
    forged_bundle(bundle, {name: b"bad"})
    with pytest.raises(S.SnapshotError):
        S.restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("mutation", ["hash", "size", "total", "extra", "schema", "bool_size"])
def test_manifest_is_not_trusted(tmp_path, mutation):
    body, name = b"fixture", "outputs/status.json"
    item = {"path": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if mutation == "hash":
        item["sha256"] = "0" * 64
    if mutation == "size":
        item["size"] += 1
    if mutation == "bool_size":
        item["size"] = True
    bundle = tmp_path / "bad.zip"
    manifest = {"schema": "other" if mutation == "schema" else S.SCHEMA, "files": [item],
                "total_bytes": 999 if mutation == "total" else item["size"]}
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(S.MANIFEST, json.dumps(manifest))
        archive.writestr(name, body)
        if mutation == "extra":
            archive.writestr("outputs/briefing/extra.json", b"{}")
    with pytest.raises(S.SnapshotError):
        S.restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_symlink_archive_member_refused(tmp_path):
    bundle = tmp_path / "link.zip"
    info = zipfile.ZipInfo("outputs/status.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(S.MANIFEST, "{}")
        archive.writestr(info, "../../elsewhere")
    with pytest.raises(S.SnapshotError, match="invalid_snapshot_members"):
        S.restore(bundle, tmp_path / "restored")


@pytest.mark.parametrize("names", [
    ("outputs/briefing/a.json", "outputs/briefing/A.json"),
    ("outputs/briefing/a.json", "outputs/briefing/a.json/b.json"),
])
def test_portable_path_collisions_refused(tmp_path, names):
    bundle = tmp_path / "collision.zip"
    forged_bundle(bundle, {n: b"{}" for n in names})
    with pytest.raises(S.SnapshotError):
        S.restore(bundle, tmp_path / "restored")


def test_known_credentials_block_pack_without_printing_value(tmp_path, capsys, monkeypatch):
    seed(tmp_path)
    secret = b"test-only-secret-never-a-real-key"
    (tmp_path / "outputs/status.json").write_bytes(secret)
    monkeypatch.setattr(S, "known_secrets", lambda: (secret,))
    assert S.main(["pack", "--root", str(tmp_path), "--bundle", str(tmp_path / "seed.zip")]) == 2
    output = capsys.readouterr().out
    assert secret.decode() not in output
    assert "SnapshotError" in output
    assert not (tmp_path / "seed.zip").exists()


def test_source_change_leaves_no_valid_commit_marker(tmp_path, monkeypatch):
    root = tmp_path / "source"
    seed(root)
    real = S.inventory
    calls = []

    def moving(*args, **kwargs):
        value = real(*args, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            value["total_bytes"] += 1
        return value

    monkeypatch.setattr(S, "inventory", moving)
    bundle = tmp_path / "failed.zip"
    with pytest.raises(S.SnapshotError, match="snapshot_changed_during_capture"):
        S.pack(root, bundle)
    with pytest.raises(S.SnapshotError, match="invalid_snapshot_members"):
        S.restore(bundle, tmp_path / "restored")


def test_no_overwrite_of_bundle_or_restore_target(tmp_path):
    root = tmp_path / "source"
    seed(root)
    bundle = tmp_path / "seed.zip"
    S.pack(root, bundle)
    original = bundle.read_bytes()
    with pytest.raises(S.SnapshotError):
        S.pack(root, bundle)
    with pytest.raises(S.SnapshotError):
        S.restore(bundle, root)
    assert bundle.read_bytes() == original


def test_size_and_count_limits(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr(S, "MAX_FILES", 1)
    with pytest.raises(S.SnapshotError, match="snapshot_file_limit"):
        S.inventory(tmp_path)
    monkeypatch.setattr(S, "MAX_FILES", 20000)
    monkeypatch.setattr(S, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(S.SnapshotError, match="snapshot_size_limit"):
        S.inventory(tmp_path)


def test_restore_size_limit_checked_before_writes(tmp_path, monkeypatch):
    bundle = tmp_path / "big.zip"
    forged_bundle(bundle, {"outputs/status.json": b"0123456789"})
    monkeypatch.setattr(S, "MAX_TOTAL_BYTES", 5)
    with pytest.raises(S.SnapshotError):
        S.restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_linked_local_ancestor_refused(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr(S, "linked", lambda p: p == tmp_path / "outputs")
    with pytest.raises(S.SnapshotError, match="snapshot_link_refused"):
        S.inventory(tmp_path)


def test_readiness_is_only_a_snapshot_check(tmp_path):
    from types import SimpleNamespace
    from fxdash import config
    seed(tmp_path)
    combos = {(p, w, m): SimpleNamespace(dates=["2026-09-11"])
              for p in config.PAIRS for w in config.WINDOWS for m in config.MODELS}
    fake = SimpleNamespace(combos=combos, date_last="2026-09-11",
                           manifest={"model_revision": config.MODEL_REVISION})
    result = P.inspect(tmp_path, snapshot_factory=lambda *a, **kw: fake)
    assert result["state"] == "ready_for_shadow"
    assert result["combinations"] == 54
    assert result["network_called"] is result["email_sent"] is False
    assert result["email_configuration_valid"] is False
    assert "not_live_or_delivery" in result["scope"]


@pytest.mark.parametrize("problem", ["missing_seed", "wrong_model", "missing_combo", "mixed_dates"])
def test_incomplete_seed_is_not_ready(tmp_path, problem):
    from types import SimpleNamespace
    from fxdash import config
    seed(tmp_path)
    combos = {(p, w, m): SimpleNamespace(dates=["2026-09-11"])
              for p in config.PAIRS for w in config.WINDOWS for m in config.MODELS}
    fake = SimpleNamespace(combos=combos, date_last="2026-09-11",
                           manifest={"model_revision": config.MODEL_REVISION})
    if problem == "missing_seed":
        (tmp_path / "outputs/alignment/profile.json").unlink()
    elif problem == "wrong_model":
        fake.manifest["model_revision"] = "not-the-frozen-revision"
    elif problem == "missing_combo":
        combos.pop(next(iter(combos)))
    else:
        next(iter(combos.values())).dates = ["2026-09-10"]
    result = P.inspect(tmp_path, snapshot_factory=lambda *a, **kw: fake)
    assert result["state"] == "incomplete"
    assert result["checks"]


def test_cloud_workflow_is_manual_offline_and_read_only():
    root = Path(__file__).resolve().parents[1]
    source = (root / ".github/workflows/cloud-readiness.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "contents: read" in source
    assert "persist-credentials: false" in source
    assert 'FXDASH_AUDIO: "off"' in source
    for forbidden in ("schedule:", "secrets.", "contents: write", "pages: write",
                      "upload-artifact@", "fxdash.run --mode live", "fxdash.narrative.subscriptions"):
        assert forbidden not in source
    for line in source.splitlines():
        if "uses:" in line:
            import re
            assert re.search(r"@[0-9a-f]{40}\b", line)
