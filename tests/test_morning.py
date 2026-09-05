import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fxdash.narrative import driver_notes as D, morning as M, morning_dispatch as G
from fxdash.web.drivers import leading_rows
from fxdash.web.store import Snapshot
from test_web import _write_fixture


def moment(hour=13, minute=50, date="2026-01-08"):
    return datetime.fromisoformat(f"{date}T{hour:02d}:{minute:02d}:00+00:00")


def packet(root, timestamp=None):
    _write_fixture(root)
    s = Snapshot(root)
    when = (timestamp or moment()).isoformat()
    rows = leading_rows(s)
    slates = {}
    for r in rows:
        r["currency_news"] = "currency:"+r["pair"]
        keys = [r["currency_news"]]
        for f in r["leading"]:
            f["news_key"] = "factor:"+f["factor"]
            keys.append(f["news_key"])
        for k in keys:
            slates[k] = {"items": [{"url": "https://example.com/news", "source": "Example",
                                    "published": "2026-01-08", "observed_at": when,
                                    "title": "Volatility eases as equity markets settle", "summary": ""}],
                         "error": None, "excluded": [], "observed_at": when}
    return {"pairs": rows, "slates": slates, "as_of": "2026-01-07", "fetched_at": when,
            "data_version": s.data_version, "attribution_observed_at": when}


def note(row):
    blocks = {
        "en": {"event": "Example reported calmer equity markets.",
               "sources_used": ["S1"]},
        "zh": {"event": "Example 报道，股票市场较为平静。",
               "sources_used": ["S1"]},
    }
    return {"assessment": "related_context", "factor": row["leading"][0]["factor"],
            "evidence": [{"source_id": "S1", "quote": "Volatility eases as equity markets settle"}],
            "field_sources": {f: ["S1"] for f in D.FIELDS}, **blocks}


@pytest.mark.parametrize("stamp,expected", [
    ("2026-01-08T13:49:00+00:00", "idle"), ("2026-01-08T13:50:00+00:00", "prepare"),
    ("2026-01-08T14:00:00+00:00", "publish"), ("2026-01-08T15:00:00+00:00", "idle"),
    ("2026-07-08T12:50:00+00:00", "prepare"), ("2026-07-08T13:00:00+00:00", "publish"),
    ("2026-07-08T14:00:00+00:00", "idle"), ("2026-09-05T13:00:00+00:00", "idle"),
    ("2026-03-09T12:50:00+00:00", "prepare"), ("2026-11-02T13:50:00+00:00", "prepare"),
])
def test_new_york_clock_including_dst_and_weekends(stamp, expected):
    assert M.slot(datetime.fromisoformat(stamp)) == expected


def test_naive_time_is_rejected_and_monday_uses_friday():
    with pytest.raises(ValueError):
        M.slot(datetime(2026, 9, 7, 9))
    assert M.previous_session(datetime(2026, 9, 7).date()) == "2026-09-04"


def test_fact_definitions_exclude_target():
    for pair in ("USDJPY", "USDAUD", "USDMXN", "USDEUR", "USDNOK", "USDCAD"):
        d = D.definitions(pair)
        assert pair not in d["DOLLAR_LOO"]["members"]
        assert pair not in d["CARRY_LOO"]["low"]+d["CARRY_LOO"]["high"]


def test_valid_note_and_code_owned_definitions(tmp_path):
    p = packet(tmp_path)
    r = p["pairs"][0]
    n = note(r)
    assert D.validate(n, r, D.source_set(p, r), p["fetched_at"]) == []
    n["en"]["event"] = "The yen is a member of this basket."
    assert any("code_owned" in e for e in D.validate(n,r,D.source_set(p,r),p["fetched_at"]))


@pytest.mark.parametrize("mutation,error", [
    (lambda n: n["en"].update(event="The move was +50 bp."), "numeric_assertion"),
    (lambda n: n["zh"].update(sources_used=["S9"]), "citation_mismatch"),
    (lambda n: n["en"].update(event="Rates caused the move."), "causal_wording"),
    (lambda n: n["en"].update(event="an increase in the CARRY_LOO factor."), "code_owned"),
    (lambda n: n["zh"].update(event="因子数值减少。"), "code_owned"),
    (lambda n: n["en"].update(event="The yen will rise."), "directional_forecast"),
    (lambda n: n["zh"].update(event="不是一个事件，而是另一个事件。"), "style_or_url"),
    (lambda n: n["en"].update(event="If equities remain calm,"), "incomplete_sentence"),
    (lambda n: n["evidence"][0].update(quote="Invented words absent from the report"), "unsupported_excerpt"),
    (lambda n: n["field_sources"].update(event=[]), "missing_evidence"),
    (lambda n: n.update(factor="INVENTED"), "factor_not_leading"),
    (lambda n: n["zh"].update(event="无法辨认的名字报道股票市场平静。"), "outlet_name_mismatch"),
])
def test_failed_notes_are_rejected(tmp_path, mutation, error):
    p = packet(tmp_path)
    r = p["pairs"][0]
    n = note(r)
    mutation(n)
    assert any(error in e for e in D.validate(n,r,D.source_set(p,r),p["fetched_at"]))


def test_source_time_and_old_reporting_rejected(tmp_path):
    p = packet(tmp_path)
    r = p["pairs"][0]
    src = D.source_set(p,r)
    src[0]["observed_at"] = moment(14).isoformat()
    assert "source_after_cutoff" in D.validate(note(r),r,src,p["fetched_at"])
    src[0]["observed_at"] = p["fetched_at"]
    src[0]["published"] = "2025-01-01"
    assert "source_after_cutoff" in D.validate(note(r),r,src,p["fetched_at"])


def test_no_sources_no_calls_and_failed_call_retained(tmp_path):
    p = packet(tmp_path)
    class Client:
        def complete(self, *args):
            raise RuntimeError("secret-provider-url")
    records = D.generate(p, Client())
    assert all(r["errors"] == ["RuntimeError"] and not r["published"] for r in records)
    assert "secret" not in str(records)
    p["slates"] = {}
    assert all(r["errors"] == ["no_sources"] for r in D.generate(p, Client()))
    with pytest.raises(ValueError):
        D.generate(p, Client(), max_calls=4)


def test_prepare_persists_first_and_is_idempotent(tmp_path):
    p = packet(tmp_path)
    calls = []
    class Client:
        def complete(self, *args):
            assert (tmp_path / "briefing/days/2026-01-08/packet.json").exists()
            calls.append(1)
            return note(p["pairs"][0])
    collector = lambda s, clock: copy.deepcopy(p)
    result = M.prepare(tmp_path, clock=lambda:moment(), collector=collector, client_factory=Client)
    assert result["state"] == "prepared"
    n = len(calls)
    assert n > 0
    assert M.prepare(tmp_path, clock=lambda:moment(), collector=collector, client_factory=Client)["state"] == "already_prepared"
    assert len(calls) == n
    root = tmp_path / "briefing/days/2026-01-08"
    before = (root / "packet.json").read_bytes()
    first = M.finalize(tmp_path, clock=lambda:moment(14,0))
    second = M.finalize(tmp_path, clock=lambda:moment(14,5))
    assert first == second and first["state"] == "ready"
    assert not first["late_publication"]
    assert (root / "packet.json").read_bytes() == before


def test_late_or_missing_inputs_do_not_get_retroactive_news(tmp_path):
    result = M.finalize(tmp_path, clock=lambda:moment(14,10))
    assert result["state"] == "inputs_unavailable" and result["late_publication"]
    assert result["text"] == {}
    with pytest.raises(ValueError):
        M.prepare(tmp_path, clock=lambda:moment(14,10))


def test_late_collection_and_stale_attribution_skip_llm(tmp_path):
    p = packet(tmp_path, moment(14,1))
    def never():
        pytest.fail("No LLM call for invalid morning inputs")
    result = M.prepare(tmp_path, clock=lambda:moment(), collector=lambda s,clock:p, client_factory=never)
    assert result["state"] == "ineligible_packet"
    assert M.finalize(tmp_path, clock=lambda:moment(14,5))["state"] == "inputs_unavailable"
    p["fetched_at"] = moment().isoformat()
    p["attribution_observed_at"] = moment().isoformat()
    p["as_of"] = "2025-12-01"
    assert not M.packet_eligible(p,moment())[0]


def test_draft_mismatch_falls_back_to_saved_numbers(tmp_path):
    p = packet(tmp_path)
    root = tmp_path / "briefing/days/2026-01-08"
    M.atomic_json(root / "packet.json", p)
    M.atomic_json(root / "draft.json", {"packet_hash":"bad", "notes":[{}]})
    result = M.finalize(tmp_path, clock=lambda:moment(14,0))
    assert result["state"] == "numbers_only" and not result["notes"]
    assert "+800000.0 bp" in result["text"]["en"]


def test_dispatch_idle_no_work_and_publish_retry_does_not_prepare(tmp_path):
    def never(*a, **k):
        pytest.fail("Unexpected prepare")
    assert G.dispatch(tmp_path,tmp_path,clock=lambda:moment(20),prepare_fn=never)["state"] == "idle"
    calls = []
    def fail(repo):
        calls.append(1)
        raise RuntimeError
    first = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,0),prepare_fn=never,publisher=fail)
    assert first["state"] == "publish_failed"
    second = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,5),prepare_fn=never,publisher=lambda p:calls.append(1))
    assert second["state"] == "published"
    third = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,10),prepare_fn=never,publisher=never)
    assert third["state"] == "already_published" and len(calls) == 2


def test_lock_releases_on_failure(tmp_path):
    path = tmp_path / "task.lock"
    with M.DayLock(path):
        with pytest.raises(M.Busy):
            with M.DayLock(path):
                pass
    with M.DayLock(path):
        pass


def test_old_prompt_and_unbound_sources_never_promote(tmp_path):
    p = packet(tmp_path)
    r = p["pairs"][0]
    record = {"pair": r["pair"], "raw": note(r), "published": True,
              "prompt_version": "old", "sources": []}
    assert M.compose_edition(p,[record],moment=moment())["state"] == "numbers_only"
    record["prompt_version"] = D.PROMPT_VERSION
    record["raw"]["evidence"][0]["quote"] = "A quote present only in a forged source"
    record["sources"] = [{"id":"S1", "title":"A quote present only in a forged source"}]
    assert M.compose_edition(p,[record],moment=moment())["state"] == "numbers_only"
    assert M.compose_edition(p,{"bad":"shape"},moment=moment())["state"] == "numbers_only"


def test_usage_and_code_written_checks_are_archived(tmp_path):
    p = packet(tmp_path)
    class Client:
        model = "test-model"
        calls = []
        def complete(self,*args):
            self.calls.append({"promptTokenCount":10,"candidatesTokenCount":3,"totalTokenCount":13})
            return note(p["pairs"][0])
    records = D.generate(p, Client())
    assert all(r["attempted"] and r["usage"]["totalTokenCount"] == 13 for r in records)
    result = M.compose_edition(p,records,moment=moment())
    assert result["notes"][0]["checks"]["en"]["condition"].startswith("If ")
    assert "watch_summary" not in result["notes"][0]["note"]["en"]


def test_preview_version_and_frozen_edition_priority(tmp_path):
    root = tmp_path / "briefing"
    M.atomic_json(root / "driver-preview.json", {"data_version":"v", "prompt_version":D.PROMPT_VERSION,
                  "validator_version":D.VALIDATOR_VERSION,
                  "state":"ready", "evidence":{"private":"archive"}})
    assert M.read_latest(tmp_path,"other") == {}
    assert "evidence" not in M.read_latest(tmp_path,"v")
    M.atomic_json(root / "days/2026-01-08/edition.json", {"state":"inputs_unavailable", "date":"2026-01-08"})
    assert M.read_latest(tmp_path,"other")["date"] == "2026-01-08"
