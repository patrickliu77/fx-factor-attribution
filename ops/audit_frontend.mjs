// Browser acceptance against a local server or a Pages-style project path.
// npm install playwright in a temporary browser environment, then set
// FXDASH_PLAYWRIGHT to its index.mjs file URL if it is outside this repository.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const { chromium } = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8784').replace(/\/$/, '');
const out = process.argv[3];
if (!out) throw new Error('Pass an artifact directory as the second argument');
await mkdir(out, { recursive:true });
const browser = await chromium.launch({headless:true});
const results = [], errors = [];
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context = await browser.newContext({viewport:{width,height:950}});
    await context.addInitScript(({lang})=>{
      localStorage.setItem('fxdash.lang',lang);
      localStorage.setItem('fxdash.window','126');
      localStorage.setItem('fxdash.model','ols');
    }, {lang});
    const page = await context.newPage();
    page.on('pageerror', e=>errors.push(String(e)));
    await page.goto(base+'/#/attribution');
    await page.locator('.attrrow').first().waitFor({timeout:60000});
    await page.evaluate(()=>document.fonts.ready);
    const layout = await page.evaluate(()=>({
      width:innerWidth, scroll:document.documentElement.scrollWidth,
      first:document.querySelector('.attrrow').getBoundingClientRect().toJSON(),
      rows:document.querySelectorAll('.attrrow').length,
      factors:document.querySelectorAll('.attrrow .leading-factors b').length,
    }));
    assert.equal(layout.rows,6);
    assert.ok(layout.scroll<=width+1, JSON.stringify(layout));
    assert.ok(layout.first.bottom<950, JSON.stringify(layout));
    assert.ok(layout.factors>=6);
    assert.ok((await page.locator('a.method-link').innerText()).length>3);
    await page.screenshot({path:path.join(out,`attribution-${lang}-${width}.png`),fullPage:true});
    results.push({lang,width,...layout});
    if (lang==='en' && width===1440) {
      for (const w of [63,126,252]) for (const model of ['ols','ridge','lasso']) for (const d of [1,5,21]) {
        await page.locator(`[data-ctl="window"] [data-v="${w}"]`).click();
        await page.locator(`[data-ctl="model"] [data-v="${model}"]`).click();
        await page.locator(`[data-days="${d}"]`).click();
        await page.locator('.attrrow').first().waitFor();
        assert.equal(await page.locator('.attrrow').count(),6);
        const displayed = await page.locator('.attrrow').evaluateAll(rows=>rows.map(r=>({
          pair:r.querySelector('.name a').hash.split('/').at(-1),
          y:Number(r.children[1].textContent.replace(/[^0-9.+-]/g,'')),
          residual:Number(r.children[3].textContent.replace(/[^0-9.+-]/g,'')),
        })));
        const data = await page.evaluate(async ({w,model,d})=>{
          const query=`window=${w}&model=${model}${d===5?'':'&days='+d}`;
          const build=await fetch('build.json');
          const params=query.split('&').sort().map(p=>'.'+p.replace('=','-')).join('');
          return (await fetch(build.ok ? `api/attribution/weekly${params}.json` : `api/attribution/weekly?${query}`)).json();
        },{w,model,d});
        for (const shown of displayed) {
          const saved=data.pairs.find(r=>r.pair===shown.pair);
          assert.ok(Math.abs(shown.y-saved.y_bp)<.051,`${w}/${model}/${d}: y`);
          assert.ok(Math.abs(shown.residual-saved.residual_bp)<.051,`${w}/${model}/${d}: residual`);
        }
      }
    }
    await page.goto(base+'/#/research/USDJPY');
    await page.locator('.comparison-row').first().waitFor({timeout:60000});
    assert.equal(await page.locator('.comparison-row').count(),8);
    await page.screenshot({path:path.join(out,`research-${lang}-${width}.png`),fullPage:true});
    await page.goto(base+'/#/news');
    await page.locator('.driver-pair').first().waitFor({timeout:60000});
    assert.equal(await page.locator('.driver-pair').count(),6);
    if (await page.locator('.brief-preview').count()) {
      const brief = page.locator('.brief-preview');
      assert.ok((await brief.innerText()).includes(lang==='en' ? 'Attribution through' : '归因截至'));
      for (const detail of await brief.locator('.brief-note').all()) {
        await detail.locator('summary').click();
        assert.equal(await detail.locator('.brief-watch dd').count(),3);
        assert.ok(await detail.locator('blockquote').count()>0);
        assert.ok((await detail.innerText()).includes(lang==='en' ? 'have not been confirmed' : '尚未得到确认'));
      }
      await brief.screenshot({path:path.join(out,`briefing-${lang}-${width}.png`)});
    }
    await page.locator('.driver-pair summary').first().click();
    await page.screenshot({path:path.join(out,`news-${lang}-${width}.png`),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,attribution_selections:27,errors},null,2));
  console.log(JSON.stringify({layouts:results.length,attribution_selections:27,errors}));
} finally { await browser.close(); }
