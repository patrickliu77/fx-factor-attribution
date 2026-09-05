"""Text-briefing preview with explicit observation times and no new LLM claims.

Writes only outputs/briefing/preview.json. Existing attribution, residual
narratives and their heartbeats are untouched. A preview cannot masquerade as a
historical 09:00 ET edition; scheduled editions need archived pre-cutoff inputs.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def build_preview(packet: dict) -> dict:
    observed = datetime.fromisoformat(packet["fetched_at"])
    if observed.tzinfo is None:
        raise ValueError("news observation time must include a timezone")
    rows = sorted((r for r in packet["pairs"] if r.get("y") is not None),
                  key=lambda r: (-abs(r["y"]), r["pair"]))
    en, zh = [], []
    for r in rows[:3]:
        pair = r["pair"].replace("USD", "USD/")
        provisional_en = " (provisional)" if r.get("provisional") else ""
        provisional_zh = "（待确认）" if r.get("provisional") else ""
        leading = r["leading"][0] if r.get("leading") else None
        residual = "n/a" if r.get("residual") is None else f"{r['residual']*1e4:+.1f}"
        en.append(f"{pair} {r['y']*1e4:+.1f} bp{provisional_en}; residual {residual} bp.")
        zh.append(f"{pair} {r['y']*1e4:+.1f} bp{provisional_zh}，残差 {residual} bp。")
        if leading:
            en.append(f"Its largest factor contribution was {leading['factor']} at {leading['contribution_bp']:+.1f} bp.")
            zh.append(f"贡献绝对值最大的是 {leading['factor']}，为 {leading['contribution_bp']:+.1f} bp。")
    if rows:
        en.append("Check the leading factor's new releases against its saved sensitivity; a changed sensitivity or a persistently large residual would weaken that reading.")
        zh.append("接下来可把主要因子的新信息与已保存敏感度对照。敏感度变号或残差持续偏大时，对这条解释应更谨慎。")
    warnings = []
    for key, slate in packet["slates"].items():
        if slate.get("error"):
            warnings.append(f"{key}: feed unavailable")
        for item in slate.get("items", []):
            stamp = datetime.fromisoformat(item["observed_at"])
            if stamp.tzinfo is None or stamp > observed:
                raise ValueError("source observation exceeds briefing cutoff")
    if any(r.get("provisional") for r in rows):
        warnings.append("Provisional attribution is included and labelled.")
    if any(r.get("date") != packet["as_of"] for r in rows):
        warnings.append("Pair observation dates differ; inspect each pair's dated context.")
    return {"available": bool(rows), "mode": "preview", "schema_version": "1.0.0",
            "attribution_as_of": packet["as_of"], "news_observed_by": packet["fetched_at"],
            "target_edition": "09:00 America/New_York", "scheduled": False,
            "generator": "deterministic_saved_facts", "data_version": packet["data_version"],
            "text": {"en": " ".join(en), "zh": "".join(zh)},
            "warnings": warnings, "evidence": packet}


def load_preview(root: Path, data_version: str) -> dict:
    path = Path(root) / "briefing" / "preview.json"
    if not path.exists():
        return {"available": False, "reason": "not_generated"}
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
        if out.get("data_version") != data_version:
            return {"available": False, "reason": "attribution_snapshot_changed"}
        # Source slate lives in the archived preview, not an unverified narration.
        return {k: v for k, v in out.items() if k != "evidence"}
    except (ValueError, OSError):
        return {"available": False, "reason": "preview_unreadable"}


def main(argv=None):
    from ..config import OUTPUT_DIR
    from ..web.drivers import collect
    from ..web.store import Snapshot
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    packet = collect(Snapshot(args.output_dir))
    payload = build_preview(packet)
    root = args.output_dir / "briefing"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "preview.json"
    temp = root / "preview.tmp"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(target)
    print(f"Text preview: {len(packet['pairs'])} pairs; {len(payload['warnings'])} warnings; no schedule registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
