"""Read-only delivery evidence from actual use, without a clock-time or day quota.

Absent days are unobserved, not failed. Delivery integrity, automation provenance
and event-context coverage are independent. No old evidence is rewritten.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import morning as M, briefing_archive as A, catchup as C
from .acceptance import _time, _digest, dispatch_records, verify_input_archive

POLICY = "actual-use-v1"
WAITING = {"waiting_for_attribution", "waiting_for_news", "waiting_for_morning", "busy"}
FAILURES = {"catchup_failed", "publish_failed", "archive_unreadable", "exception", "day_changed"}
LEGACY_ERRORS = {"GenerationError", "RuntimeError", "TimeoutError", "ConnectionError", "HTTPError"}


def context_quality(folder, public, packet, observed):
    """Read legacy failures only from a draft bound to this edition's input packet."""
    warnings = public.get("warnings", [])
    failed = any(w in {"generation_requests_failed", "generation_failed_saved_figures_used",
                      "generation_interrupted_saved_figures_used"} for w in warnings)
    draft = M.read_json(folder / "draft.json")
    stamp, packet_hash = _time(draft.get("finished_at")), _digest(packet)
    records = draft.get("notes")
    valid = bool(packet and packet_hash and draft.get("packet_hash") == packet_hash
                 and stamp and stamp <= observed and isinstance(records, list)
                 and all(isinstance(r, dict) for r in records))
    rejected = 0
    if valid:
        for record in records:
            errors = record.get("errors", [])
            errors = errors if isinstance(errors, list) else []
            request_failed = bool(record.get("generation_failure")) or (
                record.get("attempted") is True and any(isinstance(e, str) and e in LEGACY_ERRORS for e in errors))
            failed |= request_failed
            rejected += bool(record.get("attempted") and not record.get("published") and errors and not request_failed)
    count = len(public.get("notes", []))
    state = "partial" if count and (failed or rejected) else "included" if count else "unavailable" if failed else "numbers_only"
    return {"verified_notes": count, "generation_failed": failed, "rejected_notes": rejected,
            "draft_evidence": "verified" if valid else "unreadable_or_mismatched" if (folder / "draft.json").exists() else "not_recorded",
            "state": state}


def invocations(root, day, observed):
    """Validate new catch-up ledgers; later checks cannot prove original automation."""
    starts = {p.name.removesuffix(".start.json"): p for p in root.glob("*.start.json")}
    ends = {p.name.removesuffix(".finish.json"): p for p in root.glob("*.finish.json")}
    records, issues, unfinished = [], [], 0
    identity = ("run_id", "date", "source", "started_at")
    for key, path in starts.items():
        start = M.read_json(path)
        stamp = _time(start.get("started_at"))
        if not (start.get("run_id") == key and start.get("date") == day and stamp
                and stamp <= observed and M.local_time(stamp).date().isoformat() == day
                and start.get("state") == "started" and isinstance(start.get("source"), str)
                and start["source"] in {"manual", "scheduled_task"}):
            issues.append({"file": path.name, "reason": "invalid_start_record"})
            continue
        if key not in ends:
            unfinished += 1
            continue
        finish = M.read_json(ends[key])
        completed = _time(finish.get("finished_at"))
        if not (all(finish.get(k) == start.get(k) for k in identity)
                and completed and stamp <= completed <= observed
                and isinstance(finish.get("state"), str)
                and finish["state"] in WAITING | FAILURES | {"published", "already_available", "idle"}):
            issues.append({"file": ends[key].name, "reason": "invalid_finish_record"})
            continue
        records.append(finish)
    issues.extend({"file": ends[key].name, "reason": "finish_without_start"} for key in ends.keys()-starts.keys())
    return records, issues, unfinished


def assess_day(output_dir, day, *, observed):
    root = Path(output_dir)
    morning = root / "briefing" / "days" / day
    late = root / "briefing" / "catchup" / day
    records, issues, unfinished = invocations(late / "invocations", day, observed)
    morning_records, morning_issues, morning_unfinished = dispatch_records(morning / "dispatch", day)
    issues += morning_issues
    unfinished += morning_unfinished
    status = M.read_json(late / "status.json")
    status_state = status.get("state")
    if (late / "status.json").exists() and (not isinstance(status_state, str)
            or status_state not in WAITING | FAILURES | {"preparing", "publishing", "published", "already_available", "idle"}):
        issues.append({"file": "status.json", "reason": "invalid_status_record"})
        status_state = None
    # A successful original morning can be reused by the login job. Prefer its
    # actual file when no late edition exists, never manufacture a new one.
    folder, mode = (late, "catchup") if (late / "edition.json").exists() else (morning, "edition")
    raw = M.read_json(folder / "edition.json")
    public = A.read_edition(folder / "edition.json", mode=mode) if raw else {}
    packet = M.read_json(folder / "packet.json")
    push = A.receipt(folder, public)
    generated, published = _time(raw.get("generated_at")), _time(push.get("finished_at"))
    packet_warnings = C.packet_checks(packet, generated) if generated else ["missing_or_invalid_edition_time"]
    checks = {
        "readable_edition": public.get("state") in C.READY,
        "current_complete_inputs": not packet_warnings,
        "packet_hash_matches": bool(packet) and bool(_digest(packet))
                               and raw.get("packet_hash") == _digest(packet) and raw.get("evidence") == packet,
        "quant_input_archive_verified": verify_input_archive(root, packet),
        "actual_edition_date": bool(generated and M.local_time(generated).date().isoformat() == day and generated <= observed),
        "matching_push": push["state"] == "published",
        "ordered_publication": bool(generated and published and generated <= published <= observed
                                    and M.local_time(published).date().isoformat() == day),
        "invocation_records_readable": not issues,
    }
    delivered = all(checks.values())
    # Original generation and a successful retry may be different invocations.
    # Whole-second packet timestamps are compared at their recorded precision.
    captured = _time(packet.get("attribution_observed_at"))
    origin = any(r["source"] == "scheduled_task" and r["state"] in {"published", "publish_failed"}
                 and r.get("edition_hash") == public.get("edition_hash") and generated and captured
                 and _time(r["started_at"]).replace(microsecond=0) <= captured <= generated <= _time(r["finished_at"])
                 for r in records)
    automatic = origin and any(r["source"] == "scheduled_task" and r["state"] == "published"
                               and r.get("edition_hash") == public.get("edition_hash") for r in records)
    # Morning provenance needs both original collection and publication, without
    # requiring a specific clock time. Repeated publication of manual text is not enough.
    pre = [r for r in morning_records if r["source"] == "scheduled_task" and r["action"] == "prepare" and r["result"] == "prepared"]
    pub = [r for r in morning_records if r["source"] == "scheduled_task" and r["action"] == "publish" and r["result"] == "published"]
    if mode == "edition" and generated and published:
        automatic |= any(p["finished_at"] <= _time(u["started_at"]) <= generated <= published <= u["finished_at"] <= observed
                         for p in pre for u in pub)
    failures = sorted({r["state"] for r in records if r["state"] in FAILURES})
    failures = set(failures)
    if status_state in FAILURES:
        failures.add(status_state)
    failures.update(r["result"] for r in morning_records if r["result"] in
                    {"prepare_failed", "publish_failed", "finalize_failed", "ineligible_packet", "exception"})
    if push["state"] in {"publish_failed", "finalize_failed"}:
        failures.add(push["state"])
    preparation = M.read_json(morning / "prepare.json").get("state")
    if isinstance(preparation, str) and preparation in {"prepare_failed", "ineligible_packet"}:
        failures.add(preparation)
    failures = sorted(failures)
    context = context_quality(folder, public, packet, observed)
    if not public:
        context["state"] = "unavailable"
    if delivered:
        state = "delivered"
    elif issues or failures or (folder / "edition.json").exists():
        state = "attention"
    elif status_state in WAITING:
        state = "waiting"
    elif unfinished or status_state in {"preparing", "publishing"}:
        state = "completion_unconfirmed"
    else:
        state = "observed_no_delivery"
    return {"date": day, "state": state, "passed": delivered, "checks": checks,
            "mode": mode if raw else None, "edition_state": public.get("state", "missing"),
            "generated_at": raw.get("generated_at"), "attribution_as_of": public.get("attribution_as_of"),
            "published_at": push.get("finished_at"), "push_state": push["state"],
            "automation": "confirmed" if delivered and automatic else "not_proven",
            "context_included": bool(context["verified_notes"]), "context": context,
            "waiting_reason": status.get("state") if state == "waiting" else None,
            "input_warnings": packet_warnings, "failures": failures, "record_issues": issues,
            "unfinished_invocations": unfinished, "public_pages_delivery": "not_checked"}


def assess(output_dir, *, start_date=None, clock=M.now_utc):
    observed = clock()
    end = M.local_time(observed).date().isoformat()
    if start_date is not None and date.fromisoformat(start_date).isoformat() != start_date:
        raise ValueError("invalid_start_date")
    days = set()
    for kind in ("days", "catchup"):
        for folder in A.day_folders(output_dir, kind):
            if folder.name > end or (start_date and folder.name < start_date):
                continue
            # Empty shared lock folders and outside-window clock observations do
            # not establish an active usage attempt, nor prove the PC was off.
            if any((folder / name).exists() for name in ("edition.json", "packet.json", "prepare.claim", "generation.claim", "status.json")) or any((folder / "dispatch").glob("*.json")) or any((folder / "invocations").glob("*.json")):
                days.add(folder.name)
    rows = [assess_day(output_dir, day, observed=observed) for day in sorted(days)]
    return {"schema_version": 1, "policy": POLICY, "observed_at": observed.isoformat(),
            "start_date": start_date, "state": rows[-1]["state"] if rows else "no_activity",
            "days": rows, "observed_days": len(rows), "delivered_days": sum(r["passed"] for r in rows),
            "automated_days": sum(r["automation"] == "confirmed" for r in rows),
            "attention_days": sum(r["state"] == "attention" for r in rows),
            "event_context_days": sum(r["context_included"] for r in rows),
            "generation_issue_days": sum(r["context"]["generation_failed"] for r in rows),
            "scope": "Actual saved delivery evidence; no fixed publication time or consecutive-day quota. Missing days are unobserved. Public delivery checked separately."}


def main(argv=None):
    import argparse
    import json
    from ..config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start-date")
    args = parser.parse_args(argv)
    print(json.dumps(assess(args.output_dir, start_date=args.start_date), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
