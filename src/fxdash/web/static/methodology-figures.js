// Original SVG illustrations shared by Methodology and the README exports.
// All marks are schematic. No fitted values or observed returns are plotted here.
const escape = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const label = (x, y, value, cls = '', anchor = 'start') =>
  `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${escape(value)}</text>`;
const line = (x1, y1, x2, y2, cls = 'rule') => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="${cls}"/>`;
const rect = (x, y, w, h, cls) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" class="${cls}"/>`;

const copy = {
  en: {
    pipeline: ['From observations to a daily record', 'Market observations form an aligned panel. Each estimator produces its own coefficients and three contribution groups.'],
    timeline: ['The window stops before the day being explained', 'A rolling window ends at t minus one. Its fitted coefficients are applied to the factor moves on day t.'],
    lasso: ['Selection, then estimation', 'An illustrative set of eight candidate variables is reduced to three. OLS fits new coefficients on the retained columns.'],
    inputs: '01 / OBSERVATIONS', panel: '02 / ALIGN BY PAIR', fits: '03 / FIT THROUGH t−1', out: '04 / ACCOUNT FOR DAY t',
    fx: 'Six USD exchange rates', macro: 'Yields, VIX, commodities, credit', basket: 'Dollar & carry baskets',
    fxnote: 'Daily log returns', macronote: 'Official yields + market series', basketnote: 'Explained pair excluded',
    matrix: 'One date axis, matched market closes', matrixnote: 'Each row holds a return and its factor moves.',
    fitnote: '63, 126 or 252 trading days', ols: 'Baseline factor set', ridge: 'Shrink coefficients', lassoNote: 'Select, then refit with OLS',
    separate: 'Each estimator keeps its own result.', units: 'Coefficients in original units × day t factor moves',
    sys: 'Systematic', exo: 'Exogenous', res: 'Residual', sysnote: 'Dollar + carry', exonote: 'Rates + risk + commodities', resnote: 'Return less contributions',
    store: 'Daily records → dashboard & period sums', news: 'Large residuals → news retrieval & review',
    past: 'OBSERVATIONS USED TO FIT', today: 'DAY TO EXPLAIN', next: 'LATER', window: 'w trading days',
    fit: 'Fit β using this window', apply: 'Apply β to xₜ', measure: 'Read yₜ, calculate the residual',
    shift: 'Next session: move both ends of the window forward by one trading day.',
    menu: '01 / CANDIDATES', select: '02 / LASSO SELECTION', refit: '03 / OLS REFIT',
    menunote: 'Up to eight variables', selectnote: 'Keep columns with nonzero coefficients', refitnote: 'Fit new coefficients on the selected columns',
    chosen: 'Selected set S', used: 'Refit β × factor move → contribution',
    empty: 'Empty selection: all factor contributions are zero; the full return enters the residual.',
    schematic: 'Illustration of the procedure; selection varies with the pair and window.',
  },
  zh: {
    pipeline: ['从市场观测到每日归因', '市场数据按货币对对齐。每个估计量分别生成系数和三组归因结果。'],
    timeline: ['训练窗口截至被解释日的前一天', '窗口止于 t−1，用窗口内估计的系数乘以 t 日的因子变动。'],
    lasso: ['先选择变量，再估计系数', '示意图展示从八个候选变量中保留三个，再用 OLS 估计保留列的系数。'],
    inputs: '01 / 市场观测', panel: '02 / 按货币对对齐', fits: '03 / 用截至 t−1 的数据拟合', out: '04 / 计算 t 日归因',
    fx: '六组美元汇率', macro: '利率、VIX、商品与信用', basket: '美元与套息组合',
    fxnote: '日对数收益', macronote: '官方利率与市场序列', basketnote: '剔除被解释的货币对',
    matrix: '统一日期轴，匹配有效收盘时间', matrixnote: '每行包含当日收益与各因子变动。',
    fitnote: '63、126 或 252 个交易日', ols: '使用基础因子集', ridge: '收缩系数', lassoNote: '选择变量后用 OLS 重拟合',
    separate: '每种估计量分别保存自己的结果。', units: '原量纲系数 × t 日因子变动',
    sys: '系统性贡献', exo: '外生因子贡献', res: '残差', sysnote: '美元 + 套息', exonote: '利率 + 风险 + 商品', resnote: '当日收益减去因子贡献',
    store: '逐日记录 → 仪表盘与期间加总', news: '大幅残差 → 新闻检索与校验',
    past: '用于拟合的历史观测', today: '被解释日', next: '后续', window: 'w 个交易日',
    fit: '在此窗口估计 β', apply: '将 β 应用于 xₜ', measure: '读取 yₜ，计算残差',
    shift: '下一交易日，窗口的起点与终点同时向前移动一天。',
    menu: '01 / 候选变量', select: '02 / Lasso 选择', refit: '03 / OLS 重拟合',
    menunote: '最多八个候选变量', selectnote: '保留系数非零的列', refitnote: '在选中列上重新估计系数',
    chosen: '选中集合 S', used: '重拟合 β × 因子变动 → 贡献',
    empty: '若选择集为空，所有因子贡献为零，当日收益全部计入残差。',
    schematic: '图中选择结果仅作示意，实际结果随货币对和窗口变化。',
  },
};

function pipeline(c) {
  const sources = [[118,c.fx,c.fxnote], [196,c.macro,c.macronote], [274,c.basket,c.basketnote]];
  const inputs = sources.map(([y,title,note],i) => `
    ${rect(40,y-26,38,38,'tile')}
    ${i === 0 ? '<path d="M48 116h8v-12h8v16h8" class="wire"/>' : i === 1 ? '<path d="M48 198v-12m8 12v-18m8 18v-8m8 8v-23" class="wire"/>' : '<circle cx="52" cy="268" r="4" class="node"/><circle cx="67" cy="268" r="4" class="node"/><circle cx="59" cy="279" r="4" class="node"/>'}
    ${label(96,y,title,'body')}${label(96,y+23,note,'small')}
    <path d="M398 ${y-7} C448 ${y-7} 440 190 486 190" class="connector"/>`).join('');
  const grid = Array.from({length:4},(_,r)=>Array.from({length:6},(_,col)=>rect(516+col*59,129+r*26,44,12,col===0?'cell':'cell faint')).join('')).join('');
  return `${label(40,55,c.inputs,'eyebrow')}${label(512,55,c.panel,'eyebrow')}${inputs}
    <path d="M486 190h14l-6-5m6 5-6 5" class="wire"/>
    ${label(512,98,c.matrix,'body')}${grid}${label(512,261,c.matrixnote,'small')}
    ${line(40,324,920,324)}${label(40,358,c.fits,'eyebrow')}${label(920,358,c.fitnote,'small','end')}
    ${['OLS','Ridge','Lasso + OLS'].map((s,i)=>`${label(40+i*305,405,s,'heading')}${label(40+i*305,434,[c.ols,c.ridge,c.lassoNote][i],'small')}`).join('')}
    ${line(326,382,326,439)}${line(631,382,631,439)}
    ${label(40,477,c.separate,'small')}<path d="M875 386v98m-6-7 6 7 6-7" class="wire"/>
    ${line(40,508,920,508)}${label(40,544,c.out,'eyebrow')}${label(920,544,c.units,'small','end')}
    ${[[c.sys,c.sysnote,'sys'],[c.exo,c.exonote,'exo'],[c.res,c.resnote,'res']].map(([a,b,cls],i)=>`
      ${rect(40+i*305,572,265,4,cls)}${label(40+i*305,609,a,'body')}${label(40+i*305,635,b,'small')}`).join('')}
    ${label(40,685,c.store,'small')}${label(920,685,c.news,'small','end')}`;
}

function timeline(c) {
  const cells = Array.from({length:16},(_,i)=>rect(40+i*35,117,28,58,'cell')).join('');
  return `${label(40,49,c.past,'eyebrow')}${label(726,49,c.today,'eyebrow','middle')}${label(896,49,c.next,'eyebrow','middle')}
    ${label(40,90,'t−w','mono')}${label(593,90,'t−1','mono','end')}${label(726,90,'t','mono','middle')}
    ${cells}${rect(690,117,72,58,'day')}${rect(824,117,28,58,'cell faint')}${rect(864,117,28,58,'cell faint')}
    ${line(650,65,650,207,'boundary')}
    <path d="M40 192v10h553v-10" class="wire"/>${label(316,232,c.window,'body','middle')}
    <path d="M316 264v18h410m-7-6 7 6-7 6" class="wire"/>
    ${label(316,313,c.fit,'small','middle')}${label(726,232,c.apply,'body','middle')}${label(726,257,c.measure,'small','middle')}
    ${line(40,347,920,347)}${label(40,384,c.shift,'small')}`;
}

function lasso(c) {
  const chosen = [0,2,5];
  const slots = Array.from({length:8},(_,i)=>{
    const y=113+i*30;
    return `${rect(40,y-16,96,24,'tile')}${label(88,y+1,`x${i+1}`,'mono','middle')}
      <path d="M143 ${y-4}H338" class="${chosen.includes(i)?'wire':'connector faint'}"/>
      ${rect(345,y-16,110,24,chosen.includes(i)?'tile':'cell faint')}
      ${label(400,y+1,chosen.includes(i)?`x${i+1}`:'0',chosen.includes(i)?'mono':'small','middle')}
      ${chosen.includes(i)?`<path d="M464 ${y-4}C547 ${y-4} 566 ${149+chosen.indexOf(i)*59} 636 ${149+chosen.indexOf(i)*59}" class="connector"/>`:''}`;
  }).join('');
  return `${label(40,48,c.menu,'eyebrow')}${label(345,48,c.select,'eyebrow')}${label(650,48,c.refit,'eyebrow')}
    ${label(40,76,c.menunote,'small')}${label(345,76,c.selectnote,'small')}${label(650,76,c.refitnote,'small')}${slots}
    ${chosen.map((i,j)=>`${rect(646,132+j*59,64,32,'tile')}${label(678,154+j*59,`x${i+1}`,'mono','middle')}${label(741,154+j*59,`β${i+1}`,'heading')}`).join('')}
    ${label(650,321,c.chosen,'body')}${label(650,350,c.used,'small')}
    ${line(40,385,920,385)}${label(40,420,c.empty,'small')}${label(40,449,c.schematic,'small')}`;
}

export const figureNames = ['pipeline', 'timeline', 'lasso'];
export function methodologyFigure(name, lang = 'en') {
  const c = copy[lang] || copy.en;
  const spec = {pipeline:[720,pipeline], timeline:[416,timeline], lasso:[480,lasso]}[name];
  if (!spec) throw new Error(`Unknown methodology figure: ${name}`);
  const id = `method-${name}-${lang}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 ${spec[0]}" class="method-diagram" role="img" aria-labelledby="${id}-title ${id}-desc">
    <title id="${id}-title">${escape(c[name][0])}</title><desc id="${id}-desc">${escape(c[name][1])}</desc>
    <style>
      .method-diagram { background:var(--panel,#fff); }
      .method-diagram text { fill:var(--text,#20243b); font-family:var(--display,"Outfit","Segoe UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif); }
      .method-diagram .eyebrow { font-family:var(--mono,"IBM Plex Mono",Consolas,"Microsoft YaHei","PingFang SC",ui-monospace,monospace); font-size:15px; letter-spacing:1.3px; fill:var(--mute,#5f6484); }
      .method-diagram .heading { font-size:28px; font-weight:500; }
      .method-diagram .body { font-size:21px; }
      .method-diagram .small { font-size:15px; fill:var(--text-3,#535874); }
      .method-diagram .mono { font:18px var(--mono,"IBM Plex Mono",Consolas,"Microsoft YaHei","PingFang SC",ui-monospace,monospace); }
      .method-diagram .rule { stroke:var(--line-2,#dfe1eb); stroke-width:1; }
      .method-diagram .wire { stroke:var(--text-3,#535874); stroke-width:1.5; fill:none; }
      .method-diagram .connector { stroke:var(--mute,#8a8fa8); stroke-width:1.2; fill:none; }
      .method-diagram .boundary { stroke:var(--mute,#8a8fa8); stroke-dasharray:3 7; }
      .method-diagram .tile { fill:var(--raise,#f1f1f8); stroke:var(--line-2,#dfe1eb); }
      .method-diagram .cell { fill:var(--text-3,#535874); opacity:.22; }
      .method-diagram .faint { opacity:.12; }
      .method-diagram .node { fill:var(--text-3,#535874); }
      .method-diagram .day { fill:var(--raise,#f1f1f8); stroke:var(--text,#20243b); stroke-width:2; }
      .method-diagram .sys { fill:var(--sys,#6247d6); }
      .method-diagram .exo { fill:var(--exo,#1479c9); }
      .method-diagram .res { fill:var(--res,#7f9c07); }
    </style>
    ${rect(0,0,960,spec[0],'paper')}
    ${spec[1](c)}
  </svg>`.replace('class="paper"', 'fill="var(--panel,#fff)"');
}
