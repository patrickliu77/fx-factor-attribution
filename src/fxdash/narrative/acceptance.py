"""Read-only acceptance of actual morning dispatch records, no model/network calls.

Five consecutive due weekdays must have scheduled entry records, eligible inputs,
an on-time frozen edition and a matching push receipt. A push is not proof of
GitHub Pages delivery. Numbers-only and event-context coverage are reported apart.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import morning as M
from .briefing_archive import read_edition, receipt


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def _digest(value):
    try:
        return M.digest(value)
    except (TypeError, ValueError):
        return None


def dispatch_records(root, day):
    """Join ledger records by both filename and identity; corrupt rows stay visible."""
    matched, issues, unfinished = [], [], 0
    starts = {p.name.removesuffix(".start.json"): p for p in root.glob("*.start.json")}
    ends = {p.name.removesuffix(".finish.json"): p for p in root.glob("*.finish.json")}
    identity = ("run_id", "date", "source", "action", "started_at")
    for key, path in starts.items():
        record = M.read_json(path)
        stamp = _time(record.get("started_at"))
        if not (record.get("run_id") == key and record.get("date") == day and stamp
                and M.local_time(stamp).date().isoformat() == day and record.get("state") == "started"
                and record.get("source") in ("manual", "scheduled_task")
                and record.get("action") in ("prepare", "publish")):
            issues.append({"file": path.name, "reason": "invalid_start_record"})
            continue
        if key not in ends:
            unfinished += 1
            continue
        finished = M.read_json(ends[key])
        completed = _time(finished.get("finished_at"))
        if not (all(record.get(k) == finished.get(k) for k in identity) and completed
                and completed >= stamp and M.local_time(completed).date().isoformat() == day
                and finished.get("state") in ("prepared", "already_prepared", "prepare_failed", "ineligible_packet",
                     "published", "already_published", "publish_failed", "finalize_failed", "busy", "exception")):
            issues.append({"file": ends[key].name, "reason": "invalid_finish_record"})
            continue
        matched.append({**record, "result": finished["state"], "finished_at": completed})
    for key in ends.keys() - starts.keys():
        issues.append({"file": ends[key].name, "reason": "finish_without_start"})
    return matched, issues, unfinished


def assess_day(output_dir, day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("invalid_edition_date")
    root = Path(output_dir) / "briefing" / "days" / day
    end = datetime.fromisoformat(day + "T09:00:00").replace(tzinfo=ZoneInfo(M.ZONE))
    joined, issues, unfinished = dispatch_records(root / "dispatch", day)
    matched = [r for r in joined if r["source"] == "scheduled_task"]
    pre = [r for r in matched if r.get("action") == "prepare"
           and (stamp := _time(r.get("started_at"))) and end-timedelta(minutes=10) <= stamp < end
           and r["finished_at"] <= end
           and r["result"] == "prepared"]
    pub = [r for r in matched if r.get("action") == "publish"
           and (stamp := _time(r.get("started_at"))) and end <= stamp <= end+timedelta(minutes=5)
           and r["finished_at"] <= end+timedelta(minutes=5)
           and r["result"] == "published"]
    packet = M.read_json(root / "packet.json")
    try:
        eligible, reasons = M.packet_eligible(packet, end)
    except (AttributeError, TypeError, ValueError):
        eligible, reasons = False, ["invalid_packet"]
    raw = M.read_json(root / "edition.json")
    edition = read_edition(root / "edition.json") if raw else {}
    push = receipt(root, edition)
    generated = _time(raw.get("generated_at"))
    delivered = _time(push.get("finished_at"))
    pair_rows = packet.get("pairs")
    pair_rows = pair_rows if isinstance(pair_rows, list) else []
    pair_names = [r.get("pair") for r in pair_rows if isinstance(r, dict) and isinstance(r.get("pair"), str)]
    checks = {
        "dispatch_records_readable": not issues,
        "scheduled_preparation": bool(pre), "scheduled_publication": bool(pub),
        "ordered_dispatch_chain": any(p["finished_at"] <= _time(u["started_at"]) for p in pre for u in pub),
        "eligible_pre_cutoff_inputs": eligible,
        "all_six_pairs": len(pair_rows) == len(pair_names) == 6 and set(pair_names) == {"USDEUR", "USDJPY", "USDCAD", "USDNOK", "USDAUD", "USDMXN"},
        "readable_edition": edition.get("state") in {"ready", "numbers_only"},
        "packet_hash_matches": bool(packet) and bool(_digest(packet)) and raw.get("packet_hash") == _digest(packet)
                               and raw.get("evidence") == packet,
        "quant_input_archive_verified": verify_input_archive(output_dir, packet),
        "edition_on_time": bool(generated and end <= generated <= end+timedelta(minutes=2)),
        "matching_push": push["state"] == "published",
        "push_within_five_minutes": bool(delivered and generated and generated <= delivered <= end+timedelta(minutes=5)),
    }
    return {"date": day, "passed": all(checks.values()), "checks": checks,
            "edition_state": edition.get("state", "missing"),
            "context_included": edition.get("state") == "ready", "input_warnings": reasons,
            "unfinished_invocations": unfinished, "record_issues": issues,
            "public_pages_delivery": "not_checked"}


def verify_input_archive(output_dir, packet):
    from ..data.vintages import read_capture, sha, encoded
    ref = packet.get("input_archive")
    if not isinstance(ref, dict):
        return False
    try:
        root = Path(output_dir).resolve()
        path = (root / ref["manifest"]).resolve()
        if not path.is_relative_to(root / "input_archive" / "captures"):
            return False
        capture, _ = read_capture(path)
        stamp, observed = _time(capture.get("observed_at")), _time(packet.get("attribution_observed_at"))
        return bool(capture["kind"] == "engine_inputs" and ref["sha256"] == sha(encoded(capture))
                    and stamp and observed and stamp <= observed)
    except (OSError, KeyError, TypeError, ValueError, AttributeError, IndexError):
        return False


def assess(output_dir, *, start_date, clock=M.now_utc, required_days=5):
    if required_days < 1:
        raise ValueError("required_days_must_be_positive")
    observed = clock()
    local = M.local_time(observed)
    due = local.date() if (local.hour, local.minute) >= (9, 5) else local.date()-timedelta(days=1)
    day = date.fromisoformat(start_date)
    rows = []
    while day <= due:
        if day.weekday() < 5:
            rows.append(assess_day(output_dir, day.isoformat()))
        day += timedelta(days=1)
    streak = 0
    for row in rows:
        streak = streak+1 if row["passed"] else 0
    return {"schema_version": 1, "observed_at": observed.isoformat(), "start_date": start_date,
            "required_consecutive_weekdays": required_days, "consecutive_passes": streak,
            "state": "passed" if streak >= required_days else "collecting" if not rows else "not_yet_passed",
            "days": rows, "event_context_days": sum(r["context_included"] for r in rows),
            "scope": "scheduled local generation and push receipt; public delivery is separate"}


def main(argv=None):
    from ..config import OUTPUT_DIR
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, help="prospective acceptance start, YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--report", type=Path, help="optional new local JSON report")
    args = parser.parse_args(argv)
    result = assess(args.output_dir, start_date=args.start_date)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        # A report is an observation too; refuse to replace earlier evidence.
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
