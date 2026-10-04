import {getLang} from './i18n.js';
const copy=(en,zh)=>getLang()==='zh'?zh:en;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={
  not_recorded:['No supervised attempt recorded','暂无监督运行记录'],
  running:['Calculation in progress','计算正在进行'], succeeded:['Last attempt succeeded','最近计算成功'],
  failed:['Last calculation failed','最近计算失败'], crashed:['Calculation process crashed','计算进程异常退出'],
  timed_out:['Calculation timed out','计算超时'], launch_failed:['Calculation could not start','计算未能启动'],
  completion_unconfirmed:['Completion unconfirmed','计算完成情况未确认'],
  interrupted:['Attempt did not finish','运行缺少结束记录'], unreadable:['Run record unreadable','运行记录不可读'],
};

function savedFreshness(runtime,now) {
  const age=(now.getTime()-Date.parse(runtime?.last_success_at))/3600000;
  return !Number.isFinite(age)?'unknown':age<0?'future':age>72?'stale':age>26?'delayed':'current';
}

export function runtimeState(data,now=new Date()) {
  if (!data?.runtime) return {state:'unreadable',tone:'red'};
  const runtime=data.runtime, attempt=runtime.latest_attempt || {};
  let state=labels[attempt.state]?attempt.state:'unreadable';
  if (state==='running' && attempt.deadline && now.getTime()>Date.parse(attempt.deadline)) state='interrupted';
  const freshness=savedFreshness(runtime,now);
  let tone=['unknown','stale','future'].includes(freshness)?'red':freshness==='delayed'?'yellow':'green';
  if (!['not_recorded','succeeded','running'].includes(state)) tone='red';
  else if (state==='running' && tone==='green') tone='yellow';
  if (data.last_success_state==='red' || data.heartbeat?.state==='red') tone='red';
  if ((data.last_success_state==='yellow' || data.heartbeat?.state==='yellow') && tone==='green') tone='yellow';
  return {state,tone};
}

export function runtimeHtml(data,build={},now=new Date()) {
  const {state,tone}=runtimeState(data,now), runtime=data?.runtime || {}, attempt=runtime.latest_attempt || {};
  const freshness=savedFreshness(runtime,now);
  const freshnessLabels={
    future:['Saved calculation time is in the future','已保存计算时间晚于当前时间'],
    unknown:['Last successful calculation time is unknown','最近计算成功时间未知'],
    delayed:['Saved calculation is over 26 hours old','已保存计算结果超过 26 小时'],
    stale:['Saved calculation is over 72 hours old','已保存计算结果超过 72 小时'],
  };
  const statusNeedsReview=data?.heartbeat?.state==='red' || data?.last_success_state==='red';
  const summaryLabel=['succeeded','not_recorded'].includes(state)
    ?freshnessLabels[freshness] || (statusNeedsReview?['Run status needs review','运行状态需要检查']:labels[state])
    :labels[state];
  const label=copy(...summaryLabel);
  const stamp=s=>s?esc(String(s).replace('T',' ').replace(/\.\d+(?=[+-]|Z)/,'')):copy('Not recorded','未记录');
  return `<details class="runtime-details" data-runtime-tone="${tone}" data-runtime-state="${state}" data-runtime-freshness="${freshness}">
    <summary><i></i><span>${esc(label)}</span><span class="runtime-date">${copy('Attribution through','归因截至')} ${esc(runtime.attribution_as_of || 'n/a')}</span></summary>
    <dl><div><dt>${copy('Last successful calculation','最近计算成功')}</dt><dd>${stamp(runtime.last_success_at)}</dd></div>
      <div><dt>${copy('Latest attempt','最近一次尝试')}</dt><dd>${esc(copy(...labels[state]))} · ${stamp(attempt.started_at)}${attempt.exit_hex?` · ${esc(attempt.exit_hex)}`:''}</dd></div>
      <div><dt>${copy('Provisional contract rows','合同内待确认行')}</dt><dd>${esc(runtime.provisional_rows ?? 'n/a')}</dd></div>
      <div><dt>${copy('Run state observed','运行状态读取于')}</dt><dd>${stamp(runtime.observed_at)}</dd></div></dl>
    <p>${copy('Saved data remain readable after a failed attempt. A recent page build does not mean the calculation succeeded.','计算失败后仍可阅读已保存数据。网页刚刚构建，不代表计算已经成功。')}
      ${build.mode==='static'?copy('This is the state observed at build time; later runs require a new build.','这里展示构建时读取的状态，后续运行需要新构建才能反映。'):copy('Local state is refreshed every five minutes.','本地状态每五分钟检查一次。')}</p>
  </details>`;
}
