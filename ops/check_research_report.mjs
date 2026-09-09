// Optional local browser check. Uses an existing Playwright install, never downloads.
// FXDASH_BROWSER_MODULE may point to an installed module's file: URL.
// node ops/check_research_report.mjs <report.html> <new-audit-directory>
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [report, destination] = process.argv.slice(2);
if (!report || !destination) throw new Error('Expected report.html and a new audit directory');
await mkdir(destination, {recursive:false});
const {chromium} = await import(process.env.FXDASH_BROWSER_MODULE || 'playwright');
const browser = await chromium.launch({headless:true});
const checks = [];
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage({viewport:{width,height:1000}});
    const errors = [], external = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route(/^https?:/, route => {external.push(route.request().url()); return route.abort();});
    await page.goto(pathToFileURL(path.resolve(report)).href);
    await page.evaluate(() => document.fonts.ready);
    const audit = await page.evaluate(() => ({
      overflow:document.documentElement.scrollWidth > innerWidth,
      svg:document.querySelectorAll('svg').length,
      tables:document.querySelectorAll('table').length,
      outfitLoaded:document.fonts.check('16px Outfit'),
      scrollContainers:[...document.querySelectorAll('.figure-scroll,.table-scroll')]
        .filter(e => e.scrollWidth > e.clientWidth).length,
      clippedSvgLabels:[...document.querySelectorAll('svg text')].filter(el => {
        const box = el.getBBox(), view = el.ownerSVGElement.viewBox.baseVal;
        return box.x < 0 || box.y < 0 || box.x+box.width > view.width || box.y+box.height > view.height;
      }).map(el => el.textContent),
    }));
    checks.push({width,...audit,errors,external});
    await page.screenshot({path:path.join(destination,`page-${width}.png`)});
    if (width === 1440) {
      for (const id of ['estimator','menu','oil','base','expanded']) {
        if (await page.locator(`#${id}`).count()) {
          await page.locator(`#${id}`).screenshot({path:path.join(destination,`${id}.png`)});
        }
      }
    }
    await page.close();
  }
} finally {
  await browser.close();
}
await writeFile(path.join(destination,'checks.json'),JSON.stringify(checks,null,2));
console.log(JSON.stringify(checks,null,2));
if (checks.some(c => c.overflow || c.errors.length || c.external.length || c.clippedSvgLabels.length || !c.outfitLoaded)) {
  process.exitCode = 1;
}
