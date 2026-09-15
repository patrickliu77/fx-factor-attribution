"""Portable checkpoint sequencing, with all execution ports explicitly injected.

There are deliberately no live adapters, environment activation switches or CLI.
The default shadow mode cannot call the publisher, public verifier or sender.
Quantitative implementations and evidence-cutoff validation belong to the ports;
this module only owns ordering, persistence and the outward-delivery clock gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import time
from typing import Callable, Mapping

from ..narrative import morning as M
from .journal import Journal, operation_key
from .state import StateError, save_artifact, read_artifact

STAGES = ("inputs", "quant", "news", "recap", "audio-en", "audio-zh", "build")


@dataclass(frozen=True)
class Ports:
    stages: Mapping[str, Callable]
    publish: Callable | None = None
    verify: Callable | None = None
    email: Callable | None = None


def delivery_day(day, moment):
    local = M.local_time(moment)
    return local.weekday() < 5 and local.date().isoformat() == day and local.time() >= time(9)


def run(journal: Journal, day, seed: bytes, ports: Ports, *, mode="shadow", clock=M.now_utc, owned=False):
    operation_key("inputs", day)
    if (mode not in {"shadow", "delivery"} or set(ports.stages) != set(STAGES)
            or not all(callable(p) for p in ports.stages.values())
            or (mode == "delivery" and not all(callable(p) for p in (ports.publish, ports.verify, ports.email)))):
        raise StateError("invalid_pipeline_ports")
    local = M.local_time(clock())
    if local.weekday() >= 5 or local.date().isoformat() != day:
        return {"state": "outside_run_day", "mode": mode}
    with (nullcontext() if owned else journal.session()):
        journal._owned()
        hashes = {"seed": save_artifact(journal.store, seed)}
        context = {"seed": seed}

        def step(name, action):
            fingerprint = M.digest(hashes)

            def execute():
                body = action(dict(context))
                return {"artifact_sha256": save_artifact(journal.store, body)}

            receipt = journal.run_once(name, day, "edition", fingerprint, execute)
            digest = receipt["artifact_sha256"]
            # Lost artifacts never trigger a second paid generation.
            context[name] = read_artifact(journal.store, digest)
            hashes[name] = digest

        for stage in STAGES:
            step(stage, ports.stages[stage])
            if M.local_time(clock()).date().isoformat() != day:
                return {"state": "day_changed", "mode": mode}
        if mode == "shadow":
            return {"state": "shadow_ready", "mode": mode, "artifacts": hashes, "published": False, "email_sent": False}
        if not delivery_day(day, clock()):
            return {"state": "waiting_for_delivery_window", "mode": mode}
        step("publish", ports.publish)
        journal._owned()
        # This probe runs on every retry. A past green probe cannot authorize mail.
        try:
            verified = ports.verify(dict(context)) is True
        except Exception:
            return {"state": "public_probe_unavailable", "mode": mode}
        if not verified:
            return {"state": "waiting_for_public_verification", "mode": mode}
        if not delivery_day(day, clock()):
            return {"state": "outside_delivery_day", "mode": mode}
        journal._owned()
        # The email port must use deliver_guarded, which adds per-language claims
        # and repeats the age/date checks before each provider request.
        try:
            result = ports.email(dict(context), journal)
        except Exception:
            raise StateError("email_delivery_unconfirmed") from None
        if not isinstance(result, dict) or result.get("state") not in {"submitted", "attention_required"}:
            raise StateError("email_delivery_incomplete")
        if result["state"] == "submitted" and not journal.email_submitted(day):
            raise StateError("durable_email_receipts_missing")
        return {"state": result["state"], "mode": mode, "scope": "provider_submission_not_inbox_receipt"}
