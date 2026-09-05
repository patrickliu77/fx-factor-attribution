"""Replay saved driver-search candidates offline; never update a live packet.

Usage: python ops/audit_news_quality.py site/api/news.json TEMP_DIR/audit.json
The output must be outside the repository. Set PYTHONPATH=src before running.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from fxdash.narrative.news_quality import REVISION, screen


def replay(payload):
    packet = payload.get("drivers", payload)
    window = packet["news_window"]
    slates = {}
    for channel, slate in packet["slates"].items():
        candidates = slate.get("items", []) + slate.get("review", []) + slate.get("excluded", [])
        result = screen(candidates, channel, window["start"], window["end"], slate["observed_at"])
        slates[channel] = {"before": {k:len(slate.get(k, [])) for k in ("items", "review", "excluded")},
                           "query": slate.get("query"), "retrieval_error": slate.get("error"),
                           **result}
    return {"mode": "offline_replay_of_saved_candidates", "source_policy": REVISION,
            "attribution_as_of": packet["as_of"], "original_fetched_at": packet["fetched_at"],
            "news_window": window, "slates": slates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    repo = Path(__file__).resolve().parents[1]
    if target.is_relative_to(repo) or target == args.input.resolve():
        parser.error("Write the audit to a separate temporary artifact directory.")
    raw = args.input.read_bytes()
    out = replay(json.loads(raw))
    out.update(input_sha256=hashlib.sha256(raw).hexdigest(),
               audited_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode":out["mode"], "channels":{k:s["coverage"] for k,s in out["slates"].items()}}, indent=2))


if __name__ == "__main__":
    main()
