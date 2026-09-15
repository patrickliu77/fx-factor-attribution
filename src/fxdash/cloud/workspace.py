"""Disposable private workspaces and bounded subprocesses; never the live tree."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

from . import snapshot as S
from .state import StateError, MAX_BYTES

PACKAGE = Path(__file__).resolve().parents[1]
MARKER = ".fx-cloud-workspace.json"


def directory(path):
    root = Path(path).absolute()
    if not root.is_dir() or root.resolve() != root or any(S.linked(p) for p in (root, *root.parents)):
        raise StateError("invalid_worker_parent")
    return root


@contextmanager
def restored(parent, body, *, code=False):
    parent = directory(parent)
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise StateError("invalid_worker_seed")
    # TemporaryDirectory only removes this exclusively owned directory. No user
    # root, old build or previous worktree is used as a cleanup target.
    with tempfile.TemporaryDirectory(prefix="fxc-", dir=parent) as temp:
        root = Path(temp) / "work"
        S.restore(io.BytesIO(body), root)
        if code:
            total, files = 0, {}
            for path in sorted(PACKAGE.rglob("*")):
                if S.linked(path):
                    raise StateError("worker_source_linked")
                if not path.is_file():
                    continue
                rel = path.relative_to(PACKAGE)
                if path.suffix != ".py" and rel.parts[:2] != ("web", "static"):
                    continue
                data = path.read_bytes()
                total += len(data)
                if total > 40 * 1024 * 1024 or len(files) > 2000:
                    raise StateError("worker_source_size_limit")
                target = root / "src" / "fxdash" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                files[rel.as_posix()] = hashlib.sha256(data).hexdigest()
            (root / MARKER).write_text(json.dumps({"schema": 1, "files": files}), encoding="utf-8")
        yield root


def bundle(root):
    root = directory(root)
    with tempfile.TemporaryDirectory(prefix="fxp-", dir=root.parent) as temp:
        path = Path(temp) / "state.zip"
        S.pack(root, path)
        if path.stat().st_size > MAX_BYTES:
            raise StateError("runtime_seed_size_limit")
        return path.read_bytes()


def worker_environment(root, stage):
    # Never inherit the storage token, publishing credential, Brevo or speech key
    # into a quant/build child, and do not discover a desktop user's credentials.
    env = {k: v for k, v in os.environ.items() if k.upper() not in S.SECRET_NAMES
           and not k.upper().startswith(("GIT_", "GCM_", "SSH_", "PYTHON"))}
    if stage == "quant":
        for key in ("FRED_API_KEY", "BANXICO_TOKEN"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    env.update(PYTHONPATH=str(root / "src"), PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1",
               FXDASH_AUDIO="off", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return env


def execute(root, stage, *, replay_capture=None):
    root = directory(root)
    if stage not in {"quant", "build"} or not (root / MARKER).is_file():
        raise StateError("invalid_worker_request")
    args = [sys.executable, "-m", "fxdash.cloud.worker", "--stage", stage]
    if replay_capture is not None:
        if stage != "quant" or not re.fullmatch(r"input_archive/captures/[A-Za-z0-9_-]+\.json", replay_capture):
            raise StateError("invalid_replay_capture")
        args += ["--replay-capture", replay_capture]
    try:
        result = subprocess.run(args, cwd=root, env=worker_environment(root, stage),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7200 if stage == "quant" else 900,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode != 0:
            raise StateError("isolated_worker_failed")
    except StateError:
        raise
    except Exception:
        raise StateError("isolated_worker_unconfirmed") from None


def public_bundle(candidate):
    from . import publish
    files, report = publish._validated(candidate)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted(files.items()):
            archive.writestr(name, body)
    body = output.getvalue()
    if len(body) > MAX_BYTES:
        raise StateError("public_bundle_too_large")
    return body, report


def restore_public(body, destination):
    from . import publish
    import stat
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise StateError("invalid_public_bundle")
    destination = Path(destination).absolute()
    if destination.exists() or destination.resolve() != destination or S.linked(destination):
        raise StateError("public_restore_requires_new_directory")
    directory(destination.parent)
    total, files = 0, {}
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000:
                raise ValueError()
            for info in entries:
                name = info.filename
                if (info.orig_filename != name or not re.fullmatch(r"[A-Za-z0-9_=/.-]+", name)
                        or info.is_dir() or stat.S_ISLNK(info.external_attr >> 16)
                        or name.startswith("/") or any(p in {"", ".", ".."} or p.endswith(".")
                            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", p.split(".")[0])
                            for p in name.split("/"))
                        or name.lower() in {n.lower() for n in files}
                        or info.file_size > publish.MAX_FILE):
                    raise ValueError()
                total += info.file_size
                if total > publish.MAX_TOTAL:
                    raise ValueError()
                files[name] = archive.read(info)
        destination.mkdir()
        for name, data in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return publish.inspect(destination)
    except StateError:
        raise
    except Exception:
        raise StateError("invalid_public_bundle") from None
