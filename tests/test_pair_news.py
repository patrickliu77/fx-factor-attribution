"""Run the dependency-free frontend helpers under Node, with no DOM/network."""
import json
import shutil
import subprocess

import pytest
from fxdash.web.app import STATIC_DIR


def test_pair_news_display_helpers():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node needed for JavaScript display helpers")
    uri = (STATIC_DIR / "pair-news.js").as_uri()
    script = '''
      import assert from 'node:assert/strict';
      const {distinctSummary: summary, pairNewsDays: groups, pairNewsDaysHtml: html} = await import(MODULE);
      const title = "Yen Caught Between Top U.S. Officials’ Remarks";
      for (const text of [title, title+'   WSJ', title+' \\u00a0 WSJ', title+' - WSJ',
                         title+' | WSJ', title.replace('’', "'")+' WSJ']) {
        assert.equal(summary({title,source:'WSJ',summary:text}), '');
      }
      assert.equal(summary({title:'日元上涨',source:'新华社',summary:'日元上涨 · 新华社'}), '');
      assert.equal(summary({title:'Dollar +5%',summary:'Dollar -5%'}), 'Dollar -5%');
      assert.equal(summary({title,source:'WSJ',summary:title+' WSJ reported a new statement.'}),
        title+' WSJ reported a new statement.');
      assert.equal(summary({title,summary:'Officials disputed the report.'}), 'Officials disputed the report.');
      assert.equal(summary({title,summary:null}), '');
      const evidence = date => ({date,pair:'USDJPY',y_bp:-170,residual_bp:-150,residual_z:-4});
      const story = {title,url:'https://example.com/a',summary:title+' WSJ',source:'WSJ',
        evidence:[evidence('2026-09-04'),evidence('2026-09-03')],
        context:{date:'2026-09-04',why_unexplained:{en:'Latest only'}}};
      const legacy = groups({items:[story]},'USDJPY');
      assert.deepEqual(legacy.map(g=>g.date), ['2026-09-04','2026-09-03']);
      assert.equal(legacy[0].context.why_unexplained.en,'Latest only');
      assert.equal(legacy[1].context.why_unexplained,undefined);
      assert.deepEqual(groups({items:[story]},'USDCAD'), []);
      assert.deepEqual(groups({days:[],items:[story]},'USDJPY'), []);
      const feed = {days:[{pair:'USDJPY',date:'2026-09-04',context:{date:'2026-09-04',
        y_bp:-170,systematic_bp:-18,exogenous_bp:-2,residual_bp:-150,residual_z:-4,
        why_unexplained:{en:'One daily analysis'}},
        items:[story,{...story,title:'Another headline',url:'https://example.com/b',summary:'Independent detail.'}]}]};
      const esc = v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      const ui = {esc,t:s=>s,label:s=>s,fmtBp1:v=>v.toFixed(1),metaLine:()=>'',
        hasExplain:ctx=>!!ctx.why_unexplained,
        explainBlock:ctx=>'<div class="explain">'+esc(ctx.why_unexplained.en)+'</div>'};
      const result = html('USDJPY',feed,ui);
      assert.equal((result.match(/One daily analysis/g)||[]).length,1);
      assert.equal((result.match(/data-day-analysis/g)||[]).length,1);
      assert.equal((result.match(/class="pairnews__item"/g)||[]).length,2);
      assert.equal((result.match(/class="summary"/g)||[]).length,1);
      assert.equal((result.match(/aria-expanded="false"/g)||[]).length,1);
      assert.ok(result.includes('class="pairday__analysis" hidden'));
      assert.ok(!result.includes('data-explain='));
      assert.ok(!result.includes('undefined') && !result.includes('NaN'));
      feed.days[0].items[0] = {title:'<script>alert(1)</script>',url:'https://x/" onmouseover="x',summary:'<img src=x>'};
      const safe = html('USDJPY',feed,ui);
      assert.ok(!safe.includes('<script>') && !safe.includes('<img') && !safe.includes(' onmouseover="'));
    '''.replace("MODULE", json.dumps(uri))
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
