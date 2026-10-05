from datetime import timedelta
import json

from fastapi.testclient import TestClient

from fxdash import operations as O
from fxdash.narrative import morning as M
from fxdash.web.app import create_app
from test_delivery_status import NOW, configuration, digest_tree, edition, probe, receipt
from test_web import _write_fixture


def test_readonly_api_reports_saved_probe_and_never_host_credentials(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    configuration(tmp_path)
    _, _, identity = edition(tmp_path)
    receipt(tmp_path, identity)
    monkeypatch.setattr(M, "now_utc", lambda: NOW)
    before = digest_tree(tmp_path)
    response = TestClient(create_app(tmp_path, cache_dir=tmp_path / "no-cache")).get("/api/briefing/operations")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    value = response.json()
    assert value["email"]["config_enabled"] is True
    assert value["email"]["credential_configured"] is None
    assert value["runtime_observation"]["state"] == "not_observed"
    assert value["email"]["today"]["languages"]["en"]["state"] == "submitted"
    assert digest_tree(tmp_path) == before


def test_api_projection_rechecks_probe_age_and_corrupt_records_without_500(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    path, _ = probe(tmp_path)
    client = TestClient(create_app(tmp_path, cache_dir=tmp_path / "no-cache"))
    monkeypatch.setattr(M, "now_utc", lambda: NOW + timedelta(hours=27))
    assert client.get("/api/briefing/operations").json()["runtime_observation"]["state"] == "stale"
    path.write_text('{"schema_version":1,"observed_at":NaN}', encoding="utf-8")
    response = client.get("/api/briefing/operations")
    assert response.status_code == 200
    assert response.json()["runtime_observation"]["state"] == "unreadable"


def test_operations_report_includes_config_unknown_tasks_and_today_not_history(tmp_path):
    configuration(tmp_path, sender_footer="DO_NOT_EXPORT_PRIVATE_VALUE")
    report = O.collect_report(tmp_path, clock=lambda: NOW)
    assert report["delivery_operations"]["email"]["credential_configured"] is None
    html = O.render_report(report)
    assert "播报与邮件现在依据什么运行" in html
    assert "配置启用" in html and "发送凭据：尚未观察" in html
    assert "过往回执不代替当天回执" in html
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in html
    assert "DO_NOT_EXPORT_PRIVATE_VALUE" not in json.dumps(report)
    assert "{{DELIVERY}}" not in html
