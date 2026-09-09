// Browser-only catch-up fixtures. No synthetic editions are written to outputs.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass a site URL and artifact directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}), errors=[], results=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1100}});
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    const buildResponse=await context.request.get(base+'/build.json');
    const build=buildResponse.ok() ? await buildResponse.json() : null;
    const news=await (await context.request.get(base+(build?'/api/news.json':'/api/news'))).json();
    const late={available:true,mode:'catchup',date:'2026-09-08',state:'numbers_only',
      text:{en:'BROWSER FIXTURE: saved numbers after login.',zh:'浏览器测试样本：登录后使用已保存数字。'},
      notes:[],warnings:[],attribution_as_of:'2026-09-07',news_observed_by:'2026-09-08T16:01:00Z',
      generated_at:'2026-09-08T16:03:00Z',edition_hash:'catchup-fixture',scheduled:false,late_publication:true};
    const morning={...late,mode:'edition',state:'inputs_unavailable',edition_hash:'missing-fixture',text:{},warnings:['invalid_packet']};
    news.briefing=late;
    news.briefing_archive={history:[morning],catchup_history:[late],total_editions:1,total_catchups:1,
      observed_at:'2026-09-08T16:04:00Z',current_push:{state:'published'},
      latest_catchup_run:{date:late.date,state:'published',observed_at:'2026-09-08T16:04:00Z'}};
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    await page.route(/\/api\/news(?:\.json)?(?:\?|$)/,route=>route.fulfill({json:news}));
    if (build) await page.route(/\/build\.json(?:\?|$)/,route=>route.fulfill({json:{...build,
      briefing:{mode:'catchup',date:late.date,edition_hash:late.edition_hash}}}));
    await page.goto(base+'/#/news');
    const board=page.locator('.brief-board');
    await board.waitFor();
    const select=board.locator('[data-brief-history]');
    assert.equal(await select.locator('option').count(),2);
    assert.ok((await board.innerText()).includes(lang==='en'?'Catch-up briefing':'补发简报'));
    assert.ok((await board.innerText()).includes(lang==='en'?'does not count as an on-time':'不计作'));
    assert.ok(!(await board.locator('[data-brief-content]').innerText()).includes(lang==='en'?'Validation preview':'运行验收预览'));
    await select.selectOption('2026-09-08');
    assert.equal(await board.locator('[data-briefing-mode]').getAttribute('data-briefing-mode'),'edition');
    await select.selectOption('current');
    assert.equal(await board.locator('[data-briefing-mode]').getAttribute('data-briefing-mode'),'catchup');
    await page.evaluate(async ()=>{
      const B=await import(new URL('briefing-board.js',document.baseURI));
      B.refreshBriefingStatus(new Date('2026-09-08T18:00:00Z'));
    });
    assert.equal(await board.locator('[data-freshness]').getAttribute('data-freshness'),'current_catchup');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await board.screenshot({path:path.join(out,`catchup-${lang}-${width}.png`)});
    await page.evaluate(async ()=>{
      const B=await import(new URL('briefing-board.js',document.baseURI));
      B.refreshBriefingStatus(new Date('2026-09-09T18:00:00Z'));
    });
    assert.equal(await board.locator('[data-freshness]').getAttribute('data-freshness'),'older_edition');
    results.push({lang,width,same_day_archives_separate:true,catchup_label:true,stale_clock:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,browser_fixtures_only:true,results,errors},null,2));
  console.log(JSON.stringify({layouts:results.length,errors}));
} finally {await browser.close();}
