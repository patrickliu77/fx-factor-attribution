// Browser-only failure/history fixtures. Never writes editions to pipeline outputs.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const base=(process.argv[2] || 'http://127.0.0.1:8321').replace(/\/$/,'');
const out=process.argv[3];
if (!out) throw new Error('Pass an artifact directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const errors=[],results=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:950}});
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    const buildResponse=await context.request.get(base+'/build.json');
    const build=buildResponse.ok() ? await buildResponse.json() : null;
    const newsResponse=await context.request.get(base+(build ? '/api/news.json' : '/api/news'));
    const news=await newsResponse.json();
    const current={available:true,mode:'edition',date:'2026-09-04',state:'numbers_only',
      text:{en:'BROWSER TEST ONLY: latest edition.',zh:'仅限浏览器测试：最新稿件。'},
      notes:[],warnings:[],attribution_as_of:'2026-09-03',news_observed_by:'2026-09-04T12:51:00Z',
      edition_hash:'browser-fixture-current'};
    const older={...current,date:'2026-09-02',edition_hash:'browser-fixture-older',
      text:{en:'BROWSER TEST ONLY: older edition.',zh:'仅限浏览器测试：历史稿件。'}};
    const corrupt={available:true,mode:'edition',date:'2026-09-01',state:'archive_unreadable',
      text:{},notes:[],warnings:['archive_unreadable']};
    news.briefing=current;
    news.briefing_archive={history:[current,older,corrupt],total_editions:3,history_limit:20,
      observed_at:'2026-09-04T13:01:00Z',latest_run:{date:current.date,
        prepare:{state:'prepared',started_at:'2026-09-04T12:50:00Z'},
        edition:{state:'numbers_only',generated_at:'2026-09-04T13:00:00Z',hash:current.edition_hash},
        push:{state:'publish_failed',finished_at:'2026-09-04T13:01:00Z'}}};
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    await page.route(/\/api\/news(?:\.json)?(?:\?|$)/,r=>r.fulfill({json:news}));
    if (build) await page.route(/\/build\.json(?:\?|$)/,r=>r.fulfill({json:{...build,
      briefing:{date:current.date,edition_hash:current.edition_hash}}}));
    await page.goto(base+'/#/news');
    const board=page.locator('.brief-board');
    await board.waitFor({timeout:60000});
    const select=board.locator('[data-brief-history]');
    assert.equal(await select.locator('option').count(),3);
    const deliveryText=build ? (lang==='en' ? 'Included in this build' : '已包含在当前构建')
      : (lang==='en' ? 'Push failed' : '推送失败');
    assert.ok((await board.locator('[data-brief-status]').innerText()).includes(deliveryText));
    await select.selectOption('2026-09-02');
    assert.ok((await board.locator('[data-brief-content]').innerText()).includes(lang==='en' ? 'older edition' : '历史稿件'));
    await select.selectOption('2026-09-01');
    assert.equal(await board.locator('[data-briefing-state]').getAttribute('data-briefing-state'),'archive_unreadable');
    assert.ok(!(await board.locator('[data-brief-content]').innerText()).includes('older edition'));
    await select.selectOption('current');
    await page.evaluate(async ()=>{
      const {refreshBriefingStatus}=await import(new URL('briefing-board.js',document.baseURI));
      refreshBriefingStatus(new Date('2026-09-08T13:00:00Z'));
    });
    assert.equal(await board.locator('[data-freshness]').getAttribute('data-freshness'),'older_edition');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await board.screenshot({path:path.join(out,`fixture-${lang}-${width}.png`)});
    results.push({lang,width,delivery:deliveryText,history_choices:3});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  const result={base,browser_fixtures_only:true,results,errors};
  await writeFile(path.join(out,'audit.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
