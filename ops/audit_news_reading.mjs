// Actual page screenshots plus isolated overflow/source-group browser fixtures.
import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const base=process.argv[2]?.replace(/\/$/,'');
const out=process.argv[3];
if (!base || !out) throw new Error('Pass base URL and new output folder');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const errors=[], results=[];
const headlineLimit=4;
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.woff2':'font/woff2','.svg':'image/svg+xml'};
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1000}});
    await context.addInitScript(lang=>localStorage.setItem('fxdash.lang',lang),lang);
    await staticFixture(context,base);
    if (process.env.FXDASH_STATIC_ROOT && !process.env.FXDASH_STATIC_FIXTURE) {
      await context.route(base+'/**',async r=>{
        const rel=decodeURIComponent(new URL(r.request().url()).pathname.slice(new URL(base).pathname.length)).replace(/^\//,'') || 'index.html';
        try {await r.fulfill({body:await readFile(path.join(process.env.FXDASH_STATIC_ROOT,rel)),contentType:mime[path.extname(rel)] || 'application/octet-stream'});}
        catch {await r.fulfill({status:404,body:'not found'});}
      });
    }
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    const newsResponse=page.waitForResponse(response=>/\/api\/news(?:\.json)?(?:\?|$)/.test(response.url()));
    await page.goto(base+'/#/news');
    const news=await (await newsResponse).json();
    await page.locator('.driver-context').waitFor({timeout:90000});
    await page.evaluate(()=>document.fonts.ready);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const actual={lang,width,visible_stories:await page.locator('.news-flagged [data-story]:visible').count(),
                  visible_headlines:await page.locator('.news-headlines [data-headline]:visible').count(),
                  grouping:news.event_grouping};
    assert.ok(actual.visible_stories<=5);
    assert.ok(actual.visible_headlines<=headlineLimit);
    await page.screenshot({path:path.join(out,`news-${lang}-${width}.png`),fullPage:true});
    // Controlled fixture only in browser memory; saved narrative stays untouched.
    const fixture=structuredClone(news);
    const item=n=>({url:`https://example.com/event-${n}`,title:`BROWSER TEST event ${n}`,published:'2026-09-07',
                    source:'Fixture',pairs:['USDJPY'],duplicates:n===0 ? [{url:'https://example.com/alternate',title:'BROWSER TEST alternate source',source:'Second source',published:'2026-09-07'}] : []});
    fixture.week.items=[];fixture.fallback=null;
    fixture.today={mode:'fetched',date:'2026-09-07',items:Array.from({length:10},(_,i)=>item(i)),fetched_at:'2026-09-07T20:00:00Z'};
    fixture.earlier={items:[]};fixture.opinions={items:[]};
    await page.route(/\/api\/news(?:\.json)?(?:\?|$)/,r=>r.fulfill({json:fixture}));
    await page.reload();
    const headlines=page.locator('.news-headlines');
    await headlines.locator('[data-headline]').first().waitFor({timeout:60000});
    assert.equal(await headlines.locator('[data-headline]').count(),fixture.today.items.length);
    assert.equal(await headlines.locator('[data-headline]:visible').count(),headlineLimit);
    const more=headlines.locator('.news-more');
    assert.equal(await more.count(),1);
    assert.equal(await more.getAttribute('open'),null);
    await headlines.locator('[data-headline]').first().click();
    await headlines.locator('.event-sources > summary').click();
    assert.equal(await headlines.locator('.event-sources a:visible').count(),1);
    await more.locator(':scope > summary').click();
    assert.equal(await headlines.locator('[data-headline]:visible').count(),fixture.today.items.length);
    await headlines.locator('[data-headline]').last().click();
    assert.equal(await headlines.locator('[data-headline]').last().getAttribute('aria-expanded'),'true');
    await more.locator(':scope > summary').click();
    assert.equal(await headlines.locator('[data-headline]:visible').count(),headlineLimit);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    results.push({...actual,headline_limit:headlineLimit,fixture_expansion_passed:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  const result={base,results,errors};
  await writeFile(path.join(out,'audit.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
