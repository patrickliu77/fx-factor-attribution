"""Versioned audio attachments. Frozen text and quant inputs are read-only.

The scheduler calls ensure before publication and retries optional attachments
without generating new narrative text. HTTP reads and static builds never synthesize.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import morning as M, briefing_archive as A, audio_script as S

MAX_ATTEMPTS = 2
RETRY_SECONDS = 900
HASH = re.compile(r"[0-9a-f]{64}\Z")


def enabled():
    return os.environ.get("FXDASH_AUDIO", "windows" if os.name == "nt" else "off").lower() == "windows"


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def edition_path(output_dir, mode, day):
    if mode not in {"edition", "catchup"} or date.fromisoformat(day).isoformat() != day:
        raise ValueError("invalid_audio_identity")
    return Path(output_dir) / "briefing" / ("days" if mode == "edition" else "catchup") / day / "edition.json"


def sidecar(output_dir, brief):
    mode, day, identity = brief["mode"], brief["date"], brief["edition_hash"]
    edition_path(output_dir, mode, day)
    if not isinstance(identity, str) or not HASH.fullmatch(identity):
        raise ValueError("invalid_audio_identity")
    base = Path(output_dir).resolve()
    # Keep Windows temporary filenames below legacy MAX_PATH. The full hash
    # is checked in every manifest and URL; a prefix collision is refused.
    target = base / "briefing" / "audio" / mode / day / identity[:20] / S.VERSION
    for p in (target, *target.parents):
        if p == base:
            break
        if p.is_symlink() or getattr(p, "is_junction", lambda: False)():
            raise ValueError("audio_path_is_link")
    if not target.resolve().is_relative_to(base):
        raise ValueError("audio_path_outside_outputs")
    return target


def identity(brief):
    return {k: brief[k] for k in ("mode", "date", "edition_hash", "packet_hash")}


def _language(root, brief, lang):
    try:
        path = root / (lang + ".json")
        if not path.exists():
            return {"state": "not_generated"}
        value = M.read_json(path)
        state = value.get("state")
        if (path.is_symlink() or any(value.get(k) != v for k, v in identity(brief).items())
                or value.get("script_version") != S.VERSION or value.get("language") != lang):
            raise ValueError("audio_identity_mismatch")
        if state != "ready":
            return {"state": state if state in {"failed", "generating"} else "integrity_failed"}
        media, script = root / (lang + ".mp3"), root / (lang + ".txt")
        generated = datetime.fromisoformat(value["generated_at"])
        if (media.is_symlink() or script.is_symlink() or not generated.tzinfo
                or not 60 <= S.number(value["duration_seconds"]) <= 180
                or not 1000 <= media.stat().st_size <= 5_000_000 or script.stat().st_size > 21000
                or value.get("engine") != "windows-system-speech"
                or not isinstance(value.get("voice"), str) or len(value["voice"]) > 120
                or file_hash(media) != value["audio_sha256"] or file_hash(script) != value["script_sha256"]):
            raise ValueError("audio_integrity_failed")
        text = script.read_text(encoding="utf-8")
        result = {k: value[k] for k in
            ("state", "generated_at", "duration_seconds", "engine", "voice", "audio_sha256", "script_sha256")}
        result.update(transcript=text,
            url=f"media/briefing/{brief['mode']}/{brief['date']}/{brief['edition_hash']}/{S.VERSION}/{lang}.mp3")
        return result
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        return {"state": "integrity_failed"}


def inspect(output_dir, brief):
    """Small public contract. Unknown metadata and provider errors never escape."""
    result = {"state": "not_generated", "script_version": S.VERSION, "languages": {}}
    try:
        root = sidecar(output_dir, brief)
        result["languages"] = {lang: _language(root, brief, lang) for lang in ("en", "zh")}
        ready = sum(v["state"] == "ready" for v in result["languages"].values())
        result["state"] = "ready" if ready == 2 else "partial" if ready else "not_generated"
        if not ready and any(v["state"] in {"failed", "integrity_failed", "generating"} for v in result["languages"].values()):
            result["state"] = "unavailable"
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        result.update(state="integrity_failed", languages={})
    return result


def _attempt_due(previous, now):
    if not previous:
        return True
    attempts = previous.get("attempts")
    try:
        started = datetime.fromisoformat(previous["started_at"])
        return (type(attempts) is int and 0 < attempts < MAX_ATTEMPTS and started.tzinfo is not None
                and (now-started).total_seconds() >= RETRY_SECONDS)
    except (KeyError, TypeError, ValueError):
        return False


def ensure(output_dir, path, *, clock=M.now_utc, renderer=None):
    if renderer is None and not enabled():
        return {"state": "disabled"}
    from .speech import render
    path = Path(path)
    mode = "catchup" if path.parent.parent.name == "catchup" else "edition"
    brief = A.read_edition(path, mode=mode)
    if brief["state"] not in {"ready", "numbers_only"}:
        return {"state": "source_unavailable"}
    root = sidecar(output_dir, brief)
    try:
        with M.DayLock(root / "audio.lock"):
            saved = M.read_json(path)
            packet = M.read_json(path.parent / "packet.json")
            for lang in ("en", "zh"):
                manifest = root / (lang + ".json")
                previous = M.read_json(manifest)
                if previous and any(previous.get(k) != v for k, v in identity(brief).items()):
                    return {"state": "identity_mismatch"}
                # A successful attachment is immutable, including if someone
                # subsequently corrupts its file. Never silently replace it.
                if previous.get("state") == "ready":
                    continue
                if manifest.exists() and not previous:
                    continue
                now = clock()
                if not _attempt_due(previous, now):
                    continue
                value = dict(identity(brief), script_version=S.VERSION, language=lang,
                             state="generating", started_at=now.isoformat(), attempts=previous.get("attempts", 0)+1)
                M.atomic_json(manifest, value)
                try:
                    text = S.compose(saved, packet, lang)
                    with tempfile.TemporaryDirectory(prefix="render-", dir=root) as work:
                        work = Path(work)
                        script, mp3 = work / (lang+".txt"), work / (lang+".mp3")
                        script.write_text(text, encoding="utf-8")
                        info = (renderer or render)(script, mp3, lang)
                        if not 60 <= S.number(info["duration_seconds"]) <= 180 or not 1000 <= mp3.stat().st_size <= 5_000_000:
                            raise ValueError("invalid_audio_output")
                        value.update({k: info[k] for k in ("engine", "voice", "duration_seconds")})
                        value.update(audio_sha256=file_hash(mp3), script_sha256=file_hash(script),
                                     generated_at=clock().isoformat(), state="ready")
                        # Crash debris from an incomplete attempt may exist, but
                        # a ready manifest is the sole successful commit marker.
                        script.replace(root / (lang+".txt"))
                        mp3.replace(root / (lang+".mp3"))
                except Exception as exc:
                    value.update(state="failed", finished_at=clock().isoformat(), error=type(exc).__name__)
                M.atomic_json(root / "attempts" / f"{lang}-{value['attempts']}.json", value)
                M.atomic_json(manifest, value)
            return inspect(output_dir, brief)
    except M.Busy:
        return {"state": "busy"}


def prepare_optional(output_dir, path, *, clock=M.now_utc):
    try:
        return ensure(output_dir, path, clock=clock)
    except Exception as exc:
        # No raw subprocess/provider messages. Text publication remains usable.
        return {"state": "failed", "error": type(exc).__name__}


def bundle_hash(output_dir, brief):
    audio = inspect(output_dir, brief)
    ready = {lang: {k: v[k] for k in ("audio_sha256", "script_sha256")}
             for lang, v in audio["languages"].items() if v["state"] == "ready"}
    return M.digest(ready) if ready else None


def record_publication(output_dir, brief, *, state="published", clock=M.now_utc):
    try:
        digest = bundle_hash(output_dir, brief)
        if not digest:
            return
        path = sidecar(output_dir, brief) / "publication.json"
        prior = M.read_json(path)
        M.atomic_json(path, {"state": state, "bundle_hash": digest, "edition_hash": brief["edition_hash"],
                             "observed_at": clock().isoformat(), "attempts": int(prior.get("attempts", 0))+1})
    except (OSError, KeyError, ValueError, TypeError):
        pass  # A missing audio receipt causes a later retry, never a text failure.


def retry_publication(output_dir, brief, repo, publisher, *, clock=M.now_utc):
    """Optional media retry, separate from the original text push receipt."""
    if not enabled():
        return "disabled"
    try:
        digest = bundle_hash(output_dir, brief)
        if not digest:
            return "unavailable"
        prior = M.read_json(sidecar(output_dir, brief) / "publication.json")
        if prior.get("bundle_hash") == digest:
            if prior.get("state") == "published":
                return "already_published"
            if ((clock()-datetime.fromisoformat(prior["observed_at"])).total_seconds() < RETRY_SECONDS):
                return "retry_later"
        publisher(repo)
        record_publication(output_dir, brief, clock=clock)
        return "published"
    except Exception:
        record_publication(output_dir, brief, state="publish_failed", clock=clock)
        return "publish_failed"


def resolve_asset(output_dir, mode, day, edition_hash, version, lang):
    if version != S.VERSION or lang not in {"en", "zh"}:
        raise ValueError("invalid_audio_asset")
    brief = A.read_edition(edition_path(output_dir, mode, day), mode=mode)
    if brief.get("edition_hash") != edition_hash:
        raise ValueError("audio_edition_mismatch")
    info = inspect(output_dir, brief)["languages"].get(lang, {})
    if info.get("state") != "ready":
        raise ValueError("audio_unavailable")
    return sidecar(output_dir, brief) / (lang+".mp3")


def main(argv=None):
    from ..config import OUTPUT_DIR, REPO_ROOT
    from .morning_dispatch import publish_site
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--date")
    parser.add_argument("--mode", choices=["edition", "catchup"], default="catchup")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--check", action="store_true", help="read only, no synthesis or publication")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if args.check and args.publish or bool(args.date) == args.latest:
        parser.error("choose --date or --latest; --check cannot publish")
    brief = (A.dashboard(args.output_dir)["current"] if args.latest else
             A.read_edition(edition_path(args.output_dir, args.mode, args.date), mode=args.mode))
    if brief.get("state") not in {"ready", "numbers_only"}:
        print(json.dumps({"state": "source_unavailable"}))
        return 1
    path = edition_path(args.output_dir, brief["mode"], brief["date"])
    result = inspect(args.output_dir, brief) if args.check else prepare_optional(args.output_dir, path)
    if args.publish:
        result["publication"] = retry_publication(args.output_dir, brief, REPO_ROOT, publish_site)
    print(json.dumps({"date": brief["date"], "mode": brief["mode"], "state": result["state"],
                      "publication": result.get("publication"), "languages": {
                          lang: {k:v[k] for k in ("state", "duration_seconds", "voice") if k in v}
                          for lang,v in result.get("languages", {}).items()}}, ensure_ascii=True))
    return 0 if result["state"] in {"ready", "partial", "disabled"} and result.get("publication") != "publish_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
