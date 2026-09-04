"""The outputs/ isolation fixture, pinned (CLAUDE.md rule 23).

Regression guard for the dump_records incident: a test once stubbed
STATUS_PATH but not run.OUTPUT_DIR, so main_guarded's failure path wrote
through the unstubbed constant and clobbered the real
outputs/validation_log.jsonl on every full pytest run. The fixture in
conftest.py redirects path constants by discovery instead of by list;
these tests fail if that discovery ever misses a module or an attribute.
"""

import json
import pathlib
import sys

import pytest

import fxdash.config as config_mod

# Captured at module import (collection time), before any fixture patches
# config. This is the real repo outputs/ directory.
REAL_OUTPUT_ROOT = pathlib.Path(config_mod.OUTPUT_DIR).resolve()


def _real_path_leaks():
    """Every (module, attr) whose Path still resolves under real outputs/."""
    leaks = []
    for name, module in list(sys.modules.items()):
        if not (name == "fxdash" or name.startswith("fxdash.")):
            continue
        for attr, value in list(vars(module).items()):
            if not isinstance(value, pathlib.Path):
                continue
            try:
                value.resolve().relative_to(REAL_OUTPUT_ROOT)
            except ValueError:
                continue
            leaks.append(f"{name}.{attr} = {value}")
    return leaks


def test_no_module_path_constant_points_at_real_outputs(isolated_outputs):
    assert _real_path_leaks() == []


def test_known_writers_are_redirected(isolated_outputs):
    import fxdash.heartbeat as heartbeat
    import fxdash.narrative.store as nstore
    import fxdash.run as run_mod
    import fxdash.status as status_mod
    import fxdash.web.newsfeed as newsfeed

    for path in (run_mod.OUTPUT_DIR, status_mod.STATUS_PATH,
                 heartbeat.HEARTBEAT_PATH, nstore.NARRATIVE_DIR,
                 newsfeed.NARRATIVE_DIR):
        assert pathlib.Path(path).resolve().is_relative_to(
            isolated_outputs.resolve()), path


def test_guarded_failure_writes_only_inside_the_sandbox(
    isolated_outputs, monkeypatch
):
    """The original hole, replayed: main_guarded's failure path calls
    dump_records(run.OUTPUT_DIR / "validation_log.jsonl"); that write must
    land in the isolated root, and the real validation log must not change."""
    from fxdash.data.base import record, reset_records
    from fxdash.run import main_guarded

    real_log = REAL_OUTPUT_ROOT / "validation_log.jsonl"
    before = real_log.read_bytes() if real_log.exists() else None

    reset_records()

    def boom(argv=None):
        record("isolation_canary")
        raise RuntimeError("isolation canary")

    monkeypatch.setattr("fxdash.run.main", boom)
    try:
        with pytest.raises(RuntimeError, match="isolation canary"):
            main_guarded([])
    finally:
        reset_records()

    sandbox_log = isolated_outputs / "validation_log.jsonl"
    assert sandbox_log.exists()
    lines = [json.loads(line) for line in
             sandbox_log.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event") == "isolation_canary" for e in lines)

    after = real_log.read_bytes() if real_log.exists() else None
    assert after == before
