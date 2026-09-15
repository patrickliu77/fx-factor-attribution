"""Read-only checks of a restored cloud seed. Does not prove live delivery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def frozen_profile(profile):
    from ..config import OFFSETS
    if not isinstance(profile, dict) or profile.get("frozen") is not True:
        return False
    entries = profile.get("entries")
    if not isinstance(entries, list):
        return False
    expected = {(pair, group): offset for pair, groups in OFFSETS.items() for group, offset in groups.items()}
    found = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        key = (entry.get("pair"), entry.get("factor_class"))
        if (key not in expected or key in found
                or type(entry.get("frozen_offset")) is not int
                or type(entry.get("chosen_offset")) is not int
                or entry["frozen_offset"] != expected[key] or entry["chosen_offset"] != expected[key]):
            return False
        found.add(key)
    return found == set(expected)


def inspect(root, *, snapshot_factory=None):
    from .. import config
    from ..web.store import Snapshot
    from ..narrative import subscriptions
    from .snapshot import inventory

    root = Path(root)
    files = inventory(root)
    names = {f["path"] for f in files["files"]}
    required = {"data/user/fred_BAMLH0A0HYM2.csv", "outputs/alignment/profile.json",
                "outputs/run_manifest.json", "outputs/status.json", "outputs/source_as_of.json"}
    checks = []
    if required - names:
        checks.append("required_seed_files_missing")
    if not any(n.startswith("data/cache/") for n in names):
        checks.append("source_cache_missing")
    if not any(n.startswith("outputs/contract/") for n in names):
        checks.append("attribution_history_missing")
    result = {"state": "incomplete", "files": len(files["files"]), "bytes": files["total_bytes"],
              "checks": checks, "network_called": False, "email_sent": False,
              "scope": "saved_snapshot_only_not_live_or_delivery_acceptance"}
    if checks:
        return result
    try:
        profile = json.loads((root / "outputs/alignment/profile.json").read_text(encoding="utf-8"))
        if not frozen_profile(profile):
            checks.append("frozen_alignment_mismatch")
        saved = (snapshot_factory or Snapshot)(root / "outputs", cache_dir=root / "data/cache")
        expected = {(p, w, m) for p in config.PAIRS for w in config.WINDOWS for m in config.MODELS}
        if set(saved.combos) != expected:
            checks.append("attribution_combinations_mismatch")
        if saved.manifest.get("model_revision") != config.MODEL_REVISION:
            checks.append("model_revision_mismatch")
        if any(not c.dates or c.dates[-1] != saved.date_last for c in saved.combos.values()):
            checks.append("attribution_dates_mismatch")
        result.update(attribution_as_of=saved.date_last, combinations=len(saved.combos),
                      model_revision=saved.manifest.get("model_revision"),
                      email_configuration_valid=bool(subscriptions.config(root / "outputs")))
    except Exception:
        checks.append("saved_snapshot_unreadable")
    result["state"] = "ready_for_shadow" if not checks else "incomplete"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.root)
        print(json.dumps(result))
        return 0 if result["state"] == "ready_for_shadow" else 2
    except Exception as exc:
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__,
                          "network_called": False, "email_sent": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
