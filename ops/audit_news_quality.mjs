// Browser-only evidence screening fixtures. Does not write pipeline outputs.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const base=process.argv[2]?.replace(/\/$/,'');
const out=process.argv[3];
if (!base || !out) throw new Error('Pass a base URL and artifact directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const results=[],errors=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:950}});
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    await staticFixture(context,base);
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('dialog',async d=>{errors.push(d.message());await d.dismiss();});
    const newsResponse=page.waitForResponse(response=>/\/api\/news(?:\.json)?(?:\?|$)/.test(response.url()));
    await page.goto(base+'/#/news');
    const news=await (await newsResponse).json();
    await page.locator('.driver-context').waitFor({timeout:60000});
    const row=news.drivers.pairs[0], key=row.leading[0].news_key;
    const article=(title,n)=>({title,url:`https://example.com/${n}`,source:'Browser fixture',published:'2026-09-04'});
    news.drivers.source_policy='driver-sources-1';
    news.drivers.slates[key]={observed_at:'2026-09-05T12:00:00Z',error:null,
      items:[0,1,2,3].map(n=>article(`BROWSER TEST retained ${n}`,n)),
      review:[{...article('BROWSER TEST <img src=x onerror=alert(1)>',4),reason:'topic_not_clear_from_title_or_snippet'}],
      excluded:[{...article('BROWSER TEST duplicate',5),reason:'duplicate_url_or_headline',duplicate_of:'https://example.com/0'},
        {...article('BROWSER TEST unsafe link',6),url:'javascript:alert(1)',reason:'invalid_article_url'}],
      coverage:{candidates:7,retained:4,review:1,excluded:2,displayed:3,displayed_publishers:1,missing_publisher_metadata:0}};
    await page.route(/\/api\/news(?:\.json)?(?:\?|$)/,r=>r.fulfill({json:news}));
    await page.reload();
    const driver=page.locator('.driver-context');
    await driver.waitFor({timeout:60000});
    assert.equal(await driver.getAttribute('open'),null);
    await driver.locator(':scope > summary').click();
    const pair=driver.locator('.driver-pair').first();
    await pair.waitFor({timeout:60000});
    assert.equal(await pair.getAttribute('open'),null);
    await pair.locator(':scope > summary').click();
    const slate=pair.locator('.context-slate').first();
    assert.ok((await slate.locator('.context-coverage').innerText()).includes(lang==='en' ? '7 candidates' : '7 条候选'));
    assert.equal(await slate.locator(':scope > a').count(),3);
    for (const cls of ['context-review','context-excluded','context-more']) {
      const detail=slate.locator('.'+cls);
      assert.equal(await detail.getAttribute('open'),null);
      await detail.locator('summary').click();
    }
    assert.ok((await slate.locator('.context-review').innerText()).includes(lang==='en' ? 'left out of new generated' : '不送入新晨报'));
    assert.equal(await slate.locator('img').count(),0);
    assert.equal(await slate.locator('a[href^="javascript:"]').count(),0);
    assert.equal(await slate.locator('.context-excluded a[href="https://example.com/0"]').count(),1);
    assert.ok(!(await slate.innerText()).includes('topic_not_clear_from_title_or_snippet'));
    const policy=driver.locator('.driver-context-body > .news-guide');
    assert.equal(await policy.count(),1);
    assert.equal(await policy.getAttribute('open'),null);
    await policy.locator('summary').click();
    assert.ok((await page.locator('.context-policy').innerText()).includes(lang==='en' ? 'independent confirmation' : '独立证实'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await pair.screenshot({path:path.join(out,`news-quality-${lang}-${width}.png`)});
    await driver.locator(':scope > summary').click();
    assert.equal(await slate.isVisible(),false);
    assert.equal(await policy.locator('.context-policy').isVisible(),false);
    results.push({lang,width,shortlist:3,review:1,excluded:2,other_retained:1,driver_disclosure:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  const result={base,browser_fixtures_only:true,results,errors};
  await writeFile(path.join(out,'audit.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
