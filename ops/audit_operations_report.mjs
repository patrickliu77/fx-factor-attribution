// Inspect the actual offline report without changing pipeline artifacts.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium} = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [report, out] = process.argv.slice(2);
if (!report || !out) throw new Error('Pass report HTML and audit output folder');
await mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true});
const errors = [], externalRequests = [], results = [];
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({viewport:{width, height:1000}, offline:true});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => {if (/^https?:/.test(request.url())) externalRequests.push(request.url());});
    await page.goto(pathToFileURL(path.resolve(report)).href);
    await page.evaluate(() => document.fonts.ready);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.equal(await page.locator('.metric').count(), 3);
    await page.locator('summary', {hasText:'查看验收口径'}).click();
    assert.ok(await page.getByText('推送回执只证明', {exact:false}).isVisible());
    await page.locator('summary', {hasText:'查看验收口径'}).click();
    await page.screenshot({path:path.join(out, `operations-${width}.png`), fullPage:true});
    const fonts = await page.evaluate(() => ({body:getComputedStyle(document.body).fontFamily,
      mono:getComputedStyle(document.querySelector('.metric strong')).fontFamily,
      embedded:[...document.fonts].map(font => ({family:font.family,status:font.status}))}));
    assert.ok(fonts.body.includes('Outfit') && fonts.mono.includes('IBM Plex Mono'));
    assert.ok(fonts.embedded.every(font => font.status === 'loaded'));
    // Only the page clock is advanced. No saved date or run record is altered.
    await page.evaluate(() => {
      const future = Date.parse(document.getElementById('observed').dateTime) + 7 * 3600000;
      Date.now = () => future;
      showAge();
    });
    assert.ok(await page.locator('#stale').isVisible());
    results.push({width, fonts, offline:true, no_page_overflow:true, disclosure:true, stale_warning:true});
    await context.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  const result = {report, results, errors, externalRequests};
  await writeFile(path.join(out, 'audit.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
