"""Presentation contracts for the compact News page, with synthetic saved content."""
import json
import shutil
import subprocess

import pytest

from fxdash.web.app import STATIC_DIR


def run_browser_module(script, lang):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for presentation tests')
    prefix = """
      import assert from 'node:assert/strict';
      globalThis.localStorage={getItem:()=> 'en',setItem:()=>{}};
      globalThis.document={documentElement:{}};
    """
    for name, filename in [('I','i18n.js'), ('B','briefing-board.js'),
                           ('C','context.js'), ('E','briefing-extras.js')]:
        prefix += f'const {name}=await import({json.dumps((STATIC_DIR/filename).as_uri())});\n'
    prefix += f'I.setLang({json.dumps(lang)});\n'
    result = subprocess.run([node, '--input-type=module', '-e', prefix+script],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('lang',['en','zh'])
def test_saved_briefing_only_changes_presentation(lang):
    run_browser_module(r"""
      const text='USD/AUD +90.3 bp (provisional); residual +9.7 bp. USD/MXN: Saved evidence <example>.';
      const brief={available:true,mode:'catchup',date:'2026-09-11',state:'ready',
        attribution_as_of:'2026-09-11',text:{en:text,zh:text},notes:[],warnings:[],
        news_observed_by:'2026-09-12T00:45:09+00:00',generated_at:'2026-09-12T00:45:46+00:00'};
      const original=JSON.stringify(brief);
      const html=C.briefingHtml(brief,{embedded:true,runDetails:'SAVED RUN DETAILS'});
      assert.ok(!html.includes('<h2'));
      assert.ok(html.includes('class="brief-asof" hidden'));
      assert.ok(html.includes('<p>USD/AUD +90.3 bp (provisional); residual +9.7 bp.</p>'));
      assert.ok(html.includes('<p>USD/MXN: Saved evidence &lt;example&gt;.</p>'));
      assert.ok(!html.includes('<example>'));
      const details=html.indexOf('<details class="brief-source-details">');
      assert.ok(details>html.indexOf('class="brief-copy"'));
      assert.ok(html.indexOf('data-brief-status')>details);
      assert.ok(html.includes('SAVED RUN DETAILS'));
      assert.ok(!C.briefingHtml({...brief,attribution_as_of:'2026-09-10'},{embedded:true})
        .includes('class="brief-asof" hidden'));
      assert.ok(C.briefingHtml(brief).includes('<h2'));
      const board=B.briefingBoardHtml(brief,{history:[],catchup_history:[brief]},{});
      assert.equal((board.match(/<h2 /g)||[]).length,1);
      assert.ok(board.includes('class="sr-only"'));
      assert.ok(board.includes('data-brief-history'));
      assert.ok(board.includes('2026-09-11'));
      assert.equal(JSON.stringify(brief),original);
    """,lang)


@pytest.mark.parametrize('lang',['en','zh'])
def test_recap_leads_and_original_numbers_remain_in_closed_details(lang):
    run_browser_module(r"""
      const original='USD/AUD +90.3 bp (provisional); residual +15.5 bp.';
      const recap='The dollar was broadly stronger.\n\nMore <context>.';
      const brief={available:true,mode:'catchup',date:'2026-09-14',state:'numbers_only',
        attribution_as_of:'2026-09-11',text:{en:original,zh:original},
        recap:{version:'market-recap-v1',text:{en:recap,zh:recap}},notes:[],warnings:['Saved warning']};
      const before=JSON.stringify(brief),html=C.briefingHtml(brief);
      const details=html.indexOf('<details class="brief-source-details">');
      assert.ok(html.indexOf('<p>The dollar was broadly stronger.</p>')<details);
      assert.ok(html.indexOf(original)>details);
      assert.ok(html.indexOf('Saved warning')>details);
      assert.ok(!html.includes('<details class="brief-source-details" open'));
      assert.ok(html.includes('More &lt;context&gt;.') && !html.includes('<context>'));
      assert.equal(JSON.stringify(brief),before);
    """,lang)


@pytest.mark.parametrize('lang',['en','zh'])
def test_empty_calendar_collapses_without_claiming_no_releases(lang):
    run_browser_module(r"""
      const context={date:'2026-09-13',state:'partial',observed_at:'2026-09-13T12:00:00Z',events:[]};
      const empty=E.calendarHtml(context);
      assert.ok(empty.startsWith('<details class="brief-calendar brief-calendar-empty">'));
      assert.ok(!empty.includes(' open'));
      assert.ok(empty.includes(I.getLang()==='en'?'Missing items do not mean':'缺少条目不代表'));
      assert.ok(empty.includes(context.observed_at));
      const event={source:'BEA',source_url:'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics',
        title:'Example <release>',scheduled_at:'2026-09-14T12:30:00Z'};
      const populated=E.calendarHtml({...context,events:[event]});
      assert.ok(populated.startsWith('<section class="brief-calendar">'));
      assert.ok(populated.includes('Example &lt;release&gt;'));
      assert.ok(populated.includes('calendar-coverage'));
      assert.ok(E.calendarHtml({...context,events:[{...event,source_url:'https://invalid.test'}]})
        .includes('brief-calendar-empty'));
      assert.equal(E.calendarHtml(null),'');
    """,lang)


@pytest.mark.parametrize('lang',['en','zh'])
def test_subscription_cta_is_opt_in_and_has_no_eager_iframe(lang):
    run_browser_module(r"""
      const settings={enabled:true,forms:{en:'https://test.sibforms.com/serve/english=',
        zh:'https://test.sibforms.com/serve/chinese=='}};
      const html=E.subscriptionHtml(settings);
      assert.ok(html.includes('<summary class="subscribe-cta">'));
      assert.ok(html.includes('class="subscribe-panel"'));
      assert.ok(html.includes('Brevo'));
      assert.ok(html.includes('09:00'));
      assert.ok(!html.includes('<iframe'));
      assert.ok(!html.includes('<input'));
      assert.equal(E.subscriptionHtml({...settings,enabled:false}),'');
      assert.equal(E.subscriptionHtml({...settings,forms:{en:'https://bad.test',zh:'https://bad.test'}}),'');
    """,lang)


@pytest.mark.parametrize('lang',['en','zh'])
def test_factor_details_remain_available_inside_one_fold(lang):
    run_browser_module(r"""
      const data={as_of:'2026-09-11',slates:{},pairs:[{pair:'USDJPY',date:'2026-09-11',
        y:0.001,residual:0.0002,provisional:true,leading:[{factor:'DOLLAR_LOO',contribution_bp:12.3,news_key:'dollar'}]}]};
      const html=C.driversHtml(data);
      assert.ok(html.startsWith('<details class="driver-context">'));
      assert.ok(!html.includes(' open'));
      assert.ok(html.includes('driver-context-body'));
      assert.ok(html.includes('USD/JPY'));
      assert.ok(html.includes('+12.3 bp'));
      assert.ok(html.includes('2026-09-11'));
      assert.ok(html.includes(I.t('quote.provisional')));
      assert.equal(C.driversHtml({pairs:[]}), '');
    """,lang)
