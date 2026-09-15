// Display-only news grouping. A commentary describes a pair-day, not one article.
// Kept dependency-free so the same helpers can be tested without a browser.
const normalized = value => String(value || '').normalize('NFKC')
  .replace(/[\u200b-\u200d\ufeff]/g, '').replace(/[‘’]/g, "'")
  .replace(/[“”]/g, '"').replace(/[‐‑–—]/g, '-')
  .replace(/\s+/g, ' ').trim().toLocaleLowerCase().replace(/[.!。！]+$/, '');

export function distinctSummary(item) {
  const summary = String(item.summary || '').trim();
  const body = normalized(summary), title = normalized(item.title);
  if (!body || body === title) return '';
  // Google News descriptions often contain only the headline plus publisher.
  // Suppress exact boilerplate, never fuzzy-match an actual extra sentence.
  if (title && body.startsWith(title)) {
    const tail = body.slice(title.length).replace(/^[\s|:·-]+/, '');
    if (tail && tail === normalized(item.source)) return '';
  }
  return summary;
}

export function pairNewsDays(feed, pair) {
  if (Array.isArray(feed.days)) {
    return feed.days.filter(g => g.pair === pair && g.date && g.items?.length)
      .slice().sort((a, b) => b.date.localeCompare(a.date));
  }
  // Compatible with an older running server/static snapshot. It has only one
  // context per story, so never borrow that prose for a different evidence date.
  const days = new Map();
  for (const item of feed.items || []) {
    for (const e of item.evidence || []) {
      if (e.pair !== pair || !e.date) continue;
      if (!days.has(e.date)) days.set(e.date, {
        date: e.date, pair, context: {date: e.date, y_bp: e.y_bp,
          residual_bp: e.residual_bp, residual_z: e.residual_z}, items: [],
      });
      const group = days.get(e.date);
      if (item.context?.date === e.date) group.context = item.context;
      if (!group.items.some(s => s.url === item.url)) group.items.push(item);
    }
  }
  return [...days.values()].sort((a, b) => b.date.localeCompare(a.date));
}

export function pairNewsDaysHtml(pair, feed, ui) {
  const {esc, t, label, fmtBp1, metaLine, hasExplain, explainBlock} = ui;
  const groups = pairNewsDays(feed, pair);
  if (!groups.length) return `<p class="empty">${esc(t('fx.nonewsforpair'))}</p>`;
  return groups.map((g, i) => {
    const ctx = g.context || {};
    const id = `pair-day-${pair}-${i}`;
    const saved = ui.dayState?.[g.date];
    const expanded = saved?.open ?? (i === 0);
    const analysisOpen = saved?.analysis ?? false;
    const rows = g.items.map(item => {
      const summary = distinctSummary(item);
      return `<article class="pairnews__item">
        <div class="expand__top"><div class="expand__text">
          ${metaLine(item, null)}
          <div class="pairnews__title">${esc(item.title)}</div>
          ${summary ? `<div class="summary">${esc(summary)}</div>` : ''}
          ${(item.duplicates || []).length ? `<div class="hint">${esc(t('news.alsoby'))}: ${
            esc(item.duplicates.map(d => d.source || d.title).filter(Boolean).join(', '))}</div>` : ''}
        </div><div class="actions"><a class="btn" href="${esc(item.url)}" target="_blank" rel="noopener">${
          esc(t('news.readfull'))} ↗</a></div></div>
      </article>`;
    }).join('');
    const metric = (key, name, cls='') => Number.isFinite(ctx[key])
      ? `<div class="pairday__metric ${cls}"><span>${esc(name)}</span><b>${fmtBp1(ctx[key])} <small>bp</small></b></div>` : '';
    const metrics = metric('y_bp', t('fx.dayreturn')) + metric('systematic_bp', t('sys'), 'is-systematic')
      + metric('exogenous_bp', t('exo'), 'is-exogenous') + metric('residual_bp', t('res'), 'is-residual');
    return `<details class="pairday" data-pair-day="${esc(g.date)}" ${expanded ? 'open' : ''}>
      <summary class="pairday__head"><span class="pairday__date">${esc(label(pair))}<time datetime="${esc(g.date)}">${esc(g.date)}</time></span>
        <span class="pairday__count">${g.items.length} ${esc(t('fx.stories'))}</span>
      </summary>
      <div class="pairday__body">
        ${metrics ? `<div class="pairday__metrics">${metrics}</div>` : ''}
        <div class="pairday__basis"><span>${esc(t('fx.daybasis'))}</span>
          ${Number.isFinite(ctx.window) ? `<span>OLS · ${ctx.window}</span>` : ''}
          ${Number.isFinite(ctx.residual_z) ? `<span>z ${ctx.residual_z.toFixed(2)}</span>` : ''}
          ${ctx.provisional === true ? `<span class="tag">${esc(t('quote.provisional'))}</span>` : ''}</div>
        ${hasExplain(ctx) ? `<button class="btn pairday__analysis-toggle" type="button" data-day-analysis
          aria-expanded="${analysisOpen}" aria-controls="${id}">${esc(t(analysisOpen ? 'fx.hidedayanalysis' : 'fx.viewdayanalysis'))}</button>
          <div id="${id}" class="pairday__analysis" ${analysisOpen ? '' : 'hidden'}>${explainBlock(ctx,
            t('explain.headpair', {pair: label(pair)}), {showDay: false})}</div>`
          : `<p class="hint">${esc(t('fx.nodayanalysis'))}</p>`}
        <div class="pairday__sources">${esc(t('fx.citedstories'))}</div>
        <div class="pairday__stories">${rows}</div>
      </div>
    </details>`;
  }).join('');
}

export function bindPairNewsDays(panel, t, onChange = () => {}) {
  if (!panel) return;
  const remember = () => {
    const state = {};
    panel.querySelectorAll('[data-pair-day]').forEach(day => {
      state[day.dataset.pairDay] = {open: day.open,
        analysis: day.querySelector('[data-day-analysis]')?.getAttribute('aria-expanded') === 'true'};
    });
    onChange(state);
  };
  panel.querySelectorAll('[data-day-analysis]').forEach(button => {
    button.onclick = () => {
      const target = panel.ownerDocument.getElementById(button.getAttribute('aria-controls'));
      if (!target || !panel.contains(target)) return;
      const open = target.hidden;
      target.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      button.textContent = t(open ? 'fx.hidedayanalysis' : 'fx.viewdayanalysis');
      remember();
    };
  });
  panel.querySelectorAll('[data-pair-day]').forEach(day => {day.ontoggle = remember;});
  // Date and analysis toggles change only this panel, preserving price charts,
  // selected ranges, scroll position and keyboard focus.
}
