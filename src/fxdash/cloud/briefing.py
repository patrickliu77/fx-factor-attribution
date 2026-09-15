"""News and recap ports using the existing production collectors and validators.

These two ports do not run quant, render audio, install an edition, publish or
send mail. They require a pending cloud claim from pipeline.run, and return
private immutable artifacts. Model use is an explicit opt-in, never an env flag.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from ..narrative import morning as M, catchup as C, briefing_archive as A
from .journal import operation_key, unique_object
from .pipeline import STAGES
from .snapshot import linked
from .state import StateError, MAX_BYTES

MAX_PACKET = 2 * 1024 * 1024


def encode(value):
    try:
        body = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode()
        if len(body) > MAX_PACKET:
            raise ValueError()
        return body
    except (TypeError, ValueError):
        raise StateError("invalid_briefing_artifact") from None


def decode(body):
    try:
        if not isinstance(body, bytes) or len(body) > MAX_PACKET:
            raise ValueError()
        result = json.loads(body, object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError, RecursionError):
        raise StateError("invalid_briefing_artifact") from None


def packet_mode(packet, moment, day):
    try:
        if C.packet_checks(packet, moment) or packet.get("edition_date", day) != day:
            raise ValueError()
        mode = "edition" if M.packet_eligible(packet, moment)[0] else "catchup"
        if mode == "catchup" and moment < M.cutoff(moment):
            raise ValueError()
        return mode
    except (KeyError, TypeError, ValueError, AttributeError):
        raise StateError("briefing_evidence_invalid") from None


class _ClaimedClient:
    """Recheck ownership and day before every bounded model request."""
    def __init__(self, client, check):
        self._client, self._check = client, check

    def __getattr__(self, name):
        return getattr(self._client, name)

    def complete(self, *args):
        self._check()
        return self._client.complete(*args)


class BriefingPorts:
    def __init__(self, journal, day, output_dir, *, clock=M.now_utc, use_model=False, cache_dir=None):
        operation_key("recap", day)
        if type(use_model) is not bool or not callable(clock):
            raise StateError("invalid_briefing_settings")
        root = Path(output_dir).absolute()
        if any(linked(p) for p in (root, *root.parents)):
            raise StateError("briefing_path_linked")
        self.journal, self.day, self.output_dir = journal, day, root.resolve()
        self.clock, self.use_model = clock, use_model
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def _moment(self):
        moment = self.clock()
        local = M.local_time(moment)
        if local.weekday() >= 5 or local.date().isoformat() != self.day:
            raise StateError("outside_briefing_day")
        return moment

    def _claimed(self, stage, context):
        expected = {"seed", *STAGES[:STAGES.index(stage)]}
        if (not isinstance(context, dict) or set(context) != expected
                or not all(isinstance(v, bytes) and len(v) <= MAX_BYTES for v in context.values())):
            raise StateError("invalid_briefing_context")
        fingerprint = M.digest({key: hashlib.sha256(value).hexdigest() for key, value in context.items()})
        _, value = self.journal._owned()
        claim = value["claims"].get(operation_key(stage, self.day))
        if not claim or claim["state"] != "pending" or claim["fingerprint"] != fingerprint:
            raise StateError("briefing_claim_required")
        return self._moment()

    def _legacy(self, moment):
        saved = []
        for kind, mode, marker in (("days", "edition", "prepare.claim"),
                                    ("catchup", "catchup", "generation.claim")):
            row = {"mode": mode, "marker": marker}
            for name in ("edition.json", "packet.json", "draft.json", marker):
                path = self.output_dir / "briefing" / kind / self.day / name
                # All segments are code-owned except the validated ISO day.
                if any(linked(p) for p in (path, *path.parents)):
                    raise StateError("briefing_path_linked")
                if path.exists():
                    if not path.is_file() or path.stat().st_size > MAX_PACKET:
                        raise StateError("legacy_briefing_unreadable")
                    row[name] = decode(path.read_bytes())
            if len(row) > 2:
                saved.append(row)
        # Frozen editions take priority over any old in-progress preparation.
        for row in saved:
            edition = row.get("edition.json")
            if edition is None:
                continue
            if not A.valid_edition(edition, self.day, mode=row["mode"]):
                raise StateError("legacy_briefing_unreadable")
            if edition["state"] not in C.READY:
                continue
            packet = edition.get("evidence", {})
            mode = packet_mode(packet, moment, self.day)
            if (mode != row["mode"] or edition.get("packet_hash") != M.digest(packet)
                    or datetime.fromisoformat(edition["generated_at"]) > moment):
                raise StateError("legacy_briefing_unreadable")
            A.public_copy(edition)  # Recheck that the existing public reader can consume it.
            return {"packet": packet, "mode": mode, "frozen": edition, "records": [], "legacy": True}
        if not saved:
            return None
        # Prefer the explicitly saved catch-up attempt. Never start another model
        # request to replace an incomplete legacy attempt from either route.
        row = saved[-1]
        packet, draft, claim = (row.get(k) for k in ("packet.json", "draft.json", row["marker"]))
        if not all(isinstance(v, dict) for v in (packet, draft, claim)):
            raise StateError("legacy_generation_needs_review")
        mode = packet_mode(packet, moment, self.day)
        stamp = datetime.fromisoformat(claim.get("started_at", ""))
        if (stamp.tzinfo is None or M.local_time(stamp).date().isoformat() != self.day or stamp > moment
                or draft.get("packet_hash") != M.digest(packet) or not isinstance(draft.get("notes"), list)
                or (row["mode"] == "catchup" and claim.get("packet_hash") != M.digest(packet))):
            raise StateError("legacy_generation_needs_review")
        return {"packet": packet, "mode": mode, "frozen": None, "records": draft["notes"], "legacy": True}

    def news(self, context):
        moment = self._claimed("news", context)
        try:
            result = self._legacy(moment)
            if result is None:
                from ..web.store import Snapshot
                from ..web.drivers import collect
                from ..narrative.release_calendar import attach
                snapshot = (Snapshot(self.output_dir, cache_dir=self.cache_dir) if self.cache_dir is not None
                            else Snapshot(self.output_dir))
                if not M.previous_session(M.local_time(moment).date()) <= snapshot.date_last <= self.day:
                    raise StateError("waiting_for_attribution")
                observed = self._moment()
                packet = collect(snapshot, clock=self.clock)
                packet.update(attribution_observed_at=observed.isoformat(timespec="seconds"),
                              edition_date=self.day, input_archive=snapshot.manifest.get("input_archive"))
                attach(packet, self.output_dir, clock=self.clock)
                mode = packet_mode(packet, self._moment(), self.day)
                slates = packet["slates"]
                if not slates or all(s.get("error") for s in slates.values()):
                    raise StateError("waiting_for_news")
                result = {"packet": packet, "mode": mode, "frozen": None, "records": None, "legacy": False}
            return encode({"schema": "cloud-news-v1", **result})
        except StateError:
            raise
        except Exception:
            raise StateError("briefing_collection_failed") from None

    def recap(self, context):
        moment = self._claimed("recap", context)
        selected = decode(context["news"])
        required = {"schema", "packet", "mode", "frozen", "records", "legacy"}
        if (set(selected) not in (required, required | {"workspace_sha256"})
                or selected["schema"] != "cloud-news-v1" or type(selected["legacy"]) is not bool
                or selected["mode"] != packet_mode(selected["packet"], moment, self.day)):
            raise StateError("invalid_briefing_selection")
        packet, mode, notes = selected["packet"], selected["mode"], selected["records"]
        edition = selected["frozen"]
        if edition is None:
            if selected["legacy"] and not isinstance(notes, list):
                raise StateError("legacy_generation_needs_review")
            if notes is None:
                from ..narrative.driver_notes import generate
                client = None
                if self.use_model:
                    from ..narrative.client import GeminiClient
                    # At most three requests total, no hidden transport retry.
                    client = _ClaimedClient(GeminiClient(timeout=45, max_requests=3, max_attempts=1),
                                            lambda: self._claimed("recap", context))
                notes = generate(packet, client, max_calls=3)
            moment = self._moment()
            packet_mode(packet, moment, self.day)
            edition = M.compose_edition(packet, notes, moment=moment, mode=mode)
            if mode == "edition":
                edition.update(scheduled=True, late_publication=moment > M.cutoff(moment) + timedelta(minutes=2))
            else:
                edition.update(scheduled=False, late_publication=True,
                    morning_target=M.cutoff(moment).isoformat(), target_cutoff=packet["fetched_at"],
                    catchup_reason="morning_text_unavailable")
                edition.pop("target_edition", None)
        if (not A.valid_edition(edition, self.day, mode=mode) or edition.get("packet_hash") != M.digest(packet)
                or edition.get("evidence") != packet):
            raise StateError("invalid_cloud_edition")
        A.public_copy(edition)
        return encode({"schema": "cloud-briefing-v1", "edition": edition, "records": notes})
