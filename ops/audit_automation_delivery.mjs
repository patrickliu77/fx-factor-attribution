// Synthetic subscription/calendar UI only. Never contacts a real form or sends email.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2);
if (!base || !out) throw new Error('Pass local URL and audit directory');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),results=[],errors=[];
const formUrls={en:'https://test.sibforms.com/serve/english=',zh:'https://test.sibforms.com/serve/chinese=='};
const calendar={revision:'official-us-calendar-1',date:'2026-09-11',timezone:'America/New_York',
  observed_at:'2026-09-11T13:00:00+00:00',coverage:'US BLS and BEA only',state:'partial',sources:[],
  events:[{id:'example1',source:'BEA',source_url:'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics',
    title:'Example release for UI testing',scheduled_at:'2026-09-11T12:30:00+00:00'},
    {id:'example2',source:'BLS',source_url:'https://www.bls.gov/schedule/news_release/bls.ics',
    title:'Example upcoming release for UI testing',scheduled_at:'2026-09-14T14:00:00+00:00'}]};
try {
  for (const lang of ['en','zh']) for (const theme of ['dark','light']) for (const width of [390,1440]) {
    const ctx=await browser.newContext({viewport:{width,height:1100}});
    await ctx.addInitScript(({lang,theme})=>{localStorage.setItem('fxdash.lang',lang);localStorage.setItem('fxdash.theme',theme);},{lang,theme});
    const page=await ctx.newPage();let formRequests=0;
    page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/api/news*',async route=>{
      const response=await route.fetch(),body=await response.json();
      body.subscription={enabled:true,forms:formUrls};
      body.briefing.calendar=calendar;
      await route.fulfill({response,json:body});
    });
    await page.route('https://test.sibforms.com/**',route=>{
      formRequests++;
      assert.equal(route.request().headers().referer,undefined);
      return route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="en"><body><h1>Test form only</h1><label>Email<input type="email"></label><p>No submission is performed.</p></body></html>'});
    });
    await page.goto(base.replace(/\/$/,'')+'/#/news');
    await page.locator('.brief-calendar').waitFor();
    await page.evaluate(()=>document.fonts.ready);
    const urlChecks=await page.evaluate(async ()=>{
      const {subscriptionHtml}=await import(new URL('briefing-extras.js',document.baseURI));
      const visible=url=>subscriptionHtml({enabled:true,forms:{en:url,zh:url}})!=='';
      const valid=['','=','=='].map(p=>'https://test.sibforms.com/serve/Abc_123-'+p);
      const invalid=['','=','==','Abc===','Ab=c','Ab==c','Ab=c=','Abc=def==','Abc%3D','Abc/def','Abc+def']
        .map(token=>'https://test.sibforms.com/serve/'+token);
      for (const padding of ['','=','==']) {
        invalid.push(`http://test.sibforms.com/serve/a${padding}`,`https://evil.test/serve/a${padding}`,
          `https://sibforms.com/serve/a${padding}`,`https://test.sibforms.com.evil.test/serve/a${padding}`,
          `https://test.sibforms.com:8443/serve/a${padding}`,`https://test.sibforms.com/serve/a${padding}?email=x`,
          `https://u:p@test.sibforms.com/serve/a${padding}`,`https://test.sibforms.com/serve/a${padding}#x`);
      }
      return [...valid.map(url=>({url,visible:visible(url),expected:true})),
        ...invalid.map(url=>({url,visible:visible(url),expected:false}))];
    });
    for (const check of urlChecks) assert.equal(check.visible,check.expected,check.url);
    assert.equal(formRequests,0);
    assert.equal(await page.locator('.brief-subscribe iframe').count(),0);
    assert.equal(await page.locator('.brief-subscribe').evaluate(d=>d.open),false);
    assert.ok((await page.locator('.brief-calendar').innerText()).includes(lang==='zh'?'预定时刻已过':'Scheduled time passed'));
    await page.locator('.brief-calendar').screenshot({path:path.join(out,`calendar-${lang}-${theme}-${width}.png`)});
    await page.locator('.brief-subscribe summary').click();
    assert.equal(formRequests,0);
    await page.locator('.subscribe-panel').screenshot({path:path.join(out,`subscription-${lang}-${theme}-${width}.png`)});
    await page.locator('[data-load-subscription]').click();
    await page.frameLocator('.brief-subscribe iframe').getByLabel('Email').fill('test@example.test');
    assert.equal(await page.locator('.brief-subscribe iframe').getAttribute('src'),formUrls[lang]);
    assert.equal(formRequests,1);
    assert.equal(await page.evaluate(()=>JSON.stringify(localStorage).includes('test@example.test')),false);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    results.push({lang,theme,width,url_validation_cases:urlChecks.length,padded_iframe_url:true,
      no_initial_provider_request:true,click_to_load:true,no_address_stored:true,no_overflow:true});
    await ctx.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,synthetic:true,real_emails_sent:0,results,errors},null,2));
  console.log(JSON.stringify({layouts:results.length,errors,real_emails_sent:0}));
} finally {await browser.close();}
