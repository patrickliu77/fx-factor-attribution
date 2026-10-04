"""Portable publication of a reviewed static build to the fixed gh-pages branch.

The CLI only inspects/stages local files. The explicit Python push operation
requires a PAT and the previously observed remote commit. It never sends mail
and reports a Git push separately from public Pages availability.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlsplit

from .. import config
from ..web import build as B
from .journal import unique_object
from .snapshot import SECRET_NAMES, known_secrets, linked
from .state import StateError

REMOTE = "https://github.com/patrickliu77/fx-factor-attribution.git"
REF = "refs/heads/gh-pages"
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def _directory(path):
    root = Path(path).absolute()
    if linked(root) or root.resolve() != root or not root.is_dir():
        raise StateError("invalid_publication_directory")
    return root


def _files(root, *, staged=False):
    result = {}
    total = 0
    for current, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            if linked(Path(current) / name):
                raise StateError("publication_link_refused")
        if staged and Path(current) == root:
            folders[:] = [name for name in folders if name != ".git"]
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if (not re.fullmatch(r"[A-Za-z0-9_.=/\-]+", rel)
                    or any(p in {"", ".", ".."} or p.endswith(".") for p in rel.split("/"))
                    or not path.is_file() or path.stat().st_size > MAX_FILE):
                raise StateError("invalid_publication_file")
            with path.open("rb") as stream:
                body = stream.read(MAX_FILE + 1)
            total += len(body)
            if len(body) > MAX_FILE or total > MAX_TOTAL or len(result) >= 10000:
                raise StateError("publication_size_limit")
            result[rel] = body
    if len({p.lower() for p in result}) != len(result):
        raise StateError("publication_path_collision")
    return result


def _json(body):
    try:
        return json.loads(body, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        raise StateError("invalid_publication_json") from None


def _day(value):
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


def _snapshot_identity(value, meta):
    """Check fields describing the current contract, not frozen older editions."""
    for key in ("data_version", "model_revision"):
        if key in value and value[key] != meta.get(key):
            raise ValueError()
    if "as_of" in value and value["as_of"] != meta["date_range"]["last"]:
        raise ValueError()


def _publication_identity(files, manifest, meta, responses):
    last = meta["date_range"]["last"]
    if (not _day(last) or not isinstance(meta["data_version"], str) or not meta["data_version"]
            or manifest["data_version"] != meta["data_version"] or manifest["as_of"] != last
            or manifest.get("model_revision") != meta.get("model_revision")):
        raise ValueError()

    dated = {"/overview", "/news", "/narrative/daily", "/attribution/weekly", "/research/comparison"}
    versioned = {"/overview", "/research/comparison"}
    selectors = {"/overview", "/narrative/daily", "/attribution/weekly", "/research/comparison"}
    for request, value in responses.items():
        if not isinstance(value, dict):
            raise ValueError()
        path, params = urlsplit(request).path, parse_qs(urlsplit(request).query)
        _snapshot_identity(value, meta)
        if path in dated and value["as_of"] != last:
            raise ValueError()
        if path in versioned and value["data_version"] != meta["data_version"]:
            raise ValueError()
        series = re.fullmatch(r"/pairs/([^/]+)/series", path)
        for key in ("window", "model"):
            if key in params and (key in value or path in selectors or series):
                if value[key] != (int(params[key][0]) if key == "window" else params[key][0]):
                    raise ValueError()
        if path == "/research/comparison" and value.get("model_revision") != meta.get("model_revision"):
            raise ValueError()
        if series:
            dates = value["dates"]
            if (value["pair"] != series[1] or not isinstance(dates, list) or not dates
                    or not all(_day(day) for day in dates) or dates != sorted(set(dates)) or dates[-1] != last):
                raise ValueError()
        if path == "/overview":
            summary = value["summary"]
            _snapshot_identity(summary, meta)
            if any(summary[key] != value[key] for key in ("as_of", "window", "model")):
                raise ValueError()
            runtime = (value.get("status_digest") or {}).get("runtime") or {}
            for key in ("contract_last_date", "attribution_as_of"):
                if runtime.get(key) not in (None, last):
                    raise ValueError()

    status = responses["/status"]
    if (status["server"]["data_version"] != meta["data_version"]
            or status.get("model_revision") != meta.get("model_revision")):
        raise ValueError()
    for value in (status, status.get("runtime") or {}):
        for key in ("contract_last_date", "attribution_as_of"):
            if value.get(key) not in (None, last):
                raise ValueError()

    news = responses["/news"]
    if news.get("drivers"):
        _snapshot_identity(news["drivers"], meta)
    current, archive = news.get("briefing") or {}, news.get("briefing_archive") or {}
    identity = None
    if current.get("mode") in {"edition", "catchup"} and current.get("edition_hash"):
        if not _day(current["date"]) or not re.fullmatch(r"[0-9a-f]{64}", current["edition_hash"]):
            raise ValueError()
        identity = {key: current[key] for key in ("date", "edition_hash")}
        if current["mode"] == "catchup":
            identity["mode"] = "catchup"
    recorded = manifest.get("briefing")
    if identity is None:
        if recorded is not None:
            raise ValueError()
    elif (not isinstance(recorded, dict)
          or any(recorded.get(key) != identity[key] for key in ("date", "edition_hash"))
          or recorded.get("mode", "edition") != current["mode"]):
        raise ValueError()

    checked = {}
    for edition in [current] + archive.get("history", []) + archive.get("catchup_history", []):
        for lang, item in ((edition.get("audio") or {}).get("languages") or {}).items():
            if item.get("state") != "ready":
                continue
            name, digest = item["url"], item["audio_sha256"]
            parts = name.split("/")
            if (name not in manifest["media_files"] or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or parts[2:5] != [edition["mode"], edition["date"], edition["edition_hash"]]
                    or parts[-1] != lang + ".mp3" or checked.get(name, digest) != digest
                    or hashlib.sha256(files[name]).hexdigest() != digest):
                raise ValueError()
            checked[name] = digest


def _validated(candidate, *, staged=False, secrets=None):
    files = _files(_directory(candidate), staged=staged)
    try:
        manifest, meta = _json(files["build.json"]), _json(files["api/meta.json"])
        for key, allowed in (("pairs", config.PAIRS), ("windows", config.WINDOWS), ("models", config.MODELS)):
            values = meta[key]
            if not isinstance(values, list) or not values or len(set(values)) != len(values) or not set(values) <= set(allowed):
                raise ValueError()
        if meta.get("default_window") not in meta["windows"] or meta.get("default_model") not in meta["models"]:
            raise ValueError()
        expected = {request: B.file_for(request) for request in B.request_set(meta)}
        if (manifest["requests"] != expected or manifest["files"] != sorted(expected.values())
                or not isinstance(manifest["media_files"], list)
                or len(set(manifest["media_files"])) != len(manifest["media_files"])):
            raise ValueError()
        for name in manifest["media_files"]:
            if not re.fullmatch(r"media/briefing/(edition|catchup)/\d{4}-\d{2}-\d{2}/[0-9a-f]{64}/audio-v[1-5]/(en|zh)\.mp3", name):
                raise ValueError()
        assets = _files(_directory(B.STATIC_DIR))
        allowed = set(assets) | set(expected.values()) | set(manifest["media_files"]) | {"build.json", ".nojekyll"}
        if set(files) != allowed or files[".nojekyll"] != b"" or any(files[name] != body for name, body in assets.items()):
            raise ValueError()
        responses = {request: _json(files[name]) for request, name in expected.items()}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise StateError("publication_allowlist_mismatch") from None
    try:
        _publication_identity(files, manifest, meta, responses)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration):
        raise StateError("publication_identity_mismatch") from None
    secrets = known_secrets() if secrets is None else secrets
    if any(value in body for body in files.values() for value in secrets):
        raise StateError("secret_in_publication_refused")
    inventory = {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return files, {"sha256": digest, "files": len(files), "bytes": sum(map(len, files.values()))}


def inspect(candidate, *, secrets=None):
    return _validated(candidate, secrets=secrets)[1]


def git_environment(token=None):
    # Git must not inherit tracing, askpass, alternate directories, global
    # credentials, hooks, signing or line-ending transformations from the host.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("GIT_", "GCM_", "SSH_"))
           and k.upper() not in set(SECRET_NAMES)}
    settings = [("credential.helper", ""), ("core.autocrlf", "false"), ("core.safecrlf", "false"),
                ("core.hooksPath", os.devnull), ("core.attributesFile", os.devnull), ("init.templateDir", ""),
                ("commit.gpgSign", "false"), ("http.followRedirects", "false"),
                ("protocol.allow", "never"), ("protocol.https.allow", "always")]
    if token:
        encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
        settings.append(("http.https://github.com/.extraheader", "Authorization: Basic " + encoded))
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_CONFIG_COUNT=str(len(settings)))
    for i, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


def run_git(args, cwd, env):
    try:
        result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                                timeout=180, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode != 0:
            raise StateError("git_operation_unconfirmed")
        return result.stdout.decode("utf-8", errors="strict").strip()
    except StateError:
        raise
    except Exception:
        raise StateError("git_operation_unconfirmed") from None


@dataclass(frozen=True)
class Prepared:
    directory: Path
    sha256: str
    commit: str


def stage(candidate, destination, *, expected_sha256, runner=run_git, secrets=None):
    files, report = _validated(candidate, secrets=secrets)
    if report["sha256"] != expected_sha256:
        raise StateError("publication_changed_since_review")
    destination = Path(destination).absolute()
    if destination.exists() or linked(destination) or destination.resolve() != destination:
        raise StateError("publication_requires_new_directory")
    destination.mkdir()  # Existing parent; never erase an old checkout/site.
    for name, body in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(body)
    env = git_environment()
    runner(["init", "-q", "-b", "gh-pages"], destination, env)
    runner(["add", "--all"], destination, env)
    runner(["-c", "user.name=FX Dashboard", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-q", "-m", "Reviewed static build " + report["sha256"][:16]], destination, env)
    commit = runner(["rev-parse", "HEAD"], destination, env)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise StateError("invalid_publication_commit")
    return Prepared(destination, report["sha256"], commit)


def push(prepared, expected_remote_commit, *, token_provider, runner=run_git, secrets=None):
    """Explicit outward action. Call inside the durable publisher claim.

    A PAT is required for the existing branch-based Pages setup. GITHUB_TOKEN
    pushes do not trigger a Pages build. No retry occurs after an uncertain push.
    """
    if not isinstance(expected_remote_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_remote_commit):
        raise StateError("expected_publication_head_required")
    files, report = _validated(prepared.directory, staged=True, secrets=secrets)
    if report["sha256"] != prepared.sha256:
        raise StateError("publication_changed_since_review")
    try:
        token = token_provider()
    except Exception:
        raise StateError("publication_token_required") from None
    if not isinstance(token, str) or not re.fullmatch(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,240}", token):
        raise StateError("publication_pat_required")
    if any(token.encode() in body for body in files.values()):
        raise StateError("secret_in_publication_refused")
    env = git_environment(token)
    try:
        parents = runner(["rev-list", "--parents", "-n", "1", "HEAD"], prepared.directory, env)
        if parents != prepared.commit or runner(["status", "--porcelain", "--untracked-files=all"], prepared.directory, env):
            raise StateError("publication_checkout_changed")
        remote = runner(["ls-remote", REMOTE, REF], prepared.directory, env)
        if remote != expected_remote_commit + "\t" + REF:
            raise StateError("publication_remote_changed")
        runner(["push", "--quiet", f"--force-with-lease={REF}:{expected_remote_commit}",
                REMOTE, f"HEAD:{REF}"], prepared.directory, env)
        observed = runner(["ls-remote", REMOTE, REF], prepared.directory, env)
        if observed != prepared.commit + "\t" + REF:
            raise StateError("publication_push_unconfirmed")
    except StateError:
        raise
    except Exception:
        raise StateError("publication_push_unconfirmed") from None
    return {"state": "pushed", "commit": prepared.commit, "sha256": prepared.sha256, "public_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect or stage a static build locally; never push.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    try:
        if args.stage_dir:
            prepared = stage(args.candidate, args.stage_dir, expected_sha256=args.expected_sha256)
            result = {"state": "staged", "sha256": prepared.sha256, "commit": prepared.commit}
        else:
            result = {"state": "inspected", **inspect(args.candidate)}
        print(json.dumps({**result, "network_called": False, "pushed": False}))
        return 0
    except Exception:
        print(json.dumps({"state": "failed", "network_called": False, "pushed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
