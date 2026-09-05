import { getLang, t } from './i18n.js';
const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bp = x => x == null ? 'n/a' : `${x>0?'+':''}${x.toFixed(1)} bp`;
const factorName = f => t('factor.'+f) === 'factor.'+f ? f : t('factor.'+f);
const link = (url, text) => `<a href="${esc(/^https?:\/\//i.test(url) ? url : '#')}" target="_blank" rel="noopener">${text}</a>`;
const sourceLink = i => link(i.url, `<span>${esc(i.title)}</span><small>${esc(i.source || copy('Publisher unlabelled','未标注来源'))} · ${esc(i.published)} ↗</small>`);
const reasonText = reason => ({
  missing_title:copy('Missing title.','缺少标题。'),
  invalid_article_url:copy('Article link is unavailable or invalid.','报道链接缺失或无效。'),
  profile_or_reference_page:copy('Profile, quote or reference page.','资料、报价或索引页面。'),
  quote_or_chart_page:copy('Quote or live-chart page.','报价或实时图表页面。'),
  sovereign_fund_investment_without_fx_or_policy_context:copy('Fund investment with no currency, rate or policy cue in the title.','基金投资标题未提及汇率、利率或政策背景。'),
  vix_name_collision:copy('The title refers to an unrelated use of the name Vix.','标题中的 Vix 指向其他同名主题。'),
  different_volatility_market:copy('Crypto volatility; its relevance to equity VIX needs checking.','加密资产波动率，与股票 VIX 的关联待核对。'),
  generic_topic_heading:copy('Topic heading with no specific event.','主题名称，未给出具体事件。'),
  topic_not_clear_from_title_or_snippet:copy('The title and snippet do not clearly identify this search topic.','标题和摘要未清楚提及本次检索的主题。'),
  metal_market_context_not_clear:copy('A metal-market context is unclear from the title and snippet.','标题和摘要中的金属市场背景不明确。'),
  outside_current_date_window_or_undated:copy('Undated or outside the retrieval date window.','缺少日期或超出本次检索日期范围。'),
  duplicate_url_or_headline:copy('Duplicate link or matching normalised headline.','链接重复或规范化后的标题相同。'),
}[reason] || copy('Unrecognised screening reason; inspect the saved record.','筛选原因尚无对应说明，请查阅保存记录。'));

function auditHtml(items, kind) {
  if (!items?.length) return '';
  const review = kind === 'review';
  return `<details class="context-audit context-${kind}"><summary>${review ? copy('Needs review','待核对') : copy('Excluded candidates','已排除的候选')} (${items.length})</summary>
    ${review ? `<p class="hint">${copy('These links are kept for reading and left out of new generated briefing notes.','保留这些链接供阅读，不送入新晨报的解读生成。')}</p>` : ''}
    ${items.map(i=>`${sourceLink(i)}<p class="hint">${esc(reasonText(i.reason))}</p>${i.duplicate_of ? link(i.duplicate_of, esc(copy('Retained or review copy ↗','查看保留或待核对版本 ↗'))) : ''}`).join('')}</details>`;
}

function slateHtml(slate) {
  if (!slate) return `<p class="hint">${copy('No mapped search for this factor.','这个因子尚未配置新闻检索。')}</p>`;
  const items = slate.items || [];
  const c = slate.coverage;
  const coverage = c && !slate.error ? `<p class="hint context-coverage">${copy(
    `${c.candidates} candidates · ${c.retained} retained · ${c.review} need review · ${c.excluded} excluded. Shortlist: ${c.displayed} links, ${c.displayed_publishers} labelled publishers.`,
    `${c.candidates} 条候选 · ${c.retained} 条保留 · ${c.review} 条待核对 · ${c.excluded} 条排除。优先展示 ${c.displayed} 条，标注来源 ${c.displayed_publishers} 个。`)}${c.missing_publisher_metadata ? ' '+copy(`${c.missing_publisher_metadata} shortlisted links have no publisher label.`,`${c.missing_publisher_metadata} 条优先展示的链接未标注来源。`) : ''}</p>` : '';
  return `<div class="context-slate">${coverage}${items.slice(0,3).map(sourceLink).join('') || `<p class="hint">${slate.error ? copy('Feed unavailable.','新闻源暂不可用。') : copy('No retained reporting in this search.','本次检索没有保留的报道。')}</p>`}
    ${items.length>3 ? `<details class="context-audit context-more"><summary>${copy('Other retained links','其余保留链接')} (${items.length-3})</summary>${items.slice(3).map(sourceLink).join('')}</details>` : ''}
    <p class="hint">${copy('Retrieved','抓取于')} ${esc(slate.observed_at)} · ${copy('Google News redirect links','链接经 Google News 跳转')}</p>
    ${auditHtml(slate.review,'review')}${auditHtml(slate.excluded,'excluded')}</div>`;
}

export function driversHtml(data) {
  if (!data?.pairs?.length) return '';
  return `<section class="driver-context col gap14"><h2 class="sec">${copy('Leading factors and current news','主要因子与当前新闻')}</h2>
    <p class="stack-note">${esc(data.as_of)} · OLS 126 · ${copy('Daily log-return bp. Factor searches and currency searches are shown separately. Headlines provide reading context; their relevance to the observed move still needs checking.','单日对数收益 bp。因子检索与货币检索分开呈现。标题提供阅读线索，报道与这次波动的关联仍需核实。')}</p>
    ${data.source_policy ? `<p class="hint context-policy">${copy('Screening uses titles and snippets. Shortlists rotate labelled publishers, with newer reports first within each publisher. Matching links or headlines are merged; paraphrased copies can remain. Publisher labels come from RSS and do not establish independent confirmation.','筛选依据标题和摘要。优先展示列表轮流选取不同标注来源，每个来源内优先较新报道。相同链接或标题会合并，改写转载仍可能重复。来源标注取自 RSS，数量不代表独立证实。')}</p>` : ''}
    ${data.pairs.map(row=>`<details class="driver-pair"><summary><span>${esc(row.pair.replace('USD','USD/'))}</span><span>${bp(row.y == null ? null : row.y*1e4)}${row.provisional ? ' · '+esc(t('quote.provisional')) : ''}</span><small>${row.leading.map(f=>`${esc(factorName(f.factor))} ${bp(f.contribution_bp)}`).join(' · ')}</small></summary>
      <p class="hint">${copy('Observation','观测日期')} ${esc(row.date)} · ${copy('Residual','残差')} ${bp(row.residual == null ? null : row.residual*1e4)} · <a href="#/research/${esc(row.pair)}">${copy('Inspect sensitivities','查看敏感度')} ↗</a></p>
      ${row.leading.map(f=>`<h3>${esc(factorName(f.factor))}</h3>${slateHtml(data.slates[f.news_key])}`).join('')}
      <h3>${copy('Currency context','货币背景')}</h3>${slateHtml(data.slates[row.currency_news])}
    </details>`).join('')}</section>`;
}

export function briefingHtml(brief) {
  if (!brief?.available) return '';
  const lang = getLang();
  const formal = brief.mode === 'edition';
  const warningText = w => ({
    inputs_after_cutoff:copy('Inputs arrived after the cutoff.','输入晚于截止时间。'),
    inputs_not_from_this_morning:copy('No input packet captured this morning.','没有本日早晨采集的输入。'),
    previous_session_attribution_unavailable:copy('Previous-session attribution was unavailable at collection.','采集时未收到上一交易日归因。'),
    invalid_packet:copy('A usable pre-cutoff packet is unavailable.','缺少截止前的有效输入。'),
    archive_unreadable:copy('This frozen archive could not be read. It has not been replaced with older text.','这份冻结档案无法读取，未用旧稿替换。'),
    checked_draft_unavailable:copy('A checked draft was unavailable at publication.','发布时尚无通过检查的稿件。'),
    'Provisional attribution is included and labelled.':copy('Provisional figures are marked.','待确认数字已标注。'),
    'No driver commentary passed verification; saved figures remain available.':copy('No driver note passed all checks; the saved figures remain available.','没有因子解读通过全部检查，保留已保存数字。'),
  }[w] || w);
  const notes = (brief.notes || []).map(item=>{
    const note=item.note[lang], checks=item.checks[lang], d=item.definition;
    return `<details class="brief-note"><summary>${esc(item.pair.replace('USD','USD/'))} · ${esc(factorName(item.note.factor))}</summary>
      ${d ? `<p class="stack-note">${copy('Target excluded from this factor','该因子排除本货币对')}：${esc(d.excluded_target)}. ${d.members ? esc(d.members.join(', ')) : `${copy('Low-yield group','低息组')} ${esc(d.low.join(', '))}；${copy('high-yield group','高息组')} ${esc(d.high.join(', '))}`}</p>` : ''}
      <p>${esc(note.event)}</p><p class="hint">${copy('Evidence-checking plan, written in code. These observations have not been confirmed.','代码生成的核验路径，下列观测尚未得到确认。')}</p><dl class="brief-watch"><dt>${copy('Condition to check','待核对的条件')}</dt><dd>${esc(checks.condition)}</dd><dt>${copy('Evidence to seek','需要寻找的证据')}</dt><dd>${esc(checks.supports)}</dd><dt>${copy('Counterevidence','削弱这条解释的观测')}</dt><dd>${esc(checks.weakens)}</dd></dl>
      <div class="context-slate">${item.note.evidence.map(e=>{const s=item.sources.find(s=>s.id===e.source_id);return s ? `<a href="${esc(/^https?:\/\//i.test(s.url) ? s.url : '#')}" target="_blank" rel="noopener">${esc(s.title)}<small>${esc(s.source)} · ${esc(s.published)} ↗</small></a><blockquote>${esc(e.quote)}</blockquote>` : '';}).join('')}</div>
    </details>`;
  }).join('');
  return `<section class="brief-preview col gap14" data-briefing-state="${esc(brief.state || 'preview')}"><h2 class="sec">${formal ? copy('Morning briefing','文字晨报') : copy('Text briefing preview','文字简报预览')} ${esc(brief.date || '')}</h2>
    <p class="hint">${copy('Attribution through','归因截至')} ${esc(brief.attribution_as_of)} · ${copy('News observed by','新闻抓取截至')} ${esc(brief.news_observed_by)}</p>
    <p>${esc(brief.text?.[lang] || brief.text?.en || '')}</p>
    ${brief.late_publication ? `<p class="hint">${copy('This edition was finalized late.','本期延迟生成。')} ${esc(brief.generated_at)}</p>` : ''}
    <p class="stack-note">${copy('Numbers come from saved attribution. The language model uses retrieved titles and snippets; source and wording checks cannot establish causality or verify every interpretation. Publication dates have day precision.','数字取自已保存归因。语言模型阅读检索标题和摘要，来源与文字规则检查无法证明因果关系，也无法核实所有解释。新闻发布日期仅精确到日。')}</p>
    ${!formal ? `<p class="hint">${copy('Validation preview, not a historical morning edition.','运行验收预览，不代表历史晨报。')}</p>` : ''}
    ${brief.warnings?.map(w=>`<p class="hint">${esc(warningText(w))}</p>`).join('') || ''}${notes}</section>`;
}
