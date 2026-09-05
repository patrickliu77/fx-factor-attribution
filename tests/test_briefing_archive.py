import copy
import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fxdash.narrative import briefing_archive as A, morning as M, morning_dispatch as G
from fxdash.web.app import STATIC_DIR, create_app
from fxdash.web import build as B
from test_morning import moment, packet, note
from fxdash.narrative import driver_notes as D
from test_web import EMPTY_RSS, _write_cache, _write_fixture


def edition(root, day="2026-01-08"):
    p = packet(root)
    record = {"pair": p["pairs"][0]["pair"], "raw": note(p["pairs"][0]),
              "published": True, "prompt_version": D.PROMPT_VERSION}
    out = M.compose_edition(p,[record],moment=moment(14,0,date=day))
    out["scheduled"] = True
    return out


def save(root, value):
    path = root / "briefing" / "days" / value["date"] / "edition.json"
    M.atomic_json(path,value)
    return path


def test_archive_preserves_frozen_text_and_has_no_private_fields(tmp_path):
    value = edition(tmp_path)
    value["model_internal"] = {"private":"do not export"}
    value["notes"][0]["raw_reply"] = "private reply"
    path = save(tmp_path,value)
    before = path.read_bytes()
    result = A.dashboard(tmp_path,"a later attribution revision",clock=lambda:moment())
    assert result["current"]["text"] == value["text"]
    assert result["current"]["edition_hash"] == M.digest(value)
    text = json.dumps(result)
    assert all(word not in text for word in ("private", "model_internal", "raw_reply", '"evidence": {'))
    assert "event" in result["current"]["notes"][0]["note"]["en"]
    assert path.read_bytes() == before


def test_bounded_history_keeps_gaps_and_ignores_non_dates(tmp_path):
    value = edition(tmp_path)
    for i in range(23):
        v = copy.deepcopy(value)
        v["date"] = (datetime(2026,1,1)+timedelta(days=i*2)).date().isoformat()
        save(tmp_path,v)
    (tmp_path / "briefing/days/2026-99-99").mkdir()
    result = A.dashboard(tmp_path)
    assert result["total_editions"] == 23 and len(result["history"]) == A.HISTORY_LIMIT
    assert result["history"][0]["date"] > result["history"][-1]["date"]
    assert "2026-01-02" not in {e["date"] for e in result["history"]}


@pytest.mark.parametrize("mutation", [
    lambda v:v.update(date="2025-01-01"), lambda v:v.update(notes=[{}]),
    lambda v:v.update(warnings="not a list"), lambda v:v.update(text=None),
])
def test_corrupt_latest_does_not_silently_show_older_text(tmp_path,mutation):
    first = edition(tmp_path)
    save(tmp_path,first)
    latest = copy.deepcopy(first)
    latest["date"] = "2026-01-09"
    path = save(tmp_path,latest)
    mutation(latest)
    M.atomic_json(path,latest)
    result = A.dashboard(tmp_path)
    assert result["current"]["state"] == "archive_unreadable"
    assert result["current"]["date"] == "2026-01-09"
    assert result["history"][1]["state"] == "ready"


def test_latest_attempt_is_separate_from_latest_available_edition(tmp_path):
    value = edition(tmp_path)
    path = save(tmp_path,value)
    M.atomic_json(path.parent / "publish.json",{"state":"published","edition_hash":"wrong","error":"private-url"})
    assert A.dashboard(tmp_path)["latest_run"]["push"]["state"] == "receipt_mismatch"
    M.atomic_json(path.parent / "publish.json",{"state":"published","edition_hash":M.digest(value)})
    assert A.dashboard(tmp_path)["latest_run"]["push"]["state"] == "published"
    M.atomic_json(tmp_path / "briefing/days/2026-01-09/prepare.claim",{"started_at":moment().isoformat()})
    result = A.dashboard(tmp_path)
    assert result["current"]["date"] == "2026-01-08"
    assert result["latest_run"]["date"] == "2026-01-09"
    assert result["latest_run"]["prepare"]["state"] == "attempt_recorded"
    assert result["current_push"]["state"] == "published"


def test_corrupt_frozen_edition_prevents_push_and_is_left_unchanged(tmp_path):
    v = edition(tmp_path)
    path = save(tmp_path,v)
    M.atomic_json(path,{"state":"broken"})
    before = path.read_bytes()
    with pytest.raises(M.FrozenEditionError):
        M.finalize(tmp_path,clock=lambda:moment(14,0))
    result = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,0),
                        publisher=lambda p:pytest.fail("Must not push an unreadable edition"))
    assert result["state"] == "finalize_failed" and result["attempts"] == 1
    assert path.read_bytes() == before
    assert A.dashboard(tmp_path)["latest_run"]["push"]["state"] == "finalize_failed"


def test_push_attempt_is_visible_before_build_and_retry_is_counted(tmp_path):
    save(tmp_path,edition(tmp_path))
    def publisher(repo):
        assert A.dashboard(tmp_path)["latest_run"]["push"]["state"] == "publishing"
        raise RuntimeError("private URL")
    first = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,0),publisher=publisher)
    assert first["state"] == "publish_failed" and first["attempts"] == 1
    second = G.dispatch(tmp_path,tmp_path,clock=lambda:moment(14,5),publisher=lambda p:None)
    assert second["state"] == "published" and second["attempts"] == 2
    assert A.dashboard(tmp_path)["latest_run"]["push"]["state"] == "published"


def test_build_binds_the_edition_it_actually_exports(tmp_path,monkeypatch):
    from fxdash.web import headlines, market
    monkeypatch.setattr(headlines,"_fetch",lambda q:EMPTY_RSS)
    monkeypatch.setattr(market,"_fetch_dxy",lambda:None)
    root=tmp_path / "pipeline"
    root.mkdir()
    v=edition(root)
    save(root,v)
    cache=tmp_path / "cache"
    _write_cache(cache)
    app=create_app(root,cache_dir=cache)
    payload=TestClient(app).get("/api/news").json()
    assert payload["briefing_archive"]["total_editions"] == 1
    result=B.build(tmp_path / "site",app=app)
    assert result["briefing"] == {"date":v["date"],"edition_hash":M.digest(v)}
    saved=json.loads((tmp_path / "site/api/news.json").read_text(encoding="utf-8"))
    assert result["briefing"]["edition_hash"] == saved["briefing"]["edition_hash"]


def test_browser_clock_and_delivery_are_independent_of_a_stale_build():
    node=shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser clock helpers")
    uri=(STATIC_DIR / "briefing-board.js").as_uri()
    script=f'''
      globalThis.localStorage={{getItem:()=>"en"}};
      const {{dueEdition,freshness,delivery,briefingBoardHtml}}=await import({json.dumps(uri)});
      const assert=(await import('node:assert/strict')).default;
      const samples=[
        ['2026-01-08T13:59:00Z','2026-01-07'],['2026-01-08T14:00:00Z','2026-01-08'],
        ['2026-09-05T13:00:00Z','2026-09-04'],['2026-09-07T12:59:00Z','2026-09-04'],
        ['2026-09-07T13:00:00Z','2026-09-07'],['2026-03-09T13:00:00Z','2026-03-09'],
        ['2026-11-02T13:59:00Z','2026-10-30'],['2026-11-02T14:00:00Z','2026-11-02'],
      ];
      for(const [stamp,expected] of samples) assert.equal(dueEdition(new Date(stamp)),expected);
      const brief={{mode:'edition',date:'2026-09-04',edition_hash:'abc',state:'ready'}};
      assert.equal(freshness(brief,new Date('2026-09-07T13:00:00Z')).state,'older_edition');
      assert.equal(freshness({{mode:'preview'}},new Date()).state,'no_edition');
      const archive={{latest_run:{{date:brief.date,push:{{state:'publishing'}}}},history:[]}};
      assert.equal(delivery(brief,archive,{{mode:'live'}}),'publishing');
      const build={{mode:'static',info:{{briefing:{{date:brief.date,edition_hash:'abc'}}}}}};
      assert.equal(delivery(brief,archive,build),'included_in_build');
      build.info.briefing.edition_hash='different';
      assert.equal(delivery(brief,archive,build),'not_confirmed_in_build');
      assert.ok(briefingBoardHtml(null,{{history:[],total_editions:0}},{{}}).includes('No formal editions'));
    '''
    subprocess.run([node,"--input-type=module","-e",script],check=True,capture_output=True,text=True)
