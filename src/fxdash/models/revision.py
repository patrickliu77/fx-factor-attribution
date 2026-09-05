"""Prevent a daily merge from silently mixing different calculation revisions."""

import json
from pathlib import Path

from ..config import MODEL_REVISION


def require_compatible_history(output_dir, *, rewrite_history=False, complete=False):
    root = Path(output_dir)
    snapshot = root / "contract_latest.json"
    if not snapshot.exists() and not any((root / "contract").glob("year=*/part.parquet")):
        return
    previous = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.exists() else {}
    if previous.get("model_revision") == MODEL_REVISION:
        return
    if rewrite_history and complete:
        return
    raise RuntimeError(
        "Saved history uses another model revision. Back up outputs, then run a "
        "complete --mode backfill --rewrite-history from the configured start "
        "with all pairs, windows and models and no --end before resuming live."
    )
