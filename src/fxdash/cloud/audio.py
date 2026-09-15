"""Single-language cloud speech attachment. No automatic upgrade or retry."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from ..narrative import audio_briefing as A, audio_script as S, morning as M, briefing_archive as E
from .briefing import decode, encode
from .snapshot import linked
from .state import StateError


def install_json(path, value):
    path = Path(path)
    if any(linked(p) for p in (path, *path.parents)):
        raise StateError("edition_install_linked")
    if path.exists():
        if decode(path.read_bytes()) != value:
            raise StateError("frozen_edition_conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encode(value))


def install(output_dir, artifact):
    value = decode(artifact)
    edition = value.get("edition", {})
    if value.get("schema") != "cloud-briefing-v1" or not E.valid_edition(
            edition, edition.get("date"), mode=edition.get("mode")):
        raise StateError("invalid_cloud_edition")
    packet = edition.get("evidence")
    if not isinstance(packet, dict) or edition.get("packet_hash") != M.digest(packet):
        raise StateError("invalid_edition_evidence")
    path = A.edition_path(output_dir, edition["mode"], edition["date"])
    install_json(path.parent / "packet.json", packet)
    install_json(path, edition)
    brief = E.read_edition(path, mode=edition["mode"])
    if brief.get("edition_hash") != M.digest(edition):
        raise StateError("installed_edition_unreadable")
    return path, brief


def ensure_language(output_dir, artifact, lang, *, enabled=False, renderer=None, clock=M.now_utc, gate):
    if lang not in {"en", "zh"} or type(enabled) is not bool:
        raise StateError("invalid_cloud_audio_settings")
    gate()
    path, brief = install(output_dir, artifact)
    # Inspect every version before selecting one. An old interrupted attempt
    # cannot be bypassed by changing the voice version during migration.
    attempted = []
    for version in S.VERSIONS:
        root = A.sidecar(output_dir, brief, version)
        if any((root / (lang + suffix)).exists() for suffix in (".json", ".txt", ".mp3")):
            attempted.append(version)
    if attempted:
        version = attempted[-1]
        root = A.sidecar(output_dir, brief, version)
        saved = A._language(root, brief, lang, version)
        if saved.get("state") != "ready" or datetime.fromisoformat(saved["generated_at"]) > clock():
            raise StateError("legacy_audio_needs_review")
        return saved
    if not enabled:
        return {"state": "disabled"}
    # Do not produce one language in a newer version while the other language
    # still belongs to an older attachment. Review a partial legacy bundle first.
    if any((A.sidecar(output_dir, brief, version) / (other + ".json")).exists()
           for version in S.VERSIONS[:-1] for other in ("en", "zh")):
        raise StateError("legacy_audio_version_needs_review")
    from ..narrative.azure_speech import render
    version = S.NEURAL_VERSION
    root = A.sidecar(output_dir, brief, version)
    root.mkdir(parents=True, exist_ok=True)
    saved = decode(path.read_bytes())
    text = S.compose(saved, saved["evidence"], lang, version=version)
    script, media = root / (lang + ".txt"), root / (lang + ".mp3")
    value = dict(A.identity(brief), script_version=version, language=lang,
                 state="generating", started_at=clock().isoformat(), attempts=1)
    install_json(root / (lang + ".json"), value)
    with script.open("x", encoding="utf-8") as stream:
        stream.write(text)
    gate()  # Durable cloud claim and current day, immediately before the request.
    info = (renderer or render)(script, media, lang, script_version=version)
    gate()
    lower, upper = S.duration_bounds(version)
    if (not lower <= S.number(info.get("duration_seconds")) <= upper
            or info.get("engine") != "azure-neural-speech" or not isinstance(info.get("voice"), str)
            or len(info["voice"]) > 120 or linked(media) or not 1000 <= media.stat().st_size <= 5_000_000):
        raise StateError("invalid_cloud_audio_output")
    value.update({key: info[key] for key in ("engine", "voice", "duration_seconds")})
    value.update(state="ready", generated_at=clock().isoformat(), audio_sha256=A.file_hash(media),
                 script_sha256=A.file_hash(script))
    M.atomic_json(root / (lang + ".json"), value)
    install_json(root / "attempts" / (lang + "-1.json"), value)
    result = A._language(root, brief, lang, version)
    if result["state"] != "ready":
        raise StateError("cloud_audio_integrity_failed")
    return result
