import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
// Offline recovery and out-of-order response fixtures, with no listening socket.
import {staticFixture} from './browser_static.mjs';
const {chromium}=await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [base,out]=process.argv.slice(2),root=process.env.FXDASH_STATIC_FIXTURE;
if(!base || !out || !root)throw new Error('Pass base, output folder and FXDASH_STATIC_FIXTURE');
const before=process.env.FXDASH_EXPECT_OLD_RECOVERY==='1';
const meta=JSON.parse(await readFile(path.join(root,'api/meta.json'),'utf8'));
const manifest=JSON.parse(await readFile(path.join(root,'build.json'),'utf8'));
const originalTicker=JSON.parse(await readFile(path.join(root,'api/market/ticker.json'),'utf8'));
await mkdir(out,{recursive:true});
const results=[],errors=[],browser=await chromium.launch({headless:true});
const frame=page=>page.waitForTimeout(40);
const tick=page=>page.evaluate(()=>window.__fxRefreshRecoveryTick());
const gate=()=>{let release,seen;return {blocked:new Promise(r=>release=r),started:new Promise(r=>seen=r),release:()=>release(),seen:()=>seen()};};
const fresh=[new Date(Date.now()-3600000).toISOString(),new Date(Date.now()-300000).toISOString()];
const runtime=stamp=>({state:'green',last_success_state:'green',reasons:[],
  heartbeat:{state:'green',last_live_success:stamp,warn_hours:26,crit_hours:72},
  server:{data_version:meta.data_version},runtime:{observed_at:new Date().toISOString(),last_success_at:stamp,
    attribution_as_of:manifest.as_of,provisional_rows:0,latest_attempt:{state:'succeeded',started_at:stamp}}});
const pulse=stamp=>({state:'green',last_run:stamp,last_published:null,warn_hours:26,crit_hours:72,days_on_record:2,reasons:[]});
const ticker=value=>({...originalTicker,available:true,trading_day:true,session_date:'2026-10-02',
  items:[{...originalTicker.items[0],last:value,date:'2026-10-02',label:'BROWSER TEST USD/EUR'}]});
async function openContext(lang,width,label){
  const context=await browser.newContext({viewport:{width,height:1000}});
  await staticFixture(context,base);
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin
    ?route.fallback():route.abort('blockedbyclient'));
  await context.addInitScript(lang=>{
    localStorage.setItem('fxdash.lang',lang);
    localStorage.setItem('fxdash.window','126');localStorage.setItem('fxdash.model','ols');
    window.__fxUnhandled=[];
    window.addEventListener('unhandledrejection',event=>window.__fxUnhandled.push(String(event.reason)));
    const original=window.setInterval;
    window.setInterval=(callback,delay,...args)=>delay===300000
      ?(window.__fxRefreshRecoveryTick=callback,54321):original(callback,delay,...args);
  },lang);
  const page=await context.newPage();page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
  page.on('pageerror',error=>errors.push({label,lang,width,message:String(error)}));
  return {context,page};
}
async function savedApi(route){
  const url=new URL(route.request().url());
  let relative=url.pathname.slice(new URL(base).pathname.length).replace(/^\//,'');
  if(!relative.endsWith('.json')){
    const params=[...url.searchParams].sort(([a],[b])=>a.localeCompare(b));
    relative+=params.map(([k,v])=>`.${k}-${v}`).join('')+'.json';
  }
  try{return route.fulfill({json:JSON.parse(await readFile(path.join(root,relative),'utf8'))});}
  catch{return route.fulfill({status:404,body:'fixture missing'});}
}
async function snapshot(page){return page.evaluate(()=>({
  runtime_tone:document.querySelector('#runtime-status details')?.dataset.runtimeTone,
  runtime_text:document.querySelector('#runtime-status summary')?.textContent,
  pulse_state:document.querySelector('#pulse')?.dataset.state,pulse_text:document.querySelector('#pulse')?.textContent,
  built_hidden:document.querySelector('#built')?.hidden,built_state:document.querySelector('#built')?.dataset.state,
  health:[...document.querySelectorAll('.health__row')].slice(0,3).map(row=>({text:row.textContent.trim(),dot:row.querySelector('i')?.getAttribute('style')})),
  tape_hidden:document.querySelector('#tape')?.hidden,session_hidden:document.querySelector('#session')?.hidden,
  tape_text:document.querySelector('#tape')?.textContent,
  view_text:document.querySelector('#view')?.textContent.slice(0,400),unhandled:window.__fxUnhandled,
}));}
async function exposeHealth(page){await page.locator('.health').evaluate(el=>{el.closest('details').open=true;});await frame(page);}
try{
  for(const lang of ['en','zh']) for(const width of [390,1440]){
    {
      const {context,page}=await openContext(lang,width,'refresh');
      let phase='initial',tickerGate=null;
      const counts={runtime:0,narrative:0,ticker:0};
      await page.route(base+'/build.json',route=>route.fulfill({status:404,body:''}));
      await page.route(base+'/api/**',async route=>{
        const endpoint=new URL(route.request().url()).pathname.slice(new URL(base).pathname.length);
        if(endpoint==='/api/status'){
          counts.runtime++;return phase==='all_failed'?route.fulfill({status:503,body:'fixture transient failure'}):route.fulfill({json:runtime(fresh[phase==='initial'?0:1])});
        }
        if(endpoint==='/api/narrative/status'){
          counts.narrative++;return phase==='all_failed'?route.fulfill({status:503,body:'fixture transient failure'}):route.fulfill({json:pulse(fresh[phase==='initial'?0:1])});
        }
        if(endpoint==='/api/market/ticker'){
          counts.ticker++;
          if(tickerGate?.first){tickerGate.first=false;tickerGate.seen();await tickerGate.blocked;return route.fulfill({status:503,body:'fixture delayed failure'});}
          if(['all_failed','ticker_only_failed'].includes(phase))return route.fulfill({status:503,body:'fixture transient failure'});
          return route.fulfill({json:ticker(phase==='initial'?1.111:phase==='overlap'?3.444:2.333)});
        }
        return savedApi(route);
      });
      await page.goto(base+'/#/news');await page.locator('.health').waitFor({state:'attached'});
      await page.waitForFunction(()=>typeof window.__fxRefreshRecoveryTick==='function');
      await exposeHealth(page);
      const initial=await snapshot(page);
      assert.equal(initial.runtime_tone,'green');assert.equal(initial.pulse_state,'green');assert.equal(initial.tape_hidden,false);
      phase='all_failed';await tick(page);
      await page.waitForFunction(()=>document.querySelector('#runtime-status details')?.dataset.runtimeTone==='red'
        &&document.querySelector('#pulse')?.dataset.state==='red'&&document.querySelector('#tape').hidden);
      await frame(page);const failed=await snapshot(page);
      await page.screenshot({path:path.join(out,`all-failed-${lang}-${width}.png`),fullPage:true});
      phase='recovered';await tick(page);
      await page.waitForFunction(()=>document.querySelector('#runtime-status details')?.dataset.runtimeTone==='green'
        &&document.querySelector('#pulse')?.dataset.state==='green'&&!document.querySelector('#tape').hidden
        &&document.querySelector('#tape').textContent.includes('2.3330'));
      await frame(page);const recovered=await snapshot(page);
      phase='ticker_only_failed';await tick(page);
      await page.waitForFunction(()=>document.querySelector('#tape').hidden);await frame(page);
      const tickerFailed=await snapshot(page);
      await page.screenshot({path:path.join(out,`ticker-only-failed-${lang}-${width}.png`),fullPage:true});
      phase='overlap';tickerGate={...gate(),first:true};await tick(page);await tickerGate.started;
      const latestTicker=page.waitForResponse(response=>response.url()===base+'/api/market/ticker');
      await tick(page);await (await latestTicker).finished();
      await page.waitForFunction(()=>!document.querySelector('#tape').hidden&&document.querySelector('#tape').textContent.includes('3.4440'));
      await frame(page);const overlapRecovered=await snapshot(page);
      const lateFailure=page.waitForResponse(response=>response.url()===base+'/api/market/ticker');
      tickerGate.release();await (await lateFailure).finished();await frame(page);
      const afterLateFailure=await snapshot(page);
      await page.screenshot({path:path.join(out,`late-ticker-failure-${lang}-${width}.png`),fullPage:true});
      assert.equal(afterLateFailure.tape_hidden,before,'Old ticker failure must not hide a newer successful response');
      assert.deepEqual(afterLateFailure.unhandled,[]);
      results.push({scenario:'refresh_recovery',lang,width,initial,failed,recovered,ticker_only_failed:tickerFailed,
        latest_ticker_recovered:overlapRecovered,after_old_failure:afterLateFailure,counts,
        conclusions:{status_and_narrative_recover:recovered.runtime_tone==='green'&&recovered.pulse_state==='green',
          ticker_failure_leaves_independent_health_unchanged:tickerFailed.runtime_tone==='green'&&tickerFailed.pulse_state==='green',
          stale_ticker_failure_ignored:!afterLateFailure.tape_hidden}});
      await context.close();
    }
    {
      const {context,page}=await openContext(lang,width,'meta_retry');const first=gate();let calls=0;
      await page.route(base+'/build.json',route=>route.fulfill({status:404,body:''}));
      await page.route(base+'/api/**',async route=>{
        const endpoint=new URL(route.request().url()).pathname.slice(new URL(base).pathname.length);
        if(endpoint==='/api/status')return route.fulfill({json:runtime(fresh[1])});
        if(endpoint==='/api/narrative/status')return route.fulfill({json:pulse(fresh[1])});
        if(endpoint==='/api/market/ticker')return route.fulfill({json:ticker(2.333)});
        if(endpoint==='/api/meta'){
          if(++calls===1){first.seen();await first.blocked;return route.fulfill({status:503,body:'fixture transient failure'});}
          return calls===2?route.fulfill({status:503,body:'fixture transient failure'}):route.fulfill({json:meta});
        }
        return savedApi(route);
      });
      await page.goto(base+'/#/news');await first.started;
      await page.evaluate(()=>location.hash='#/attribution');
      await page.waitForFunction(()=>document.querySelector('#view').textContent.includes('503 /meta'));
      const failed=await snapshot(page);first.release();
      await page.waitForFunction(()=>typeof window.__fxRefreshRecoveryTick==='function');
      await page.evaluate(()=>location.hash='#/news');await page.locator('.driver-context').waitFor({state:'attached'});
      await frame(page);const recovered=await snapshot(page);
      await page.screenshot({path:path.join(out,`meta-recovered-${lang}-${width}.png`),fullPage:true});
      assert.ok(calls>=3);assert.deepEqual(recovered.unhandled,[]);
      results.push({scenario:'meta_retry',lang,width,calls,failed,recovered,retries_and_recovers:calls>=3&&!recovered.view_text.includes('503 /meta')});
      await context.close();
    }
    for(const failureKind of ['http_503','network','invalid_json']){
      const {context,page}=await openContext(lang,width,'build_retry_'+failureKind);
      let calls=0,available=false;const requests=[],initialLookup=gate();
      await context.route(base+'/api/market/ticker.json',route=>route.fulfill({json:ticker(2.333)}));
      await context.route(base+'/api/narrative/status.json',route=>route.fulfill({json:pulse(fresh[1])}));
      await context.route(base+'/api/status.json',route=>route.fulfill({json:runtime(fresh[1])}));
      await page.route(base+'/build.json',async route=>{
        if(++calls===1){initialLookup.seen();await initialLookup.blocked;}
        if(available)return route.fulfill({json:manifest});
        if(failureKind==='network')return route.abort('failed');
        if(failureKind==='invalid_json')return route.fulfill({status:200,contentType:'application/json',body:'not json'});
        return route.fulfill({status:503,body:'fixture transient failure'});
      });
      page.on('request',request=>{if(new URL(request.url()).pathname.includes('/api/'))requests.push(request.url());});
      await page.goto(base+'/#/news');await initialLookup.started;
      // Navigate while the initial lookup is pending. A retry must share that
      // request, and rejection must leave later navigation able to try again.
      await page.evaluate(()=>{location.hash='#/attribution';});await frame(page);
      assert.equal(calls,1,'Build lookup must use a single in-flight request');
      initialLookup.release();await page.locator('#view code').waitFor({state:'attached'});
      await page.waitForFunction(()=>typeof window.__fxRefreshRecoveryTick==='function');
      const failed=await snapshot(page);
      if(!before){
        assert.equal(await page.locator('[data-build-retry]').count(),1);
        assert.ok(failed.view_text.includes(lang==='zh'?'可以重试':'You can retry'));
        // The retry button is a bounded user action, not an automatic loop.
        const callsBeforeRetry=calls;
        await page.locator('[data-build-retry]').click();
        await page.waitForFunction(()=>document.querySelector('#view code')?.textContent.includes('temporarily unavailable')
          ||document.querySelector('#view code')?.textContent.includes('暂不可用'));
        await frame(page);
        assert.ok(calls>callsBeforeRetry);
      }
      available=true;
      let afterRetry=null;
      if(!before&&failureKind==='http_503'){
        await page.locator('[data-build-retry]').click();
        await page.locator('.attrrow').first().waitFor({state:'attached'});
        await frame(page);afterRetry=await snapshot(page);
        assert.equal(afterRetry.tape_hidden,false);
        assert.equal(afterRetry.session_hidden,false);
        assert.equal(afterRetry.built_hidden,false);
        assert.equal(afterRetry.pulse_state,'green');
        assert.deepEqual(afterRetry.unhandled,[]);
      }
      await page.evaluate(()=>{location.hash='#/news';});
      if(before)await page.waitForTimeout(100);
      else await page.locator('.driver-context').waitFor({state:'attached'});
      await frame(page);
      const afterNavigation=await snapshot(page);
      const recovered=calls>1&&!afterNavigation.view_text.includes('404 /meta')&&afterNavigation.health.length>0;
      assert.equal(recovered,!before,'Build lookup failure must be recoverable on a later navigation');
      assert.deepEqual(afterNavigation.unhandled,[]);
      const normalPage=await context.newPage();normalPage.setDefaultTimeout(15000);
      await normalPage.goto(base+'/#/news');await normalPage.locator('.health').waitFor({state:'attached'});
      await frame(normalPage);const normal=await snapshot(normalPage);await normalPage.close();
      const headersRecovered=afterNavigation.tape_hidden===normal.tape_hidden
        &&afterNavigation.session_hidden===normal.session_hidden&&afterNavigation.built_hidden===normal.built_hidden
        &&afterNavigation.built_state===normal.built_state&&afterNavigation.pulse_state===normal.pulse_state;
      await page.screenshot({path:path.join(out,`build-${failureKind}-${lang}-${width}.png`),fullPage:true});
      results.push({scenario:'build_retry',failure_kind:failureKind,lang,width,build_calls:calls,failed,after_retry:afterRetry,after_navigation:afterNavigation,requests,
        normal_static_headers:normal,single_flight_checked:true,static_headers_recovered:headersRecovered,
        recovers_after_build_endpoint_returns:recovered});
      assert.equal(headersRecovered,!before,'Recovered static header must match a normal static load');
      await context.close();
    }
  }
}catch(error){errors.push({setup_failure:true,message:String(error),stack:error.stack});}
finally{
  await browser.close();
  const audit={base,fixture_only:true,before,production_writes:false,results,errors};
  await writeFile(path.join(out,'audit.json'),JSON.stringify(audit,null,2));
  console.log(JSON.stringify({scenarios:results.length,errors,conclusions:results.map(({scenario,lang,width,conclusions,retries_and_recovers,recovers_after_build_endpoint_returns})=>({scenario,lang,width,conclusions,retries_and_recovers,recovers_after_build_endpoint_returns}))}));
}
if(errors.length)process.exitCode=1;
