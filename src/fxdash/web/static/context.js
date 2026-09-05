import { getLang, t } from './i18n.js';
const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bp = x => x == null ? 'n/a' : `${x>0?'+':''}${x.toFixed(1)} bp`;
const factorName = f => t('factor.'+f) === 'factor.'+f ? f : t('factor.'+f);

function slateHtml(slate) {
  if (!slate) return `<p class="hint">${copy('No mapped search for this factor.','这个因子尚未配置新闻检索。')}</p>`;
  const items = slate.items || [];
  return `<div class="context-slate">${items.slice(0,3).map(i=>`<a href="${esc(/^https?:\/\//i.test(i.url) ? i.url : '#')}" target="_blank" rel="noopener"><span>${esc(i.title)}</span><small>${esc(i.source)} · ${esc(i.published)} ↗</small></a>`).join('') || `<p class="hint">${slate.error ? copy('Feed unavailable.','新闻源暂不可用。') : copy('No retained reporting in this search.','本次检索没有保留的报道。')}</p>`}
    <p class="hint">${copy('Retrieved','抓取于')} ${esc(slate.observed_at)} · ${copy('Google News redirect links','链接经 Google News 跳转')}</p>
    ${slate.excluded?.length ? `<details class="context-audit"><summary>${copy('Excluded candidates','排除的候选报道')} (${slate.excluded.length})</summary>${slate.excluded.map(i=>`<p class="hint">${esc(i.title)}<br>${esc(i.reason)}</p>`).join('')}</details>` : ''}</div>`;
}

export function driversHtml(data) {
  if (!data?.pairs?.length) return '';
  return `<section class="driver-context col gap14"><h2 class="sec">${copy('Leading factors and current news','主要因子与当前新闻')}</h2>
    <p class="stack-note">${esc(data.as_of)} · OLS 126 · ${copy('Daily log-return bp. Factor searches and currency searches are shown separately. Headlines provide reading context; their relevance to the observed move still needs checking.','单日对数收益 bp。因子检索与货币检索分开呈现。标题提供阅读线索，报道与这次波动的关联仍需核实。')}</p>
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
