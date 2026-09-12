import copy
from datetime import datetime
from types import SimpleNamespace

import pytest

from fxdash.narrative import morning as M, catchup as C, briefing_archive as A, acceptance
from test_morning import packet, moment, note
from test_briefing_archive import edition, save


def inputs(root):
    p = packet(root, moment(16, 0))
    p["pairs"] = [dict(p["pairs"][0], pair=pair) for pair in sorted(C.PAIRS)]
    return p


def options(p):
    return dict(clock=lambda: moment(16, 0), collector=lambda *args, **kw: copy.deepcopy(p),
                snapshot_factory=lambda root: SimpleNamespace(date_last=p["as_of"], manifest={}),
                client_factory=lambda: None, publisher=lambda repo: None)


@pytest.mark.parametrize("stamp,expected", [
    ("2026-01-08T14:04:59+00:00",False), ("2026-01-08T14:05:00+00:00",True),
    ("2026-07-08T13:05:00+00:00",True), ("2026-03-09T13:05:00+00:00",True),
    ("2026-11-02T14:05:00+00:00",True), ("2026-09-05T18:00:00+00:00",False),
    ("2026-09-08T03:00:00+00:00",True), ("2026-09-08T04:00:00+00:00",False),
])
def test_time_gate(stamp, expected):
    assert C.due(datetime.fromisoformat(stamp)) == expected


def test_idle_has_no_writes(tmp_path):
    before = set(tmp_path.rglob("*"))
    assert C.run(tmp_path,tmp_path,clock=lambda:moment()) == {"state":"idle"}
    assert set(tmp_path.rglob("*")) == before


def test_late_packet_is_saved_once_and_never_passes_morning_acceptance(tmp_path):
    p = inputs(tmp_path)
    settings = options(p)
    calls = []
    class Client:
        def complete(self,*args):
            assert (tmp_path / "briefing/catchup/2026-01-08/generation.claim").exists()
            calls.append(1)
            return note(p["pairs"][0])
    settings["client_factory"] = Client
    assert C.run(tmp_path,tmp_path,**settings)["state"] == "published"
    root = tmp_path / "briefing/catchup/2026-01-08"
    original = (root / "edition.json").read_bytes()
    assert calls and len(calls) <= 3
    count = len(calls)
    settings["collector"] = lambda *a,**k: pytest.fail("Cannot fetch again")
    assert C.run(tmp_path,tmp_path,**settings)["state"] == "already_available"
    assert len(calls) == count and (root / "edition.json").read_bytes() == original
    body = M.read_json(root / "edition.json")
    assert body["mode"] == "catchup" and body["scheduled"] is False
    assert body["target_cutoff"] == p["fetched_at"] and body["morning_target"] != p["fetched_at"]
    assert not list((tmp_path / "briefing/days").rglob("edition.json"))
    assert not acceptance.assess_day(tmp_path,"2026-01-08")["passed"]


def test_publish_retry_uses_frozen_text(tmp_path):
    p=inputs(tmp_path)
    settings=options(p)
    settings["publisher"]=lambda repo: (_ for _ in ()).throw(RuntimeError("private-url"))
    assert C.run(tmp_path,tmp_path,**settings)["state"] == "publish_failed"
    settings.update(publisher=lambda repo:None,client_factory=lambda:pytest.fail("No second model call"),
                    collector=lambda *a,**kw:pytest.fail("No second fetch"))
    result=C.run(tmp_path,tmp_path,**settings)
    assert result["state"]=="published" and result["attempts"]==2


def test_existing_morning_is_republished_without_generation(tmp_path):
    v=edition(tmp_path)
    path=save(tmp_path,v)
    original=path.read_bytes()
    p=inputs(tmp_path)
    settings=options(p)
    settings["collector"]=lambda *a,**kw:pytest.fail("Morning already exists")
    result=C.run(tmp_path,tmp_path,**settings)
    assert result["state"]=="published" and result["kind"]=="edition"
    assert path.read_bytes()==original
    assert not list((tmp_path / "briefing/catchup").rglob("edition.json"))


def test_interrupted_generation_resumes_saved_numbers_without_call(tmp_path):
    p=inputs(tmp_path)
    settings=options(p)
    def interrupt():
        raise KeyboardInterrupt()
    settings["client_factory"]=interrupt
    with pytest.raises(KeyboardInterrupt):
        C.run(tmp_path,tmp_path,**settings)
    settings.update(client_factory=lambda:pytest.fail("Generation was claimed"),
                    collector=lambda *a,**kw:pytest.fail("Use original packet"))
    assert C.run(tmp_path,tmp_path,**settings)["state"]=="published"
    body=M.read_json(tmp_path / "briefing/catchup/2026-01-08/edition.json")
    assert body["state"]=="numbers_only" and not body["notes"]
    assert "generation_interrupted_saved_figures_used" in body["warnings"]


def test_wait_for_quant_and_network_without_spending_claim(tmp_path):
    p=inputs(tmp_path)
    settings=options(p)
    settings["snapshot_factory"]=lambda root:SimpleNamespace(date_last="2026-01-01")
    assert C.run(tmp_path,tmp_path,**settings)["state"]=="waiting_for_attribution"
    settings=options(p)
    for slate in p["slates"].values(): slate["error"]="network_unavailable"
    assert C.run(tmp_path,tmp_path,**settings)["state"]=="waiting_for_news"
    assert not list((tmp_path / "briefing/catchup").rglob("generation.claim"))
    for slate in p["slates"].values(): slate["error"]=None
    assert C.run(tmp_path,tmp_path,**settings)["state"]=="published"


def test_valid_morning_packet_keeps_original_publication_route(tmp_path):
    p=packet(tmp_path)
    M.atomic_json(tmp_path / "briefing/days/2026-01-08/packet.json",p)
    result=C.run(tmp_path,tmp_path,clock=lambda:moment(14,6),
                 collector=lambda *a,**kw:pytest.fail("Morning packet should publish first"))
    assert result["state"]=="waiting_for_morning"


@pytest.mark.parametrize("change", [
    lambda p:p.update(as_of="2026-01-09"),
    lambda p:p.update(fetched_at="2026-01-08T17:00:00+00:00"),
    lambda p:p.update(attribution_observed_at="2026-01-08T16:00:00"),
    lambda p:p["pairs"].pop(),
    lambda p:p["pairs"][0].update(date="2026-01-06"),
    lambda p:next(iter(p["slates"].values()))["items"][0].update(observed_at="2026-01-08T16:01:00+00:00"),
])
def test_bad_packets_cannot_become_current_context(tmp_path,change):
    p=inputs(tmp_path)
    change(p)
    assert C.packet_checks(p,moment(16,0))


def test_catchup_archive_is_separate_sanitized_and_cannot_hide_next_missing_day(tmp_path):
    p=inputs(tmp_path)
    assert C.run(tmp_path,tmp_path,**options(p))["state"]=="published"
    report=A.dashboard(tmp_path)
    assert report["total_editions"]==0 and report["total_catchups"]==1
    assert report["history"]==[] and report["current"]["mode"]=="catchup"
    assert "evidence" not in report["current"] and "records" not in report["current"]
    assert report["current_push"]["state"]=="published"
    morning=edition(tmp_path,day="2026-01-09")
    save(tmp_path,morning)
    assert A.dashboard(tmp_path)["current"]["mode"]=="edition"


def test_corrupt_frozen_catchup_is_not_replaced_or_published(tmp_path):
    p=inputs(tmp_path)
    path=tmp_path / "briefing/catchup/2026-01-08/edition.json"
    M.atomic_json(path,{"mode":"catchup"})
    before=path.read_bytes()
    settings=options(p)
    settings["publisher"]=lambda repo:pytest.fail("Cannot publish corrupt text")
    assert C.run(tmp_path,tmp_path,**settings)["state"]=="catchup_failed"
    assert path.read_bytes()==before


def test_build_binds_catchup_mode_and_keeps_morning_count_zero(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fxdash.web import headlines, market, build as B
    from fxdash.web.app import create_app
    from test_web import EMPTY_RSS, _write_cache
    monkeypatch.setattr(headlines,"_fetch",lambda q:EMPTY_RSS)
    monkeypatch.setattr(market,"_fetch_dxy",lambda:None)
    root=tmp_path / "pipeline"
    root.mkdir()
    p=inputs(root)
    assert C.run(root,root,**options(p))["state"]=="published"
    cache=tmp_path / "cache"
    _write_cache(cache)
    app=create_app(root,cache_dir=cache)
    payload=TestClient(app).get("/api/news").json()
    assert payload["briefing"]["mode"]=="catchup"
    assert payload["briefing_archive"]["total_editions"]==0
    result=B.build(tmp_path / "site",app=app)
    assert result["briefing"]=={"mode":"catchup","date":"2026-01-08",
                                "edition_hash":payload["briefing"]["edition_hash"]}


def test_browser_labels_and_same_day_history_remain_distinct():
    import json
    import shutil
    import subprocess
    from fxdash.web.app import STATIC_DIR
    node=shutil.which("node")
    if not node: pytest.skip("Node is required for display helpers")
    script="""
      import assert from 'node:assert/strict';
      globalThis.localStorage={getItem:()=> 'en',setItem:()=>{}};
      globalThis.document={documentElement:{}};
      const B=await import(BOARD), I=await import(LANG), C=await import(CONTEXT);
      const late={available:true,mode:'catchup',date:'2026-09-08',state:'numbers_only',edition_hash:'late',
        text:{en:'Saved numbers.',zh:'已保存数字。'},notes:[],attribution_as_of:'2026-09-07',
        generated_at:'2026-09-08T16:00:00Z',news_observed_by:'2026-09-08T15:55:00Z'};
      const original={...late,mode:'edition',state:'inputs_unavailable',edition_hash:'original'};
      const archive={history:[original],catchup_history:[late],total_editions:1,total_catchups:1};
      assert.equal(B.freshness(late,new Date('2026-09-08T17:00:00Z')).state,'current_catchup');
      assert.equal(B.freshness(late,new Date('2026-09-09T17:00:00Z')).state,'older_edition');
      assert.equal(B.delivery(late,archive,{mode:'static',info:{briefing:{date:late.date,mode:'catchup',edition_hash:'late'}}}),'included_in_build');
      assert.equal(B.delivery(late,archive,{mode:'static',info:{briefing:{date:late.date,edition_hash:'late'}}}),'not_confirmed_in_build');
      for (const lang of ['en','zh']) {
        I.setLang(lang);
        const html=C.briefingHtml(late), desk=B.briefingBoardHtml(late,archive,{},new Date('2026-09-08T17:00:00Z'));
        assert.ok(html.includes(lang==='en'?'Catch-up briefing':'补发简报'));
        assert.ok(html.includes(lang==='en'?'no fixed publication deadline':'不设固定出刊时刻'));
        assert.ok(!desk.includes(lang==='en'?'requirement remains unmet':'按时出刊要求仍未满足'));
        assert.ok(!html.includes(lang==='en'?'Validation preview':'运行验收预览'));
        assert.ok(desk.includes('value="2026-09-08"'));
        assert.ok(!/[—–]/.test(html));
      }
    """.replace("BOARD",json.dumps((STATIC_DIR / "briefing-board.js").as_uri())).replace(
        "LANG",json.dumps((STATIC_DIR / "i18n.js").as_uri())).replace(
        "CONTEXT",json.dumps((STATIC_DIR / "context.js").as_uri()))
    result=subprocess.run([node,"--input-type=module","-e",script],capture_output=True,text=True,encoding="utf-8")
    assert result.returncode==0,result.stderr
