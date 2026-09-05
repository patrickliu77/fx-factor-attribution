"""Offline, frozen news-review bundles. No fetching, model calls or live writes.

Export saved /news responses or driver packets, then score separately saved labels.
Labels are reviewer declarations, not independently verified ground truth.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .news_quality import canonical_url

SCHEMA = "news-review-1"
AXES = {"relevance": ("related", "unrelated", "unclear"),
        "redundancy": ("unique", "duplicate", "unclear"),
        "evidence": ("supports_event", "insufficient", "unclear")}
ORIGINS = ("human", "ai_assisted", "synthetic")
SOURCE_FIELDS = ("title", "summary", "url", "source", "publisher_domain", "published", "observed_at")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read_json(path):
    def reject_constant(value):
        raise ValueError("Non-finite JSON value")
    return json.loads(Path(path).read_bytes(), parse_constant=reject_constant)


def freeze(paths):
    """Keep every candidate in the explicitly supplied snapshots, without relabelling."""
    snapshots, items, seen = [], [], set()
    for path in paths:
        raw = Path(path).read_bytes()
        sid = hashlib.sha256(raw).hexdigest()
        if sid in seen:
            raise ValueError("The same snapshot was supplied more than once")
        seen.add(sid)
        payload = json.loads(raw)
        packet = payload.get("drivers", payload)
        window = {k:packet["news_window"][k] for k in ("start", "end")}
        for key in ("start", "end"):
            if date.fromisoformat(window[key]).isoformat() != window[key]:
                raise ValueError("Publication window needs ISO dates")
        if window["start"] > window["end"]:
            raise ValueError("Publication window is reversed")
        snapshots.append({"id":sid, "attribution_as_of":packet["as_of"],
                          "fetched_at":packet["fetched_at"], "news_window":window,
                          "source_policy":packet.get("source_policy", "legacy")})
        for channel, slate in sorted(packet["slates"].items()):
            for bucket, decision in (("items", "retained"), ("review", "review"), ("excluded", "excluded")):
                for rank, source in enumerate(slate.get(bucket, [])):
                    clean = {k:source.get(k) for k in SOURCE_FIELDS}
                    if any(v is not None and not isinstance(v, str) for v in clean.values()):
                        raise ValueError("Candidate fields must be strings or null")
                    clean["observed_at"] = source.get("observed_at") or slate.get("observed_at")
                    row = {"snapshot_id":sid, "channel":channel, "query":slate.get("query"),
                           "source":clean, "decision":decision, "reason":source.get("reason"),
                           "duplicate_of_url":source.get("duplicate_of"), "rank":rank,
                           "retrieval_error":slate.get("error")}
                    row["id"] = "c-"+digest(row)
                    items.append(row)
    if not snapshots or not items:
        raise ValueError("No saved candidates to review")
    # Hash order avoids grouping the reviewer-facing sheet by policy decision.
    core = {"schema":SCHEMA, "snapshots":sorted(snapshots, key=lambda s:s["id"]),
            "items":sorted(items, key=lambda i:i["id"])}
    return {**core, "dataset_id":digest(core)}


def verify_dataset(dataset):
    if not isinstance(dataset, dict) or set(dataset) != {"schema", "snapshots", "items", "dataset_id"}:
        raise ValueError("Invalid dataset structure")
    if dataset["schema"] != SCHEMA or digest({k:v for k,v in dataset.items() if k != "dataset_id"}) != dataset["dataset_id"]:
        raise ValueError("Dataset version or content hash mismatch")
    ids = [i["id"] for i in dataset["items"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate ids")


def blank_labels(dataset):
    verify_dataset(dataset)
    return {"schema":SCHEMA, "dataset_id":dataset["dataset_id"],
            "reviewer":{"alias":"", "origin":None},
            "labels":[{"id":i["id"], **dict.fromkeys(AXES), "duplicate_of":None,
                       "notes":"", "reviewed_at":None} for i in dataset["items"]]}


def validate_labels(dataset, labels):
    verify_dataset(dataset)
    if not isinstance(labels, dict) or set(labels) != {"schema", "dataset_id", "reviewer", "labels"}:
        raise ValueError("Invalid label structure")
    if labels["schema"] != SCHEMA or labels["dataset_id"] != dataset["dataset_id"]:
        raise ValueError("Labels belong to a different dataset or version")
    reviewer = labels["reviewer"]
    if (not isinstance(reviewer, dict) or set(reviewer) != {"alias", "origin"}
            or not isinstance(reviewer["alias"], str) or len(reviewer["alias"]) > 64
            or reviewer["origin"] not in (None, *ORIGINS)):
        raise ValueError("Invalid reviewer declaration")
    if not isinstance(labels["labels"], list):
        raise ValueError("Labels must be an array")
    candidates = {i["id"]:i for i in dataset["items"]}
    result, touched = {}, False
    for label in labels["labels"]:
        if not isinstance(label, dict) or set(label) != {"id", *AXES, "duplicate_of", "notes", "reviewed_at"}:
            raise ValueError("Invalid candidate label structure")
        cid = label["id"]
        if not isinstance(cid, str) or cid not in candidates or cid in result:
            raise ValueError("Unknown or repeated candidate id")
        if any(label[k] not in (None, *values) for k,values in AXES.items()):
            raise ValueError("Unknown label value")
        if not isinstance(label["notes"], str) or len(label["notes"]) > 1200:
            raise ValueError("Notes must contain at most 1200 characters")
        changed = any(label[k] is not None for k in AXES) or bool(label["notes"].strip())
        stamp = label["reviewed_at"]
        if changed or stamp is not None:
            try:
                if datetime.fromisoformat(stamp).tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("Reviewed labels need a timestamp with a timezone") from None
        target = label["duplicate_of"]
        if label["redundancy"] == "duplicate":
            if not isinstance(target, str) or target not in candidates or target == cid:
                raise ValueError("Duplicate labels need another candidate id")
            if any(candidates[cid][k] != candidates[target][k] for k in ("snapshot_id", "channel")):
                raise ValueError("Compare duplicates within the same snapshot and search channel")
        elif target is not None:
            raise ValueError("Only duplicate labels can reference another candidate")
        result[cid] = dict(label)
        touched |= changed
    for cid in result:
        visited, cursor = set(), cid
        while cursor in result and result[cursor]["duplicate_of"]:
            if cursor in visited:
                raise ValueError("Duplicate references contain a cycle")
            visited.add(cursor)
            cursor = result[cursor]["duplicate_of"]
    if touched and (not reviewer["alias"].strip() or reviewer["origin"] is None):
        raise ValueError("Declare a reviewer alias and label origin before scoring")
    return result


def fraction(numerator, denominator):
    return {"numerator":numerator, "denominator":denominator,
            "rate":numerator/denominator if denominator else None}


def score(dataset, labels):
    by_id = validate_labels(dataset, labels)
    snapshots = {s["id"]:s for s in dataset["snapshots"]}
    def group(rows):
        matrix = {k:dict.fromkeys(("retained", "review", "excluded"), 0)
                  for k in ("related", "unrelated", "unclear", "unlabelled")}
        counts = {k:0 for k in AXES}
        keep_known = keep_wrong = duplicate_known = duplicate_kept = evidence_known = insufficient = 0
        eligible = lost = deferred = complete = 0
        for item in rows:
            lab = by_id.get(item["id"], {})
            rel = lab.get("relevance")
            matrix[rel or "unlabelled"][item["decision"]] += 1
            for axis in AXES:
                counts[axis] += lab.get(axis) is not None
            complete += all(lab.get(axis) is not None for axis in AXES)
            if item["decision"] == "retained":
                keep_known += rel in ("related", "unrelated")
                keep_wrong += rel == "unrelated"
                duplicate_known += lab.get("redundancy") in ("unique", "duplicate")
                duplicate_kept += lab.get("redundancy") == "duplicate"
                evidence_known += lab.get("evidence") in ("supports_event", "insufficient")
                insufficient += lab.get("evidence") == "insufficient"
            window = snapshots[item["snapshot_id"]]["news_window"]
            source = item["source"]
            try:
                published = date.fromisoformat(source.get("published"))
                in_window = window["start"] <= published.isoformat() <= window["end"]
            except (ValueError, TypeError):
                in_window = False
            if (rel == "related" and lab.get("redundancy") == "unique"
                    and lab.get("evidence") == "supports_event" and in_window
                    and canonical_url(source.get("url")) and (source.get("title") or "").strip()):
                eligible += 1
                lost += item["decision"] == "excluded"
                deferred += item["decision"] == "review"
        return {"candidates":len(rows), "labelled_by_axis":counts, "fully_labelled":complete,
                "relevance_by_saved_decision":matrix,
                "metrics":{
                    "unrelated_among_retained":fraction(keep_wrong, keep_known),
                    "duplicates_among_retained":fraction(duplicate_kept, duplicate_known),
                    "insufficient_event_evidence_among_retained":fraction(insufficient, evidence_known),
                    "excluded_among_eligible_event_candidates":fraction(lost, eligible),
                    "held_for_review_among_eligible_event_candidates":fraction(deferred, eligible)}}
    overall = group(dataset["items"])
    return {"schema":SCHEMA, "dataset_id":dataset["dataset_id"],
            "reviewer":dict(labels["reviewer"]), "reviewer_identity_verified":False,
            "status":"unassessed" if not any(overall["labelled_by_axis"].values()) else (
                "complete_labels" if overall["fully_labelled"] == overall["candidates"] else "partial_labels"),
            "scope":"supplied_snapshot_candidates_only", "snapshot_count":len(snapshots),
            "overall":overall, "channels":{channel:group([i for i in dataset["items"] if i["channel"] == channel])
                                            for channel in sorted({i["channel"] for i in dataset["items"]})},
            "limits":["Unknown and unlabelled axes are excluded from rate denominators.",
                      "False exclusion uses related, unique, event-supporting, dated, in-window candidates with usable links and titles.",
                      "Review deferrals are reported separately from exclusions.",
                      "Repeated channels and snapshots can contain the same reporting; observations are not independent.",
                      "These labels do not measure web-wide recall, factual truth, causality or generated-note accuracy.",
                      "Reviewer identity and human involvement are self-reported; AI-assisted and synthetic labels remain labelled."]}


def review_html(dataset):
    verify_dataset(dataset)
    assets = Path(__file__).with_name("review_assets")
    static = Path(__file__).resolve().parents[1] / "web" / "static"
    css = (assets / "review.css").read_text(encoding="utf-8")
    for family, filename in (("Outfit", "outfit-latin.woff2"), ("IBM Plex Mono", "ibm-plex-mono-latin-400.woff2")):
        font = base64.b64encode((static / "fonts" / filename).read_bytes()).decode("ascii")
        css += f'\n@font-face{{font-family:"{family}";src:url(data:font/woff2;base64,{font}) format("woff2");font-weight:{"100 900" if family == "Outfit" else "400"};font-display:swap}}'
    js = (assets / "review.js").read_text(encoding="utf-8")
    visible = {"schema":SCHEMA, "dataset_id":dataset["dataset_id"], "snapshots":dataset["snapshots"],
               "items":[{k:i[k] for k in ("id", "snapshot_id", "channel", "query", "source")} for i in dataset["items"]]}
    # Never embed the saved decisions/reasons in the blind review sheet.
    blob = encoded(visible).decode("utf-8").replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    csp_hash = lambda text:base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
    csp = (f"default-src 'none'; script-src 'sha256-{csp_hash(js)}'; style-src 'sha256-{csp_hash(css)}'; "
           "font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
    template = (assets / "review.html").read_text(encoding="utf-8")
    substitutions = {"CSP":csp, "STYLE":css, "DATA":blob, "SCRIPT":js}
    return re.sub(r"\{\{(CSP|STYLE|DATA|SCRIPT)\}\}", lambda m:substitutions[m[1]], template)


def outside_project(path):
    from ..config import OUTPUT_DIR, REPO_ROOT
    target = Path(path).resolve()
    if target.is_relative_to(REPO_ROOT.resolve()) or target.is_relative_to(OUTPUT_DIR.resolve()):
        raise ValueError("Review artifacts must be outside the repository and pipeline outputs")
    return target


def export_bundle(paths, out):
    dataset = freeze(paths)
    html = review_html(dataset)
    target = outside_project(out)
    # Exclusive creation protects unfinished human review work from overwrite.
    target.mkdir(parents=True, exist_ok=False)
    for name, obj in (("dataset.json", dataset), ("labels.blank.json", blank_labels(dataset))):
        (target / name).write_bytes(encoded(obj))
    (target / "review.html").write_text(html, encoding="utf-8", newline="\n")
    fonts = Path(__file__).resolve().parents[1] / "web/static/fonts"
    (target / "FONT-LICENSES.txt").write_text("\n\n".join((fonts / name).read_text(encoding="utf-8")
        for name in ("OFL-Outfit.txt", "OFL-IBM-Plex-Mono.txt")), encoding="utf-8")
    return dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("inputs", type=Path, nargs="+")
    export.add_argument("--out", type=Path, required=True)
    evaluate = commands.add_parser("score")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    evaluate.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            dataset = export_bundle(args.inputs, args.out)
            print(json.dumps({"dataset_id":dataset["dataset_id"], "candidates":len(dataset["items"]),
                              "labelled":0, "status":"unassessed"}))
        else:
            report = score(read_json(args.dataset), read_json(args.labels))
            report["evaluated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            target = outside_project(args.out)
            with target.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            print(json.dumps({k:report[k] for k in ("status", "reviewer", "overall")}, ensure_ascii=True))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f"Review stopped: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
