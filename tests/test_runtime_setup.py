"""Persistent-base and pinned-dependency boundaries without environment creation."""
import importlib.util
from pathlib import Path
import sys

import pytest


OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))
import check_delivery_runtime as R


@pytest.fixture
def runtime_fixture(tmp_path, monkeypatch):
    temporary = tmp_path / "ephemeral"
    stable = tmp_path / "persistent"
    (stable / "project").mkdir(parents=True)
    repo = stable / "project"
    (repo / "requirements.txt").write_text("pandas==3.0.5\ntzdata==2026.3\n", encoding="utf-8")
    original = R.persistent_path
    monkeypatch.setattr(R, "persistent_path", lambda path: original(path, [temporary]))
    monkeypatch.setattr(R.sys, "executable", str(stable / "project/.venv/Scripts/python.exe"))
    monkeypatch.setattr(R.sys, "base_prefix", str(stable / "python"))
    monkeypatch.setattr(R.sys, "version_info", (3, 13, 15))
    monkeypatch.setattr(R.metadata, "version", lambda name: {"pandas": "3.0.5", "tzdata": "2026.3"}[name])
    monkeypatch.setattr(R.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(R, "ZoneInfo", lambda zone: object())
    return repo, temporary, stable


@pytest.mark.parametrize("target", ["interpreter", "base"])
def test_saved_venv_cannot_hide_a_temporary_interpreter_or_base(runtime_fixture, monkeypatch, target):
    repo, temporary, _ = runtime_fixture
    attribute = "executable" if target == "interpreter" else "base_prefix"
    monkeypatch.setattr(R.sys, attribute, str(temporary / "python"))
    result = R.runtime_status(repo)
    assert result["state"] == "not_persistent"
    assert result["persistent"] is False


def test_temporary_directory_prefix_does_not_reject_a_different_stable_directory(tmp_path):
    assert R.persistent_path(tmp_path / "temp-production/python.exe", [tmp_path / "temp"])
    assert not R.persistent_path(tmp_path / "temp/base/python.exe", [tmp_path / "temp"])


def test_persistent_base_with_exact_pins_is_ready(runtime_fixture):
    repo, _, _ = runtime_fixture
    result = R.runtime_status(repo)
    assert result["state"] == "ready"
    assert result["persistent"] is result["dependencies_ready"] is True


@pytest.mark.parametrize("actual", [None, "3.0.4"])
def test_dependency_missing_or_drift_is_not_ready(runtime_fixture, monkeypatch, actual):
    repo, _, _ = runtime_fixture

    def version(name):
        if name == "pandas":
            if actual is None:
                raise R.metadata.PackageNotFoundError(name)
            return actual
        return "2026.3"

    monkeypatch.setattr(R.metadata, "version", version)
    result = R.runtime_status(repo)
    assert result["state"] == "missing_dependencies"
    assert result["dependencies_ready"] is False


def test_base_probe_does_not_require_packages_before_creating_venv(runtime_fixture, monkeypatch):
    repo, _, _ = runtime_fixture
    monkeypatch.setattr(R.metadata, "version", lambda name: pytest.fail("base must not inspect packages"))
    result = R.runtime_status(repo, check_dependencies=False)
    assert result["state"] == "ready" and result["dependencies_ready"] is None


def test_timezone_or_import_failure_is_a_probe_failure(runtime_fixture, monkeypatch):
    repo, _, _ = runtime_fixture

    def broken(zone):
        raise ValueError("synthetic missing timezone")

    monkeypatch.setattr(R, "ZoneInfo", broken)
    result = R.runtime_status(repo)
    assert result["state"] == "probe_failed" and result["dependencies_ready"] is False


def test_other_python_version_is_rejected_before_task_registration(runtime_fixture, monkeypatch):
    repo, _, _ = runtime_fixture
    monkeypatch.setattr(R.sys, "version_info", (3, 12, 10))
    assert R.runtime_status(repo, check_dependencies=False)["state"] == "probe_failed"


def test_requirement_file_cannot_silently_install_unpinned_versions(tmp_path):
    (tmp_path / "requirements.txt").write_text("pandas>=3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exact_pins"):
        R.requirements(tmp_path)
