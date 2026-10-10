"""Task exits reflect delivery attention; synthetic entries never launch a worker."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from fxdash.narrative import automation, catchup, morning, morning_dispatch
from fxdash import scheduled_environment


OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))
import check_delivery_runtime as R


@pytest.mark.parametrize("delivery,expected,exit_code", [
    ({"state": "delivery_check_failed"}, "failed", 1),
    ({"state": "checked", "public": {"state": "verified"}, "email": {"state": "failed"}}, "failed", 1),
    ({"state": "checked", "email": {"state": "review_required"}}, "attention_required", 2),
    ({"state": "checked", "email": {"state": "attention_required", "languages": {"en": "submitted", "zh": "review_required"}}}, "attention_required", 2),
    ({"state": "checked", "email": {"state": "submitted_pending"}}, "submitted_pending", 0),
    ({"state": "checked", "email": {"state": "submitted"}}, "submitted_pending", 0),
    ({"state": "checked", "email": {"state": "disabled"}}, "disabled", 0),
    ({"state": "checked", "public": {"state": "pending"}, "email": {"state": "disabled"}}, "pending", 0),
    ({"state": "checked", "email": {"state": "outside_delivery_day"}}, "idle", 0),
    ({"state": "checked", "public": {"state": "pending"}, "email": {"state": "waiting_for_public_audio"}}, "pending", 0),
    ({"state": "waiting_for_push"}, "pending", 0),
    ({"state": "idle"}, "idle", 0),
    ({"state": "checked", "email": {"state": "confirmed"}}, "confirmed", 0),
    ({"state": "unknown"}, "not_observed", 2),
])
def test_dispatch_success_is_separate_from_delivery_state(tmp_path, delivery, expected, exit_code):
    status = R.record_task_status(tmp_path, "briefing", 0, delivery,
                                  clock=lambda: datetime(2026, 10, 5, 16, tzinfo=timezone.utc))
    assert status["state"] == expected and status["exit_code"] == exit_code
    assert status["dispatch_exit_code"] == 0
    saved = json.loads((tmp_path / "automation/2026-10-05/task-status/briefing.json").read_text())
    assert saved == status


def test_dispatch_failure_cannot_be_hidden_by_successful_delivery(tmp_path):
    result = R.record_task_status(tmp_path, "catchup", 7, {"state": "checked", "email": {"state": "confirmed"}})
    assert result["state"] == "dispatch_failed" and result["exit_code"] == 7


def test_task_result_contains_no_raw_provider_data(tmp_path):
    private = "synthetic-private-provider-value"
    result = R.record_task_status(tmp_path, "briefing", 0, {
        "state": "delivery_check_failed", "error": private,
        "email": {"state": "review_required", "campaign_id": private},
    })
    assert private not in json.dumps(result)
    assert private not in next(tmp_path.rglob("briefing.json")).read_text()


def entry(kind):
    specification = importlib.util.spec_from_file_location("synthetic_" + kind, OPS / ("run_" + kind + "_task.py"))
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def scheduled_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(R, "runtime_status", lambda *args: {"state": "ready"})
    monkeypatch.setattr(R, "bootstrap_tools", lambda: {})
    monkeypatch.setattr(scheduled_environment, "refresh", lambda kind: {'state': 'refreshed'})
    monkeypatch.setattr(morning, "slot", lambda moment: "publish")
    monkeypatch.setattr(catchup, "due", lambda moment: True)
    monkeypatch.setattr(automation, "before", lambda *args: {"state": "inputs_ready", "proceed": True})
    from fxdash import operations
    monkeypatch.setattr(operations, "collect_report", lambda *args: {"acceptance": {}})
    monkeypatch.setattr(operations, "save_report", lambda *args: None)
    return tmp_path


@pytest.mark.parametrize("kind", ["briefing", "catchup"])
@pytest.mark.parametrize("delivery,expected", [
    ({"state": "delivery_check_failed"}, 1),
    ({"state": "checked", "email": {"state": "review_required"}}, 2),
    ({"state": "checked", "email": {"state": "submitted_pending"}}, 0),
    ({"state": "checked", "email": {"state": "disabled"}}, 0),
    ({"state": "checked", "public": {"state": "pending"}}, 0),
])
def test_actual_wrapper_uses_delivery_result_once_and_restores_streams(scheduled_fixture, monkeypatch, kind, delivery, expected):
    calls = []

    def dispatch(arguments):
        calls.append(arguments)
        return 0

    monkeypatch.setattr(morning_dispatch, "main", dispatch)
    monkeypatch.setattr(catchup, "main", dispatch)
    monkeypatch.setattr(automation, "after", lambda *args: delivery)
    stdout, stderr = sys.stdout, sys.stderr
    result = entry(kind).main(scheduled_fixture)
    assert result == expected and calls == [["--scheduled-task"]]
    assert sys.stdout is stdout and sys.stderr is stderr
    status = json.loads(next((scheduled_fixture / "outputs/automation").rglob(kind + ".json")).read_text())
    assert status["exit_code"] == expected and status["dispatch_exit_code"] == 0


@pytest.mark.parametrize("kind", ["briefing", "catchup"])
def test_unhealthy_runtime_exits_without_refreshing_credentials_or_dispatch(scheduled_fixture, monkeypatch, kind):
    monkeypatch.setattr(R, "runtime_status", lambda *args: {"state": "not_persistent"})
    monkeypatch.setattr(scheduled_environment, "refresh", lambda kind: pytest.fail("credentials touched"))
    monkeypatch.setattr(morning_dispatch, "main", lambda *args: pytest.fail("real dispatch"))
    monkeypatch.setattr(catchup, "main", lambda *args: pytest.fail("real dispatch"))
    assert entry(kind).main(scheduled_fixture) == 3


def test_catchup_outside_window_is_idle_without_dispatch_or_delivery_check(scheduled_fixture, monkeypatch):
    monkeypatch.setattr(catchup, "due", lambda moment: False)
    monkeypatch.setattr(catchup, "main", lambda *args: pytest.fail("outside-window dispatch"))
    monkeypatch.setattr(automation, "after", lambda *args: pytest.fail("outside-window delivery check"))
    assert entry("catchup").main(scheduled_fixture) == 0
    saved = json.loads(next((scheduled_fixture / "outputs/automation").rglob("catchup.json")).read_text())
    assert saved["state"] == "idle" and saved["exit_code"] == 0
