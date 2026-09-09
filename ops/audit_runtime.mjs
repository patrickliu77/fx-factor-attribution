// Browser fixtures only. Never writes a failure into real pipeline state.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),errors=[],results=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1050}});
    await staticFixture(context,base);
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    const observed=new Date().toISOString();
    const fixture={state:'red',last_success_state:'green',runtime:{observed_at:observed,
      last_success_at:observed,attribution_as_of:'2026-09-07',provisional_rows:63,
      latest_attempt:{state:'crashed',started_at:observed,finished_at:observed,exit_hex:'0xc000001d'}}};
    await page.route(/\/api\/status(?:\.json)?(?:\?|$)/,r=>r.fulfill({json:fixture}));
    await page.goto(base+'/#/news');
    const status=page.locator('#runtime-status');
    await status.locator('details').waitFor();
    assert.equal(await status.locator('details').getAttribute('data-runtime-tone'),'red');
    assert.ok((await status.innerText()).includes(lang==='zh'?'计算进程异常退出':'Calculation process crashed'));
    await status.locator('summary').click();
    assert.ok((await status.innerText()).includes('0xc000001d'));
    assert.ok((await status.innerText()).includes('2026-09-07'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await status.screenshot({path:path.join(out,`runtime-${lang}-${width}.png`)});
    await page.evaluate(async fixture=>{
      const {runtimeHtml}=await import(new URL('runtime-status.js',document.baseURI));
      fixture.runtime.latest_attempt={state:'running',deadline:'2020-01-01T00:00:00Z'};
      document.querySelector('#runtime-status').innerHTML=runtimeHtml(fixture,{mode:'static'});
    },fixture);
    assert.equal(await status.locator('details').getAttribute('data-runtime-state'),'interrupted');
    results.push({lang,width,failure_visible:true,stale_attempt:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,fixture_only:true,results,errors},null,2));
  console.log(JSON.stringify({layouts:results.length,errors}));
} finally {await browser.close();}
