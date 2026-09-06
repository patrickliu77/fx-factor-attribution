// Read-only browser acceptance for the FX pair-day news panels.
// FXDASH_STATIC_ROOT optionally serves a generated tree through browser routing,
// under a Pages-style URL, without starting another HTTP server.
import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium} = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const base = process.argv[2]?.replace(/\/$/, '');
const out = process.argv[3];
if (!base || !out) throw new Error('Pass a base URL and artifact directory');
await mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true});
const errors = [], results = [];
const pairList = ['USDNOK','USDCAD','USDJPY','USDAUD','USDMXN','USDEUR'];
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css',
  '.json':'application/json','.svg':'image/svg+xml','.woff2':'font/woff2'};
try {
  for (const lang of ['en','zh']) for (const width of [390,1440]) {
    const context = await browser.newContext({viewport:{width,height:1000}});
    await context.addInitScript(lang => localStorage.setItem('fxdash.lang', lang), lang);
    if (process.env.FXDASH_STATIC_ROOT) {
      const root = path.resolve(process.env.FXDASH_STATIC_ROOT);
      await context.route(base+'/**', async route => {
        const relative = decodeURIComponent(route.request().url().split('?')[0].slice(base.length)).replace(/^\//,'') || 'index.html';
        const target = path.resolve(root, relative);
        assert.ok(target.startsWith(root+path.sep));
        try {await route.fulfill({body:await readFile(target),contentType:mime[path.extname(target)] || 'application/octet-stream'});}
        catch (e) {if(e.code!=='ENOENT') throw e; await route.fulfill({status:404,body:'Not found'});}
      });
    }
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(String(e)));
    page.on('dialog', async d => {errors.push(d.message()); await d.dismiss();});
    await page.goto(base+'/#/fx');
    await page.locator('[data-card="USDJPY"]').waitFor({timeout:60000});
    await page.waitForFunction(() => document.querySelectorAll('[data-chart][_echarts_instance_]').length===6);
    await page.evaluate(() => {
      window.__newsChartNodes = [...document.querySelectorAll('[data-chart]')];
      window.__newsChartIds = window.__newsChartNodes.map(el => el.getAttribute('_echarts_instance_'));
    });
    const loadFeed = pair => page.evaluate(async pair => {
      const build = await fetch('build.json');
      return (await fetch(`api/pairs/${pair}/news${build.ok ? '.json' : ''}`)).json();
    }, pair);
    for (const pair of pairList) {
      const feed = await loadFeed(pair);
      assert.ok(Array.isArray(feed.days), 'The running backend/build must expose date-local groups');
      await page.locator(`[data-card="${pair}"] .card__name`).click();
      const panel = page.locator('.pairnews');
      await panel.waitFor();
      assert.ok((await panel.locator('h2').innerText()).includes(pair.slice(0,3)+'/'+pair.slice(3)));
      const days = panel.locator('[data-pair-day]');
      assert.equal(await days.count(),feed.days.length);
      assert.equal(await panel.locator('[data-explain]').count(),0);
      if (feed.days.length) {
        const first = days.first();
        assert.notEqual(await first.getAttribute('open'),null);
        assert.equal(await panel.locator('[data-pair-day][open]').count(),1);
        assert.equal(await first.locator('.pairnews__item').count(),feed.days[0].items.length);
        const button = first.locator('[data-day-analysis]');
        if (await button.count()) {
          await button.click();
          assert.equal(await button.getAttribute('aria-expanded'),'true');
          assert.equal(await first.locator('.explain:visible').count(),1);
          const expected = feed.days[0].context.why_unexplained?.[lang] || feed.days[0].context.why_unexplained?.en;
          if (expected) assert.ok((await first.locator('.explain').innerText()).includes(expected));
          assert.equal(await first.locator('.pairnews__item .explain').count(),0);
          if (pair==='USDJPY') {
            await first.screenshot({path:path.join(out,`jpy-${lang}-${width}.png`)});
            // A theme redraw keeps the selected day and analysis expanded.
            const theme = page.locator('#themebtn');
            if (await theme.count()) {
              await theme.click();
              await page.locator('.pairnews [data-day-analysis][aria-expanded="true"]').waitFor();
              await page.screenshot({path:path.join(out,`jpy-theme-${lang}-${width}.png`)});
              await theme.click();
              await page.locator('.pairnews [data-day-analysis][aria-expanded="true"]').waitFor();
              // Theme changes intentionally redraw charts; update the reference.
              await page.waitForFunction(() => document.querySelectorAll('[data-chart][_echarts_instance_]').length===6);
              await page.evaluate(() => {
                window.__newsChartNodes=[...document.querySelectorAll('[data-chart]')];
                window.__newsChartIds=window.__newsChartNodes.map(el=>el.getAttribute('_echarts_instance_'));
              });
            }
          }
          await button.click();
          assert.equal(await button.getAttribute('aria-expanded'),'false');
        }
        if (feed.days.length>1) {
          const second=days.nth(1);
          assert.equal(await second.getAttribute('open'),null);
          await second.locator(':scope > summary').click();
          const olderButton=second.locator('[data-day-analysis]');
          if(await olderButton.count()) {
            await olderButton.click();
            const expected=feed.days[1].context.why_unexplained?.[lang] || feed.days[1].context.why_unexplained?.en;
            if(expected) assert.ok((await second.locator('.explain').innerText()).includes(expected));
          }
          await second.locator(':scope > summary').click();
          assert.equal(await second.locator('.explain:visible').count(),0);
        }
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth+1));
      assert.ok(await page.evaluate(() => window.__newsChartNodes.every((node,i) =>
        node === document.querySelectorAll('[data-chart]')[i] && node.getAttribute('_echarts_instance_') === window.__newsChartIds[i])));
      results.push({lang,width,pair,days:feed.days.length,legacy_explain_buttons:0,chart_instances_preserved:true});
    }
    await context.close();
  }
  assert.deepEqual(errors,[]);
  const report={base,results,errors};
  await writeFile(path.join(out,'pair-news-audit.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close();}
