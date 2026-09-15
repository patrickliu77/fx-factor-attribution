"""Daily cloud controller, explicit bootstrap/cutover and safe command-line entry.

No resource provisioning. The default mode cannot publish or send. A delivery
run additionally requires a persisted operator cutover attestation.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from ..narrative import morning as M
from . import pipeline as P, workspace as W, preflight
from .briefing import encode, decode
from .journal import Journal, KEY as JOURNAL_KEY, operation_key, valid_hash
from .ports import ProductionPorts
from .snapshot import validate_archive
from .state import StateError, read_artifact, save_artifact

HEAD = "control/runtime-v1"


def read_head(store):
    row = store.read(HEAD)
    if row is None:
        raise StateError("cloud_bootstrap_required")
    value = decode(row.body)
    if (set(value) != {"schema", "latest_seed", "delivery_authorized", "active", "last_result", "cutover"}
            or type(value.get("schema")) is not int or value.get("schema") != 1 or not valid_hash(value.get("latest_seed"))
            or type(value.get("delivery_authorized")) is not bool
            or not isinstance(value.get("active"), (dict, type(None)))):
        raise StateError("cloud_head_corrupt")
    active = value["active"]
    if active:
        try:
            policy = active["policy"]
            if (date.fromisoformat(active["day"]).isoformat() != active["day"]
                    or not valid_hash(active["seed_sha256"])
                    or not re.fullmatch(r"[0-9a-f]{32}", active["owner"])
                    or any(active.get(k) is not None and (type(active[k]) is not int or active[k] <= 0)
                           for k in ("run_id", "run_attempt"))
                    or set(policy) != {"mode", "model", "speech", "replay_capture"}
                    or policy["mode"] not in {"shadow", "delivery"}
                    or any(type(policy[k]) is not bool for k in ("model", "speech"))):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise StateError("cloud_head_corrupt") from None
    if value["delivery_authorized"] and (not isinstance(value["cutover"], dict)
            or value["cutover"].get("local_sender_stopped") is not True
            or not valid_hash(value["cutover"].get("seed_sha256"))):
        raise StateError("cloud_cutover_record_corrupt")
    return row, value


def validate_seed(seed, parent):
    with W.restored(parent, seed) as root:
        if preflight.inspect(root)["state"] != "ready_for_shadow":
            raise StateError("bootstrap_seed_not_ready")


def bootstrap(store, seed, parent):
    if store.read(HEAD) is not None or store.read(JOURNAL_KEY) is not None:
        raise StateError("cloud_already_initialized")
    validate_seed(seed, parent)
    digest = save_artifact(store, seed)
    Journal.initialize(store)
    store.compare_and_swap(HEAD, None, encode({"schema": 1, "latest_seed": digest,
        "delivery_authorized": False, "active": None, "last_result": None, "cutover": None}))
    return {"state": "shadow_initialized", "delivery_authorized": False}


def authorize_delivery(store, seed, parent, *, local_sender_stopped=False, clock=M.now_utc):
    if local_sender_stopped is not True:
        raise StateError("local_sender_stop_attestation_required")
    validate_seed(seed, parent)
    moment = clock()
    day = M.local_time(moment).date().isoformat()
    journal = Journal(store, clock=clock)
    with journal.session():
        row, value = read_head(store)
        if value["delivery_authorized"]:
            raise StateError("cloud_already_authorized")
        # A shadow attempt earlier today must not be transformed into a second
        # daily generation using a different seed. Activate on a fresh run day.
        claims = journal._owned()[1]["claims"]
        if any(key.split("/")[1] >= day for key in claims):
            raise StateError("cutover_requires_fresh_run_day")
        digest = save_artifact(store, seed)
        value.update(latest_seed=digest, delivery_authorized=True, active=None,
            cutover={"attested_at": moment.isoformat(), "local_sender_stopped": True, "seed_sha256": digest})
        store.compare_and_swap(HEAD, row.version, encode(value))
    return {"state": "delivery_authorized", "attestation": "operator_supplied_not_remote_pc_verification"}


def promotable(journal, day, default):
    claims = journal._owned()[1]["claims"]
    for stage in ("audio-zh", "audio-en", "quant", "inputs"):
        row = claims.get(operation_key(stage, day))
        if row and row["state"] == "succeeded":
            digest = row["receipt"]["artifact_sha256"]
            # Only a verified private migration bundle can become tomorrow's seed.
            with zipfile.ZipFile(io.BytesIO(read_artifact(journal.store, digest))) as archive:
                validate_archive(archive)
            return digest
    return default


def run_day(store, parent, *, mode="shadow", model=False, speech=False, clock=M.now_utc,
            run_id=None, run_attempt=None, port_factory=ProductionPorts, replay_capture=None):
    local = M.local_time(clock())
    if local.weekday() >= 5:
        return {"state": "outside_run_day", "mode": mode}
    day = local.date().isoformat()
    if (mode not in {"shadow", "delivery"} or type(model) is not bool or type(speech) is not bool
            or (run_id is not None and (type(run_id) is not int or run_id <= 0))
            or (run_attempt is not None and (type(run_attempt) is not int or run_attempt <= 0))
            or (mode == "delivery" and (not speech or replay_capture is not None))):
        raise StateError("invalid_runtime_policy")
    policy = {"mode": mode, "model": model, "speech": speech, "replay_capture": replay_capture}
    journal = Journal(store, clock=clock)
    with journal.session():
        row, head = read_head(store)
        if mode == "delivery" and not head["delivery_authorized"]:
            raise StateError("cloud_cutover_not_authorized")
        active = head.get("active")
        if active and active["day"] > day:
            raise StateError("runtime_clock_reversed")
        if active and active["day"] == day:
            if active["policy"] != policy:
                raise StateError("daily_runtime_policy_changed")
        else:
            if active:
                head["latest_seed"] = promotable(journal, active["day"], head["latest_seed"])
            active = {"day": day, "seed_sha256": head["latest_seed"], "policy": policy}
        active.update(run_id=run_id, run_attempt=run_attempt, owner=journal.owner)
        head["active"] = active
        store.compare_and_swap(HEAD, row.version, encode(head))
        result = {"state": "runtime_failed", "mode": mode}
        try:
            ports = port_factory(journal, day, parent, model=model, speech=speech,
                                 clock=clock, replay_capture=replay_capture)
            result = P.run(journal, day, read_artifact(store, active["seed_sha256"]), ports.ports(),
                           mode=mode, clock=clock, owned=True)
            return result
        except Exception:
            raise StateError("cloud_run_needs_review") from None
        finally:
            # Even if news fails, preserve a completed quant run for tomorrow.
            # The original daily seed remains fixed for same-day stage replay.
            journal._owned()
            row, head = read_head(store)
            head["latest_seed"] = promotable(journal, day, head["latest_seed"])
            report = {"date": day, "state": result["state"], "mode": mode,
                      "observed_at": clock().isoformat(), "run_id": run_id}
            head["last_result"] = report
            store.compare_and_swap(HEAD, row.version, encode(head))
            key = "runs/" + day
            old = store.read(key)
            store.compare_and_swap(key, old.version if old else None, encode(report))


def health(store, *, clock=M.now_utc):
    moment = clock()
    local = M.local_time(moment)
    _, head = read_head(store)
    report = {"date": local.date().isoformat(), "observed_at": moment.isoformat(),
              "delivery_authorized": head["delivery_authorized"]}
    if not head["delivery_authorized"]:
        return {**report, "state": "shadow_only"}
    if local.weekday() >= 5 or local.time() < time(9, 30):
        return {**report, "state": "not_due"}
    row = store.read("runs/" + report["date"])
    saved = decode(row.body) if row else {}
    journal = Journal(store)._read()[1]
    claims = journal["claims"]
    receipts = [claims.get(operation_key(kind, report["date"], lang), {})
                for lang in ("en", "zh") for kind in ("email-create", "email-send")]
    complete = (all(r.get("state") == "succeeded" for r in receipts)
                and receipts[0].get("fingerprint") == receipts[1].get("fingerprint")
                and receipts[2].get("fingerprint") == receipts[3].get("fingerprint"))
    state = "submitted" if (saved.get("date") == report["date"] and saved.get("state") == "submitted"
        and saved.get("mode") == "delivery" and complete) else "attention_required"
    return {**report, "state": state, "last_run_state": saved.get("state", "missing_run"),
            "run_id": saved.get("run_id")}


def azure_store(*, local_operator=False):
    from .azure_blob import AzureBlobStore
    from .identity import GitHubStorageToken
    if local_operator:
        # Explicit one-time private seed upload/cutover from the operator's PC.
        # A scoped, short-lived token is provided in memory, never a SAS URL.
        return AzureBlobStore(os.environ.get("FXDASH_STORAGE_ACCOUNT"), os.environ.get("FXDASH_STORAGE_CONTAINER"))
    # Only the intended default branch can use the workflow's federated identity.
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "patrickliu77/fx-factor-attribution"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise StateError("trusted_actions_context_required")
    token = GitHubStorageToken(os.environ.get("AZURE_TENANT_ID"), os.environ.get("AZURE_CLIENT_ID"),
                              os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"))
    return AzureBlobStore(os.environ.get("FXDASH_STORAGE_ACCOUNT"), os.environ.get("FXDASH_STORAGE_CONTAINER"),
                          token_provider=token)


def check_settings(*, model=False, speech=False, delivery=False):
    """Local syntax/presence checks only, before claiming any daily work."""
    import shutil
    from .azure_blob import AzureBlobStore
    from .identity import GUID
    required = ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "FXDASH_STORAGE_ACCOUNT", "FXDASH_STORAGE_CONTAINER"]
    if model:
        required.append("GEMINI_API_KEY")
    if speech:
        required += ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"]
    if delivery:
        required += ["BREVO_API_KEY", "FXDASH_PUBLISH_TOKEN"]
    present = {k: bool(os.environ.get(k, "").strip()) for k in required}
    valid = all(present.values())
    probe = bool(shutil.which("ffprobe"))
    try:
        if not all(re.fullmatch(GUID, os.environ.get(k, "")) for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID")):
            valid = False
        AzureBlobStore(os.environ.get("FXDASH_STORAGE_ACCOUNT"), os.environ.get("FXDASH_STORAGE_CONTAINER"))
        if speech:
            from ..narrative.azure_speech import credentials
            credentials()
            valid = valid and probe
        if delivery:
            token = os.environ.get("FXDASH_PUBLISH_TOKEN", "")
            key = os.environ.get("BREVO_API_KEY", "")
            valid = bool(valid and re.fullmatch(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,240}", token)
                         and 10 <= len(key) <= 512 and key.strip("*")
                         and all(33 <= ord(c) <= 126 for c in key))
    except Exception:
        valid = False
    return {"state": "configured" if valid else "configuration_required", "configuration_present": present,
            "speech_probe_available": probe, "network_called": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "bootstrap", "authorize-delivery", "run", "health"))
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--work-parent", type=Path)
    parser.add_argument("--mode", choices=("shadow", "delivery"), default="shadow")
    parser.add_argument("--model", action="store_true")
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--local-sender-stopped", action="store_true")
    parser.add_argument("--local-operator", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = check_settings(model=args.model, speech=args.speech, delivery=args.mode == "delivery")
        else:
            if args.local_operator and args.command not in {"bootstrap", "authorize-delivery", "health"}:
                raise StateError("local_operator_cannot_run_cloud_production")
            if args.command == "run" and check_settings(model=args.model, speech=args.speech,
                    delivery=args.mode == "delivery")["state"] != "configured":
                raise StateError("cloud_provider_configuration_required")
            store = azure_store(local_operator=args.local_operator)
            parent = args.work_parent or Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
            if args.command in {"bootstrap", "authorize-delivery"}:
                if not args.seed or not args.seed.is_file() or args.seed.stat().st_size > 128 * 1024 * 1024:
                    raise StateError("private_seed_required")
                result = (bootstrap(store, args.seed.read_bytes(), parent) if args.command == "bootstrap" else
                    authorize_delivery(store, args.seed.read_bytes(), parent, local_sender_stopped=args.local_sender_stopped))
            elif args.command == "health":
                result = health(store)
            else:
                identity = os.environ.get("GITHUB_RUN_ID", "")
                attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
                if not all(re.fullmatch(r"[1-9][0-9]{0,20}", s) for s in (identity, attempt)):
                    raise StateError("github_run_identity_required")
                result = run_day(store, parent, mode=args.mode, model=args.model, speech=args.speech,
                                 run_id=int(identity), run_attempt=int(attempt))
        print(json.dumps({k: v for k, v in result.items() if k != "artifacts"}))
        return 0 if result["state"] in {"configured", "shadow_initialized", "delivery_authorized", "shadow_ready",
            "submitted", "not_due", "shadow_only", "outside_run_day", "waiting_for_delivery_window",
            "waiting_for_public_verification"} else 2
    except Exception as exc:
        # StateError strings are code-owned. Unknown exception text is never logged.
        print(json.dumps({"state": "failed", "code": str(exc) if isinstance(exc, StateError) else "runtime_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
