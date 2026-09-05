import json

import pytest

from fxdash.config import MODEL_REVISION
from fxdash.models.revision import require_compatible_history


def test_empty_history_can_start(tmp_path):
    require_compatible_history(tmp_path)


@pytest.mark.parametrize("previous", [None, "older-revision"])
@pytest.mark.parametrize("rewrite,complete", [(False, False), (True, False), (False, True)])
def test_revision_change_requires_complete_explicit_rewrite(tmp_path, previous, rewrite, complete):
    (tmp_path / "contract_latest.json").write_text(
        json.dumps({"model_revision": previous}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="complete"):
        require_compatible_history(tmp_path, rewrite_history=rewrite, complete=complete)


def test_complete_rewrite_can_migrate_legacy_history(tmp_path):
    (tmp_path / "contract_latest.json").write_text('{"schema_version":"1.1.0"}', encoding="utf-8")
    require_compatible_history(tmp_path, rewrite_history=True, complete=True)


def test_current_revision_allows_daily_run(tmp_path):
    (tmp_path / "contract_latest.json").write_text(
        json.dumps({"model_revision": MODEL_REVISION}), encoding="utf-8")
    require_compatible_history(tmp_path)


def test_contract_without_snapshot_cannot_silently_start_live(tmp_path):
    part = tmp_path / "contract/year=2026"
    part.mkdir(parents=True)
    (part / "part.parquet").touch()
    with pytest.raises(RuntimeError):
        require_compatible_history(tmp_path)
