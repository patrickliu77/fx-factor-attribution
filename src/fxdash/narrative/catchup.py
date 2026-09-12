"""A dated late-day briefing, distinct from the frozen 09:00 morning edition.

One generation claim per NY weekday. Inputs and drafts survive interruptions;
publication retries use the frozen text. No attribution calculation runs here.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time
from pathlib import Path
import uuid

from . import morning as M, briefing_archive as A

PAIRS = {"USDEUR", "USDJPY", "USDCAD", "USDNOK", "USDAUD", "USDMXN"}
READY = {"ready", "numbers_only"}


def due(moment):
    local = M.local_time(moment)
    return local.weekday() < 5 and local.time() >= time(9, 5)


def packet_checks(packet, moment):
    """Late evidence is allowed only with its real same-day observation times."""
    local = M.local_time(moment)
    try:
        observed = datetime.fromisoformat(packet["attribution_observed_at"])
        fetched = datetime.fromisoformat(packet["fetched_at"])
        as_of = date.fromisoformat(packet["as_of"])
        if not (observed.tzinfo and fetched.tzinfo and observed <= fetched <= moment
                and M.local_time(observed).date() == M.local_time(fetched).date() == local.date()):
            return ["invalid_observation_times"]
        if not (date.fromisoformat(M.previous_session(local.date())) <= as_of <= local.date()):
            return ["waiting_for_attribution"]
        rows = packet["pairs"]
        if len(rows) != 6 or {r["pair"] for r in rows} != PAIRS or any(r["date"] != packet["as_of"] for r in rows):
            return ["waiting_for_attribution"]
        for slate in packet["slates"].values():
            for item in slate.get("items", []):
                stamp = datetime.fromisoformat(item["observed_at"])
                if not stamp.tzinfo or not observed <= stamp <= fetched:
                    return ["invalid_source_times"]
    except (KeyError, ValueError, TypeError, AttributeError):
        return ["invalid_packet"]
    return []


def _publish(root, edition, repo, publisher, clock, kind):
    public = A.read_edition(root / "edition.json", mode=kind)
    if public["state"] not in READY:
        raise M.FrozenEditionError("Saved briefing is unreadable; left unchanged.")
    previous = M.read_json(root / "publish.json")
    if A.receipt(root, public)["state"] == "published":
        return {"state": "already_available", "date": edition["date"], "kind": kind}
    attempts = previous.get("attempts", 0)
    attempts = attempts if type(attempts) is int and attempts >= 0 else 0
    result = {"state": "publishing", "date": edition["date"], "kind": kind,
              "attempts": attempts + 1, "started_at": clock().isoformat(),
              "edition_hash": M.digest(edition), "edition_state": edition["state"]}
    M.atomic_json(root / "publish.json", result)
    if kind == "catchup":
        status = M.read_json(root / "status.json")
        M.atomic_json(root / "status.json", {**status, "state": "publishing",
                                            "observed_at": clock().isoformat()})
    try:
        publisher(repo)
        result.update(state="published", finished_at=clock().isoformat())
    except Exception as exc:
        result.update(state="publish_failed", error=type(exc).__name__, finished_at=clock().isoformat())
    M.atomic_json(root / "publish.json", result)
    return result


def _run(output_dir, repo, day, *, clock, collector, client_factory, snapshot_factory, publisher):
    moment = clock()
    if not due(moment) or M.local_time(moment).date().isoformat() != day:
        return {"state": "idle"}
    morning = output_dir / "briefing" / "days" / day
    original = morning / "edition.json"
    if original.exists() and A.read_edition(original)["state"] in READY:
        return _publish(morning, M.read_json(original), repo, publisher, clock, "edition")
    if M.slot(moment) == "publish":
        # Give eligible saved morning evidence its original publication route.
        if M.packet_eligible(M.read_json(morning / "packet.json"), moment)[0]:
            return {"state": "waiting_for_morning", "date": day}

    root = output_dir / "briefing" / "catchup" / day
    destination = root / "edition.json"
    if destination.exists():
        return _publish(root, M.read_json(destination), repo, publisher, clock, "catchup")

    claim_path = root / "generation.claim"
    if claim_path.exists():
        packet = M.read_json(root / "packet.json")
        claim = M.read_json(claim_path)
        if packet_checks(packet, moment) or claim.get("packet_hash") != M.digest(packet):
            return {"state": "archive_unreadable", "date": day}
        draft = M.read_json(root / "draft.json")
        notes = draft.get("notes", []) if draft.get("packet_hash") == M.digest(packet) else []
        warnings = draft.get("warnings", []) if draft else ["generation_interrupted_saved_figures_used"]
    else:
        from ..web.store import Snapshot
        from ..web.drivers import collect
        from .driver_notes import generate
        snapshot = (snapshot_factory or Snapshot)(output_dir)
        # Do not start retrieval or a model call while yesterday's quant job is
        # still catching up after login. The regular task can retry later.
        if not M.previous_session(M.local_time(moment).date()) <= snapshot.date_last <= day:
            return {"state": "waiting_for_attribution", "date": day}
        observed = clock()
        packet = (collector or collect)(snapshot, clock=clock)
        packet["attribution_observed_at"] = observed.isoformat(timespec="seconds")
        packet["input_archive"] = snapshot.manifest.get("input_archive")
        packet["edition_date"] = day
        warnings = packet_checks(packet, clock())
        if warnings:
            return {"state": warnings[0], "date": day}
        slates = packet["slates"]
        if slates and all(s.get("error") for s in slates.values()):
            return {"state": "waiting_for_news", "date": day}
        M.atomic_json(root / "packet.json", packet)
        # Persist before any optional paid call. A restart never repeats it.
        M.atomic_json(claim_path, {"started_at": clock().isoformat(), "packet_hash": M.digest(packet)})
        try:
            notes = generate(packet, client_factory())
        except Exception as exc:
            notes, warnings = [], ["generation_failed_saved_figures_used"]
        M.atomic_json(root / "draft.json", {"packet_hash": M.digest(packet), "notes": notes,
                                           "finished_at": clock().isoformat(), "warnings": warnings})
    finished = clock()
    if M.local_time(finished).date().isoformat() != day:
        return {"state": "day_changed", "date": day}
    edition = M.compose_edition(packet, notes, moment=finished, mode="catchup", warnings=warnings)
    edition.update(scheduled=False, late_publication=True,
                   morning_target=M.cutoff(finished).isoformat(),
                   target_cutoff=packet["fetched_at"], catchup_reason="morning_text_unavailable")
    edition.pop("target_edition", None)
    M.atomic_json(destination, edition)
    return _publish(root, edition, repo, publisher, clock, "catchup")


def run(output_dir, repo, *, clock=M.now_utc, collector=None, client_factory=M.make_client,
        snapshot_factory=None, publisher=None, invocation_source="manual"):
    moment = clock()
    if not due(moment):
        return {"state": "idle"}
    if invocation_source not in {"manual", "scheduled_task"}:
        raise ValueError("invalid_invocation_source")
    from .morning_dispatch import publish_site
    day = M.local_time(moment).date().isoformat()
    output_dir = Path(output_dir)
    try:
        # Share the original dispatch lock to avoid racing morning publication.
        with M.DayLock(output_dir / "briefing" / "days" / day / "dispatch.lock"):
            ledger = output_dir / "briefing" / "catchup" / day / "invocations"
            run_id = moment.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
            started = {"run_id": run_id, "source": invocation_source, "date": day,
                       "started_at": moment.isoformat(), "state": "started"}
            M.atomic_json(ledger / (run_id + ".start.json"), started)
            M.atomic_json(output_dir / "briefing" / "catchup" / day / "status.json",
                          {"state": "preparing", "source": invocation_source, "date": day,
                           "started_at": moment.isoformat(), "observed_at": moment.isoformat()})
            try:
                result = _run(output_dir, repo, day, clock=clock, collector=collector,
                              client_factory=client_factory, snapshot_factory=snapshot_factory,
                              publisher=publisher or publish_site)
            except Exception as exc:
                result = {"state": "catchup_failed", "date": day, "error": type(exc).__name__}
            except BaseException:
                M.atomic_json(ledger / (run_id + ".finish.json"), dict(started,
                              state="exception", finished_at=clock().isoformat()))
                raise
            observation = {**result, "source": invocation_source,
                           "started_at": moment.isoformat(), "observed_at": clock().isoformat()}
            M.atomic_json(output_dir / "briefing" / "catchup" / day / "status.json", observation)
            kind = result.get("kind", "catchup")
            saved = output_dir / "briefing" / ("days" if kind == "edition" else "catchup") / day / "edition.json"
            edition = A.read_edition(saved, mode=kind) if saved.exists() else {}
            M.atomic_json(ledger / (run_id + ".finish.json"), dict(started, state=result["state"],
                          finished_at=clock().isoformat(), edition_hash=edition.get("edition_hash")))
            return result
    except M.Busy:
        return {"state": "busy", "date": day}


def main(argv=None):
    import json
    from ..config import OUTPUT_DIR, REPO_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scheduled-task", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"due": due(M.now_utc()), "timezone": M.ZONE}))
        return 0
    result = run(args.output_dir, REPO_ROOT,
                 invocation_source="scheduled_task" if args.scheduled_task else "manual")
    if result["state"] != "idle":
        print(json.dumps(result, ensure_ascii=True))
    return 1 if result["state"] in {"catchup_failed", "publish_failed", "archive_unreadable"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
