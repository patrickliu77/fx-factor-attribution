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

export function runtimeState(data,now=new Date()) {
  if (!data?.runtime) return {state:'unreadable',tone:'red'};
  const runtime=data.runtime, attempt=runtime.latest_attempt || {};
  let state=labels[attempt.state]?attempt.state:'unreadable';
  if (state==='running' && attempt.deadline && now.getTime()>Date.parse(attempt.deadline)) state='interrupted';
  const age=(now.getTime()-Date.parse(runtime.last_success_at))/3600000;
  let tone=!Number.isFinite(age) || age>72?'red':age>26?'yellow':'green';
  if (!['not_recorded','succeeded','running'].includes(state)) tone='red';
  else if (state==='running' && tone==='green') tone='yellow';
  if (data.last_success_state==='red') tone='red';
  if (data.last_success_state==='yellow' && tone==='green') tone='yellow';
  return {state,tone};
}

export function runtimeHtml(data,build={},now=new Date()) {
  const {state,tone}=runtimeState(data,now), runtime=data?.runtime || {}, attempt=runtime.latest_attempt || {};
  const label=copy(...labels[state]);
  const stamp=s=>s?esc(String(s).replace('T',' ').replace(/\.\d+(?=[+-]|Z)/,'')):copy('Not recorded','未记录');
  return `<details class="runtime-details" data-runtime-tone="${tone}" data-runtime-state="${state}">
    <summary><i></i><span>${esc(label)}</span><span class="runtime-date">${copy('Attribution through','归因截至')} ${esc(runtime.attribution_as_of || 'n/a')}</span></summary>
    <dl><div><dt>${copy('Last successful calculation','最近计算成功')}</dt><dd>${stamp(runtime.last_success_at)}</dd></div>
      <div><dt>${copy('Latest attempt','最近一次尝试')}</dt><dd>${stamp(attempt.started_at)}${attempt.exit_hex?` · ${esc(attempt.exit_hex)}`:''}</dd></div>
      <div><dt>${copy('Provisional contract rows','合同内待确认行')}</dt><dd>${esc(runtime.provisional_rows ?? 'n/a')}</dd></div>
      <div><dt>${copy('Run state observed','运行状态读取于')}</dt><dd>${stamp(runtime.observed_at)}</dd></div></dl>
    <p>${copy('Saved data remain readable after a failed attempt. A recent page build does not mean the calculation succeeded.','计算失败后仍可阅读已保存数据。网页刚刚构建，不代表计算已经成功。')}
      ${build.mode==='static'?copy('This is the state observed at build time; later runs require a new build.','这里展示构建时读取的状态，后续运行需要新构建才能反映。'):copy('Local state is refreshed every five minutes.','本地状态每五分钟检查一次。')}</p>
  </details>`;
}
