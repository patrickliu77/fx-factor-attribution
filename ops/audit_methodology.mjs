// Read-only browser acceptance for the bilingual research and delivery notes.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass dashboard URL and audit directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),results=[],errors=[];
try {
  for (const lang of ['en','zh']) for (const theme of ['dark','light']) for (const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:1000}});
    await staticFixture(context,base);
    await context.addInitScript(({lang,theme})=>{
      localStorage.setItem('fxdash.lang',lang);localStorage.setItem('fxdash.theme',theme);
    },{lang,theme});
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base.replace(/\/$/,'')+'/#/methodology');
    await page.locator('article.method').waitFor();
    await page.evaluate(()=>document.fonts.ready);
    await page.waitForFunction(()=>document.querySelectorAll('mjx-container').length>=10);
    assert.equal(await page.locator('.method__section').count(),8);
    assert.equal(await page.locator('.method-figure svg').count(),4);
    assert.equal(await page.locator('mjx-merror').count(),0);
    assert.equal(await page.locator('.method__detail[open]').count(),0);
    assert.equal(await page.locator('.method iframe,.method audio').count(),0);
    const delivery=page.locator('.method-figure').last();
    const bounds=await page.locator('.method-figure svg').evaluateAll(svgs=>svgs.map(svg=>({
      title:svg.querySelector('title')?.textContent,
      desc:svg.querySelector('desc')?.textContent,
      font:getComputedStyle(svg.querySelector('text.body')).fontFamily,
      labelFont:getComputedStyle(svg.querySelector('text.eyebrow')).fontFamily,
      overflow:[...svg.querySelectorAll('text')].flatMap(text=>{
        const b=text.getBBox(),v=svg.viewBox.baseVal;
        return b.x < -1 || b.y < -1 || b.x+b.width>v.width+1 || b.y+b.height>v.height+1
          ? [text.textContent] : [];
      }),
    })));
    for (const figure of bounds) {
      assert.ok(figure.title && figure.desc);
      assert.ok(figure.font.includes('Outfit'));
      assert.ok(figure.labelFont.includes('IBM Plex Mono'));
      assert.deepEqual(figure.overflow,[],JSON.stringify({lang,width,figure}));
    }
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const summaries=page.locator('.method__detail > summary');
    for (let n=0;n<await summaries.count();n++) {
      await summaries.nth(n).focus();await page.keyboard.press('Enter');
      assert.equal(await summaries.nth(n).evaluate(s=>s.parentElement.open),true);
    }
    assert.equal(await page.locator('mjx-merror').count(),0);
    const text=await page.locator('.method').innerText();
    for (const name of ['Brevo','Azure','BLS','BEA','PCA']) assert.ok(text.includes(name),name);
    assert.ok(text.includes(lang==='en'?'same-day':'当天'));
    for (let n=0;n<await summaries.count();n++) await summaries.nth(n).click();
    await page.screenshot({path:path.join(out,`methodology-${lang}-${theme}-${width}.png`),fullPage:true});
    await delivery.evaluate(e=>e.scrollIntoView({block:'center'}));
    await delivery.screenshot({path:path.join(out,`delivery-${lang}-${theme}-${width}.png`)});
    for (const name of ['pipeline','timeline','lasso','delivery']) {
      const response=await page.evaluate(async url=>{
        const r=await fetch(url);return {status:r.status,body:await r.text()};
      },new URL(`figures/${name}-${lang}.svg`,base.endsWith('/')?base:base+'/').href);
      assert.equal(response.status,200);
      const svg=response.body;
      assert.ok(svg.includes('data:font/woff2;base64,'));
      assert.ok(svg.includes('SIL OPEN FONT LICENSE'));
    }
    results.push({lang,theme,width,sections:8,figures:4,math_rendered:true,folds_accessible:true,no_overflow:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,errors},null,2));
  console.log(JSON.stringify({base,layouts:results.length,errors}));
} finally {await browser.close();}
