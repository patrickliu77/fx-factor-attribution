"""Cloud-journal email adapter, explicitly injected into the existing sender.

No default provider or executable entry point. Caller must hold the journal's
ownership session. All existing opt-in, date and public-byte gates still apply.
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

from ..narrative import subscriptions as S, public_delivery as P, morning as M
from .journal import Journal
from .state import StateError


class GuardedProvider:
    def __init__(self, journal: Journal, provider_factory, messages, day, verified, *, clock):
        self.journal, self.provider_factory = journal, provider_factory
        self.messages, self.day, self.verified, self.clock = messages, day, verified, clock
        self.campaigns, self._provider = {}, None

    def _gate(self):
        moment = self.clock()
        local = M.local_time(moment)
        try:
            age = (moment - datetime.fromisoformat(self.verified["observed_at"])).total_seconds()
            if (local.weekday() >= 5 or local.time() < time(9) or local.date().isoformat() != self.day
                    or self.verified.get("state") != "verified" or not 0 <= age <= 900):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise StateError("delivery_gate_closed") from None
        self.journal._owned()

    def _get_provider(self):
        if self._provider is None:
            self._provider = self.provider_factory()
        return self._provider

    def create(self, message):
        fingerprint = M.digest(message)
        languages = [lang for lang, value in self.messages.items() if M.digest(value) == fingerprint]
        if len(languages) != 1:
            raise StateError("unexpected_email_payload")
        lang = languages[0]
        self._gate()

        def create():
            self._gate()
            return {"campaign_id": self._get_provider().create(message)}

        receipt = self.journal.run_once("email-create", self.day, lang, fingerprint, create)
        identity = receipt["campaign_id"]
        if identity in self.campaigns and self.campaigns[identity] != (lang, fingerprint):
            raise StateError("duplicate_provider_identity")
        self.campaigns[identity] = (lang, fingerprint)
        return identity

    def send(self, identity):
        if type(identity) is not int or identity not in self.campaigns:
            raise StateError("unknown_email_campaign")
        lang, fingerprint = self.campaigns[identity]
        self._gate()

        def send():
            self._gate()
            self._get_provider().send(identity)
            return {"submitted": True}

        return self.journal.run_once("email-send", self.day, lang, fingerprint, send)


def deliver_guarded(output_dir, brief, verified, journal: Journal, *, provider_factory, clock=M.now_utc):
    """Use the real sender's validation and formatting, with durable effect claims.

    This adapter must never be installed on top of a blank production journal:
    local historical claims must first be reconciled during the planned cutover.
    """
    journal._owned()
    if not callable(provider_factory):
        raise StateError("explicit_email_provider_required")

    def factory():
        # Carry forward this day's pre-cloud receipts before the local sender can
        # skip them. A later runner may restore an older seed without those rows.
        for lang in ("en", "zh"):
            path = Path(output_dir) / "subscriptions" / "deliveries" / brief["date"] / (lang + ".json")
            if path.exists():
                journal.remember_local_delivery(brief["date"], lang, M.read_json(path))
        expected = P.expectation(output_dir, brief)
        settings = S.config(output_dir)
        messages = {lang: S.payload(settings, expected, expected["media"][lang], lang) for lang in ("en", "zh")}
        return GuardedProvider(journal, provider_factory, messages, brief["date"], verified, clock=clock)

    return S.deliver(output_dir, brief, verified, clock=clock, provider_factory=factory)
