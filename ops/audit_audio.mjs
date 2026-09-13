// Real saved MP3s; headless/muted playback. No generation or provider requests.
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
    const context=await browser.newContext({viewport:{width,height:1000}});
    await context.addInitScript(({lang})=>localStorage.setItem('fxdash.lang',lang),{lang});
    const page=await context.newPage(),requests=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>{if(r.url().includes('/media/briefing/')) requests.push(r.url());});
    await page.goto(base.replace(/\/$/,'')+'/#/news');
    const panel=page.locator('.brief-audio'),player=panel.locator('audio');
    await panel.waitFor();
    await page.evaluate(()=>document.fonts.ready);
    assert.equal(await player.getAttribute('preload'),'none');
    assert.equal(await player.getAttribute('autoplay'),null);
    assert.equal(await player.evaluate(a=>a.paused),true);
    assert.equal(requests.length,0,'Opening the page must not download/play an MP3');
    assert.ok((await player.getAttribute('src')).endsWith('/'+lang+'.mp3'));
    await player.evaluate(async a=>{window.__auditAudio=a;a.muted=true;await a.play();});
    await page.waitForFunction(()=>window.__auditAudio.currentTime>0.2);
    const actual=await player.evaluate(a=>({duration:a.duration,source:a.currentSrc,error:a.error?.code??null}));
    assert.ok(actual.duration>=60 && actual.duration<=180);
    assert.equal(actual.error,null);
    await player.evaluate(a=>{a.pause();a.currentTime=30;});
    await page.waitForFunction(()=>Math.abs(window.__auditAudio.currentTime-30)<1);
    await panel.locator('details').evaluate(d=>{d.open=true;});
    assert.ok((await panel.innerText()).includes(lang==='en'?'economic':'经济'));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await panel.screenshot({path:path.join(out,`audio-${lang}-${width}.png`)});
    await player.evaluate(a=>a.dispatchEvent(new Event('error')));
    assert.equal(await panel.locator('[data-audio-error]').isVisible(),true);
    const select=page.locator('[data-brief-history]');
    const older=await select.locator('option').evaluateAll(items=>items.find(i=>i.value!=='current')?.value);
    if (older) {
      await select.selectOption(older);
      assert.equal(await page.evaluate(()=>window.__auditAudio.paused),true);
      assert.equal(await page.locator('[data-brief-content] audio').count(),0);
      await select.selectOption('current');
    }
    await page.click(`[data-lang="${lang==='en'?'zh':'en'}"]`);
    await page.waitForFunction(expected=>document.querySelector('.brief-audio audio')?.getAttribute('src')?.endsWith('/'+expected+'.mp3'),lang==='en'?'zh':'en');
    assert.equal(await page.locator('.brief-audio audio').evaluate(a=>a.paused),true);
    results.push({lang,width,...actual,no_autoplay:true,no_initial_download:true,seek:true,transcript:true,history:true,error_hint:true,language_switch:true});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'audit.json'),JSON.stringify({base,results,errors},null,2));
  console.log(JSON.stringify({base,layouts:results.length,errors}));
} finally {await browser.close();}
