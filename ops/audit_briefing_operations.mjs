// Read-only localhost acceptance. All synthetic states stay inside browser routes.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium} = await import(process.env.FXDASH_PLAYWRIGHT || 'playwright');
const [baseArgument, outputArgument] = process.argv.slice(2);
const origin = new URL(baseArgument);
assert.equal(origin.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname));
assert.ok(!origin.username && !origin.password && !origin.search && !origin.hash);
const base = origin.href.replace(/\/$/, '');
assert.ok(outputArgument, 'An isolated evidence directory is required');
const output = path.resolve(outputArgument);
await mkdir(output, {recursive:true});
const fixed = '2026-10-05T17:00:00.000Z';
const tasks = ['fxdash-live', 'fxdash-narrative', 'fxdash-publish', 'fxdash-briefing', 'fxdash-catchup'];
const row = (en, zh, date='2026-10-05') => ({date, languages:{
  en:{state:en, content_mode:'audio', confirmation:{state:'not_observed'}},
  zh:{state:zh, content_mode:'text_only', confirmation:{state:'not_observed'}},
}});
const ready = () => ({schema_version:1, observed_at:fixed, scope:'saved_artifacts_only',
  runtime_observation:{state:'current', observed_at:fixed,
    runtime:{state:'ready', persistent:true, dependencies_ready:true, audio_backend:'azure'},
    tools:{git:{available:true},ffprobe:{available:true}},
    credentials:{BANXICO_TOKEN:{configured:true}, AZURE_SPEECH_KEY:{configured:true},
      AZURE_SPEECH_REGION:{configured:true}},
    tasks:Object.fromEntries(tasks.map(name=>[name,{registered:true,enabled:true}]))},
  email:{config_enabled:true,configuration_valid:true,credential_configured:true,delivery_policy:'allow_text',
    today:{...row('not_recorded','not_recorded'),phase:'delivery_due',due:true},
    history:[row('submitted','submitted','2026-09-18')]}});

// This endpoint projects saved files only. It does not inspect host settings or
// query a provider. Store a small public contract projection, never campaign IDs.
const actualResponse = await fetch(base+'/api/briefing/operations', {redirect:'error'});
assert.equal(actualResponse.status, 200, 'Restart the candidate with the current read-only API');
assert.match(actualResponse.headers.get('cache-control') || '', /no-store/);
const actual = await actualResponse.json();
assert.equal(actual.schema_version, 1);
assert.equal(actual.scope, 'saved_artifacts_only');
const serialized = JSON.stringify(actual);
for (const privateField of ['campaign_id','payload_hash','edition_hash','sender_footer','listIds','htmlContent'])
  assert.ok(!serialized.includes('"'+privateField+'"'), 'Operations API exposed '+privateField);
assert.notEqual(actual.email.today.date, '2026-09-18');
assert.ok(actual.email.history.some(item=>item.date==='2026-09-18'
  && item.languages.en.state==='submitted' && item.languages.zh.state==='review_required'));
const actualProjection = {
  api_status:actualResponse.status, cache_control:actualResponse.headers.get('cache-control'),
  runtime_state:actual.runtime_observation.state,
  credential_configured:actual.email.credential_configured,
  missing_tasks:tasks.filter(name=>actual.runtime_observation.tasks[name]?.registered===false),
  today:{date:actual.email.today.date,state:actual.email.today.state,
    en:actual.email.today.languages.en.state,zh:actual.email.today.languages.zh.state},
  historical_counts:actual.email.counts,
};
assert.equal(actualProjection.credential_configured, false, 'Expected the saved missing-key observation');
assert.equal(actualProjection.missing_tasks.length, 5, 'Expected the saved missing-task observation');

const stale = ready();
stale.runtime_observation.observed_at = new Date(Date.parse(fixed)-27*3600000).toISOString();
const historical = ready();
const partial = ready();
partial.email.today = {...row('submitted','review_required'),phase:'delivery_due',due:true};
partial.email.today.languages.zh.confirmation = {state:'provider_confirmed',stats:{delivered:2},observed_at:fixed};
// Extra private provider fields must not become visible in browser markup.
partial.email.today.languages.zh.campaign_id = 'DO_NOT_EXPOSE_PROVIDER_ID';
const submitted = ready();
submitted.email.today = {...row('submitted','submitted'),phase:'delivery_due',due:true};
submitted.email.today.languages.en.confirmation = {state:'provider_sent',stats:{sent:2,delivered:0},observed_at:fixed};
const wrongDate = ready();
wrongDate.email.today = {...row('submitted','submitted','2026-09-18'),phase:'delivery_due',due:true};
const scenarios = [
  {name:'actual-missing-prerequisites', report:actual, expected:'attention', actual:true},
  {name:'api-unavailable', unavailable:true, expected:'not_observed'},
  {name:'stale-runtime', report:stale, expected:'stale'},
  {name:'historical-success', report:historical, expected:'waiting'},
  {name:'today-language-difference', report:partial, expected:'attention'},
  {name:'today-submitted', report:submitted, expected:'submitted'},
  {name:'wrong-today-date', report:wrongDate, expected:'waiting'},
];
const browser = await chromium.launch({headless:true});
const results = [], errors = [], externalAttempts = [];
try {
  for (const lang of ['en','zh']) for (const width of [1440,390]) {
    for (const scenario of scenarios) {
      const context = await browser.newContext({viewport:{width,height:1000}});
      await context.route('**/*', route=>{
        const url = new URL(route.request().url());
        if (url.origin !== origin.origin) {
          externalAttempts.push({scenario:scenario.name,origin:url.origin});
          return route.abort('blockedbyclient');
        }
        return route.fallback();
      });
      await context.addInitScript(value=>localStorage.setItem('fxdash.lang',value),lang);
      const page = await context.newPage();
      page.on('pageerror', error=>errors.push({scenario:scenario.name,message:String(error)}));
      if (!scenario.actual) await page.clock.setFixedTime(new Date(fixed));
      if (!scenario.actual) await page.route(/\/api\/briefing\/operations(?:\.json)?(?:\?|$)/,
        route=>scenario.unavailable ? route.fulfill({status:404,body:'not found'})
          : route.fulfill({json:scenario.report,headers:{'Cache-Control':'no-store'}}));
      await page.goto(base+'/#/news');
      const panel = page.locator('.brief-operations');
      await panel.waitFor();
      assert.equal(await panel.getAttribute('data-operations-state'),scenario.expected,
        `${scenario.name}/${lang}/${width}`);
      await panel.locator('summary').click();
      assert.ok(await panel.evaluate(element=>element.open));
      const text = await panel.innerText();
      assert.ok(!text.includes('DO_NOT_EXPOSE_PROVIDER_ID'));
      assert.ok(text.includes(lang==='zh'?'不证明进入收件箱':'does not prove inbox arrival'));
      if (scenario.name==='api-unavailable') {
        const policy = panel.locator('dt').filter({hasText:lang==='zh'?'语音策略':'Audio policy'}).locator('..').locator('dd');
        assert.equal(await policy.innerText(),lang==='zh'?'未知':'Unknown');
      }
      if (scenario.name==='actual-missing-prerequisites') {
        assert.ok(text.includes(lang==='zh'?'任务未注册':'Tasks are missing'));
        assert.ok(text.includes(lang==='zh'?'发信凭据缺失':'Email credentials are missing'));
        assert.ok(text.includes('2026-09-18'));
        assert.ok(text.includes(lang==='zh'?'已提交邮件服务商':'Submitted to the email service'));
        assert.ok(text.includes(lang==='zh'?'需要人工核查':'Manual review required'));
      }
      if (scenario.name==='stale-runtime') {
        assert.ok(text.includes(lang==='zh'?'暂无当前检查':'No current check'));
        assert.ok(text.includes(lang==='zh'?'尚未检查':'Not checked'));
      }
      if (scenario.name==='historical-success' || scenario.name==='wrong-today-date') {
        assert.ok(text.includes('2026-09-18'));
        assert.ok(!(await panel.locator('summary').innerText()).includes(lang==='zh'?'今日邮件已提交':'Today’s emails submitted'));
      }
      if (scenario.name==='today-language-difference') {
        assert.ok(text.includes('2026-10-05'));
        assert.ok(text.includes(lang==='zh'?'需要人工核查':'Manual review required'));
        assert.ok(text.includes(lang==='zh'?'服务商报告送达，原发送记录保留':'Service reports delivery; the send claim is retained'));
        assert.ok(await panel.locator('.brief-status-grid small').evaluateAll(elements=>
          elements.every(element=>getComputedStyle(element).display==='block')));
      }
      if (scenario.name==='today-submitted') {
        assert.ok(text.includes(lang==='zh'?'文字版':'Text only'));
        assert.ok(text.includes(lang==='zh'?'送达尚未确认':'delivery is not confirmed'));
      }
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),
        `Horizontal overflow ${scenario.name}/${lang}/${width}`);
      const screenshot = `${scenario.name}-${lang}-${width}.png`;
      await panel.screenshot({path:path.join(output,screenshot)});
      if (scenario.name==='today-submitted') {
        await page.evaluate(async ()=>{
          const {refreshBriefingOperations} = await import(new URL('brief-operations.js',document.baseURI));
          refreshBriefingOperations(new Date('2026-10-06T14:00:00Z'));
        });
        assert.equal(await page.locator('.brief-operations').getAttribute('data-operations-state'),'waiting');
        assert.ok(await page.locator('.brief-operations').evaluate(element=>element.open));
      }
      results.push({scenario:scenario.name,lang,width,state:scenario.expected,screenshot,
        no_provider_identifiers:true,no_horizontal_overflow:true,
        next_day_downgrade:scenario.name==='today-submitted'});
      await context.close();
    }
  }
  assert.deepEqual(errors,[]);
} finally {
  await browser.close();
  await writeFile(path.join(output,'audit.json'),JSON.stringify({base,actual_api:actualProjection,
    fixture_only_mutations:true,external_requests_allowed:false,layouts:4,checks:results.length,
    results,errors,external_attempts_blocked:externalAttempts},null,2));
}
console.log(JSON.stringify({layouts:4,checks:results.length,errors,external_attempts_blocked:externalAttempts.length}));
