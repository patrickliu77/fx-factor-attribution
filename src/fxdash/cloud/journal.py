"""Fail-closed effect claims. An uncertain external action is never replayed.

Ownership does not expire automatically. A killed owner requires operator
reconciliation, including proof that the old worker stopped. This avoids a late
worker writing or sending after a clock-based lease has been stolen.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import hashlib
import re
import uuid

from .state import StateError, Store

KEY = "control/journal-v1"
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
KINDS = {"inputs", "quant", "news", "recap", "audio-en", "audio-zh", "build", "publish",
         "email-create", "email-send"}


def encode(value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > MAX_JOURNAL_BYTES:
        raise StateError("journal_capacity_reached")
    return body


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def operation_key(kind, day, scope="edition"):
    if kind not in KINDS or scope not in {"edition", "catchup", "en", "zh"}:
        raise StateError("invalid_operation")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise StateError("invalid_operation_day") from None
    return f"{kind}/{day}/{scope}"


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def receipt_valid(kind, receipt):
    if not isinstance(receipt, dict):
        return False
    if kind == "email-create":
        return set(receipt) == {"campaign_id"} and type(receipt["campaign_id"]) is int and receipt["campaign_id"] > 0
    if kind == "email-send":
        return receipt == {"submitted": True} and receipt["submitted"] is True
    return set(receipt) == {"artifact_sha256"} and valid_hash(receipt["artifact_sha256"])


class Journal:
    def __init__(self, store: Store, *, clock=lambda: datetime.now(timezone.utc)):
        self.store, self.clock, self.owner = store, clock, None

    @staticmethod
    def initialize(store: Store):
        # Explicit bootstrap only. Runtime never initializes a missing journal.
        store.compare_and_swap(KEY, None, encode({"schema": 1, "owner": None, "claims": {}}))

    def _read(self):
        row = self.store.read(KEY)
        if row is None or len(row.body) > MAX_JOURNAL_BYTES:
            raise StateError("journal_missing_or_corrupt")
        try:
            value = json.loads(row.body, object_pairs_hook=unique_object)
            if (set(value) != {"schema", "owner", "claims"} or type(value["schema"]) is not int
                    or value["schema"] != 1 or not isinstance(value["claims"], dict)
                    or (value["owner"] is not None and not re.fullmatch(r"[0-9a-f]{32}", value["owner"]))):
                raise ValueError()
            for key, claim in value["claims"].items():
                kind, day, scope = key.split("/")
                if (operation_key(kind, day, scope) != key or not isinstance(claim, dict)
                        or set(claim) - {"fingerprint", "state", "started_at", "finished_at", "receipt", "origin"}
                        or not valid_hash(claim.get("fingerprint"))
                        or claim.get("state") not in {"pending", "succeeded", "review_required"}
                        or (claim["state"] == "succeeded" and not receipt_valid(kind, claim.get("receipt")))):
                    raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            raise StateError("journal_missing_or_corrupt") from None
        return row, value

    def _owned(self):
        row, value = self._read()
        if self.owner is None or value["owner"] != self.owner:
            raise StateError("ownership_lost")
        return row, value

    @contextmanager
    def session(self):
        row, value = self._read()
        if self.owner is not None or value["owner"] is not None:
            raise StateError("owner_busy")
        owner = uuid.uuid4().hex
        value["owner"] = owner
        self.store.compare_and_swap(KEY, row.version, encode(value))
        self.owner = owner
        try:
            yield self
        finally:
            try:
                row, value = self._owned()
                value["owner"] = None
                self.store.compare_and_swap(KEY, row.version, encode(value))
            finally:
                self.owner = None

    def run_once(self, kind, day, scope, fingerprint, action):
        key = operation_key(kind, day, scope)
        if not valid_hash(fingerprint):
            raise StateError("invalid_operation_fingerprint")
        row, value = self._owned()
        previous = value["claims"].get(key)
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                raise StateError("operation_input_changed")
            if previous["state"] != "succeeded":
                raise StateError("operation_needs_review")
            return previous["receipt"]
        now = self.clock()
        if now.utcoffset() is None:
            raise StateError("aware_clock_required")
        claim = {"fingerprint": fingerprint, "state": "pending", "started_at": now.isoformat()}
        value["claims"][key] = claim
        # External action cannot begin until the claim has been acknowledged.
        self.store.compare_and_swap(KEY, row.version, encode(value))
        self._owned()
        try:
            receipt = action()
            if not receipt_valid(kind, receipt):
                raise StateError("invalid_operation_receipt")
        except Exception:
            row, value = self._owned()
            value["claims"][key].update(state="review_required", finished_at=self.clock().isoformat())
            self.store.compare_and_swap(KEY, row.version, encode(value))
            raise StateError("operation_needs_review") from None
        # An interrupted/failed completion write leaves a pending claim. A later
        # worker blocks instead of assuming the provider rejected the request.
        row, value = self._owned()
        value["claims"][key].update(state="succeeded", receipt=receipt,
                                    finished_at=self.clock().isoformat())
        self.store.compare_and_swap(KEY, row.version, encode(value))
        return receipt

    def remember_local_delivery(self, day, lang, saved):
        """Import a dated legacy receipt without promoting uncertain submissions.

        Existing cloud claims are authoritative and never overwritten. Even an
        unreadable local receipt becomes a blocking marker, not a fresh attempt.
        """
        if lang not in {"en", "zh"}:
            raise StateError("invalid_operation")
        row, value = self._owned()
        saved = saved if isinstance(saved, dict) else {}
        fingerprint = saved.get("payload_hash")
        if not valid_hash(fingerprint):
            fingerprint = hashlib.sha256(b"unreadable-legacy-receipt").hexdigest()
        accepted = (saved.get("state") == "submitted" and saved.get("date") == day
                    and saved.get("language") == lang and valid_hash(saved.get("payload_hash"))
                    and type(saved.get("campaign_id")) is int and saved["campaign_id"] > 0)
        changed = False
        for kind in ("email-create", "email-send"):
            key = operation_key(kind, day, lang)
            if key in value["claims"]:
                continue
            marker = {"fingerprint": fingerprint, "state": "succeeded" if accepted else "review_required",
                      "origin": "local_seed", "started_at": self.clock().isoformat()}
            if accepted:
                marker["receipt"] = {"campaign_id": saved["campaign_id"]} if kind == "email-create" else {"submitted": True}
            value["claims"][key] = marker
            changed = True
        if changed:
            self.store.compare_and_swap(KEY, row.version, encode(value))

    def email_submitted(self, day):
        _, value = self._owned()
        for lang in ("en", "zh"):
            rows = [value["claims"].get(operation_key(kind, day, lang), {}) for kind in ("email-create", "email-send")]
            if any(r.get("state") != "succeeded" for r in rows) or rows[0]["fingerprint"] != rows[1]["fingerprint"]:
                return False
        return True
