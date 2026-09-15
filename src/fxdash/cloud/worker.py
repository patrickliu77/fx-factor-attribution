"""Quant/build child used only inside a verified disposable code copy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .state import StateError
from .workspace import MARKER


def check_workspace():
    from ..config import REPO_ROOT
    root = REPO_ROOT.resolve()
    if root != Path.cwd().resolve() or not (root / MARKER).is_file():
        raise StateError("worker_requires_disposable_copy")
    marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
    if marker.get("schema") != 1 or not marker.get("files"):
        raise StateError("invalid_worker_marker")
    for name, digest in marker["files"].items():
        path = root / "src" / "fxdash" / name
        if (not path.resolve().is_relative_to(root / "src/fxdash") or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise StateError("worker_source_changed")
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("quant", "build"), required=True)
    parser.add_argument("--replay-capture")
    args = parser.parse_args(argv)
    try:
        root = check_workspace()
        if args.stage == "quant":
            from .preflight import inspect
            from .. import run
            if inspect(root)["state"] != "ready_for_shadow":
                raise StateError("worker_seed_not_ready")
            if args.replay_capture:
                # Explicit offline rehearsal only, never the scheduled live route.
                import re
                from ..data.vintages import replay_raw
                if not re.fullmatch(r"input_archive/captures/[A-Za-z0-9_-]+\.json", args.replay_capture):
                    raise StateError("invalid_replay_capture")
                raw = replay_raw(root / "outputs" / args.replay_capture)
                run.panel_mod.load_raw = lambda: raw
            return run.main(["--mode", "live", "--skip-report"])
        if args.replay_capture:
            raise StateError("invalid_replay_stage")
        import os
        import re
        from ..web import build
        revision = os.environ.get("GITHUB_SHA", "")
        # A copied worker has no Git repository of its own. Never discover an
        # unrelated ancestor checkout when running a local rehearsal.
        build._source_commit = lambda: revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None
        build.build(root / "site", output_dir=root / "outputs", cache_dir=root / "data/cache")
        return 0
    except Exception:
        # Quant/provider diagnostics never go to public Actions logs.
        print('{"state":"isolated_worker_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
