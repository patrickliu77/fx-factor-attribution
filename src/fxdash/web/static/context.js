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
  return `<section class="brief-preview col gap14"><h2 class="sec">${copy('Text briefing preview','文字简报预览')}</h2>
    <p class="hint">${copy('Attribution through','归因截至')} ${esc(brief.attribution_as_of)} · ${copy('News observed by','新闻抓取截至')} ${esc(brief.news_observed_by)}</p>
    <p>${esc(brief.text?.[lang] || brief.text?.en || '')}</p>
    <p class="stack-note">${copy('A rules-based draft from saved figures, with source context below. This preview is not a scheduled 09:00 ET edition. Source dates have day precision.','这份数据摘要使用已保存数字，下方提供新闻阅读线索。预览尚未接入美东 09:00 定时发布，新闻发布日期仅精确到日。')}</p>
    ${brief.warnings?.map(w=>`<p class="hint">${esc(w)}</p>`).join('') || ''}</section>`;
}
