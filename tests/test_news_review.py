import copy
import json
import re

import pytest

from fxdash.narrative import news_review as R

STAMP = "2026-09-05T12:00:00+00:00"


def packet():
    def item(title, n, **kw):
        return {"title":title, "summary":"Saved snippet", "url":f"https://example.com/{n}",
                "source":"Example", "published":"2026-09-04", "observed_at":STAMP, **kw}
    return {"as_of":"2026-09-04", "fetched_at":STAMP, "source_policy":"driver-sources-1",
            "news_window":{"start":"2026-09-02", "end":"2026-09-05"},
            "slates":{"factor:dVIX":{"query":"VIX when:3d", "observed_at":STAMP, "error":None,
                "items":[item("VIX update", 1), item("TV programme", 2), item("Repeated VIX update", 3)],
                "review":[item("Topic unclear", 4)],
                "excluded":[item("A relevant candidate", 5, reason="test_reason"),
                            item("Old reporting", 6, published="2026-08-01"),
                            item("Broken link", 7, url="javascript:alert(1)")]}},
            "private":"private fixture field"}


def fixture(tmp_path, p=None):
    path = tmp_path / "news.json"
    path.write_text(json.dumps({"drivers":p or packet(), "private":"not exported"}), encoding="utf-8")
    return path, R.freeze([path])


def mark(labels, dataset, title, **values):
    item = next(i for i in dataset["items"] if i["source"]["title"] == title)
    entry = next(l for l in labels["labels"] if l["id"] == item["id"])
    entry.update(reviewed_at=STAMP, **values)
    labels["reviewer"] = {"alias":"test fixture", "origin":"synthetic"}
    return entry


def test_freeze_is_stable_preserves_sources_and_strips_private_fields(tmp_path):
    source, dataset = fixture(tmp_path)
    raw = source.read_bytes()
    assert R.freeze([source]) == dataset
    assert len(dataset["items"]) == 7
    assert dataset["snapshots"][0]["fetched_at"] == STAMP
    assert all(i["source"]["observed_at"] == STAMP for i in dataset["items"])
    assert "private" not in json.dumps(dataset)
    assert source.read_bytes() == raw
    with pytest.raises(ValueError, match="more than once"):
        R.freeze([source, source])


def test_empty_and_reversed_window_fail_before_export(tmp_path):
    p = packet()
    p["slates"] = {}
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(p), encoding="utf-8")
    with pytest.raises(ValueError, match="No saved candidates"):
        R.export_bundle([path], tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    p["news_window"]["end"] = "2020-01-01"
    path.write_text(json.dumps(p), encoding="utf-8")
    with pytest.raises(ValueError, match="reversed"):
        R.freeze([path])


def test_no_labels_means_no_rates_and_no_accuracy_claim(tmp_path):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    report = R.score(dataset, labels)
    assert report["status"] == "unassessed"
    assert report["overall"]["fully_labelled"] == 0
    assert all(m == {"numerator":0, "denominator":0, "rate":None}
               for m in report["overall"]["metrics"].values())
    assert not report["reviewer_identity_verified"]


def test_metric_denominators_partial_labels_and_legitimate_date_exclusions(tmp_path):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    a = mark(labels, dataset, "VIX update", relevance="related", redundancy="unique", evidence="supports_event")
    mark(labels, dataset, "TV programme", relevance="unrelated", redundancy="unique", evidence="insufficient")
    mark(labels, dataset, "Repeated VIX update", relevance="related", redundancy="duplicate", duplicate_of=a["id"], evidence="unclear")
    mark(labels, dataset, "Topic unclear", relevance="related", redundancy="unique", evidence="supports_event")
    mark(labels, dataset, "A relevant candidate", relevance="related", redundancy="unique", evidence="supports_event")
    mark(labels, dataset, "Old reporting", relevance="related", redundancy="unique")
    mark(labels, dataset, "Broken link", relevance="related", redundancy="unique")
    original = copy.deepcopy(labels)
    report = R.score(dataset, labels)
    assert labels == original
    assert report["status"] == "partial_labels" and report["reviewer"]["origin"] == "synthetic"
    overall = report["overall"]
    assert overall["fully_labelled"] == 5
    assert overall["labelled_by_axis"] == {"relevance":7, "redundancy":7, "evidence":5}
    metrics = overall["metrics"]
    assert metrics["unrelated_among_retained"] == R.fraction(1, 3)
    assert metrics["duplicates_among_retained"] == R.fraction(1, 3)
    assert metrics["insufficient_event_evidence_among_retained"] == R.fraction(1, 2)
    assert metrics["excluded_among_eligible_event_candidates"] == R.fraction(1, 3)
    assert metrics["held_for_review_among_eligible_event_candidates"] == R.fraction(1, 3)
    assert report["channels"]["factor:dVIX"] == overall


def test_topic_relevance_alone_does_not_make_a_reference_page_a_false_exclusion(tmp_path):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    mark(labels, dataset, "A relevant candidate", relevance="related", redundancy="unique", evidence="insufficient")
    metrics = R.score(dataset, labels)["overall"]["metrics"]
    assert metrics["excluded_among_eligible_event_candidates"] == R.fraction(0, 0)


@pytest.mark.parametrize("axis", list(R.AXES))
def test_unclear_is_counted_as_reviewed_but_not_a_binary_rate_denominator(tmp_path, axis):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    mark(labels, dataset, "VIX update", **{axis:"unclear"})
    report = R.score(dataset, labels)
    assert report["overall"]["labelled_by_axis"][axis] == 1
    assert all(m["rate"] is None for m in report["overall"]["metrics"].values())


@pytest.mark.parametrize("mutation,match", [
    (lambda l:l.update(dataset_id="wrong"), "different dataset"),
    (lambda l:l.update(schema="future-version"), "different dataset"),
    (lambda l:l["labels"].append(l["labels"][0]), "repeated"),
    (lambda l:l["labels"][0].update(id="unknown"), "Unknown"),
    (lambda l:l["labels"][0].update(relevance="maybe"), "Unknown label"),
    (lambda l:l["labels"][0].update(notes="a"*1201), "1200"),
    (lambda l:l["labels"][0].update(relevance="related", reviewed_at=None), "timestamp"),
    (lambda l:l["labels"][0].update(relevance="related", reviewed_at="2026-09-05"), "timezone"),
    (lambda l:l["labels"][0].update(duplicate_of="unknown"), "Only duplicate"),
    (lambda l:l["labels"][0].update(redundancy="duplicate", reviewed_at=STAMP), "another candidate"),
])
def test_invalid_labels_fail_closed(tmp_path, mutation, match):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    mutation(labels)
    with pytest.raises(ValueError, match=match):
        R.score(dataset, labels)


def test_missing_provenance_is_rejected_and_partial_files_are_allowed(tmp_path):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    entry = mark(labels, dataset, "VIX update", relevance="related")
    labels["labels"] = [entry]
    assert R.score(dataset, labels)["overall"]["labelled_by_axis"]["relevance"] == 1
    labels["reviewer"] = {"alias":"", "origin":None}
    with pytest.raises(ValueError, match="Declare"):
        R.score(dataset, labels)


def test_content_binding_rejects_modified_source_and_duplicate_cycles(tmp_path):
    _, dataset = fixture(tmp_path)
    labels = R.blank_labels(dataset)
    altered = copy.deepcopy(dataset)
    altered["items"][0]["source"]["title"] = "changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        R.score(altered, labels)
    a = mark(labels, dataset, "VIX update", redundancy="duplicate")
    b = mark(labels, dataset, "Repeated VIX update", redundancy="duplicate", duplicate_of=a["id"])
    a["duplicate_of"] = b["id"]
    with pytest.raises(ValueError, match="cycle"):
        R.score(dataset, labels)


def test_duplicates_cannot_cross_channels(tmp_path):
    p = packet()
    p["slates"]["currency:USDJPY"] = copy.deepcopy(p["slates"]["factor:dVIX"])
    _, dataset = fixture(tmp_path, p)
    labels = R.blank_labels(dataset)
    a = next(l for l in labels["labels"] if next(i for i in dataset["items"] if i["id"] == l["id"])["channel"] == "factor:dVIX")
    b = next(i for i in dataset["items"] if i["channel"] == "currency:USDJPY")
    a.update(redundancy="duplicate", duplicate_of=b["id"], reviewed_at=STAMP)
    with pytest.raises(ValueError, match="same snapshot"):
        R.score(dataset, labels)


def test_html_is_blind_escaped_offline_and_uses_shared_fonts(tmp_path):
    p = packet()
    p["slates"]["factor:dVIX"]["items"][0]["title"] = '</script><img src=x onerror=alert(1)> {{SCRIPT}}'
    _, dataset = fixture(tmp_path, p)
    html = R.review_html(dataset)
    raw = re.search(r'<script type="application/json" id="review-data">(.*?)</script>', html, re.S)[1]
    visible = json.loads(raw)
    assert "<img" not in raw
    assert any("{{SCRIPT}}" in i["source"]["title"] for i in visible["items"])
    assert all("decision" not in i and "reason" not in i for i in visible["items"])
    assert "connect-src 'none'" in html
    assert 'font-family:"Outfit"' in html and 'font-family:"IBM Plex Mono"' in html
    assert "data:font/woff2;base64," in html
    from html.parser import HTMLParser
    class Options(HTMLParser):
        current = None
        values = {}
        def handle_starttag(self, tag, attrs):
            fields = dict(attrs)
            if tag == "select":
                self.current = fields["id"]
                self.values[self.current] = []
            if tag == "option" and self.current:
                self.values[self.current].append(fields.get("value"))
        def handle_endtag(self, tag):
            if tag == "select":
                self.current = None
    parsed = Options()
    parsed.feed(html)
    for axis, values in R.AXES.items():
        assert parsed.values[axis] == ["", *values]


def test_export_and_score_are_exclusive_and_do_not_touch_inputs(tmp_path):
    source, dataset = fixture(tmp_path)
    raw = source.read_bytes()
    out = tmp_path / "bundle"
    assert R.export_bundle([source], out) == dataset
    assert {p.name for p in out.iterdir()} == {"dataset.json", "labels.blank.json", "review.html", "FONT-LICENSES.txt"}
    with pytest.raises(FileExistsError):
        R.export_bundle([source], out)
    report = tmp_path / "report.json"
    args = ["score", "--dataset", str(out / "dataset.json"), "--labels", str(out / "labels.blank.json"), "--out", str(report)]
    assert R.main(args) == 0 and R.read_json(report)["status"] == "unassessed"
    with pytest.raises(SystemExit):
        R.main(args)
    assert source.read_bytes() == raw


def test_project_and_pipeline_paths_are_rejected(isolated_outputs):
    from fxdash.config import REPO_ROOT
    for path in (REPO_ROOT / "review-new", isolated_outputs / "review-new"):
        with pytest.raises(ValueError, match="outside"):
            R.outside_project(path)
