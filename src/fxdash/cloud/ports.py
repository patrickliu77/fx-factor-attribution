"""Production pipeline bindings. All persistent state comes from stage artifacts."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from ..narrative import morning as M, subscriptions as S, public_delivery as V
from . import workspace as W, briefing as B, audio, publish, preflight, pipeline as P
from .delivery import deliver_guarded
from .journal import operation_key
from .state import StateError, read_artifact, save_artifact


def pending(journal, day, stage, context, clock):
    stages = (*P.STAGES, "publish")
    if not isinstance(context, dict) or set(context) != {"seed", *stages[:stages.index(stage)]}:
        raise StateError("invalid_runtime_context")
    fingerprint = M.digest({k: hashlib.sha256(v).hexdigest() for k, v in context.items()})
    claim = journal._owned()[1]["claims"].get(operation_key(stage, day))
    if not claim or claim["state"] != "pending" or claim["fingerprint"] != fingerprint:
        raise StateError("runtime_claim_required")
    local = M.local_time(clock())
    if local.weekday() >= 5 or local.date().isoformat() != day:
        raise StateError("runtime_day_changed")


class ProductionPorts:
    def __init__(self, journal, day, parent, *, model=False, speech=False, clock=M.now_utc,
                 worker=W.execute, renderer=None, provider_factory=S.Provider, fetcher=None,
                 git_runner=publish.run_git, publish_token=lambda: os.environ.get("FXDASH_PUBLISH_TOKEN", ""),
                 replay_capture=None):
        operation_key("inputs", day)
        if type(model) is not bool or type(speech) is not bool:
            raise StateError("invalid_runtime_settings")
        self.journal, self.day, self.parent = journal, day, W.directory(parent)
        self.model, self.speech, self.clock, self.worker = model, speech, clock, worker
        self.renderer, self.provider_factory, self.fetcher = renderer, provider_factory, fetcher
        self.git_runner, self.publish_token, self.replay_capture = git_runner, publish_token, replay_capture
        self.verified = None

    def gate(self, stage, context):
        pending(self.journal, self.day, stage, context, self.clock)

    def _ready(self, root, *, fresh=False):
        report = preflight.inspect(root)
        if report["state"] != "ready_for_shadow":
            raise StateError("runtime_seed_not_ready")
        if fresh and not M.previous_session(M.local_time(self.clock()).date()) <= report["attribution_as_of"] <= self.day:
            raise StateError("runtime_attribution_not_current")
        return report

    def inputs(self, context):
        self.gate("inputs", context)
        with W.restored(self.parent, context["seed"]) as root:
            self._ready(root)
        return context["seed"]

    def quant(self, context):
        self.gate("quant", context)
        with W.restored(self.parent, context["inputs"], code=True) as root:
            self.worker(root, "quant", replay_capture=self.replay_capture)
            self.gate("quant", context)
            self._ready(root, fresh=True)
            return W.bundle(root)

    def news(self, context):
        self.gate("news", context)
        with W.restored(self.parent, context["quant"]) as root:
            port = B.BriefingPorts(self.journal, self.day, root / "outputs", clock=self.clock,
                                  use_model=self.model, cache_dir=root / "data/cache")
            selected = B.decode(port.news(context))
            selected["workspace_sha256"] = save_artifact(self.journal.store, W.bundle(root))
            return B.encode(selected)

    def _news_state(self, context):
        value = B.decode(context["news"])
        return read_artifact(self.journal.store, value["workspace_sha256"])

    def recap(self, context):
        self.gate("recap", context)
        with W.restored(self.parent, self._news_state(context)) as root:
            return B.BriefingPorts(self.journal, self.day, root / "outputs", clock=self.clock,
                                  use_model=self.model, cache_dir=root / "data/cache").recap(context)

    def _audio(self, context, lang):
        stage = "audio-" + lang
        self.gate(stage, context)
        source = self._news_state(context) if lang == "en" else context["audio-en"]
        with W.restored(self.parent, source) as root:
            audio.ensure_language(root / "outputs", context["recap"], lang, enabled=self.speech,
                renderer=self.renderer, clock=self.clock, gate=lambda: self.gate(stage, context))
            return W.bundle(root)

    def build(self, context):
        self.gate("build", context)
        with W.restored(self.parent, context["audio-zh"], code=True) as root:
            if self.speech:
                from ..narrative.audio_briefing import inspect as inspect_audio
                _, brief = audio.install(root / "outputs", context["recap"])
                if inspect_audio(root / "outputs", brief)["state"] != "ready":
                    raise StateError("cloud_audio_incomplete")
            self.worker(root, "build")
            body, reviewed = W.public_bundle(root / "site")
            site_hash = save_artifact(self.journal.store, body)
            return B.encode({"schema": "cloud-build-v1", "site_sha256": site_hash,
                             "inventory_sha256": reviewed["sha256"],
                             "state_sha256": hashlib.sha256(context["audio-zh"]).hexdigest()})

    def publish(self, context):
        self.gate("publish", context)
        if not P.delivery_day(self.day, self.clock()):
            raise StateError("runtime_delivery_closed")
        build = B.decode(context["build"])
        with tempfile.TemporaryDirectory(prefix="fxpub-", dir=self.parent) as folder:
            folder = Path(folder)
            report = W.restore_public(read_artifact(self.journal.store, build["site_sha256"]), folder / "candidate")
            if report["sha256"] != build["inventory_sha256"]:
                raise StateError("runtime_candidate_changed")
            prepared = publish.stage(folder / "candidate", folder / "staged", expected_sha256=report["sha256"],
                                     runner=self.git_runner)
            # This is a read-only anonymous probe of the fixed public repository.
            remote = self.git_runner(["ls-remote", publish.REMOTE, publish.REF], folder, publish.git_environment())
            if not re.fullmatch(r"[0-9a-f]{40}\trefs/heads/gh-pages", remote):
                raise StateError("runtime_publication_head_unconfirmed")
            self.gate("publish", context)
            result = publish.push(prepared, remote.split("\t")[0], token_provider=self.publish_token, runner=self.git_runner)
            return B.encode(result)

    def verify(self, context):
        self.journal._owned()
        self.verified = None
        with W.restored(self.parent, context["audio-zh"]) as root:
            _, brief = audio.install(root / "outputs", context["recap"])
            self.verified = V.verify(root / "outputs", brief, fetcher=self.fetcher, clock=self.clock, force=True)
            self.journal._owned()
            # Retain evidence for operations, but never reuse it as a fresh gate.
            report = B.encode(self.verified)
            digest = save_artifact(self.journal.store, report)
            key = "probes/" + self.day
            old = self.journal.store.read(key)
            self.journal.store.compare_and_swap(key, old.version if old else None,
                B.encode({"artifact_sha256": digest, "observed_at": self.clock().isoformat()}))
        return self.verified.get("state") == "verified"

    def email(self, context, journal):
        if journal is not self.journal:
            raise StateError("runtime_journal_mismatch")
        with W.restored(self.parent, context["audio-zh"]) as root:
            _, brief = audio.install(root / "outputs", context["recap"])
            return deliver_guarded(root / "outputs", brief, self.verified or {}, journal,
                                   provider_factory=self.provider_factory, clock=self.clock)

    def ports(self):
        return P.Ports({"inputs": self.inputs, "quant": self.quant, "news": self.news, "recap": self.recap,
                        "audio-en": lambda c: self._audio(c, "en"), "audio-zh": lambda c: self._audio(c, "zh"),
                        "build": self.build}, publish=self.publish, verify=self.verify, email=self.email)
