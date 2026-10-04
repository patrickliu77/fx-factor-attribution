// Browser-only API/timer fixtures. No live server, provider or pipeline writes.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2),root=process.env.FXDASH_STATIC_FIXTURE;
if (!base || !out || !root) throw new Error('Pass base, output folder and FXDASH_STATIC_FIXTURE');
await mkdir(out,{recursive:true});
const before=process.env.FXDASH_EXPECT_OLD_REFRESH==='1';
const manifest=JSON.parse(await readFile(path.join(root,'build.json'),'utf8'));
const meta=JSON.parse(await readFile(path.join(root,'api/meta.json'),'utf8'));
const tick=page=>page.evaluate(()=>window.__fxRefreshTick());
const frame=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const browser=await chromium.launch({headless:true}),results=[],errors=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1050}});
    await staticFixture(context,base);
    await context.addInitScript(lang=>{
      localStorage.setItem('fxdash.lang',lang);
      const original=window.setInterval;
      window.setInterval=(callback,delay,...args)=>{
        if (delay===300000) {window.__fxRefreshTick=callback;return 54321;}
        return original(callback,delay,...args);
      };
    },lang);
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    const stamps=[new Date(Date.now()-80*3600000).toISOString(),new Date(Date.now()-3600000).toISOString()];
    let stage=0,releasePulse,seenPulse;
    const delayedPulse=new Promise(resolve=>releasePulse=resolve),pulseStarted=new Promise(resolve=>seenPulse=resolve);
    const runtime=stamp=>({state:'green',last_success_state:'green',reasons:[],
      heartbeat:{last_live_success:stamp,warn_hours:26,crit_hours:72},
      server:{data_version:meta.data_version},runtime:{observed_at:new Date().toISOString(),
        last_success_at:stamp,attribution_as_of:manifest.as_of,provisional_rows:0,
        latest_attempt:{state:'succeeded',started_at:stamp}}});
    const pulse=stamp=>({last_run:stamp,last_published:null,warn_hours:26,crit_hours:72,days_on_record:2,reasons:[]});
    let future=false;
    const futureStamp=new Date(Date.now()+120*3600000).toISOString();
    await page.route(base+'/build.json',route=>future
      ?route.fulfill({json:{...manifest,built_at:futureStamp}}):route.fulfill({status:404,body:''}));
    await page.route(base+'/api/**',async route=>{
      const url=new URL(route.request().url());
      let relative=url.pathname.slice(new URL(base).pathname.length).replace(/^\//,'');
      if (relative.startsWith('api/status')) return route.fulfill({json:runtime(future?futureStamp:stamps[stage])});
      if (relative.startsWith('api/narrative/status')) {
        if (stage===1 && !future) {seenPulse();await delayedPulse;}
        return route.fulfill({json:pulse(future?futureStamp:stamps[stage])});
      }
      if (!relative.endsWith('.json')) {
        const params=[...url.searchParams].sort(([a],[b])=>a.localeCompare(b));
        relative+=params.map(([k,v])=>`.${k}-${v}`).join('')+'.json';
      }
      const body=JSON.parse(await readFile(path.join(root,relative),'utf8'));
      if (relative.startsWith('api/overview')) body.status_digest={last_live_success:stamps[0],state:'red',reasons:[]};
      await route.fulfill({json:body});
    });
    await page.goto(base+'/#/news');
    await page.locator('.health').waitFor({state:'attached'});
    await page.waitForFunction(()=>typeof window.__fxRefreshTick==='function');
    stage=1;await tick(page);await pulseStarted;
    await page.waitForFunction(()=>document.querySelector('#runtime-status details')?.dataset.runtimeTone==='green');
    const response=page.waitForResponse(r=>r.url().endsWith('/api/narrative/status'));
    releasePulse();await (await response).finished();await frame(page);
    const updated=await page.evaluate(()=>{
      const rows=[...document.querySelectorAll('.health__row')];
      return {pipeline_stamp:rows[0].querySelector('b').textContent,
        narrative_stamp:rows[1].querySelector('b').textContent,
        narrative_dot:rows[1].querySelector('i').getAttribute('style'),
        pulse_state:document.querySelector('#pulse').dataset.state};
    });
    const wanted=stamps[1].slice(0,16).replace('T',' ');
    const pipelineUpdated=updated.pipeline_stamp.startsWith(wanted);
    const narrativeUpdated=updated.narrative_stamp.startsWith(wanted) && updated.narrative_dot.includes('var(--up)');
    assert.equal(updated.pulse_state,'green');
    assert.equal(pipelineUpdated,!before);
    assert.equal(narrativeUpdated,!before);
    if (!before) {
      const pattern=base+'/api/narrative/status';
      let releaseOld,seenOld,count=0;
      const blocked=new Promise(resolve=>releaseOld=resolve),started=new Promise(resolve=>seenOld=resolve);
      const handler=async route=>{
        if (++count===1) {seenOld();await blocked;return route.fulfill({json:pulse(stamps[0])});}
        return route.fulfill({json:pulse(stamps[1])});
      };
      await page.route(pattern,handler);
      await tick(page);await started;
      const latest=page.waitForResponse(r=>r.url()===pattern);
      await tick(page);await (await latest).finished();await frame(page);
      const oldResponse=page.waitForResponse(r=>r.url()===pattern);
      releaseOld();await (await oldResponse).finished();await frame(page);
      assert.equal(await page.locator('#pulse').getAttribute('data-state'),'green');
      assert.ok((await page.locator('.health__row').nth(1).locator('b').textContent()).startsWith(wanted));
      await page.unroute(pattern,handler);
      future=true;
      await page.reload();
      await page.locator('.health').waitFor({state:'attached'});
      assert.equal(await page.locator('#runtime-status details').getAttribute('data-runtime-tone'),'red');
      assert.equal(await page.locator('#runtime-status details').getAttribute('data-runtime-freshness'),'future');
      assert.equal(await page.locator('#pulse').getAttribute('data-state'),'red');
      assert.equal(await page.locator('#built').getAttribute('data-state'),'red');
      const text=await page.locator('.health').textContent();
      assert.ok(text.includes(lang==='zh'?'时钟不一致':'Clock mismatch'));
      assert.ok(!/-\d+(?:m|h|d)/.test(text));
    }
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(out,`refresh-${lang}-${width}.png`),fullPage:true});
    results.push({lang,width,pipeline_updated:pipelineUpdated,narrative_updated:narrativeUpdated,
      late_narrative_ignored:!before,future_clock_checked:!before,old_bug_reproduced:before,observed:updated});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,fixture_only:true,before,results,errors},null,2));
  console.log(JSON.stringify({before,layouts:results.length,errors}));
} finally {await browser.close();}
