// Inspect actual saved briefings in both languages and viewport sizes. No generation.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass dashboard URL and audit directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),errors=[],results=[];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1100}});
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base.replace(/\/$/,'')+'/#/news');
    const board=page.locator('.brief-board');
    await board.waitFor();
    await page.evaluate(()=>document.fonts.ready);
    const body=await board.innerText();
    assert.ok(!body.includes('timing requirement remains unmet') && !body.includes('按时出刊要求仍未满足'));
    assert.ok(!body.includes('最近应出刊日期'));
    await board.locator('.brief-run-details').evaluate(el=>{el.open=true;});
    assert.ok((await board.innerText()).includes(lang==='en'?'consecutive-day quota':'连续天数门槛'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const state=await board.locator('[data-freshness]').getAttribute('data-freshness');
    const mode=await board.locator('[data-briefing-mode]').getAttribute('data-briefing-mode');
    const options=await board.locator('[data-brief-history] option').count();
    assert.ok(options>0);
    await board.screenshot({path:path.join(out,`usage-${lang}-${width}.png`)});
    results.push({lang,width,state,mode,archive_options:options,no_overflow:true,usage_policy_visible:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,errors},null,2));
  console.log(JSON.stringify({base,layouts:results.length,errors}));
} finally {await browser.close();}
