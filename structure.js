// Saved panel diagnostics only. No factor selection, model fit or attribution here.
import { getLang } from './i18n.js';
import { tokens } from './charts.js';

const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ratio = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1 + 1e-12 ? Math.min(v, 1) : null;
const percent = v => v == null ? copy('Unavailable', '暂无数据') : `${(v * 100).toFixed(1)}%`;

export function structureSnapshot(data) {
  const dates = data?.dates;
  if (!data?.available || ![63,126,252].includes(data.window) || !Array.isArray(dates) || !dates.length ||
      dates.some((d, i) => typeof d !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(d) ||
        !Number.isFinite(Date.parse(d)) || new Date(d).toISOString().slice(0, 10) !== d || (i && d <= dates[i - 1]))) {
    return {available:false};
  }
  const start = Math.max(0, dates.length - 252);
  const values = (key, absolute = false) => dates.slice(start).map((_, i) => {
    const v = data[key]?.length === dates.length ? data[key][start + i] : null;
    return ratio(absolute && typeof v === 'number' ? Math.abs(v) : v);
  });
  return {available:true, window:data.window, dates:dates.slice(start),
    variance:values('var_pc1'), dollar:values('corr_pc1_dollar', true), carry:values('carry_projection_r2')};
}

export function structureShell() {
  return `<details class="structure" id="market-structure"><summary><span>${copy('Market structure · PCA', '市场共同结构 · PCA')}</span><small>${copy('Diagnostic view', '诊断视图')}</small></summary>
    <div class="structure__body"><p class="stack-note" role="status">${copy('Loading saved diagnostics…', '正在读取已保存的诊断…')}</p></div></details>`;
}

export function structureHtml(raw) {
  const data = structureSnapshot(raw);
  if (!data.available) return `<p class="empty">${copy('PCA diagnostics are unavailable for this window. Attribution remains available above.', '该窗口的 PCA 诊断暂无数据，上方归因不受影响。')}</p>`;
  const last = data.dates.length - 1;
  const card = (name, value, note) => `<div class="structure__metric"><span>${name}</span><strong>${percent(value)}</strong><p>${note}</p></div>`;
  return `<p class="stack-note">${copy(
    `Six currencies · ${data.window} observations per fit · latest record ${esc(data.dates[last])}. Each fit ends before its record date.`,
    `六个货币 · 每次拟合使用 ${data.window} 个观测 · 最新记录 ${esc(data.dates[last])}。拟合窗口截至记录日期的前一个共同观测。`)}</p>
    <div class="structure__metrics">
      ${card(copy('PC1 variance share', 'PC1 方差占比'), data.variance[last], copy('Concentration of standardised FX returns in one common direction.', '标准化汇率收益集中在一个共同方向上的程度。'))}
      ${card(copy('Dollar alignment |corr|', '美元方向一致度 |corr|'), data.dollar[last], copy('Similarity between PC1 and the full-panel dollar basket.', 'PC1 与全篮子美元因子的相似程度。'))}
      ${card(copy('Carry projection R²', '套息投影 R²'), data.carry[last], copy('How much of the panel carry factor lies in the PC2/PC3 space.', '面板套息因子落在 PC2、PC3 空间内的比例。'))}
    </div>
    <div class="structure__plots">
      <figure><figcaption>${copy('How concentrated is the common structure?', '共同结构有多集中？')}</figcaption><div class="structure__chart" data-structure="variance" role="img" aria-label="${copy('PC1 variance share history', 'PC1 方差占比历史')}"></div></figure>
      <figure><figcaption>${copy('Do the named baskets track that structure?', '美元与套息篮子是否贴合共同结构？')}</figcaption><div class="structure__chart" data-structure="alignment" role="img" aria-label="${copy('Dollar alignment and carry projection history', '美元方向一致度与套息投影历史')}"></div></figure>
    </div>
    <p class="stack-note">${copy('Showing up to 252 saved records. The window selector applies here; the regression model and 1D/5D/21D return period do not change PCA.', '展示最近至多 252 条已保存记录。训练窗口选择作用于本区域；回归模型与 1D、5D、21D 收益区间不会改变 PCA。')}</p>
    <details class="structure__guide"><summary>${copy('How to use these readings', '这些读数怎么用')}</summary>
      <p>${copy('A higher PC1 share means more of the standardised panel variation is concentrated in one direction. Check dollar alignment before giving that direction a dollar interpretation. Lower carry projection invites a review of the carry basket and the period being studied.', 'PC1 占比越高，标准化面板的变化越集中于同一个方向。先看美元一致度，再判断这个方向与美元篮子的关系。套息投影下降时，可以复查套息篮子的构造和所处时期。')}</p>
      <p>${copy('Dollar alignment uses absolute correlation; carry uses a projection R². Their historical reference levels are 90% and 50%, respectively, not calibrated trading signals. PC2 alone is retained in the archive for legacy comparison; this view uses the PC2/PC3 plane because its axes can rotate.', '美元一致度使用绝对相关系数，套息使用投影 R²，历史参考线分别为 90% 和 50%，不能作为交易信号。单独 PC2 的旧指标仍留在档案中；本页使用 PC2、PC3 的整个平面，减少坐标轴旋转带来的干扰。')}</p>
      <p>${copy('These are diagnostics of the same six-return panel, not independent economic evidence. The monitoring baskets include the full panel; attribution uses target-excluded baskets. None of these percentages is a share of today’s FX move, a forecast score or a causal estimate. PCA never changes the contributions above.', '这些指标来自同一组六货币收益，独立经济证据仍需另行寻找。监测篮子使用完整面板，单币归因篮子则排除目标货币。这些百分比不代表当天涨跌的解释份额、预测得分或因果效应，PCA 不改动上方贡献数字。')}</p>
    </details>`;
}

export function structureOptions(raw) {
  const data = structureSnapshot(raw);
  if (!data.available) return {};
  const C = tokens();
  const line = (name, values, color) => ({name,type:'line',data:values,showSymbol:false,connectNulls:false,
    lineStyle:{color,width:1.8},itemStyle:{color}});
  const base = {animation:false,textStyle:{color:C.text,fontFamily:C.mono},
    grid:{left:48,right:16,top:45,bottom:38},
    tooltip:{trigger:'axis',confine:true,backgroundColor:C.raise,borderColor:C.line,textStyle:{color:C.text},
      valueFormatter:v => percent(v)},
    legend:{top:4,textStyle:{color:C.mute,fontSize:11}},
    xAxis:{type:'category',data:data.dates,boundaryGap:false,axisLabel:{color:C.mute,formatter:s=>s.slice(2)},axisLine:{lineStyle:{color:C.line}}},
    yAxis:{type:'value',min:0,max:1,axisLabel:{color:C.mute,formatter:v=>`${Math.round(v*100)}%`},splitLine:{lineStyle:{color:C.grid}}}};
  return {
    variance:{...base,series:[line(copy('PC1 variance share','PC1 方差占比'),data.variance,C.sys)]},
    alignment:{...base,series:[line(copy('Dollar |corr|','美元 |corr|'),data.dollar,C.exo),line(copy('Carry R²','套息 R²'),data.carry,C.accent)]},
  };
}
