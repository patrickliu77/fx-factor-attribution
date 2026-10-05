from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from fxdash.narrative import delivery_status as D, morning as M

NOW = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
DAY = "2026-10-05"


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def probe(root, **changes):
    value = {"schema_version": 1, "observed_at": NOW.isoformat(), "host": {"platform": "windows"},
             "runtime": {"state": "ready", "persistent": True, "dependencies_ready": True, "audio_backend": "azure"},
             "credentials": {name: True for name in D.CREDENTIALS},
             "tasks": {name: {"registered": True, "enabled": True,
                              "last_run_at": (NOW - timedelta(hours=1)).isoformat(),
                              "next_run_at": (NOW + timedelta(hours=1)).isoformat(), "last_result": 0}
                       for name in D.TASKS}}
    value.update(changes)
    return write(root, "automation/runtime-readiness.json", value), value


def configuration(root, **changes):
    value = {"enabled": True, "double_opt_in_confirmed": True, "quota_approved": True,
             "sender_id": 1, "sender_footer": "Fixture public sender",
             "forms": {lang: f"https://fixture.sibforms.com/serve/{lang}" for lang in D.LANGUAGES},
             "lists": {"en": 1, "zh": 2}}
    value.update(changes)
    return write(root, "subscriptions/config.json", value)


def edition(root, *, day=DAY, generated=None, **changes):
    value = {"available": True, "mode": "edition", "date": day, "state": "numbers_only",
             "generated_at": generated or (NOW - timedelta(hours=1)).isoformat(),
             "attribution_as_of": "2026-10-02", "text": {"en": "Saved fixture", "zh": "Saved fixture"},
             "notes": [], "warnings": [], "packet_hash": "a" * 64}
    value.update(changes)
    path = write(root, f"briefing/days/{day}/edition.json", value)
    return path, value, M.digest(value)


def receipt(root, identity, *, day=DAY, lang="en", state="submitted", **changes):
    value = {"date": day, "language": lang, "edition_hash": identity, "payload_hash": "b" * 64,
             "state": state, "started_at": (NOW - timedelta(minutes=40)).isoformat(),
             "finished_at": (NOW - timedelta(minutes=35)).isoformat(), "campaign_id": 123,
             "delivery_policy": "require_audio", "content_mode": "audio"}
    value.update(changes)
    return write(root, f"subscriptions/deliveries/{day}/{lang}.json", value), value


def confirmation(root, receipt_value, *, delivered=1, status="sent", **changes):
    value = {"schema_version": 1, **{key: receipt_value[key] for key in
             ("date", "language", "edition_hash", "payload_hash", "campaign_id")},
             "provider_observation": {"state": "provider_confirmed" if delivered else "provider_sent",
                                      "provider_status": status, "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
                                      "stats": {"delivered": delivered, "sent": 10}}}
    value.update(changes)
    return write(root, f"subscriptions/provider-observations/{value['date']}/{value['language']}.json", value), value


def collect(root, now=NOW):
    result = D.snapshot(root, clock=lambda: now)
    json.dumps(result, allow_nan=False)
    return result


def digest_tree(root):
    return {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_missing_probe_preserves_enabled_configuration_without_reading_host(tmp_path, monkeypatch):
    from fxdash.narrative import subscriptions, public_delivery, audio_briefing
    configuration(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only status must not inspect host or perform delivery")
    monkeypatch.setattr(subscriptions, "api_key", forbidden)
    monkeypatch.setattr(public_delivery, "verify", forbidden)
    monkeypatch.setattr(audio_briefing, "ensure", forbidden)
    before = digest_tree(tmp_path)
    result = collect(tmp_path)
    assert result["email"]["config_enabled"] is True
    assert result["email"]["configuration_valid"] is True
    assert result["email"]["credential_configured"] is None
    assert result["email"]["account_validation"] == "not_verified"
    assert result["runtime_observation"]["state"] == "not_observed"
    assert all(task["registered"] is None for task in result["runtime_observation"]["tasks"].values())
    assert digest_tree(tmp_path) == before


def test_recent_probe_has_presence_only_and_allows_future_next_run(tmp_path):
    probe(tmp_path)
    result = collect(tmp_path)["runtime_observation"]
    assert result["state"] == "current" and result["age_hours"] == 0
    assert result["runtime"]["state"] == "ready"
    assert result["credentials"]["BREVO_API_KEY"]["state"] == "configured_unverified"
    assert result["tasks"]["fxdash-briefing"]["state"] == "registered_enabled"
    assert result["tasks"]["fxdash-briefing"]["next_run_at"] == (NOW + timedelta(hours=1)).isoformat()


@pytest.mark.parametrize("age,state", [(26, "current"), (26.001, "stale"), (-0.001, "future")])
def test_probe_freshness_boundaries_do_not_claim_current_credentials(tmp_path, age, state):
    probe(tmp_path, observed_at=(NOW - timedelta(hours=age)).isoformat())
    result = collect(tmp_path)
    assert result["runtime_observation"]["state"] == state
    assert result["email"]["credential_configured"] is (True if state == "current" else None)
    if state != "current":
        assert result["runtime_observation"]["tasks"]["fxdash-briefing"]["registered"] is None


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '"secret"', '{"schema_version":1,"observed_at":NaN}'])
def test_broken_probe_remains_serializable_and_unreadable(tmp_path, raw):
    path = tmp_path / "automation/runtime-readiness.json"
    path.parent.mkdir()
    path.write_text(raw, encoding="utf-8")
    result = collect(tmp_path)
    assert result["runtime_observation"]["state"] == "unreadable"
    assert result["email"]["credential_configured"] is None


@pytest.mark.parametrize("stamp", [None, "bad", "2026-10-05T13:00:00", [], {}])
def test_invalid_or_naive_probe_time_is_not_a_valid_observation(tmp_path, stamp):
    probe(tmp_path, observed_at=stamp)
    assert collect(tmp_path)["runtime_observation"]["state"] == "unreadable"


@pytest.mark.parametrize("change", [{"state": []}, {"persistent": "true"}, {"dependencies_ready": 1}])
def test_invalid_runtime_readiness_cannot_claim_ready(tmp_path, change):
    _, value = probe(tmp_path)
    value["runtime"].update(change)
    write(tmp_path, "automation/runtime-readiness.json", value)
    assert collect(tmp_path)["runtime_observation"]["runtime"]["state"] == "unreadable"


@pytest.mark.parametrize("stamp", [(NOW + timedelta(seconds=1)).isoformat(), "bad", NOW.replace(tzinfo=None).isoformat()])
def test_task_last_run_must_be_valid_and_not_future(tmp_path, stamp):
    _, value = probe(tmp_path)
    value["tasks"]["fxdash-briefing"]["last_run_at"] = stamp
    write(tmp_path, "automation/runtime-readiness.json", value)
    assert collect(tmp_path)["runtime_observation"]["tasks"]["fxdash-briefing"]["state"] == "unreadable"


def test_probe_sanitizes_unknown_fields_values_and_false_presence(tmp_path):
    _, value = probe(tmp_path)
    secret = "DO_NOT_EXPORT_PRIVATE_VALUE"
    value.update(sender=secret, absolute_path=secret)
    value["credentials"]["BREVO_API_KEY"] = secret
    value["credentials"]["PRIVATE_KEY"] = secret
    value["runtime"].update(executable=secret, error=secret)
    value["tasks"]["fxdash-briefing"].update(arguments=secret, account=secret)
    write(tmp_path, "automation/runtime-readiness.json", value)
    result = collect(tmp_path)
    assert secret not in json.dumps(result)
    assert result["email"]["credential_configured"] is None


@pytest.mark.parametrize("value", [[], None, "private", {"enabled": True, "delivery_policy": []}])
def test_broken_email_configuration_does_not_crash_or_claim_valid(tmp_path, value):
    write(tmp_path, "subscriptions/config.json", value)
    assert collect(tmp_path)["email"]["configuration_valid"] is False


def test_configuration_exports_only_state_and_policy(tmp_path):
    configuration(tmp_path, sender_footer="DO_NOT_EXPORT_PRIVATE_VALUE", delivery_policy="allow_text")
    result = collect(tmp_path)
    assert result["email"]["policy"] == "allow_text"
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in json.dumps(result)
    assert "forms" not in result["email"] and "lists" not in result["email"]


def test_latest_saved_edition_does_not_manufacture_today_delivery(tmp_path):
    edition(tmp_path, day="2026-10-02", generated="2026-10-02T14:00:00Z")
    result = collect(tmp_path)
    assert result["briefing"]["latest"]["state"] == "numbers_only"
    assert result["briefing"]["latest"]["freshness"] == "stale"
    assert result["email"]["today"]["date"] == DAY
    assert result["email"]["today"]["due"] is True
    assert result["email"]["today"]["state"] == "not_recorded"


@pytest.mark.parametrize("now,phase", [(NOW.replace(hour=12), "before_delivery"),
                                      (NOW, "delivery_due"), (NOW - timedelta(days=1), "weekend")])
def test_today_uses_new_york_weekday_and_delivery_clock(tmp_path, now, phase):
    assert collect(tmp_path, now)["email"]["today"]["phase"] == phase


@pytest.mark.parametrize("generated", [(NOW + timedelta(seconds=1)).isoformat(), "bad", NOW.replace(tzinfo=None).isoformat()])
def test_future_or_invalid_edition_never_claims_readable_delivery(tmp_path, generated):
    edition(tmp_path, generated=generated)
    assert collect(tmp_path)["briefing"]["latest"]["state"] == "unreadable"


def test_saved_submission_and_review_counts_remain_distinct_from_confirmation(tmp_path):
    _, _, identity = edition(tmp_path)
    receipt(tmp_path, identity, lang="en")
    receipt(tmp_path, identity, lang="zh", state="review_required",
            error={"stage": "send", "http_status": 401, "provider_code": "unauthorized",
                   "uncertain_outcome": False, "private_message": "DO_NOT_EXPORT_PRIVATE_VALUE"})
    result = collect(tmp_path)
    assert result["email"]["today"]["state"] == "attention_required"
    assert result["email"]["counts"] == {"submitted": 1, "review_required": 1, "unreadable": 0, "confirmed": 0}
    assert result["email"]["scope"] == "provider_submission_not_inbox_receipt"
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in json.dumps(result)


def test_create_response_error_preserves_success_http_status(tmp_path):
    _, _, identity = edition(tmp_path)
    receipt(tmp_path, identity, state="review_required",
            error={"stage": "create", "http_status": 201, "provider_code": None,
                   "uncertain_outcome": True})
    row = collect(tmp_path)["email"]["today"]["languages"]["en"]
    assert row["state"] == "review_required"
    assert row["error"] == {"stage": "create", "http_status": 201,
                            "provider_code": None, "uncertain_outcome": True}


def test_boolean_campaign_identity_cannot_confirm_numeric_receipt(tmp_path):
    _, _, identity = edition(tmp_path)
    _, value = receipt(tmp_path, identity, campaign_id=1)
    path, saved = confirmation(tmp_path, value)
    saved["campaign_id"] = True
    path.write_text(json.dumps(saved), encoding="utf-8")
    result = collect(tmp_path)
    assert result["email"]["counts"]["confirmed"] == 0
    assert result["email"]["today"]["languages"]["en"]["confirmation"]["state"] == "unreadable"


@pytest.mark.parametrize("delivered,expected", [(0, "provider_sent"), (1, "provider_confirmed")])
def test_provider_sent_is_not_delivery_confirmation(tmp_path, delivered, expected):
    _, _, identity = edition(tmp_path)
    _, value = receipt(tmp_path, identity)
    confirmation(tmp_path, value, delivered=delivered)
    result = collect(tmp_path)
    observed = result["email"]["today"]["languages"]["en"]["confirmation"]
    assert observed["state"] == expected
    assert result["email"]["counts"]["confirmed"] == int(delivered > 0)
    assert result["email"]["confirmation_scope"] == "provider_reported_delivery_not_inbox"


@pytest.mark.parametrize("change", [{"payload_hash": "c" * 64}, {"campaign_id": 999},
                                      {"edition_hash": "c" * 64}, {"language": "zh"}])
def test_provider_observation_must_match_original_receipt(tmp_path, change):
    _, _, identity = edition(tmp_path)
    _, value = receipt(tmp_path, identity)
    path, saved = confirmation(tmp_path, value)
    saved.update(change)
    path.write_text(json.dumps(saved), encoding="utf-8")
    result = collect(tmp_path)
    assert result["email"]["counts"]["confirmed"] == 0
    assert result["email"]["today"]["languages"]["en"]["confirmation"]["state"] == "unreadable"


@pytest.mark.parametrize("stamp", [(NOW + timedelta(seconds=1)).isoformat(), "bad", NOW.replace(tzinfo=None).isoformat()])
def test_provider_future_or_invalid_observation_is_not_confirmed(tmp_path, stamp):
    _, _, identity = edition(tmp_path)
    _, value = receipt(tmp_path, identity)
    path, saved = confirmation(tmp_path, value)
    saved["provider_observation"]["observed_at"] = stamp
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert collect(tmp_path)["email"]["counts"]["confirmed"] == 0


def test_provider_observation_drops_private_fields_and_invalid_counters(tmp_path):
    _, _, identity = edition(tmp_path)
    _, value = receipt(tmp_path, identity)
    path, saved = confirmation(tmp_path, value)
    saved["provider_observation"]["stats"].update(delivered=True, campaign_id=123, private="DO_NOT_EXPORT_PRIVATE_VALUE")
    saved["provider_observation"].update(raw_exception="DO_NOT_EXPORT_PRIVATE_VALUE")
    path.write_text(json.dumps(saved), encoding="utf-8")
    result = collect(tmp_path)
    assert result["email"]["counts"]["confirmed"] == 0
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in json.dumps(result)
    assert "campaign_id" not in json.dumps(result)


@pytest.mark.parametrize("changes", [{"edition_hash": []}, {"state": {}}, {"started_at": "bad"},
                                       {"finished_at": (NOW + timedelta(seconds=1)).isoformat()},
                                       {"payload_hash": "not a hash"}, {"state": "confirmed"}])
def test_invalid_receipt_remains_visible_and_never_confirmed(tmp_path, changes):
    _, _, identity = edition(tmp_path)
    receipt(tmp_path, identity, **changes)
    result = collect(tmp_path)
    assert result["email"]["today"]["languages"]["en"]["state"] == "unreadable"
    assert result["email"]["counts"]["confirmed"] == 0


def test_ambiguous_clock_produces_diagnostics_without_crashing(tmp_path):
    probe(tmp_path)
    result = collect(tmp_path, NOW.replace(tzinfo=None))
    assert result["observed_at"] is None
    assert result["email"]["today"]["phase"] == "unreadable"
    assert result["runtime_observation"]["state"] == "unreadable"


def test_latest_audio_and_public_observation_are_read_only_verified_records(tmp_path):
    from test_public_delivery import fixture
    from fxdash.narrative import public_delivery as P
    _, brief, responses = fixture(tmp_path)
    observed = datetime.fromisoformat("2026-01-08T17:01:00+00:00")
    P.verify(tmp_path, brief, clock=lambda: observed, fetcher=lambda relative, limit: responses[relative])
    before = digest_tree(tmp_path)
    result = collect(tmp_path, observed)["briefing"]["latest"]
    assert result["state"] == "ready"
    assert result["audio"]["en"]["state"] == result["audio"]["zh"]["state"] == "ready"
    assert result["public"]["state"] == "verified"
    assert result["public"]["freshness"] == "current"
    assert digest_tree(tmp_path) == before


@pytest.mark.parametrize("record", ["audio", "public"])
def test_future_attachment_or_public_observation_does_not_claim_verified(tmp_path, record):
    from test_public_delivery import fixture
    from fxdash.narrative import public_delivery as P, audio_briefing as B
    _, brief, responses = fixture(tmp_path)
    observed = datetime.fromisoformat("2026-01-08T17:01:00+00:00")
    P.verify(tmp_path, brief, clock=lambda: observed, fetcher=lambda relative, limit: responses[relative])
    path = B.sidecar(tmp_path, brief, "audio-v1") / "en.json" if record == "audio" else P.target(tmp_path, brief)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["generated_at" if record == "audio" else "observed_at"] = (observed + timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(value), encoding="utf-8")
    latest = collect(tmp_path, observed)["briefing"]["latest"]
    assert latest["public"]["state"] == "unreadable"
    if record == "audio":
        assert latest["audio"]["en"]["state"] == "unreadable"


def test_unknown_tools_and_stale_tool_presence_are_not_current_readiness(tmp_path):
    _, value = probe(tmp_path)
    value["tools"] = {"git": {"available": True, "executable": "DO_NOT_EXPORT_PRIVATE_VALUE"},
                      "ffprobe": {"available": False, "private_error": "DO_NOT_EXPORT_PRIVATE_VALUE"}}
    write(tmp_path, "automation/runtime-readiness.json", value)
    result = collect(tmp_path)
    assert result["runtime_observation"]["tools"]["ffprobe"]["available"] is False
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in json.dumps(result)
    stale = collect(tmp_path, NOW + timedelta(hours=27))
    assert stale["runtime_observation"]["runtime"]["state"] == "stale"
    assert stale["runtime_observation"]["tools"]["git"]["available"] is None


def test_declared_future_day_never_manufactures_current_saved_edition(tmp_path):
    edition(tmp_path, day="2026-10-06")
    assert collect(tmp_path)["briefing"]["latest"]["state"] == "unreadable"


@pytest.mark.parametrize("audio_verified", [[], ["en", "en"], ["en"], ["en", "zh", "en"]])
def test_public_verified_requires_each_language_once(tmp_path, audio_verified):
    from test_public_delivery import fixture
    from fxdash.narrative import public_delivery as P
    _, brief, responses = fixture(tmp_path)
    observed = datetime.fromisoformat("2026-01-08T17:01:00+00:00")
    value = P.verify(tmp_path, brief, clock=lambda: observed, fetcher=lambda relative, limit: responses[relative])
    value["audio_verified"] = audio_verified
    P.target(tmp_path, brief).write_text(json.dumps(value), encoding="utf-8")
    assert collect(tmp_path, observed)["briefing"]["latest"]["public"]["state"] == "unreadable"


@pytest.mark.parametrize("audio_missing", [True, False])
def test_allow_text_receipt_preserves_partial_public_audio_contract(tmp_path, audio_missing):
    from test_public_delivery import fixture
    from fxdash.narrative import public_delivery as P
    _, brief, responses = fixture(tmp_path, audio=not audio_missing)
    observed = datetime.fromisoformat("2026-01-08T17:01:00+00:00")
    def fetch(relative, limit):
        if relative.endswith('/en.mp3'):
            raise ValueError("Synthetic unavailable media")
        return responses[relative]
    public = P.verify(tmp_path, brief, clock=lambda: observed, fetcher=fetch)
    assert public["state"] == ("text_verified" if audio_missing else "pending")
    configuration(tmp_path, delivery_policy="allow_text")
    receipt(tmp_path, brief["edition_hash"], day=brief["date"], delivery_policy="allow_text", content_mode="text_only",
            started_at=(observed-timedelta(minutes=10)).isoformat(), finished_at=(observed-timedelta(minutes=5)).isoformat())
    result = collect(tmp_path, observed)
    assert result["email"]["delivery_policy"] == "allow_text"
    assert result["email"]["today"]["languages"]["en"]["state"] == "submitted"
    assert result["email"]["today"]["languages"]["en"]["content_mode"] == "text_only"
    assert result["briefing"]["latest"]["public"]["state"] == public["state"]
    assert result["briefing"]["latest"]["public"]["text_verified"] is True
    assert result["briefing"]["latest"]["public"]["audio_verified"]["en"] is False
