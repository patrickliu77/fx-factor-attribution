import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from fxdash import operations as O
from fxdash.narrative import morning as M, morning_dispatch as G
from test_morning_acceptance import completed_day
from test_morning import moment
from test_vintages import capture


def test_empty_future_report_is_read_only_and_honest(tmp_path):
    before = set(tmp_path.rglob("*"))
    report = O.collect_report(tmp_path, start_date="2026-09-08", clock=lambda: datetime.fromisoformat("2026-09-07T21:00:00+00:00"))
    assert report["acceptance"]["state"] == "collecting"
    assert report["archive"]["state"] == "missing"
    assert set(tmp_path.rglob("*")) == before
    html = O.render_report(report)
    assert "等待首个验收日" in html and "还没有到期的验收日" in html
    assert "data:font/woff2;base64," in html
    assert "{{" not in html
    assert '<script src=' not in html and '<link ' not in html
    assert "GitHub Pages" in html and "report.json" in html


def test_report_is_escaped_single_pass(tmp_path):
    report = O.collect_report(tmp_path)
    report["archive"]["issues"] = [{"file": '<img src=x onerror=alert(1)>{{STATUS}}', "reason": "<bad>", "error_type": "ValueError"}]
    html = O.render_report(report)
    assert '<img src=' not in html and '&lt;img src=' in html and '{{STATUS}}' in html
    assert "留档需要检查" in html


def test_append_reports_preserve_history_and_update_only_latest(tmp_path):
    report = O.collect_report(tmp_path)
    first = O.save_report(tmp_path, report)
    original = Path(first["html"]).read_bytes()
    updated = copy.deepcopy(report)
    updated["acceptance"]["start_date"] = "2026-09-08"
    second = O.save_report(tmp_path, updated)
    assert first["snapshot"] != second["snapshot"]
    assert Path(first["html"]).read_bytes() == original
    assert json.loads(Path(first["json"]).read_text(encoding="utf-8")) == report
    assert Path(second["latest"]).read_bytes() == Path(second["html"]).read_bytes() != original
    assert all(p.is_relative_to(tmp_path / "operations-acceptance" / "reports") for p in tmp_path.rglob("*") if p.is_file())


def test_real_synthetic_chain_has_checks_and_no_public_delivery_claim(tmp_path, synthetic_raw):
    completed_day(tmp_path, synthetic_raw)
    report = O.collect_report(tmp_path, start_date="2026-01-08", clock=lambda: moment(14, 6))
    html = O.render_report(report)
    assert report["acceptance"]["consecutive_passes"] == 1
    assert report["acceptance"]["event_context_days"] == 0
    assert "调用记录完整且身份一致" in html
    assert "此报告尚未核对 GitHub Pages" in html
    assert "完整引擎输入" in html


def test_comparison_details_render_without_hiding_structure_changes(tmp_path, synthetic_raw):
    capture(synthetic_raw, tmp_path)
    synthetic_raw.hy_oas.iloc[-2:] += 1
    capture(synthetic_raw, tmp_path)
    report = O.collect_report(tmp_path)
    html = O.render_report(report)
    assert report["archive"]["comparison"]["state"] == "compared"
    assert "数值修订" in html and "查看变化样例" in html and "hy_oas" in html


def test_invalid_enrollment_cannot_reduce_acceptance_to_zero(tmp_path):
    M.atomic_json(tmp_path / "briefing/acceptance.json", {"start_date": "2026-09-08", "required_consecutive_weekdays": 0})
    with pytest.raises(ValueError, match="invalid_acceptance_enrollment"):
        O.collect_report(tmp_path)


@pytest.mark.parametrize("active", [False, True])
def test_windowless_entry_generates_reports_only_in_active_gate(tmp_path, monkeypatch, active):
    source = Path(__file__).resolve().parents[1] / "ops" / "run_briefing_task.py"
    spec = importlib.util.spec_from_file_location("test_windowless_entry", source)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(entry, "__file__", str(tmp_path / "ops" / "run_briefing_task.py"))
    # The entry changes process-wide streams/cwd/path; restore all through the fixture.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(M, "slot", lambda now: "publish" if active else "idle")
    calls = []
    monkeypatch.setattr(G, "main", lambda args: calls.append(args) or 0)
    M.atomic_json(tmp_path / "outputs/briefing/acceptance.json", {"start_date": "2026-09-08"})
    assert entry.main() == 0
    assert calls == [["--scheduled-task"]]
    assert (tmp_path / "outputs/operations-acceptance/reports/latest.html").exists() == active
    assert not list((tmp_path / "outputs/briefing").rglob("edition.json"))
