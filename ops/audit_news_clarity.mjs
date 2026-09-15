// Actual saved data, no synthesis. Expanded details remain available by keyboard.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass dashboard URL and audit directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),errors=[],results=[];
try {
  for (const lang of ['en','zh']) for (const theme of ['dark','light']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1000}});
    await staticFixture(context,base);
    await context.addInitScript(({lang,theme})=>{
      localStorage.setItem('fxdash.lang',lang);localStorage.setItem('fxdash.theme',theme);
    },{lang,theme});
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    const response=page.waitForResponse(r=>/\/api\/news(?:\.json)?(?:\?|$)/.test(r.url()));
    await page.goto(base.replace(/\/$/,'')+'/#/news');
    const feed=await (await response).json();
    const board=page.locator('.brief-board'),sources=board.locator('.brief-source-details');
    await sources.waitFor();
    await page.waitForFunction(()=>!document.querySelector('[data-mini-asof]')?.textContent.match(/Loading|正在/));
    await page.evaluate(()=>document.fonts.ready);
    // The fixture date is only used to test healthy status presentation. Real
    // edition content and displayed as-of dates are never replaced.
    await page.evaluate(async ()=>{
      const B=await import(new URL('briefing-board.js',document.baseURI));
      const date=document.querySelector('[data-brief-history] option').textContent.match(/\d{4}-\d{2}-\d{2}/)[0];
      B.refreshBriefingStatus(new Date(date+'T18:00:00Z'));
    });
    const text=await board.innerText();
    assert.ok(!/\d{4}-\d{2}-\d{2}T\d{2}:/.test(text),'Raw operational timestamps must be folded');
    for (const phrase of ['fixed publication deadline','Reference weekday','Provisional figures are marked',
      '不设固定出刊时刻','日期参考','待确认数字已标注']) assert.ok(!text.includes(phrase),phrase);
    assert.equal(await board.locator('[data-freshness]').isVisible(),false);
    assert.equal(await sources.getAttribute('open'),null);
    const hasRecap=!!feed.briefing?.recap?.text?.[lang];
    assert.equal(await board.locator('.brief-asof').isVisible(),!hasRecap && feed.briefing.attribution_as_of!==feed.briefing.date);
    assert.equal(await page.locator('.news-header h1').count(),1);
    assert.equal(await page.locator('.storyhead').count(),0);
    assert.equal(await page.locator('.driver-context').evaluate(d=>d.open),false);
    assert.equal(await page.locator('.news-feed-details').evaluate(d=>d.open),false);
    assert.ok((await page.locator('.news-headlines .hrow:visible').count())<=4);
    if (feed.subscription?.enabled) {
      const cta=page.locator('.news-header .subscribe-cta');
      assert.equal(await cta.isVisible(),true);
      assert.equal(await cta.evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(213, 255, 95)');
      assert.ok((await cta.boundingBox()).y<(await board.boundingBox()).y);
      await cta.focus();await page.keyboard.press('Enter');
      assert.equal(await page.locator('.subscribe-panel').isVisible(),true);
      assert.equal(await page.locator('.brief-subscribe iframe').count(),0);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.subscribe-panel').isVisible(),false);
      assert.equal(await cta.evaluate(e=>e===document.activeElement),true);
    }
    assert.equal(await page.locator('#mini-note').isVisible(),false);
    // The recap stays readable; exact figures and status labels stay in details.
    if (hasRecap) {
      assert.ok(!/\bbp\b|DOLLAR_LOO|CARRY_LOO|provisional|待确认/i.test(await board.locator('.brief-copy').innerText()));
      assert.ok(/provisional|待确认/i.test(await sources.locator('.brief-quant-copy').textContent()));
    } else {
      assert.ok(/provisional|待确认/i.test(await board.locator('[data-brief-content]').innerText()));
    }
    assert.equal(await page.locator('.brief-audio audio').getAttribute('preload'),'none');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(out,`news-${lang}-${theme}-${width}.png`),fullPage:true});
    const summary=sources.locator(':scope > summary');
    await summary.focus();await page.keyboard.press('Enter');
    assert.equal(await sources.evaluate(el=>el.open),true);
    if (hasRecap) assert.ok(/provisional|待确认/i.test(await sources.locator('.brief-quant-copy').innerText()));
    assert.ok(/\d{4}-\d{2}-\d{2}T/.test(await sources.innerText()));
    assert.equal(await sources.locator('[data-brief-status]').count(),1);
    await sources.locator('.brief-run-details summary').click();
    assert.equal(await sources.locator('.brief-status-grid').last().isVisible(),true);
    // Calendar/source timestamps remain inspectable, not deleted to reduce clutter.
    await summary.press('Enter');
    const guide=page.locator('.mini-guide > summary');
    await guide.focus();await guide.press('Enter');
    assert.equal(await page.locator('#mini-note').isVisible(),true);
    assert.ok(/scale|缩放/.test(await page.locator('#mini-note').innerText()));
    await guide.press('Enter');
    const driver=page.locator('.driver-context');
    await driver.locator(':scope > summary').click();
    assert.equal(await driver.locator('.driver-pair').count(),6);
    await driver.locator('.driver-pair > summary').first().click();
    assert.ok(await driver.locator('.context-slate').first().isVisible());
    const headline=page.locator('.news-headlines [data-headline]').first();
    await headline.click();
    assert.equal(await headline.getAttribute('aria-expanded'),'true');
    await headline.click();
    const story=page.locator('.news-flagged [data-story]').first();
    if (await story.count()) {
      await story.click();
      assert.equal(await story.getAttribute('aria-expanded'),'true');
      assert.equal(await story.locator('..').locator('.expand').count(),1);
    }
    results.push({lang,theme,width,default_copy_trimmed:true,dates_and_provisional_retained:true,
      top_subscription_cta:!!feed.subscription?.enabled,keyboard_disclosures:true,news_expansion:true,no_overflow:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,errors},null,2));
  console.log(JSON.stringify({base,layouts:results.length,errors}));
} finally {await browser.close();}
