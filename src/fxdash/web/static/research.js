// Research views read saved coefficients and contributions; no model fitting here.
import { getLang } from './i18n.js';
import { tokens } from './charts.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const bp = x => x == null ? 'n/a' : `${x > 0 ? '+' : ''}${(x * 1e4).toFixed(1)}`;
const unit = f => f === 'dVIX' ? copy('VIX point','VIX 点') : f.startsWith('d')
  ? copy('yield percentage point','收益率百分点') : copy('log return','对数收益');

export function researchHtml(data, label, controls, comparison = null) {
  if (!data.dates.length) return `<p class="empty">${copy('No observations for this selection.','当前选择没有观测。')}</p>`;
  const last = data.dates.length - 1;
  const panel = (key,title,note) => `<section class="research-panel"><h2 class="sec">${title}</h2><p class="stack-note">${note}</p><div class="research-chart" data-research="${key}"></div></section>`;
  return `<article class="research">
    <a class="card__research" href="#/attribution">← ${copy('Attribution','归因')}</a>
    <header class="headrow"><div><h1 class="page">${esc(label)}</h1><p class="lede">${copy('Factor contributions and sensitivity','因子贡献与敏感度')}</p></div>${controls}</header>
    <p class="stack-note">${esc(data.dates[0])} ${copy('to','至')} ${esc(data.dates[last])}, ${data.dates.length} ${copy('trading observations. Saved results for the selected model and training window.','个交易观测。以下展示所选模型与训练窗口的已保存结果。')}</p>
    <div class="research-facts"><span>${copy('Latest return','最新收益')} <b>${bp(data.y[last])} bp</b></span><span>${copy('Residual','残差')} <b>${bp(data.residual[last])} bp</b></span>${data.provisional[last] ? `<span class="tag">${copy('PROVISIONAL','待确认')}</span>` : ''}</div>
    ${panel('contribution',copy('Latest daily contributions','最新单日贡献'),copy('Individual factor contributions retain their signs. Residual is shown separately.','各因子保留贡献的正负号，残差单独列出。'))}
    ${comparisonHtml(comparison, data.pair)}
    <section class="research-panel"><div class="between"><h2 class="sec">${copy('Sensitivity through time','敏感度如何变化')}</h2><label class="research-select">${copy('Factor','因子')} <select id="beta-factor">${data.factors.map(f=>`<option value="${esc(f)}">${esc(f)} (${unit(f)})</option>`).join('')}</select></label></div>
    <p class="stack-note">${copy('Original coefficient β: log FX return per one factor unit. Units differ across factors, so coefficient sizes cannot be compared directly. Each estimate uses a window ending on the preceding observation.','原量纲系数 β：因子每变动一个单位所对应的汇率对数收益。各因子单位不同，系数大小不能直接横向比较。每次估计的窗口截至前一个观测。')}</p><div class="research-chart" data-research="beta"></div></section>
    ${panel('fit',copy('Fit inside the training window','训练窗口内的拟合'),copy('Full model and a separate refit excluding dollar and carry. These are in-sample R² values.','全模型与剔除美元、套息后单独重拟合的 R²。两个指标均为样本内拟合程度。'))}
    ${panel('residual',copy('Residual history','残差历史'),copy('Daily residual z scores use the preceding 126 observations. Reference lines at ±2 are only one part of the narrative trigger.','每日残差 z 分数使用此前 126 个观测缩放。±2 参考线只是新闻触发条件的一部分。'))}
    ${data.selected ? panel('selection',copy('Lasso selection history','Lasso 因子选择历史'),copy('A coloured cell indicates selection before the OLS refit. Empty columns indicate abstention.','有色格表示 OLS 重拟合前选中了该因子，整列空白表示该日选择集为空。')) : `<p class="stack-note">${copy('Choose Lasso above to inspect factor selection history.','切换到 Lasso 可查看因子选择历史。')}</p>`}
  </article>`;
}

function comparisonHtml(report, pair) {
  const row = report?.pairs?.find(r => r.pair === pair);
  const title = copy('Compare saved models','已保存模型比较');
  if (!row?.available) return `<section class="research-panel"><h2 class="sec">${title}</h2><p class="stack-note">${copy('Comparison unavailable. All three models and matched final observations are required.','比较暂不可用，需要三种模型及其共有的已确认观测。')}</p></section>`;
  const number = v => v == null ? 'n/a' : v.toFixed(2);
  const sample = (s, name) => {
    const maximum = Math.max(s.zero_mae_bp, ...s.models.map(m=>m.mae_bp), 1);
    return `<div class="comparison-sample"><h3>${name}</h3><p class="hint">${esc(s.start)} ${copy('to','至')} ${esc(s.end)} · n = ${s.observations}</p>
      <div class="comparison-table"><div class="comparison-row comparison-head"><span>${copy('Model','模型')}</span><span>MAE bp</span><span>RMSE bp</span><span>${copy('Factor Δ vs OLS','因子分配差异')}</span></div>${s.models.map(m=>`<div class="comparison-row"><span>${esc(m.model === 'lasso' ? 'post-Lasso' : m.model.toUpperCase())}</span><span>${number(m.mae_bp)}<i class="comparison-bar" style="width:${m.mae_bp / maximum * 100}%"></i></span><span>${number(m.rmse_bp)}</span><span>${number(m.allocation_l1_vs_ols_bp)}</span></div>`).join('')}</div>
      <p class="hint">${copy('Zero-contribution reference','零贡献参照')} MAE ${number(s.zero_mae_bp)} bp · RMSE ${number(s.zero_rmse_bp)} bp</p>
      ${s.models.filter(m=>m.selection).map(m=>`<p class="hint">Lasso ${copy('selection changes between retained observations','保留观测之间的选集变动率')}: ${number(m.selection.switch_fraction == null ? null : m.selection.switch_fraction*100)}% · ${copy('empty selections','空集比例')} ${number(m.selection.empty_fraction*100)}%</p>`).join('')}
    </div>`;
  };
  return `<section class="research-panel"><h2 class="sec">${title}</h2>
    <p class="stack-note">${copy('Same final dates, same training window. MAE and RMSE measure residual size using realised factors. Lower values mean closer reconstruction on this sample. Lasso also has a wider factor menu.','使用相同的已确认日期和训练窗口。MAE、RMSE 衡量使用当日实际因子后的残差大小，数值较低表示在这组样本中还原波动更接近。Lasso 的候选因子范围也更宽。')}</p>
    ${sample(row.samples.recent,copy('Latest 252 matched observations','最近 252 个共有观测'))}
    <details><summary>${copy('Full matched history and definitions','完整共有历史与定义')}</summary>${sample(row.samples.all,copy('Full matched history','完整共有历史'))}
      <p class="stack-note">${copy('Allocation difference is the daily sum of absolute factor-contribution differences from OLS, averaged across dates. Selection changes include gaps between retained dates. The zero reference sets every contribution to zero. This is contemporaneous attribution with historical data, so it does not establish forecast performance or causality.','因子分配差异为每日各因子贡献相对 OLS 的绝对差之和，再对日期取平均。选集变动率包含保留日期之间的间隔。零贡献参照把所有贡献设为零。本页使用历史数据评估同期归因，预测能力与因果关系需要独立检验。')}</p>
    </details></section>`;
}

export function researchOptions(data, factor) {
  const C = tokens(), last = data.dates.length - 1;
  const base = {
    animation:false,
    textStyle:{color:C.text,fontFamily:C.mono},
    grid:{left:64,right:24,top:20,bottom:68,containLabel:false},
    tooltip:{trigger:'axis',backgroundColor:C.raise,borderColor:C.line,textStyle:{color:C.text}},
    xAxis:{type:'category',data:data.dates,boundaryGap:false,axisLabel:{color:C.mute,formatter:s=>s.slice(2)},axisLine:{lineStyle:{color:C.line}}},
    yAxis:{type:'value',scale:true,axisLabel:{color:C.mute},splitLine:{lineStyle:{color:C.grid}}},
    dataZoom:[{type:'slider',height:18,bottom:8,borderColor:C.line,textStyle:{color:C.mute}}],
  };
  const line = (name,values,color) => ({name,type:'line',data:values,showSymbol:false,connectNulls:false,lineStyle:{color,width:1.8},itemStyle:{color}});
  const signed = data.factors.map(f=>({name:f,value:data.contributions[f][last]}));
  signed.push({name:copy('Residual','残差'),value:data.residual[last]});
  const options = {
    contribution:{...base,dataZoom:[],grid:{left:132,right:30,top:16,bottom:30},
      tooltip:{...base.tooltip,trigger:'item',formatter:p=>`${esc(p.name)}: ${p.value == null ? 'n/a' : Number(p.value).toFixed(1)} bp`},
      xAxis:{...base.yAxis,type:'value',name:'bp',scale:false},
      yAxis:{type:'category',data:signed.map(s=>s.name),inverse:true,axisLabel:{color:C.mute,fontSize:11},axisTick:{show:false},axisLine:{show:false}},
      series:[{type:'bar',barMaxWidth:22,data:signed.map((s,i)=>({name:s.name,value:s.value==null?null:s.value*1e4,itemStyle:{color:i===signed.length-1?C.res:data.factor_groups.systematic.includes(s.name)?C.sys:C.exo}}))}]},
    beta:{...base,series:[line(factor,data.betas[factor],C.accent)]},
    fit:{...base,legend:{top:0,textStyle:{color:C.mute}},grid:{...base.grid,top:40},series:[line(copy('Full model','全模型'),data.r2_full,C.sys),line(copy('External variables only','仅外部变量'),data.r2_exog,C.exo)]},
    residual:{...base,series:[{...line('z',data.residual_z,C.res),markLine:{silent:true,symbol:'none',lineStyle:{color:C.mute,type:'dashed'},data:[{yAxis:2},{yAxis:-2}]}}]},
  };
  if(data.selected){
    const cells=data.factors.flatMap((f,i)=>data.selected[f].map((v,j)=>[j,i,v]));
    options.selection={...base,grid:{left:132,right:30,top:20,bottom:76},
      xAxis:{...base.xAxis,boundaryGap:true},
      yAxis:{type:'category',data:data.factors,inverse:true,axisLabel:{color:C.mute,fontSize:11}},
      visualMap:{show:false,min:0,max:1,inRange:{color:[C.panel,C.accent]}},
      tooltip:{...base.tooltip,trigger:'item',formatter:p=>`${esc(data.dates[p.value[0]])}<br>${esc(data.factors[p.value[1]])}: ${p.value[2] ? copy('selected','选中') : copy('not selected','未选中')}`},
      series:[{type:'heatmap',data:cells,progressive:0}]};
  }
  return options;
}
