"""A frozen 09:00 America/New_York text edition, independent of daily model runs.

prepare: capture at 08:50..08:59 ET, persist inputs before optional generation.
finalize: after 09:00, use only that morning's pre-cutoff packet. Never backfill
missing inputs from a fresh late search. No model or old narrative file is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = "America/New_York"


def now_utc():
    return datetime.now(timezone.utc)


def local_time(moment):
    if moment.tzinfo is None:
        raise ValueError("a timezone-aware time is required")
    return moment.astimezone(ZoneInfo(ZONE))


def slot(moment) -> str:
    local = local_time(moment)
    if local.weekday() >= 5:
        return "idle"
    if time(8, 50) <= local.time() < time(9):
        return "prepare"
    if time(9) <= local.time() < time(10):
        return "publish"
    return "idle"


def previous_session(day):
    # FX weekday calendar, not the US equity exchange holiday calendar.
    prior = day - timedelta(days=1)
    while prior.weekday() >= 5:
        prior -= timedelta(days=1)
    return prior.isoformat()


def cutoff(moment):
    local = local_time(moment)
    return datetime.combine(local.date(), time(9), ZoneInfo(ZONE))


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Busy(RuntimeError):
    pass


class DayLock:
    """OS-backed nonblocking lock, released even if the worker is terminated."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            raise Busy("day already claimed") from exc
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def make_client():
    from .client import GeminiClient
    try:
        return GeminiClient(timeout=45)
    except Exception:
        return None


def prepare(output_dir: Path, *, clock=now_utc, collector=None, client_factory=make_client):
    started = clock()
    if slot(started) != "prepare":
        raise ValueError("prepare is only allowed before the morning cutoff on weekdays")
    day = local_time(started).date().isoformat()
    root = output_dir / "briefing" / "days" / day
    if (root / "packet.json").exists() or (root / "prepare.claim").exists():
        return {"state": "already_prepared", "date": day}
    with DayLock(root / "prepare.lock"):
        if (root / "prepare.claim").exists():
            return {"state": "already_prepared", "date": day}
        # Persistent attempt marker limits spend even if generation is interrupted.
        atomic_json(root / "prepare.claim", {"started_at": started.isoformat()})
        from .driver_notes import generate
        from ..web.drivers import collect
        from ..web.store import Snapshot
        try:
            snapshot = Snapshot(output_dir)
            observed = clock()
            packet = (collector or collect)(snapshot, clock=clock)
            packet["attribution_observed_at"] = observed.isoformat(timespec="seconds")
            packet["edition_date"] = day
            packet["target_cutoff"] = cutoff(started).isoformat(timespec="seconds")
            # Save evidence even when collection finishes after the cutoff.
            atomic_json(root / "packet.json", packet)
            eligible, reasons = packet_eligible(packet, started)
            notes = generate(packet, client_factory()) if eligible else []
            draft = {"date": day, "packet_hash": digest(packet), "notes": notes,
                     "finished_at": clock().isoformat(timespec="seconds"), "warnings": reasons}
            atomic_json(root / "draft.json", draft)
            result = {"state": "prepared" if eligible else "ineligible_packet", "date": day,
                      "published_notes": sum(n["published"] for n in notes), "warnings": reasons}
        except Exception as exc:
            result = {"state": "prepare_failed", "date": day, "error": type(exc).__name__}
        atomic_json(root / "prepare.json", result)
        return result


def packet_eligible(packet, moment):
    reasons = []
    local = local_time(moment)
    end = cutoff(moment)
    try:
        observed = datetime.fromisoformat(packet["attribution_observed_at"])
        fetched = datetime.fromisoformat(packet["fetched_at"])
        if any(t.tzinfo is None for t in (observed, fetched)) or observed > fetched or fetched > end:
            reasons.append("inputs_after_cutoff")
        if local_time(observed).date() != local.date() or local_time(fetched).date() != local.date():
            reasons.append("inputs_not_from_this_morning")
        expected = previous_session(local.date())
        if packet["as_of"] != expected or any(r.get("date") != expected for r in packet["pairs"]):
            reasons.append("previous_session_attribution_unavailable")
        if not packet["pairs"]:
            reasons.append("no_attribution")
        for slate in packet["slates"].values():
            for item in slate.get("items", []):
                stamp = datetime.fromisoformat(item["observed_at"])
                if stamp.tzinfo is None or stamp > fetched:
                    reasons.append("source_time_invalid")
    except (KeyError, TypeError, ValueError):
        reasons.append("invalid_packet")
    return not reasons, sorted(set(reasons))


def public_notes(records):
    # Never trust a stored published boolean alone when promoting a draft.
    return [{"pair": r["pair"], "note": r["raw"], "sources": r["sources"]}
            for r in records if r.get("published")]


def observation_plan(pair, factor, lang):
    """Code-owned evidence checks, not model-generated policy counterfactuals."""
    loo = factor in ("DOLLAR_LOO", "CARRY_LOO")
    if lang == "zh":
        return {
            "watch_summary": "后续核对报道的事件时间与独立来源；更正或相反证据会削弱这条解读。",
            "condition": "如果后续资料能够确认所引报道的事件时间与该归因日期相符，",
            "supports": (f"独立资料在 {factor} 的其他货币成员中也记录到相关变化。{pair} 已从该因子排除。"
                         if loo else f"独立资料在 {factor} 对应市场中记录到相关变化。"),
            "weakens": "报道被更正、事件实际发生在其他时期，或独立资料给出相反观测。仅有相同标题的转载无法补足证据。",
        }
    return {
        "watch_summary": "Check the event date and independent sources; corrections or conflicting evidence would weaken this reading.",
        "condition": "If later evidence confirms that the cited event belongs to the attribution date,",
        "supports": (f"Independent observations of related changes among the other currencies in {factor}. {pair} is excluded."
                     if loo else f"Independent observations of related changes in the market measured by {factor}."),
        "weakens": "A correction, a different event date, or conflicting observations. Copies of the same headline add no independent evidence.",
    }


def compose_edition(packet, notes, *, moment, mode="edition", warnings=()):
    from .briefing import build_preview
    from .driver_notes import PROMPT_VERSION, VALIDATOR_VERSION, definitions, source_set, validate
    base = build_preview(packet)
    records = []
    rows = {r["pair"]: r for r in packet["pairs"]}
    for record in notes if isinstance(notes, list) else []:
        if not isinstance(record, dict) or record.get("prompt_version") != PROMPT_VERSION:
            continue
        row = rows.get(record.get("pair"))
        if row and record.get("published") and not validate(
                record.get("raw"), row, source_set(packet, row), packet["fetched_at"]):
            if record["raw"]["assessment"] != "insufficient_evidence":
                records.append(dict(record, sources=source_set(packet, row)))
    linked = public_notes(records)
    for item in linked:
        item["definition"] = definitions(item["pair"]).get(item["note"]["factor"])
        item["checks"] = {lang: observation_plan(item["pair"], item["note"]["factor"], lang)
                          for lang in ("en", "zh")}
    # Numbers are code-rendered. Only one checked event/watch goes into the short
    # read; complete pair notes, source excerpts and caveats appear below it.
    for lang in ("en", "zh"):
        # build_preview's generic final sentence is replaced with a checked watch.
        text = base["text"][lang]
        generic = ("Check the leading factor's new releases" if lang == "en" else "接下来可把主要因子")
        text = text.split(generic)[0].strip()
        if linked:
            note = linked[0]["note"][lang]
            text += (" " if lang == "en" else "") + linked[0]["pair"].replace("USD", "USD/") + ": " + note["event"]
            text += (" " if lang == "en" else "") + linked[0]["checks"][lang]["watch_summary"]
        base["text"][lang] = text
    base.update(mode=mode, state="ready" if linked else "numbers_only",
                date=local_time(moment).date().isoformat(),
                generated_at=moment.isoformat(timespec="seconds"),
                target_cutoff=cutoff(moment).isoformat(timespec="seconds"),
                generator="saved_facts_with_source_checked_context", prompt_version=PROMPT_VERSION, notes=linked,
                validator_version=VALIDATOR_VERSION,
                warnings=list(base["warnings"])+list(warnings), evidence=packet,
                packet_hash=digest(packet))
    if not linked:
        base["warnings"].append("No driver commentary passed verification; saved figures remain available.")
    return base


def finalize(output_dir: Path, *, clock=now_utc):
    moment = clock()
    if slot(moment) != "publish":
        raise ValueError("editions are finalized between 09:00 and 10:00 ET on weekdays")
    day = local_time(moment).date().isoformat()
    root = output_dir / "briefing" / "days" / day
    destination = root / "edition.json"
    if destination.exists():
        return read_json(destination)
    with DayLock(root / "finalize.lock"):
        if destination.exists():
            return read_json(destination)
        packet = read_json(root / "packet.json")
        eligible, reasons = packet_eligible(packet, moment)
        if eligible:
            draft = read_json(root / "draft.json")
            notes = draft.get("notes", []) if draft.get("packet_hash") == digest(packet) else []
            if not notes:
                reasons.append("checked_draft_unavailable")
            edition = compose_edition(packet, notes, moment=moment, warnings=reasons)
        else:
            edition = {"available": True, "mode": "edition", "date": day,
                       "state": "inputs_unavailable", "notes": [], "text": {},
                       "attribution_as_of": packet.get("as_of"),
                       "news_observed_by": packet.get("fetched_at"),
                       "generated_at": moment.isoformat(timespec="seconds"),
                       "target_cutoff": cutoff(moment).isoformat(timespec="seconds"),
                       "warnings": reasons, "evidence": packet}
        edition["scheduled"] = True
        edition["late_publication"] = moment > cutoff(moment)+timedelta(minutes=2)
        atomic_json(destination, edition)
        return edition


def read_latest(output_dir, data_version=None):
    from .driver_notes import PROMPT_VERSION, VALIDATOR_VERSION
    root = Path(output_dir) / "briefing"
    editions = sorted(root.glob("days/????-??-??/edition.json"), reverse=True)
    if editions:
        result = read_json(editions[0])
    else:
        result = read_json(root / "driver-preview.json")
        if (result.get("data_version") != data_version or result.get("prompt_version") != PROMPT_VERSION
                or result.get("validator_version") != VALIDATOR_VERSION):
            return {}
    return {k: v for k, v in result.items() if k != "evidence"}


def preview(output_dir, *, clock=now_utc, client_factory=make_client):
    from ..web.drivers import collect
    from ..web.store import Snapshot
    from .driver_notes import generate
    snapshot = Snapshot(output_dir)
    observed = clock()
    packet = collect(snapshot, clock=clock)
    packet["attribution_observed_at"] = observed.isoformat(timespec="seconds")
    notes = generate(packet, client_factory())
    edition = compose_edition(packet, notes, moment=clock(), mode="preview")
    edition["scheduled"] = False
    audit = {"edition": edition, "records": notes}
    stamp = observed.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    atomic_json(output_dir / "briefing" / "validation" / (stamp+".json"), audit)
    atomic_json(output_dir / "briefing" / "driver-preview.json", edition)
    return edition


def main(argv=None):
    from ..config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "finalize", "preview"), required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    result = {"prepare": prepare, "finalize": finalize, "preview": preview}[args.mode](args.output_dir)
    print(json.dumps({"mode": args.mode, "state": result.get("state"), "date": result.get("date"),
                      "published_notes": result.get("published_notes", len(result.get("notes", [])))}, ensure_ascii=True))
    return 1 if result.get("state") == "prepare_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
