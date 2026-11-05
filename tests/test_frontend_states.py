"""Precise saved-data and request-state regressions without browser/network access."""
import json
import shutil
import subprocess

import pytest

from fxdash.web.app import STATIC_DIR


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend state checks")
    script = r"""
      import assert from 'node:assert/strict';
      import vm from 'node:vm';
      import {readFileSync} from 'node:fs';
      globalThis.localStorage={getItem:()=> 'en',setItem:()=>{}};
      globalThis.document={documentElement:{}};
      const I=await import(LANGUAGE);
      const P=await import(PRESENTATION);
      const doc={documentElement:{},body:{dataset:{}},querySelector:()=>null,getElementById:()=>null};
      const sandbox=vm.createContext({assert,Number,JSON,Math,Promise,document:doc,
        window:{addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},
        t:I.t,getLang:I.getLang,setLang:I.setLang,rankedContributions:P.rankedContributions,residualFlag:P.residualFlag,
        fetch:()=>{throw new Error('Every request must be synthetic');},
        requestAnimationFrame:callback=>callback(),CSS:{escape:value=>value},scrollX:0,scrollY:0});
      let source=readFileSync(SOURCE,'utf8').replace(/^import .*;\r?$/gm,'');
      source=source.slice(0,source.lastIndexOf('(async function start()'));
      vm.runInContext(source,sandbox);
      const run=code=>vm.runInContext(code,sandbox);
    """
    script = script.replace("LANGUAGE", json.dumps((STATIC_DIR / "i18n.js").as_uri())).replace(
        "PRESENTATION", json.dumps((STATIC_DIR / "presentation.js").as_uri())).replace(
        "SOURCE", json.dumps(str(STATIC_DIR / "app.js")))
    result = subprocess.run([node, "--input-type=module", "-e", script + body], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("row,state", [
    ({"systematic": 0, "exogenous": 0, "residual": 0}, "empty"),
    ({"systematic": None, "exogenous": 1, "residual": 2}, "unavailable"),
    ({"exogenous": 1, "residual": 2}, "unavailable"),
    ({"systematic": "missing", "exogenous": 1, "residual": 2}, "unavailable"),
])
def test_missing_and_zero_contributions_do_not_paint_a_residual_allocation(row, state):
    run_js(f"""
      sandbox.fixture={json.dumps(row)};
      assert.equal(run('meterOf(fixture).state'),{json.dumps(state)});
      const html=run("cardHtml('USDJPY',fixture,{{}},null)");
      assert.match(html,/data-meter-state="{state}"/);
      assert.doesNotMatch(html,/class="meter__segment/);
      assert.equal((html.match(/class="meter-note"/g)||[]).length,1);
      assert.doesNotMatch(html,/flex:1;background/);
    """)


def test_finite_signed_contributions_retain_proportions_and_invalid_values_fail_closed():
    run_js(r"""
      run(`const good={systematic:-2,exogenous:1,residual:-1};
        const allocation=meterOf(good);
        assert.equal(allocation.state,'available');
        assert.deepEqual(allocation.shares,[.5,.25,.25]);
        const html=cardHtml('USDJPY',good,{},null);
        for(const group of ['systematic','exogenous','residual']) {
          assert.ok(html.includes('meter__segment--'+group));
          assert.ok(html.includes('split__swatch--'+group));
        }
        assert.ok(!html.includes('meter-note')&&!html.includes('background:var(--sys)'));
        assert.ok(html.includes('flex-grow:0.5;flex-basis:0'));
        for(const bad of [NaN,Infinity,-Infinity,'2',null,undefined]) {
          const row={systematic:bad,exogenous:1,residual:2};
          assert.equal(meterOf(row).state,'unavailable');
          assert.ok(!cardHtml('USDJPY',row,{},null).includes('class="meter__segment'));
        }
        assert.equal(meterOf({systematic:Number.MAX_VALUE,exogenous:Number.MAX_VALUE,residual:1}).state,'unavailable');
        assert.equal(fmtBp1(NaN),'n/a');
        assert.equal(fmtBp1(Infinity),'n/a');
        assert.equal(fmtBp1('bad'),'n/a');
        assert.equal(fmtBp1(1.25),'+1.3');`);
    """)


def test_request_failures_have_retry_classification_without_exposing_endpoint_text():
    run_js(r"""
      run("build.mode='live'");
      for(const status of [503,500,429,408,403,404,400]) {
        sandbox.fetch=async()=>({ok:false,status});
        let error;try{await run("api('/private/endpoint')");}catch(value){error=value;}
        assert.equal(error.retryable,status>=500||[429,408].includes(status));
        assert.equal(error.status,status);
        assert.ok(!error.message.includes('/private/endpoint'));
      }
      sandbox.fetch=async()=>{throw new Error('private transport details');};
      await assert.rejects(run("api('/private/endpoint')"),error=>error.retryable===true&&!error.message.includes('private'));
      sandbox.fetch=async()=>({ok:true,json:async()=>{throw new Error('bad payload');}});
      await assert.rejects(run("api('/private/endpoint')"),error=>error.retryable===true);
      sandbox.fetch=async()=>({ok:true,json:async()=>({saved:true})});
      assert.equal((await run("api('/public')")).saved,true);
      run("build.mode='unknown'");
      sandbox.fetch=async()=>({ok:false,status:503});
      await assert.rejects(run("api('/public')"),error=>error.retryBuild===true);
    """)


def test_updated_tape_is_pending_until_acceptance_and_metadata_or_failure_does_not_clear_quotes():
    run_js(r"""
      const original={available:true,trading_day:true,session_date:'2026-11-03',
        items:[{pair:'USDJPY',date:'2026-11-03',last:150,prev:149,chg_pct:.67}]};
      sandbox.original=original;sandbox.painted=[];
      run(`build.mode='live';state.quotes=original;
        document.querySelector=()=>({});paintTape=value=>painted.push(value);syncNotices=()=>{};`);
      sandbox.next={...original,fetched_at:'new metadata only'};
      run("api=async()=>next");await run('renderTape()');
      assert.equal(run('state.pendingQuotes'),null);
      run('painted.length=0');
      sandbox.next={...original,items:[{...original.items[0],last:151}]};
      await run('renderTape()');
      assert.equal(run('state.quotes.items[0].last'),150);
      assert.equal(run('state.pendingQuotes.items[0].last'),151);
      assert.equal(run('painted.length'),0);
      sandbox.next={available:false,items:[]};await run('renderTape()');
      assert.equal(run('state.quotes.items[0].last'),150);
      assert.equal(run('state.pendingQuotes.items[0].last'),151);
      assert.equal(run('painted.length'),0);
      run('api=async()=>{throw new Error("offline")}');await run('renderTape()');
      assert.equal(run('state.quotes.items[0].last'),150);
      run('acceptQuotes(state.pendingQuotes)');
      assert.equal(run('state.quotes.items[0].last'),151);
      assert.equal(run('state.pendingQuotes'),null);
      assert.equal(run('painted.length'),1);
    """)


def test_late_ticker_response_cannot_replace_the_newer_pending_observation():
    run_js(r"""
      sandbox.resolvers=[];
      run(`build.mode='live';state.quotes={available:true,session_date:'2026-11-03',items:[]};
        document.querySelector=()=>({});syncNotices=()=>{};paintTape=()=>{};
        api=()=>new Promise(resolve=>resolvers.push(resolve));`);
      const first=run('renderTape()'),second=run('renderTape()');
      sandbox.resolvers[1]({available:true,session_date:'2026-11-05',items:[]});await second;
      sandbox.resolvers[0]({available:true,session_date:'2026-11-04',items:[]});await first;
      assert.equal(run('state.pendingQuotes.session_date'),'2026-11-05');
      assert.equal(run('state.quotes.session_date'),'2026-11-03');
    """)


def test_first_valid_quotes_do_not_update_only_the_header_of_an_existing_empty_fx_view():
    run_js(r"""
      sandbox.next={available:true,session_date:'2026-11-04',items:[{pair:'USDJPY',date:'2026-11-04',last:151}]};
      sandbox.painted=[];
      run(`build.mode='live';state.quotes=null;document.querySelector=()=>({});
        api=async()=>next;paintTape=value=>painted.push(value);syncNotices=()=>{};`);
      await run('renderTape()');
      assert.equal(run('state.quotes'),null);
      assert.equal(run('state.pendingQuotes.items[0].last'),151);
      assert.equal(run('painted.length'),0);
    """)


def test_a_newer_observation_matching_accepted_quotes_clears_an_obsolete_pending_notice():
    run_js(r"""
      sandbox.original={available:true,session_date:'2026-11-03',items:[{pair:'USDJPY',date:'2026-11-03',last:150}]};
      sandbox.next={...sandbox.original,items:[{...sandbox.original.items[0],last:151}]};
      run(`build.mode='live';state.quotes=original;document.querySelector=()=>({});
        api=async()=>next;paintTape=()=>{};syncNotices=()=>{};`);
      await run('renderTape()');
      assert.equal(run('state.pendingQuotes.items[0].last'),151);
      sandbox.next={...sandbox.original,fetched_at:'later observation'};
      await run('renderTape()');
      assert.equal(run('state.pendingQuotes'),null);
      assert.equal(run('state.quotes.items[0].last'),150);
    """)


def test_focus_restore_is_generation_guarded_and_does_not_steal_a_new_focus():
    run_js(r"""
      const frames=[],scrolls=[];
      sandbox.requestAnimationFrame=callback=>frames.push(callback);
      sandbox.window.scrollTo=value=>scrolls.push(value);
      const button={focus:()=>{doc.activeElement=button;}};
      const userLink={focus:()=>{doc.activeElement=userLink;}};
      doc.querySelector=()=>button;doc.querySelectorAll=()=>[];
      sandbox.fixtureContext={restore:{control:{kind:'model',value:'ridge'},x:0,y:420,scrolls:[]}};
      run('shownContext=fixtureContext');
      doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>true)');frames.shift()();
      assert.equal(doc.activeElement,button);
      assert.equal(scrolls[0].top,420);
      doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>false)');frames.shift()();
      assert.equal(doc.activeElement,doc.body);
      assert.equal(scrolls.length,1);
      run('restoreControl(fixtureContext,()=>true)');
      doc.activeElement=userLink;frames.shift()();
      assert.equal(doc.activeElement,userLink);
    """)


def test_scroll_anchor_lock_survives_superseded_requests_and_restores_the_original_setting():
    run_js(r"""
      const frames=[];sandbox.requestAnimationFrame=callback=>frames.push(callback);
      doc.documentElement.style={overflowAnchor:'auto'};
      doc.documentElement.getBoundingClientRect=()=>({});
      let busy=true;doc.getElementById=()=>({hasAttribute:()=>busy});
      run('holdScrollAnchor()');
      assert.equal(doc.documentElement.style.overflowAnchor,'none');
      run('holdScrollAnchor()');
      run('releaseScrollAnchor(()=>false)');frames.shift()();
      assert.equal(doc.documentElement.style.overflowAnchor,'none');
      run('releaseScrollAnchor(()=>true)');frames.shift()();
      assert.equal(doc.documentElement.style.overflowAnchor,'none');
      busy=false;
      run('releaseScrollAnchor(()=>true)');frames.shift()();
      assert.equal(doc.documentElement.style.overflowAnchor,'auto');
      run('releaseScrollAnchor(()=>true)');frames.shift()();
      assert.equal(doc.documentElement.style.overflowAnchor,'auto');
    """)


def test_quote_refresh_has_a_view_focus_fallback_and_new_range_focus_takes_precedence():
    run_js(r"""
      let refresh;
      const button={addEventListener:(type,callback)=>{refresh=callback;}};
      const notices={children:[{}],querySelector:selector=>selector==='[data-quotes-refresh]'?button:null};
      const host={querySelector:()=>notices};
      doc.getElementById=()=>host;doc.querySelector=()=>({});
      sandbox.intent=null;
      run(`build.mode='live';state.pendingQuotes={available:true,items:[]};
        render=options=>{intent=options;};syncNotices();`);
      refresh();
      assert.equal(sandbox.intent.control.kind,'view');
      assert.equal(sandbox.intent.quoteSnapshot.available,true);
      const frames=[];sandbox.requestAnimationFrame=callback=>frames.push(callback);
      sandbox.window.scrollTo=()=>{};doc.querySelectorAll=()=>[];
      let selector;
      const target={focus:()=>{doc.activeElement=target;}};
      doc.querySelector=value=>{selector=value;return target;};
      sandbox.fixtureContext={restore:{control:sandbox.intent.control,x:0,y:180,scrolls:[]}};
      run('shownContext=fixtureContext');doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>true)');frames.shift()();
      assert.equal(selector,'#view');assert.equal(doc.activeElement,target);
      const rangeButton={dataset:{r:'1m'},matches:value=>value==='button[data-r]',
        closest:value=>value==='[data-ranges]'?{dataset:{ranges:'USDMXN'}}:null};
      doc.activeElement=rangeButton;
      const current=run('focusedControl()');
      assert.equal(current.kind,'range');assert.equal(current.pair,'USDMXN');assert.equal(current.value,'1m');
      sandbox.fixtureContext.restore.control=current;doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>true)');frames.shift()();
      assert.equal(selector,'[data-ranges="USDMXN"] [data-r="1m"]');
      assert.equal(doc.activeElement,target);
    """)


def test_retry_without_an_original_control_requests_view_focus():
    run_js(r"""
      let retry;
      const attributes=new Set();
      const view={setAttribute:name=>attributes.add(name),removeAttribute:name=>attributes.delete(name),
        hasAttribute:name=>attributes.has(name),querySelector:selector=>selector==='[data-view-retry]'
          ? {addEventListener:(event,handler)=>{retry=handler;}} : null};
      doc.getElementById=()=>view;doc.documentElement.style={overflowAnchor:''};
      doc.documentElement.getBoundingClientRect=()=>({});
      sandbox.location={hash:'#/fx'};sandbox.intent=null;
      run(`build.mode='live';state.meta={};renderRuntime=async()=>{};refreshResolvedHeader=async()=>{};
        renderNav=()=>{};renderFooter=()=>{};syncNotices=()=>{};
        pageFX=async()=>{throw Object.assign(new Error('synthetic failure'),{retryable:true});};`);
      await run('render()');
      assert.equal(typeof retry,'function');
      run('render=options=>{intent=options;}');retry();
      assert.equal(sandbox.intent.control.kind,'view');
    """)


def test_focus_only_view_restore_does_not_apply_an_old_routes_scroll():
    run_js(r"""
      let scrolled=false,focused=false;
      sandbox.window.scrollTo=()=>{scrolled=true;};
      doc.activeElement=doc.body;doc.querySelector=()=>({focus:()=>{focused=true;}});
      sandbox.fixtureContext={restore:{control:{kind:'view'},focusOnly:true}};
      run('shownContext=fixtureContext;restoreControl(fixtureContext,()=>true)');
      assert.equal(focused,true);assert.equal(scrolled,false);
    """)


def test_focus_only_restores_language_or_falls_back_to_view_if_a_control_is_missing():
    run_js(r"""
      let focused=null;
      sandbox.window.scrollTo=()=>{throw new Error('Focus-only restore must not scroll');};
      doc.querySelectorAll=()=>{throw new Error('Focus-only restore must not touch old tables');};
      doc.querySelector=selector=>selector==='[data-lang="zh"]'||selector==='#view'
        ? {focus:()=>{focused=selector;}} : null;
      sandbox.fixtureContext={restore:{control:{kind:'lang',value:'zh'},focusOnly:true}};
      run('shownContext=fixtureContext');doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>true)');
      assert.equal(focused,'[data-lang="zh"]');
      sandbox.fixtureContext.restore.control={kind:'model',value:'ridge'};
      doc.activeElement=doc.body;
      run('restoreControl(fixtureContext,()=>true)');
      assert.equal(focused,'#view');
    """)
