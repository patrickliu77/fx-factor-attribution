"""Static site builder (SPEC_web §7).

Renders the dashboard into a directory that GitHub Pages can serve: index.html and
the static assets copied as they are, every API response the frontend can request
written as a file under api/, and build.json.

    python -m fxdash.web.build                 # -> <repo>/site
    python -m fxdash.web.build --out some/dir

The build goes through the FastAPI app with a test client, so no business logic is
duplicated and the web layer's three iron rules are inherited: read outputs/ only,
never touch the pipeline's files, the only new math is summation. The one thing the
build adds is a timestamp: the page carries build.json so the browser can compute
how old the page is, which is the heartbeat of the publish step.

Static hosting ignores query strings, so a request's parameters are encoded into its
file name; file_for() below and staticPath() in app.js implement the same rule.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from ..config import REPO_ROOT, display_path
from .app import STATIC_DIR, create_app
from .market import RANGES as MARKET_RANGES

DEFAULT_OUT = REPO_ROOT / "site"
API_PREFIX = "/api"


def file_for(request: str) -> str:
    """Static file for one API request: drop the leading slash, append the query
    parameters sorted by key as ".key-value", then ".json".

    "/overview?window=126&model=ols" -> "api/overview.model-ols.window-126.json"
    """
    path, _, query = request.partition("?")
    params = sorted(kv.split("=", 1) for kv in query.split("&") if kv) if query else []
    suffix = "".join(f".{key}-{value}" for key, value in params)
    return f"api{path}{suffix}.json"


def request_set(meta: dict) -> list[str]:
    """Every request app.js can make.

    Pairs, windows and models come from /meta, which derives them from the data,
    never from config; the range table is the market layer's own. The FX and News
    pages are pinned to the default window and model, the Attribution page can ask
    for any combination, and the price charts can ask for any range of any pair.
    """
    pairs = list(meta["pairs"])
    windows = list(meta["windows"])
    models = list(meta["models"])
    default_window = meta.get("default_window") or windows[0]
    default_model = meta.get("default_model") or models[0]
    canonical = f"?window={default_window}&model={default_model}"
    requests = [
        "/meta",
        "/market/ticker",
        "/narrative/status",
        "/news",
        "/overview" + canonical,
        "/narrative/daily" + canonical,
    ]
    requests += [f"/attribution/weekly?window={w}&model={m}" for w in windows for m in models]
    requests += [f"/attribution/weekly?window={w}&model={m}&days={d}"
                 for w in windows for m in models for d in (1, 21)]
    requests += [f"/pairs/{p}/series?window={w}&model={m}&observations=252"
                 for p in pairs for w in windows for m in models]
    requests += [f"/pairs/{p}/news" for p in pairs]
    requests += [f"/market/series/{p}?range={r}" for p in pairs for r in MARKET_RANGES]
    return requests


def _tz_offset(moment: datetime) -> str:
    """'-0500' -> '-05:00'. The page reads naive pipeline timestamps in this offset."""
    text = moment.strftime("%z")
    return f"{text[:3]}:{text[3:]}" if text else "+00:00"


def _source_commit() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or None
    except Exception:
        return None


def build(out: Path, *, app=None, output_dir=None, cache_dir=None,
          now: datetime | None = None) -> dict:
    """Render the site into `out`, which is wiped first. Returns the manifest that
    is also written as build.json."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # static assets as they are. build.json is never taken from the source tree:
    # its presence is what tells app.js it is running from a build
    for src in STATIC_DIR.rglob("*"):
        if src.is_dir() or src.name == "build.json":
            continue
        dst = out / src.relative_to(STATIC_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (out / ".nojekyll").write_bytes(b"")

    app = app or create_app(output_dir, cache_dir=cache_dir)
    client = TestClient(app)
    meta_response = client.get(API_PREFIX + "/meta")
    meta_response.raise_for_status()
    meta = meta_response.json()

    files: list[str] = []
    requests: dict[str, str] = {}
    for request in request_set(meta):
        response = client.get(API_PREFIX + request)
        response.raise_for_status()
        rel = file_for(request)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        files.append(rel)
        requests[request] = rel

    moment = (now or datetime.now()).astimezone()
    manifest = {
        "built_at": moment.isoformat(timespec="seconds"),
        "tz_offset": _tz_offset(moment),
        "data_version": meta.get("data_version"),
        "as_of": (meta.get("date_range") or {}).get("last"),
        "source_commit": _source_commit(),
        "files": sorted(files),
        "requests": requests,
    }
    (out / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the dashboard as a static site.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="target directory, wiped first")
    parser.add_argument("--output-dir", default=None,
                        help="pipeline outputs/ to read (default: the repository's)")
    parser.add_argument("--cache-dir", default=None,
                        help="data/cache/ for price levels (default: the repository's)")
    args = parser.parse_args(argv)

    manifest = build(Path(args.out), output_dir=args.output_dir, cache_dir=args.cache_dir)
    total = sum((Path(args.out) / f).stat().st_size for f in manifest["files"])
    print(f"site      {display_path(args.out)}")
    print(f"built_at  {manifest['built_at']}   as_of {manifest['as_of']}   "
          f"data_version {manifest['data_version']}")
    print(f"api files {len(manifest['files'])}   {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
