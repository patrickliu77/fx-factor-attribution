"""Artifacts, freezing, and the narrative layer's own heartbeat (SPEC_phase3 §2.3, §6).

Three rules:

1. **Write once, never silently rewrite.** LLM output is not reproducible, so it
   needs freezing even more than the contract does; otherwise the same day's
   explanation changes shape on a rerun. Recomputation takes an explicit flag and
   leaves an audit trail.
2. **Store the full record even when the verdict is discard** (D10). Every source
   retrieved, the raw model output, which check failed, and why -- all of it, with
   only the `published` bit set false. **These failure samples are the only
   evidence base for future prompt tuning; throw them away and all that is left is
   impressions.**
3. **Do not touch the attribution pipeline's status.** The narrative layer writes
   its own `outputs/narrative/status.json` and does not modify a single byte of
   `outputs/status.json`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import HEARTBEAT_CRIT_HOURS, HEARTBEAT_WARN_HOURS, OUTPUT_DIR

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"
NARRATIVE_DIR = OUTPUT_DIR / "narrative"

# The model's judgement on whether the retrieved reporting can account for the
# observed magnitude. The order deliberately runs from "cannot account" to "can
# account": the honest options come first in the list so they do not read like a
# fallback (SPEC_phase3 §4.2).
ASSESSMENTS = (
    "no_relevant_reporting",
    "does_not_account_for",
    "partially_accounts_for",
    "accounts_for",
)

# Which assessments have something worth publishing.
#
# Note that `does_not_account_for` **is on the publish list**: SPEC_phase3 §10.2
# defines "there is X reporting, but the unexplained part is far larger than what
# that kind of reporting usually corresponds to" as **correct output**, not a
# failure. Suppressing it along with the rest would hide exactly the kind of honest
# conclusion this layer exists to produce. Only "no relevant reporting at all"
# genuinely has nothing to publish.
PUBLISHABLE_ASSESSMENTS = (
    "does_not_account_for",
    "partially_accounts_for",
    "accounts_for",
)


class ArtifactExists(RuntimeError):
    """The day's artifact already exists and no explicit recompute was authorized."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def artifact_path(date: str, root: Path | None = None) -> Path:
    return Path(root or NARRATIVE_DIR) / f"date={date}.json"


def decide_published(verification_passed: bool, assessment: str | None) -> bool:
    """Publish only if verification passed and there is a publishable conclusion."""
    return bool(verification_passed) and assessment in PUBLISHABLE_ASSESSMENTS


def content_hash(payload: dict) -> str:
    """Content hash. Strip the hash field itself first, otherwise it is self-referential."""
    body = {k: v for k, v in payload.items() if k != "content_hash"}
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_payload(
    date: str,
    *,
    window: int,
    model: str,
    llm_model: str,
    prompt_version: str,
    trigger: dict,
    pairs: list[dict],
    daily: dict | None = None,
    usage: dict | None = None,
) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generated_at": _now(),
        "window": window,
        "model": model,
        "engine": {"llm_model": llm_model, "prompt_version": prompt_version},
        "trigger": trigger,
        "daily": daily or {},
        "pairs": pairs,
        "usage": usage or {},
    }
    payload["content_hash"] = content_hash(payload)
    return payload


def pair_record(
    *,
    fact,
    sources: list[dict],
    raw_output: dict | None,
    narrative: dict | None,
    evidence: dict | None,
    findings,
    error: str | None = None,
) -> dict:
    """One pair's full record. Failure and success take the same path, with every
    field present (D10)."""
    from .verify import failures, passed

    findings = list(findings or [])
    ok = passed(findings) if findings else False
    assessment = (evidence or {}).get("assessment")
    return {
        "pair": fact.pair,
        "facts": fact.to_dict(),
        "sources": sources,
        # Store the raw model output verbatim. When verification discards a record,
        # this is the only thing left to trace back through
        "raw_output": raw_output,
        "narrative": narrative,
        "evidence": evidence or {},
        "verification": {
            "passed": ok,
            "findings": [
                {"check": f.check, "ok": f.ok, "detail": f.detail,
                 "data": getattr(f, "data", {}) or {}} for f in findings
            ],
            "failures": failures(findings),
        },
        "error": error,
        "published": decide_published(ok, assessment),
    }


def write_day(payload: dict, root: Path | None = None, rewrite: bool = False) -> Path:
    """Write the day's artifact. Refuse if it exists and rewrite was not given;
    never overwrite silently."""
    root = Path(root or NARRATIVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    path = artifact_path(payload["date"], root)
    if path.exists() and not rewrite:
        raise ArtifactExists(f"{path.name} already exists; recompute must pass rewrite")
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("supersedes", []).append({
            "generated_at": previous.get("generated_at"),
            "content_hash": previous.get("content_hash"),
        })
        payload["content_hash"] = content_hash(payload)
        log.warning("rewrote day artifact %s; previous version %s recorded in supersedes",
                    path.name, previous.get("content_hash"))
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_day(date: str, root: Path | None = None) -> dict | None:
    path = artifact_path(date, root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_days(root: Path | None = None) -> list[str]:
    root = Path(root or NARRATIVE_DIR)
    if not root.exists():
        return []
    return sorted(p.stem.split("=", 1)[1] for p in root.glob("date=*.json"))


def _first_sentence(text: str | None) -> str | None:
    """One-sentence gist of the first paragraph, not the full text."""
    if not text:
        return None
    # Sentence terminators: ASCII and the CJK full stop (narratives can be zh).
    for stop in (". ", "。"):
        i = text.find(stop)
        if i > 0:
            return text[:i + 1].strip()
    return text.strip()[:200]


def previous_trigger(pair: str, before: str, root: Path | None = None,
                     calendar: list[str] | None = None) -> dict:
    """The pair's most recent triggered day that left a record. **Neutral facts, no
    judgement attached.**

    Deliberately provides no "same event or not" field: that would draw the
    conclusion on the model's behalf, and it would then write up two triggers two
    weeks apart with entirely different event kinds as a continuation. Whether it
    is the same wave is for the model to decide (SPEC_phase3 §4.4).
    """
    days = sorted(calendar or [])
    for date in reversed(list_days(root)):
        if date >= before:
            continue
        day = read_day(date, root) or {}
        for rec in day.get("pairs", []):
            if rec.get("pair") != pair:
                continue
            evidence = rec.get("evidence") or {}
            gist = _first_sentence(
                ((rec.get("narrative") or {}).get("en") or {}).get("what_happened"))
            ago = None
            if date in days and before in days:
                ago = days.index(before) - days.index(date)
            return {
                "date": date,
                "trading_days_ago": ago,
                "event_kind": evidence.get("event_kind"),
                "assessment": evidence.get("assessment"),
                "what_happened_gist": gist,
                "published": bool(rec.get("published")),
            }
    return {}


# ------------------------------------------------------------- heartbeat
def heartbeat_state(age_hours: float | None) -> tuple[str, list[str]]:
    """Thresholds share the source of the main heartbeat (26 / 72 in config).
    **The argument is the time elapsed since the last run.**

    The narrative layer dying silently for three days while the page shows nothing
    is the most dangerous failure mode of an unattended system, so this layer needs
    its own independently visible heartbeat.

    What it watches is **whether it ran**, not **whether it wrote a note**. The
    latter is decided by the market, not by the system: triggers measure out at once
    every 4.5 to 5 days, so running that against a 26-hour yellow line would alarm
    on the vast majority of days. An always-on alarm is a wrong criterion; people
    learn to ignore it, and then a real failure goes unseen too.
    """
    if age_hours is None:
        return "red", ["narrative layer has never run"]
    if age_hours > HEARTBEAT_CRIT_HOURS:
        return "red", [f"{age_hours:.1f} hours since last run, over {HEARTBEAT_CRIT_HOURS}"]
    if age_hours > HEARTBEAT_WARN_HOURS:
        return "yellow", [f"{age_hours:.1f} hours since last run, over {HEARTBEAT_WARN_HOURS}"]
    return "green", []


def write_status(
    root: Path | None = None,
    *,
    last_run: str | None,
    last_published: str | None = None,
    now: datetime | None = None,
    extra_reasons: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Write the narrative layer's own status.json. **Never touches outputs/status.json.**

    **The colour is judged on last_run only, not last_published.** These are two
    different things:

    - `last_run` is the last time this layer finished without crashing. It measures
      whether the system is dead, and is the only thing the 26 / 72 hour thresholds
      should watch.
    - `last_published` is the last time a note was actually written. That is decided
      by the market: residual anomalies measure out at once every 4.5 to 5 days, so
      quiet days are this layer's normal state, not a fault.

    An earlier version mistook the latter for the heartbeat; the cost was turning
    yellow on every four- or five-day stretch without a trigger and red two days
    after that, while the system was fine throughout. So `last_published` is demoted
    to display only and never takes part in judging the colour.

    `extra_reasons` are alarms and push green up to yellow; `notes` are explanatory
    (for example, no new work on a non-trading day) and are shown as usual without
    changing the colour. Keeping them apart stops explanatory text from polluting
    the criterion.
    """
    root = Path(root or NARRATIVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(timezone.utc).astimezone()

    def _age(stamp_text):
        if not stamp_text:
            return None
        stamp = datetime.fromisoformat(stamp_text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=moment.tzinfo)
        return (moment - stamp).total_seconds() / 3600.0

    age = _age(last_run)
    state, reasons = heartbeat_state(age)
    reasons = reasons + list(extra_reasons or []) + list(notes or [])
    if extra_reasons and state == "green":
        state = "yellow"

    published_age = _age(last_published)
    status = {
        "state": state,
        "generated_at": moment.isoformat(timespec="seconds"),
        "last_run": last_run,
        "last_published": last_published,
        "age_hours": None if age is None else round(age, 2),
        "published_age_hours": (None if published_age is None
                                else round(published_age, 2)),
        "warn_hours": HEARTBEAT_WARN_HOURS,
        "crit_hours": HEARTBEAT_CRIT_HOURS,
        "reasons": reasons,
    }
    (root / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def read_status(root: Path | None = None) -> dict:
    path = Path(root or NARRATIVE_DIR) / "status.json"
    if not path.exists():
        return {"state": "red", "reasons": ["narrative status.json does not exist"],
                "last_run": None, "last_published": None, "age_hours": None}
    return json.loads(path.read_text(encoding="utf-8"))


def now_stamp() -> str:
    """Current local time with tzinfo. Artifacts and heartbeat share one time source."""
    return _now()


def last_published(root: Path | None = None) -> str | None:
    """Generation time of the most recent artifact with **anything published**.

    This is content freshness, not the heartbeat. Five quiet days mean the market
    gave no trigger, not that this layer is broken, so it is display only; for the
    colour see write_status.
    """
    for date in reversed(list_days(root)):
        day = read_day(date, root)
        if day and any(p.get("published") for p in day.get("pairs", [])):
            return day.get("generated_at")
        if day and day.get("daily", {}).get("published"):
            return day.get("generated_at")
    return None
