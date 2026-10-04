// Delayed responses are browser fixtures; saved API files and pipeline state stay unchanged.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass base URL and artifact directory');
await mkdir(out,{recursive:true});
const frame=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

async function holdFirst(page,pattern,{body,status=200,laterBody}={}) {
  let unblock,seen,count=0;
  const gate=new Promise(resolve=>unblock=resolve),started=new Promise(resolve=>seen=resolve);
  const handler=async route=>{
    if (++count!==1) {
      return laterBody ? route.fulfill({json:laterBody}) : route.fallback();
    }
    seen(); await gate;
    if (body!==undefined) await route.fulfill({status,json:body});
    else await route.fallback();
  };
  await page.route(pattern,handler);
  const boundedStart=(async()=>{
    let timer;
    try {
      await Promise.race([started,new Promise((_,reject)=>{
        timer=setTimeout(()=>reject(new Error('Expected delayed request was not sent: '+pattern)),30000);
      })]);
    } finally {clearTimeout(timer);}
  })();
  return {started:boundedStart,async release(){
    const response=page.waitForResponse(r=>pattern.test(r.url()));
    unblock(); await (await response).finished(); await frame(page);
    await page.unroute(pattern,handler);
  }};
}

const browser=await chromium.launch({headless:true}),results=[],errors=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1050}});
    await staticFixture(context,base);
    await context.addInitScript(lang=>localStorage.setItem('fxdash.lang',lang),lang);
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    const news=/\/api\/news(?:\.json)?(?:\?|$)/;
    for (const fail of [false,true]) {
      await page.goto(base+'/#/fx');
      await page.locator('[data-card]').first().waitFor();
      const pending=await holdFirst(page,news,fail?{status:503,body:{error:'BROWSER TEST'}}:{});
      await page.evaluate(()=>location.hash='#/news');
      await pending.started;
      await page.evaluate(()=>location.hash='#/attribution');
      await page.locator('.attribution-page').waitFor();
      await pending.release();
      assert.equal(await page.locator('.attribution-page').count(),1);
      assert.equal(await page.locator('.news-header').count(),0);
      assert.equal(await page.evaluate(()=>location.hash),'#/attribution');
    }
    await page.screenshot({path:path.join(out,`route-${lang}-${width}.png`)});

    const oldPrice=await holdFirst(page,/\/api\/market\/series\/USDJPY(?:\.range-6m\.json|\?range=6m)$/,
      {body:{available:false,reason:'no_cache'}});
    await page.goto(base+'/#/fx');
    await page.locator('[data-card="USDJPY"]').waitFor();
    await oldPrice.started;
    const nextPrice=/\/api\/market\/series\/USDJPY(?:\.range-1y\.json|\?range=1y)$/;
    const priceHandler=route=>route.fulfill({json:{available:false,reason:'too_short'}});
    await page.route(nextPrice,priceHandler);
    await page.locator('[data-ranges="USDJPY"] [data-r="1y"]').click();
    const expected=lang==='zh'?'该区间历史不足。':'Not enough history for this range.';
    await page.waitForFunction(expected=>document.querySelector('[data-chart="USDJPY"]')?.textContent===expected,expected);
    await oldPrice.release();
    assert.equal(await page.locator('[data-chart="USDJPY"]').innerText(),expected);
    assert.equal(await page.locator('[data-ranges="USDJPY"] [aria-pressed="true"]').getAttribute('data-r'),'1y');
    await page.unroute(nextPrice,priceHandler);

    // Also protect a usable newer chart, rather than only unavailable messages.
    const stalePrice=await holdFirst(page,nextPrice,{body:{available:false,reason:'no_cache'}});
    await page.locator('[data-ranges="USDJPY"] [data-r="1y"]').click();
    await stalePrice.started;
    await page.locator('[data-ranges="USDJPY"] [data-r="5y"]').click();
    await page.waitForFunction(()=>{
      const box=document.querySelector('[data-chart="USDJPY"]');
      return window.echarts?.getInstanceByDom(box)?.getOption().series?.length>0;
    });
    const chartId=await page.evaluate(()=>echarts.getInstanceByDom(document.querySelector('[data-chart="USDJPY"]')).id);
    await stalePrice.release();
    assert.equal(await page.evaluate(()=>echarts.getInstanceByDom(document.querySelector('[data-chart="USDJPY"]'))?.id),chartId);
    assert.equal(await page.locator('[data-chart="USDJPY"] canvas').count(),1);

    const pair=/\/api\/pairs\/USDJPY\/news(?:\.json)?(?:\?|$)/;
    const feed=count=>({items:[],headlines:[],count});
    const oldPanel=await holdFirst(page,pair,{body:feed(111),laterBody:feed(333)});
    await page.locator('[data-card="USDJPY"] .card__name').click();
    await oldPanel.started;
    await page.locator('[data-card="USDCAD"] .card__name').click();
    await page.locator('[data-card="USDJPY"] .card__name').click();
    await page.waitForFunction(()=>document.querySelector('.pairnews__head .hint')?.textContent.includes('333'));
    await oldPanel.release();
    assert.equal(await page.locator('.pairnews').count(),1);
    assert.ok((await page.locator('.pairnews__head .hint').innerText()).includes('333'));
    assert.ok(!(await page.locator('.pairnews__head .hint').innerText()).includes('111'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(out,`selection-${lang}-${width}.png`)});

    const stamp=new Date().toISOString();
    const run=state=>({state:state==='crashed'?'red':'green',last_success_state:'green',runtime:{
      observed_at:stamp,last_success_at:stamp,attribution_as_of:'2026-09-18',provisional_rows:0,
      latest_attempt:{state,started_at:stamp}}});
    const oldStatus=await holdFirst(page,/\/api\/status(?:\.json)?(?:\?|$)/,
      {body:run('succeeded'),laterBody:run('crashed')});
    // A hash navigation reuses the cached static status. Reload the document
    // explicitly to exercise two overlapping initial status requests.
    await page.reload();
    await oldStatus.started;
    await page.evaluate(()=>location.hash='#/attribution');
    await page.locator('.attribution-page').waitFor();
    const runtime=page.locator('#runtime-status details');
    assert.equal(await runtime.getAttribute('data-runtime-state'),'crashed');
    await runtime.locator('summary').click();
    await oldStatus.release();
    assert.equal(await runtime.getAttribute('data-runtime-state'),'crashed');
    assert.equal(await runtime.getAttribute('data-runtime-tone'),'red');
    assert.notEqual(await runtime.getAttribute('open'),null);
    assert.equal(await page.locator('.attribution-page').count(),1);
    await page.screenshot({path:path.join(out,`runtime-${lang}-${width}.png`)});
    results.push({lang,width,late_page_ignored:true,late_error_ignored:true,
      latest_price_range_retained:true,latest_chart_instance_retained:true,pair_selection_aba_safe:true,
      latest_runtime_retained:true,runtime_expansion_retained:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,fixture_only:true,results,errors},null,2));
  console.log(JSON.stringify({layouts:results.length,errors}));
} finally {await browser.close();}
