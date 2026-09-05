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
                 "prompt_version", "validator_version", "generator", "schema_version")


def day_folders(output_dir):
    root = Path(output_dir) / "briefing" / "days"
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


def valid_edition(value, day):
    try:
        return (value.get("mode") == "edition" and value.get("date") == day
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


def unreadable(day):
    return {"available": True, "mode": "edition", "date": day, "state": "archive_unreadable",
            "text": {}, "notes": [], "warnings": ["archive_unreadable"]}


def read_edition(path):
    value = M.read_json(path)
    day = path.parent.name
    if not valid_edition(value, day):
        return unreadable(day)
    try:
        result = public_copy(value)
        result["edition_hash"] = M.digest(value)
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return unreadable(day)


def receipt(root, edition):
    raw = M.read_json(root / "publish.json")
    allowed = {"published", "publish_failed", "publishing", "finalize_failed"}
    result = {k: raw[k] for k in ("state", "started_at", "finished_at", "attempts") if k in raw}
    if result.get("state") not in allowed:
        result["state"] = "not_recorded"
    if result["state"] == "published" and (not edition.get("edition_hash")
                                         or raw.get("edition_hash") != edition["edition_hash"]):
        result["state"] = "receipt_mismatch"
    return result


def run_record(root):
    preparation = M.read_json(root / "prepare.json")
    claim = M.read_json(root / "prepare.claim")
    state = preparation.get("state")
    if state not in {"prepared", "prepare_failed", "ineligible_packet"}:
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
    current = history[0] if history else {}
    if not history:
        preview = M.read_json(Path(output_dir) / "briefing" / "driver-preview.json")
        if (preview.get("data_version") == data_version and preview.get("prompt_version") == PROMPT_VERSION
                and preview.get("validator_version") == VALIDATOR_VERSION):
            try:
                current = public_copy(preview)
            except (KeyError, TypeError, ValueError, AttributeError):
                current = {}
    return {"current": current, "history": history, "history_limit": HISTORY_LIMIT,
            "current_push": receipt(paths[0].parent, current) if paths else None,
            "total_editions": len(paths), "observed_at": (clock or M.now_utc)().isoformat(timespec="seconds"),
            "latest_run": run_record(folders[0]) if folders else None, "timezone": M.ZONE}
