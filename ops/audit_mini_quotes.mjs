// Live/static/public checks for daily numbers, final segments, dates and fallbacks.
import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium} = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [rawBase, out] = process.argv.slice(2);
if (!rawBase || !out) throw new Error('Pass a dashboard base URL and audit output folder');
const base = rawBase.replace(/\/$/, '');
await mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true});
const errors = [], results = [], fixtureResults = [];
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.woff2':'font/woff2','.svg':'image/svg+xml'};
async function ready(page) {
  await page.waitForFunction(() => {
    const date = document.querySelector('[data-mini-asof]');
    return date && !/Loading|正在/.test(date.textContent);
  }, {timeout:60000});
  await page.evaluate(() => document.fonts.ready);
}
async function inspect(page) {
  return page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    const rgb = color => {
      const el = document.createElement('span'); el.style.color = color; document.body.append(el);
      const value = getComputedStyle(el).color; el.remove(); return value;
    };
    const cards = [...document.querySelectorAll('[data-mini]')].map(card => {
      const chart = echarts.getInstanceByDom(card.querySelector('[data-spark]'));
      const option = chart?.getOption();
      const values = option?.series[0].data || [];
      const delta = values.length ? (values.at(-1)/values.at(-2)-1)*100 : null;
      const direction = delta > 0 ? 'up' : delta < 0 ? 'down' : 'mute';
      const expected = css.getPropertyValue('--'+direction).trim();
      return {pair:card.dataset.mini, price:card.querySelector('.mini__px').textContent,
        change:card.querySelector('.mini__chg').textContent,
        quoteDate:card.dataset.quoteDate || null, description:card.getAttribute('aria-label'),
        basis:card.querySelector('[data-mini-basis]').textContent,
        dateVisible:!card.querySelector('[data-mini-date]').hidden,
        dateText:card.querySelector('[data-mini-date]').textContent,
        values, delta, dates:option?.xAxis[0].data || [],
        historyColor:option?.series[0].lineStyle.color,
        latest:option?.series[1].data || [], latestColor:option?.series[1].lineStyle.color,
        expected, neutral:css.getPropertyValue('--mute').trim(),
        metricColorMatches:getComputedStyle(card.querySelector('.mini__chg')).color === rgb(expected)};
    });
    return {cards, asof:document.querySelector('[data-mini-asof]').textContent,
      note:document.querySelector('#mini-note').textContent,
      overflow:document.documentElement.scrollWidth > innerWidth + 1};
  });
}
function verifyCards(result) {
  assert.equal(result.cards.length, 6);
  assert.equal(result.overflow, false);
  for (const card of result.cards.filter(c => c.values.length)) {
    assert.equal(card.historyColor, card.neutral, card.pair);
    assert.equal(card.latestColor, card.expected, card.pair);
    assert.equal(card.metricColorMatches, true, card.pair);
    assert.deepEqual(card.latest, card.values.map((v,i) => i >= card.values.length-2 ? v : null));
    const text = (card.delta > 0 ? '+' : '') + card.delta.toFixed(2) + '%';
    assert.ok(card.change.endsWith(text), card.pair+': '+card.change);
    assert.ok(Math.abs(Number(card.price.replaceAll(',',''))-card.values.at(-1)) <= 0.000051);
    assert.equal(card.quoteDate, card.dates.at(-1));
    assert.ok(card.description.includes(card.dates[0]) && card.description.includes(card.quoteDate));
    assert.ok(card.basis.includes(String(card.values.length)));
  }
}
try {
  for (const lang of ['en','zh']) for (const theme of ['dark','light']) for (const width of [390,1440]) {
    const context = await browser.newContext({viewport:{width,height:1000}});
    await context.addInitScript(({lang,theme}) => {
      localStorage.setItem('fxdash.lang',lang); localStorage.setItem('fxdash.theme',theme);
    }, {lang,theme});
    if (process.env.FXDASH_STATIC_ROOT) {
      await context.route(base+'/**', async r => {
        const rel = decodeURIComponent(new URL(r.request().url()).pathname.slice(new URL(base).pathname.length)).replace(/^\//,'') || 'index.html';
        try { await r.fulfill({body:await readFile(path.join(process.env.FXDASH_STATIC_ROOT,rel)),contentType:mime[path.extname(rel)] || 'application/octet-stream'}); }
        catch { await r.fulfill({status:404,body:'not found'}); }
      });
    }
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(base+'/#/news'); await ready(page);
    const actual = await inspect(page); verifyCards(actual);
    assert.ok(actual.cards.every(c => c.values.length >= 2));
    assert.ok(/scale|缩放/.test(actual.note) && /USD|美元/.test(actual.note));
    await page.locator('.news__side > .col').first().screenshot({path:path.join(out,`quotes-${lang}-${theme}-${width}.png`)});
    results.push({lang,theme,width,...actual});
    if (lang === 'en' && theme === 'dark' && width === 390) {
      let fixture = {available:true,range:'5d',pair:'USDJPY',digits:4,direction:-1,
        dates:['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07'],values:[160,159,157,155,156]};
      await page.route(/\/api\/market\/series\/USDJPY(?:\.range-5d\.json|\?range=5d)$/, r => r.fulfill({json:fixture}));
      for (const kind of ['up_over_down','down_over_up','flat_short_mixed_dates','unavailable']) {
        if (kind === 'down_over_up') fixture = {...fixture,direction:1,values:[150,153,157,158,156]};
        if (kind === 'flat_short_mixed_dates') fixture = {...fixture,direction:0,dates:['2026-09-03','2026-09-04'],values:[156,156]};
        if (kind === 'unavailable') fixture = {available:false,reason:'no_cache'};
        await page.reload(); await ready(page);
        const state = await inspect(page); verifyCards(state);
        const card = state.cards.find(c => c.pair === 'USDJPY');
        if (kind === 'flat_short_mixed_dates') {
          assert.equal(card.values.length, 2); assert.equal(card.latestColor, card.neutral);
          assert.equal(card.dateVisible, true); assert.equal(card.dateText, '2026-09-04');
          assert.ok(state.asof.includes('vary'));
        } else if (kind === 'unavailable') {
          assert.equal(card.values.length, 0); assert.ok(card.basis.includes('unavailable'));
          assert.ok(card.quoteDate && card.price !== 'n/a');
        } else {
          assert.equal(card.price, '156.0000'); // Uses chart input, not the ticker's older quote.
          assert.equal(Math.sign(card.delta), kind === 'up_over_down' ? 1 : -1);
        }
        fixtureResults.push({kind,card});
      }
      await page.route(/\/api\/market\/ticker(?:\.json)?$/, r => r.fulfill({json:{available:false,items:[]}}));
      await page.reload(); await ready(page);
      const absent = (await inspect(page)).cards.find(c => c.pair === 'USDJPY');
      assert.equal(absent.price,'n/a'); assert.equal(absent.values.length,0);
      assert.equal(absent.dateVisible,true); assert.ok(absent.dateText.includes('unavailable'));
      fixtureResults.push({kind:'quote_and_chart_unavailable',card:absent});
    }
    await context.close();
  }
  assert.deepEqual(errors, []);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,fixtureResults,errors},null,2));
  console.log(JSON.stringify({base,actualCases:results.length,fixtureCases:fixtureResults.length,errors,passed:true}));
} finally {await browser.close();}
