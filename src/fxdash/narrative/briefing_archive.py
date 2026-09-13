"""Read-only, bounded morning history and separate preparation/push observations.

No clock is used to manufacture an edition. Missing days stay missing. The browser
compares dated observations with its current New York clock, including stale builds.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import morning as M

HISTORY_LIMIT = 20
EDITION_STATES = {"ready", "numbers_only", "inputs_unavailable"}
PUBLIC_FIELDS = ("available", "mode", "date", "state", "text", "attribution_as_of",
                 "news_observed_by", "generated_at", "target_cutoff", "scheduled",
                 "late_publication", "warnings", "packet_hash", "data_version",
                 "prompt_version", "validator_version", "generator", "schema_version",
                 "morning_target", "catchup_reason")


def day_folders(output_dir, kind="days"):
    if kind not in {"days", "catchup"}:
        raise ValueError("invalid_archive_kind")
    root = Path(output_dir) / "briefing" / kind
    if not root.exists():
        return []
    folders = []
    for path in root.iterdir():
        try:
            if (path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                    and date.fromisoformat(path.name).isoformat() == path.name):
                folders.append(path)
        except ValueError:
            continue
    return sorted(folders, reverse=True)


def valid_edition(value, day, *, mode="edition"):
    try:
        return (mode in {"edition", "catchup"} and value.get("mode") == mode and value.get("date") == day
                and (mode != "catchup" or (value.get("scheduled") is False
                     and isinstance(value.get("morning_target"), str)))
                and value.get("state") in EDITION_STATES and value.get("available") is True
                and isinstance(value["text"], dict) and isinstance(value["notes"], list)
                and all(isinstance(v, str) for v in value["text"].values())
                and (value["state"] == "inputs_unavailable" or all(value["text"].get(lang) for lang in ("en", "zh")))
                and isinstance(value.get("warnings", []), list)
                and all(isinstance(v, str) for v in value.get("warnings", [])))
    except (TypeError, KeyError, AttributeError):
        return False


def public_copy(value):
    result = {k: value[k] for k in PUBLIC_FIELDS if k in value}
    result["text"] = {lang: value.get("text", {}).get(lang, "") for lang in ("en", "zh")}
    # The reader exports only the display contract, never an input packet, a raw
    # model reply, usage metadata or a provider exception added to an archive.
    notes = []
    for item in value.get("notes", []):
        note = item["note"]
        public_note = {k: note[k] for k in ("factor", "assessment")}
        public_note.update({lang: {"event": note[lang]["event"]} for lang in ("en", "zh")})
        public_note["evidence"] = [{"source_id": e["source_id"], "quote": e["quote"]}
                                   for e in note["evidence"]]
        sources = [{k: s.get(k) for k in ("id", "url", "title", "source", "published", "observed_at")}
                   for s in item["sources"]]
        definition = item.get("definition")
        if definition:
            definition = {k: definition[k] for k in ("excluded_target", "members", "low", "high", "measurement")
                          if k in definition}
        checks = {lang: {k: item["checks"][lang][k] for k in
                         ("condition", "supports", "weakens", "watch_summary")} for lang in ("en", "zh")}
        notes.append({"pair": item["pair"], "note": public_note, "sources": sources,
                      "definition": definition, "checks": checks})
    result["notes"] = notes
    return result


def unreadable(day, mode="edition"):
    return {"available": True, "mode": mode, "date": day, "state": "archive_unreadable",
            "text": {}, "notes": [], "warnings": ["archive_unreadable"]}


def read_edition(path, *, mode="edition"):
    value = M.read_json(path)
    day = path.parent.name
    if not valid_edition(value, day, mode=mode):
        return unreadable(day, mode)
    try:
        result = public_copy(value)
        result["edition_hash"] = M.digest(value)
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return unreadable(day, mode)


def receipt(root, edition):
    raw = M.read_json(root / "publish.json")
    allowed = {"published", "publish_failed", "publishing", "finalize_failed"}
    result = {k: raw[k] for k in ("state", "started_at", "finished_at", "attempts") if k in raw}
    if not isinstance(result.get("state"), str) or result["state"] not in allowed:
        result["state"] = "not_recorded"
    if result["state"] == "published" and (not edition.get("edition_hash")
                                         or raw.get("edition_hash") != edition["edition_hash"]):
        result["state"] = "receipt_mismatch"
    return result


def run_record(root):
    preparation = M.read_json(root / "prepare.json")
    claim = M.read_json(root / "prepare.claim")
    state = preparation.get("state")
    if not isinstance(state, str) or state not in {"prepared", "prepare_failed", "ineligible_packet"}:
        state = "attempt_recorded" if (root / "prepare.claim").exists() else "not_recorded"
    edition = read_edition(root / "edition.json") if (root / "edition.json").exists() else {}
    return {"date": root.name, "prepare": {"state": state, "started_at": claim.get("started_at")},
            "edition": {"state": edition.get("state", "not_recorded"),
                        "generated_at": edition.get("generated_at"), "hash": edition.get("edition_hash")},
            "push": receipt(root, edition)}


def dashboard(output_dir, data_version=None, *, clock=None):
    from .driver_notes import PROMPT_VERSION, VALIDATOR_VERSION
    folders = day_folders(output_dir)
    paths = [p / "edition.json" for p in folders if (p / "edition.json").exists()]
    history = [read_edition(p) for p in paths[:HISTORY_LIMIT]]
    late_folders = day_folders(output_dir, "catchup")
    late_paths = [p / "edition.json" for p in late_folders if (p / "edition.json").exists()]
    catchups = [read_edition(p, mode="catchup") for p in late_paths[:HISTORY_LIMIT]]
    # Read-only attachments, outside the frozen edition hash. No TTS on a GET.
    from .audio_briefing import inspect as audio_inspect
    for brief in history + catchups:
        if brief.get("edition_hash") and brief["state"] in {"ready", "numbers_only"}:
            brief["audio"] = audio_inspect(output_dir, brief)
    current = history[0] if history else {}
    if catchups and (not current or catchups[0]["date"] > current["date"]
                    or (catchups[0]["date"] == current["date"] and current["state"] not in {"ready", "numbers_only"})):
        current = catchups[0]
    if not history and not catchups:
        preview = M.read_json(Path(output_dir) / "briefing" / "driver-preview.json")
        if (preview.get("data_version") == data_version and preview.get("prompt_version") == PROMPT_VERSION
                and preview.get("validator_version") == VALIDATOR_VERSION):
            try:
                current = public_copy(preview)
            except (KeyError, TypeError, ValueError, AttributeError):
                current = {}
    current_path = late_paths[0] if current.get("mode") == "catchup" else paths[0] if paths else None
    late_run = M.read_json(late_folders[0] / "status.json") if late_folders else {}
    # Export only a small status vocabulary, never error strings or model replies.
    late_state = late_run.get("state")
    if not isinstance(late_state, str) or late_state not in {"published", "already_available", "publish_failed", "preparing", "publishing",
            "waiting_for_attribution", "waiting_for_news", "waiting_for_morning", "catchup_failed", "archive_unreadable"}:
        late_state = "not_recorded"
    return {"current": current, "history": history, "history_limit": HISTORY_LIMIT,
            "current_push": receipt(current_path.parent, current) if current_path else None,
            "catchup_history": catchups, "total_catchups": len(late_paths),
            "latest_catchup_run": {"state": late_state, "date": late_folders[0].name,
                                   "observed_at": late_run.get("observed_at")} if late_folders else None,
            "total_editions": len(paths), "observed_at": (clock or M.now_utc)().isoformat(timespec="seconds"),
            "latest_run": run_record(folders[0]) if folders else None, "timezone": M.ZONE}
