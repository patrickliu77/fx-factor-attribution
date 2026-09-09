"""Append-only observations of parsed engine inputs, never inferred past vintages.

Content-addressed parquet objects are shared across captures. A manifest becomes
visible only after all its objects exist. The reader checks hashes before replay.
This captures local availability, not the provider's original publication time.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def now_utc():
    return datetime.now(timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _publish(path, data):
    """Atomic creation without replacing an existing object, even on Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("archive_object_conflict")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("archive_object_conflict")
    finally:
        temporary.unlink(missing_ok=True)


def _stamp(moment):
    if moment.tzinfo is None:
        raise ValueError("timezone_required")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _table(root, value):
    frame = value.to_frame() if isinstance(value, pd.Series) else value
    payload = frame.to_parquet(index=True)
    digest = sha(payload)
    _publish(root / "objects" / (digest + ".parquet"), payload)
    return {"sha256": digest, "rows": len(frame), "columns": list(map(str, frame.columns)),
            "kind": "series" if isinstance(value, pd.Series) else "frame",
            "series_name": value.name if isinstance(value, pd.Series) else None,
            "first_date": str(frame.index.min().date()) if len(frame) else None,
            "last_date": str(frame.index.max().date()) if len(frame) else None}


def _save(output_dir, tables, *, kind, started_at, observed_at, metadata):
    start, end = _stamp(started_at), _stamp(observed_at)
    if started_at > observed_at:
        raise ValueError("capture_clock_reversed")
    root = Path(output_dir) / "input_archive"
    entries = {key: _table(root, value) for key, value in tables.items()}
    payload = {"schema_version": 1, "kind": kind, "capture_started_at": start,
               "observed_at": end, "provider_publication_time": None,
               "availability_claim": "observed_locally_at_capture_only",
               "tables": entries, "metadata": metadata}
    digest = sha(encoded(payload))
    envelope = {"sha256": digest, "capture": payload}
    name = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12] + ".json"
    _publish(root / "captures" / name, encoded(envelope))
    return {"manifest": "input_archive/captures/" + name, "sha256": digest,
            "observed_at": end, "kind": kind, "tables": len(entries)}


def capture_raw(raw, output_dir, *, started_at, source_records, mode, clock=now_utc):
    from dataclasses import fields
    from ..config import MODEL_REVISION, REPO_ROOT
    tables = {}
    for field in fields(raw):
        value = getattr(raw, field.name)
        if isinstance(value, dict):
            tables.update({field.name + "/" + key: obj for key, obj in value.items()})
        elif isinstance(value, (pd.Series, pd.DataFrame)):
            tables[field.name] = value
    # Explicit allowlist excludes provider exception strings, URLs and credentials.
    audit = []
    for row in source_records:
        entry = {k: row[k] for k in ("event", "series", "pair", "ticker", "first", "last", "rows", "n",
                 "observed_at", "splice_date", "break_date", "n_from_user", "n_from_fred") if k in row}
        if row.get("file"):
            entry["file"] = Path(row["file"]).name
        audit.append(entry)
    code = {}
    for folder in ("data", "factors", "models", "attribution", "schedule"):
        for path in sorted((REPO_ROOT / "src" / "fxdash" / folder).rglob("*.py")):
            code[path.relative_to(REPO_ROOT).as_posix()] = sha(path.read_bytes())
    for rel in ("src/fxdash/run.py", "src/fxdash/config.py", "outputs/alignment/profile.json"):
        path = REPO_ROOT / rel
        if path.is_file():
            code[rel] = sha(path.read_bytes())
    return _save(output_dir, tables, kind="engine_inputs", started_at=started_at,
                 observed_at=clock(), metadata={"mode": mode, "model_revision": MODEL_REVISION,
                 "source_records": audit, "code_hashes": code,
                 "hy_oas_splice": str(raw.hy_oas_splice) if raw.hy_oas_splice is not None else None})


def capture_cache(cache_dir, output_dir, *, clock=now_utc):
    """Bootstrap what exists now; no network and no claim about earlier runs."""
    started = clock()
    tables = {path.stem: pd.read_parquet(path) for path in sorted(Path(cache_dir).glob("*.parquet"))}
    if not tables:
        raise ValueError("no_cache_to_capture")
    return _save(output_dir, tables, kind="cache_baseline", started_at=started,
                 observed_at=clock(), metadata={"complete_engine_input": False,
                 "limitation": "Current cache only; source acquisition times unknown. HY splice may be absent."})


def read_capture(path):
    path = Path(path)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    capture = envelope["capture"]
    if sha(encoded(capture)) != envelope["sha256"]:
        raise ValueError("manifest_hash_mismatch")
    tables = {}
    for key, entry in capture["tables"].items():
        digest = entry["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid_object_hash")
        payload = (path.parent.parent / "objects" / (digest + ".parquet")).read_bytes()
        if sha(payload) != digest:
            raise ValueError("object_hash_mismatch")
        frame = pd.read_parquet(io.BytesIO(payload))
        tables[key] = frame.iloc[:, 0].rename(entry["series_name"]) if entry["kind"] == "series" else frame
    return capture, tables


def replay_raw(path):
    from .panel import RawData
    capture, tables = read_capture(path)
    if capture["kind"] != "engine_inputs":
        raise ValueError("cache_baseline_is_not_an_engine_replay")
    values = {name: {} for name in ("fx_levels", "cmdty", "etfs", "us_yields", "foreign")}
    for key, value in tables.items():
        if "/" in key:
            group, name = key.split("/", 1)
            values[group][name] = value
        else:
            values[key] = value
    splice = capture["metadata"]["hy_oas_splice"]
    return RawData(**values, hy_oas_splice=pd.Timestamp(splice) if splice else None)


def main(argv=None):
    from ..config import CACHE_DIR, OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-cache", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.capture_cache:
        result = capture_cache(CACHE_DIR, OUTPUT_DIR)
    elif args.verify:
        capture, tables = read_capture(args.verify)
        result = {"verified": True, "kind": capture["kind"], "tables": len(tables)}
    else:
        parser.error("choose --capture-cache or --verify")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
