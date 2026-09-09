import json
from dataclasses import fields
from datetime import datetime, timezone

import pandas as pd
import pytest

from fxdash.data import vintages as V

NOW = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)


def capture(raw, root):
    return V.capture_raw(raw, root, started_at=NOW, clock=lambda: NOW, mode="live",
                         source_records=[{"event": "fallback_cache", "series": "DGS2",
                                          "last": "2026-09-04", "reason": "secret=do-not-save"}])


def test_exact_raw_replay_including_hy_splice(tmp_path, synthetic_raw):
    saved = capture(synthetic_raw, tmp_path)
    restored = V.replay_raw(tmp_path / saved["manifest"])
    for field in fields(synthetic_raw):
        original, replayed = getattr(synthetic_raw, field.name), getattr(restored, field.name)
        if isinstance(original, dict):
            assert set(original) == set(replayed)
            for key in original:
                (pd.testing.assert_series_equal if isinstance(original[key], pd.Series) else pd.testing.assert_frame_equal)(original[key], replayed[key], check_freq=False)
        elif isinstance(original, pd.Series):
            pd.testing.assert_series_equal(original, replayed, check_freq=False)
        elif isinstance(original, pd.DataFrame):
            pd.testing.assert_frame_equal(original, replayed, check_freq=False)
        else:
            assert original == replayed
    assert "do-not-save" not in (tmp_path / saved["manifest"]).read_text()


def test_capture_append_only_and_revisions_survive(tmp_path, synthetic_raw):
    first = capture(synthetic_raw, tmp_path)
    root = tmp_path / "input_archive"
    originals = {p.name: p.read_bytes() for p in (root / "objects").glob("*")}
    capture(synthetic_raw, tmp_path)
    assert len(list((root / "captures").glob("*.json"))) == 2
    assert len(list((root / "objects").glob("*"))) == len(originals)
    synthetic_raw.hy_oas.iloc[-1] += 1
    third = capture(synthetic_raw, tmp_path)
    assert len(list((root / "objects").glob("*"))) == len(originals)+1
    assert V.replay_raw(tmp_path / first["manifest"]).hy_oas.iloc[-1] != V.replay_raw(tmp_path / third["manifest"]).hy_oas.iloc[-1]
    assert all((root / "objects" / k).read_bytes() == v for k, v in originals.items())


def test_corruption_is_not_silently_overwritten(tmp_path, synthetic_raw):
    saved = capture(synthetic_raw, tmp_path)
    blob = next((tmp_path / "input_archive" / "objects").glob("*.parquet"))
    blob.write_bytes(b"broken")
    with pytest.raises(ValueError, match="object_hash_mismatch"):
        V.read_capture(tmp_path / saved["manifest"])
    with pytest.raises(ValueError, match="archive_object_conflict"):
        capture(synthetic_raw, tmp_path)


def test_manifest_tamper_rejected(tmp_path, synthetic_raw):
    saved = capture(synthetic_raw, tmp_path)
    path = tmp_path / saved["manifest"]
    data = json.loads(path.read_text())
    data["capture"]["observed_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        V.read_capture(path)


def test_cache_is_current_baseline_not_historical_pit(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    pd.DataFrame({"x": [1., 2.]}, index=pd.date_range("2020-01-01", periods=2)).to_parquet(cache / "x.parquet")
    saved = V.capture_cache(cache, tmp_path, clock=lambda: NOW)
    manifest, _ = V.read_capture(tmp_path / saved["manifest"])
    assert manifest["observed_at"].startswith("2026-09-07")
    assert manifest["provider_publication_time"] is None
    with pytest.raises(ValueError, match="cache_baseline"):
        V.replay_raw(tmp_path / saved["manifest"])


def test_naive_or_reversed_clock_rejected(tmp_path, synthetic_raw):
    with pytest.raises(ValueError, match="timezone_required"):
        V.capture_raw(synthetic_raw, tmp_path, started_at=NOW.replace(tzinfo=None), clock=lambda: NOW,
                      source_records=[], mode="live")


def test_archive_failure_stops_before_model_or_contract_write(monkeypatch, synthetic_raw, isolated_outputs):
    import fxdash.run as pipeline
    monkeypatch.setattr(pipeline.panel_mod, "load_raw", lambda: synthetic_raw)
    def fail(*args, **kwargs):
        raise OSError("archive disk unavailable")
    monkeypatch.setattr(pipeline, "capture_raw", fail)
    monkeypatch.setattr(pipeline, "check_offsets", lambda *args: pytest.fail("Must stop before modelling"))
    with pytest.raises(OSError):
        pipeline.main_guarded(["--mode", "live", "--skip-report"])
    assert json.loads((isolated_outputs / "status.json").read_text())["state"] == "red"
    assert not list((isolated_outputs / "contract").rglob("*.parquet"))
