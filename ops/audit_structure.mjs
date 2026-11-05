// Read-only browser checks of PCA presentation on a live or project-path static site.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium} = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base, out] = process.argv.slice(2);
if (!base || !out) throw new Error('Pass a site URL and audit artifact directory');
await mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true});
const rows = [], errors = [];
try {
  for (const lang of ['en','zh']) for (const theme of ['dark','light']) for (const width of [390,1440]) {
    const context = await browser.newContext({viewport:{width,height:1050}});
    await context.addInitScript(({lang,theme})=>{
      localStorage.setItem('fxdash.lang',lang);
      localStorage.setItem('fxdash.theme',theme);
      localStorage.setItem('fxdash.window','126');
      localStorage.setItem('fxdash.model','ols');
    }, {lang,theme});
    const page = await context.newPage();
    page.on('pageerror', error=>errors.push(String(error)));
    const pcaRequests = [];
    page.on('request', request=>{if (request.url().includes('/pca')) pcaRequests.push(request.url());});
    await page.goto(base.replace(/\/$/,'')+'/#/attribution');
    const panel = page.locator('#market-structure');
    await panel.waitFor();
    assert.equal(await panel.getAttribute('open'), null);
    assert.equal(pcaRequests.length,0);
    await panel.locator(':scope > summary').click();
    await page.locator('[data-structure="variance"] canvas').waitFor();
    await page.evaluate(()=>document.fonts.ready);
    const info = await page.evaluate(()=>{
      const root = document.querySelector('#market-structure');
      const options = [...root.querySelectorAll('[data-structure]')].map(el=>echarts.getInstanceByDom(el).getOption());
      return {width:innerWidth,scroll:document.documentElement.scrollWidth,
        labels:[...root.querySelectorAll('.structure__metric strong')].map(el=>el.textContent),
        font:getComputedStyle(root).fontFamily, numberFont:getComputedStyle(root.querySelector('strong')).fontFamily,
        dates:options[0].xAxis[0].data.length, variance:options[0].series[0].data.at(-1),
        dollar:options[1].series[0].data.at(-1), carry:options[1].series[1].data.at(-1)};
    });
    assert.ok(info.scroll<=width+1, JSON.stringify(info));
    assert.ok(info.font.includes('Outfit') && info.numberFont.includes('IBM Plex Mono'));
    assert.equal(info.dates,252);
    assert.equal(info.labels[0],(info.variance*100).toFixed(1)+'%');
    assert.equal(info.labels[1],(info.dollar*100).toFixed(1)+'%');
    assert.equal(info.labels[2],(info.carry*100).toFixed(1)+'%');
    await panel.screenshot({path:path.join(out,`pca-${lang}-${theme}-${width}.png`)});
    await panel.locator(':scope > summary').click();
    await panel.locator(':scope > summary').click();
    assert.equal(pcaRequests.length,1);
    if (lang==='en' && theme==='dark' && width===1440) {
      for (const window of [63,126,252]) {
        await page.locator(`[data-ctl="window"] [data-v="${window}"]`).click();
        await page.locator('#market-structure').waitFor();
        await page.locator('#market-structure > summary').click();
        await page.locator('[data-structure="variance"] canvas').waitFor();
        assert.ok((await page.locator('.structure__body').innerText()).includes(`${window} observations per fit`));
      }
      await page.locator('[data-ctl="model"] [data-v="ridge"]').click();
      await page.locator('[data-days="21"]').click();
      await page.locator('#market-structure > summary').click();
      await page.locator('[data-structure="variance"] canvas').waitFor();
      assert.ok((await page.locator('.structure__body').innerText()).includes('252 observations per fit'));
    }
    await page.goto(base.replace(/\/$/,'')+'/#/methodology');
    await page.locator('.method__detail').first().waitFor();
    await page.locator('summary', {hasText:lang==='en'?'PCA and model health':'PCA 与模型健康检查'}).click();
    await page.locator('mjx-container').first().waitFor();
    assert.equal(await page.locator('mjx-merror').count(),0);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    rows.push({lang,theme,...info,folded_by_default:true,lazy_loaded_once:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,rows,errors},null,2));
  console.log(JSON.stringify({layouts:rows.length,errors}));
} finally {await browser.close();}
