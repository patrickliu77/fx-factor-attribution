"""Allowlisted private migration bundles, separate from public site exports.

No upload, synthesis, model call, publication or email operation lives here.
Bundles seed a cloud installation; they are not a runtime send-claim journal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

SCHEMA = "fx-cloud-snapshot-v1"
MAX_FILES = 20000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MANIFEST = "snapshot.json"
DIRECTORIES = (
    "data/cache", "outputs/contract", "outputs/input_archive",
    "outputs/narrative", "outputs/briefing", "outputs/calendar",
    "outputs/delivery", "outputs/subscriptions", "outputs/automation",
)
SINGLE_FILES = (
    "data/user/fred_BAMLH0A0HYM2.csv", "outputs/alignment/profile.json",
    "outputs/status.json", "outputs/run_manifest.json", "outputs/coverage.json",
    "outputs/heartbeat.json", "outputs/source_as_of.json",
    "outputs/contract_latest.json", "outputs/pca_monitor.parquet",
)
SUFFIXES = {".json", ".parquet", ".csv", ".mp3", ".txt", ".claim"}
SECRET_NAMES = (
    "FRED_API_KEY", "BANXICO_TOKEN", "GEMINI_API_KEY", "AZURE_SPEECH_KEY",
    "BREVO_API_KEY", "FXDASH_BLOB_SAS", "FXDASH_BLOB_ACCESS_TOKEN",
    "FXDASH_PUBLISH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
)


class SnapshotError(ValueError):
    """Messages are fixed codes, never file contents, provider bodies or secrets."""


def relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise SnapshotError("invalid_snapshot_path")
    parts = value.split("/")
    if (PurePosixPath(value).as_posix() != value
            or any(not re.fullmatch(r"[A-Za-z0-9_.=-]+", p) or p in {".", ".."}
                   or p.endswith(".") or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", p.split(".")[0])
                   for p in parts)):
        raise SnapshotError("invalid_snapshot_path")
    allowed = value in SINGLE_FILES or any(value.startswith(p + "/") for p in DIRECTORIES)
    if (not allowed or PurePosixPath(value).suffix not in SUFFIXES
            or any(p.startswith("render-") for p in parts)):
        raise SnapshotError("snapshot_path_not_allowed")
    return value


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def local_path(root, name):
    path = root / relative_path(name)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if linked(parent):
            raise SnapshotError("snapshot_link_refused")
    if not path.resolve().is_relative_to(root):
        raise SnapshotError("snapshot_path_outside_root")
    return path


def known_secrets():
    """Read only named environment settings. Do not enumerate the registry."""
    values = [os.environ.get(name, "") for name in SECRET_NAMES]
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as handle:
                for name in SECRET_NAMES:
                    try:
                        value, kind = winreg.QueryValueEx(handle, name)
                        if kind == winreg.REG_SZ:
                            values.append(value)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
    return tuple({v.encode() for v in values if isinstance(v, str) and len(v) >= 8})


def selected_paths(root):
    root = Path(root).absolute()
    if linked(root) or root.resolve() != root:
        raise SnapshotError("snapshot_root_is_link")
    selected = []
    for name in SINGLE_FILES:
        path = local_path(root, name)
        if path.exists():
            selected.append((name, path))
    for prefix in DIRECTORIES:
        directory = local_path(root, prefix + "/probe.json").parent
        if linked(directory):
            raise SnapshotError("snapshot_link_refused")
        if not directory.exists():
            continue
        for current, folders, files in os.walk(directory, followlinks=False):
            for folder in folders:
                if linked(Path(current) / folder):
                    raise SnapshotError("snapshot_link_refused")
            folders[:] = [f for f in folders if not f.startswith("render-")]
            for filename in files:
                path = Path(current) / filename
                if linked(path):
                    raise SnapshotError("snapshot_link_refused")
                if path.suffix not in SUFFIXES:
                    continue  # Locks, temp files, logs and executable files stay local.
                name = path.relative_to(root).as_posix()
                selected.append((name, local_path(root, name)))
    if len(selected) > MAX_FILES or len({n.lower() for n, _ in selected}) != len(selected):
        raise SnapshotError("snapshot_file_limit")
    return sorted(selected)


def read_file(path, secrets):
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise SnapshotError("snapshot_file_limit")
    with path.open("rb") as stream:
        body = stream.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise SnapshotError("snapshot_file_limit")
    if any(value in body for value in secrets):
        raise SnapshotError("secret_in_snapshot_refused")
    return body


def inventory(root, *, secrets=None):
    secrets = known_secrets() if secrets is None else secrets
    files, total = [], 0
    for name, path in selected_paths(root):
        body = read_file(path, secrets)
        total += len(body)
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError("snapshot_size_limit")
        files.append({"path": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    return {"schema": SCHEMA, "files": files, "total_bytes": total}


def pack(root, destination, *, secrets=None):
    """Create only a new private file; refuse changes during the capture."""
    root, destination = Path(root).absolute(), Path(destination)
    secrets = known_secrets() if secrets is None else secrets
    if destination.suffix != ".zip" or destination.exists() or linked(destination):
        raise SnapshotError("snapshot_destination_exists")
    expected = inventory(root, secrets=secrets)
    manifest = dict(expected, captured_at=datetime.now(timezone.utc).isoformat())
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise SnapshotError("snapshot_manifest_limit")
    # mode=x avoids replacing an existing bundle, including on a concurrent run.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in expected["files"]:
            body = read_file(local_path(root, item["path"]), secrets)
            if len(body) != item["size"] or hashlib.sha256(body).hexdigest() != item["sha256"]:
                raise SnapshotError("snapshot_changed_during_capture")
            archive.writestr(item["path"], body)
        if inventory(root, secrets=secrets) != expected:
            raise SnapshotError("snapshot_changed_during_capture")
        # The manifest is the last commit marker. A failed capture is not usable.
        archive.writestr(MANIFEST, encoded)
    return {"files": len(expected["files"]), "bytes": expected["total_bytes"],
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "uploaded": False}


def validate_archive(archive):
    members = archive.infolist()
    names = [i.filename for i in members]
    if (len(members) > MAX_FILES + 1 or len(names) != len({n.lower() for n in names}) or MANIFEST not in names
            or any(i.is_dir() or stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1
                   for i in members)):
        raise SnapshotError("invalid_snapshot_members")
    if archive.getinfo(MANIFEST).file_size > MAX_MANIFEST_BYTES:
        raise SnapshotError("snapshot_manifest_limit")
    manifest = json.loads(archive.read(MANIFEST))
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
            or not isinstance(manifest.get("files"), list)):
        raise SnapshotError("invalid_snapshot_manifest")
    paths, total = [], 0
    for item in manifest["files"]:
        if (not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}
                or type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES
                or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise SnapshotError("invalid_snapshot_entry")
        paths.append(relative_path(item["path"]))
        total += item["size"]
    occupied = {n.lower() for n in paths}
    if (len(paths) != len(occupied) or set(names) != {MANIFEST, *paths}
            or type(manifest.get("total_bytes")) is not int or manifest["total_bytes"] != total
            or total > MAX_TOTAL_BYTES
            or any(parent.as_posix().lower() in occupied for n in paths for parent in PurePosixPath(n).parents)):
        raise SnapshotError("snapshot_manifest_mismatch")
    for item in manifest["files"]:
        info = archive.getinfo(item["path"])
        if info.file_size != item["size"]:
            raise SnapshotError("snapshot_size_mismatch")
        digest, actual = hashlib.sha256(), 0
        with archive.open(info) as stream:
            while chunk := stream.read(65536):
                actual += len(chunk)
                if actual > item["size"]:
                    raise SnapshotError("snapshot_size_mismatch")
                digest.update(chunk)
        if actual != item["size"] or digest.hexdigest() != item["sha256"]:
            raise SnapshotError("snapshot_hash_mismatch")
    return manifest


def restore(source, destination):
    """Validate everything before creating a new destination. Never overwrite."""
    destination = Path(destination).absolute()
    if destination.exists() or linked(destination) or destination.resolve() != destination:
        raise SnapshotError("restore_requires_new_directory")
    with zipfile.ZipFile(source) as archive:
        manifest = validate_archive(archive)
        destination.mkdir()  # Exclusive create, with an already-existing parent.
        for item in manifest["files"]:
            target = local_path(destination, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, actual = hashlib.sha256(), 0
            with archive.open(item["path"]) as origin, target.open("xb") as output:
                while chunk := origin.read(65536):
                    actual += len(chunk)
                    if actual > item["size"]:
                        raise SnapshotError("snapshot_changed_during_restore")
                    digest.update(chunk)
                    output.write(chunk)
            if actual != item["size"] or digest.hexdigest() != item["sha256"]:
                raise SnapshotError("snapshot_changed_during_restore")
    return {"files": len(manifest["files"]), "bytes": manifest["total_bytes"],
            "restored": True, "network_called": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "pack", "restore"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "inspect" and args.root:
            value = inventory(args.root)
            result = {"files": len(value["files"]), "bytes": value["total_bytes"], "network_called": False}
        elif args.operation == "pack" and args.root and args.bundle:
            result = pack(args.root, args.bundle)
        elif args.operation == "restore" and args.bundle and args.destination:
            result = restore(args.bundle, args.destination)
        else:
            parser.error("inspect needs --root; pack needs --root/--bundle; restore needs --bundle/--destination")
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__, "network_called": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
