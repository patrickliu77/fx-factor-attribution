import {getLang} from './i18n.js';
import {briefingHtml} from './context.js';
const copy = (en,zh) => getLang()==='zh' ? zh : en;
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Compare calendar dates, never infer that an unavailable host failed to run.
// The optional 09:00 timetable is not the current product acceptance policy.
export function dueEdition(now=new Date()) {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{
    timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',hourCycle:'h23',
  }).formatToParts(now).map(p=>[p.type,p.value]));
  const day=new Date(`${parts.year}-${parts.month}-${parts.day}T12:00:00Z`);
  while ([0,6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate()-1);
  return day.toISOString().slice(0,10);
}

export function freshness(current, now=new Date()) {
  const due=dueEdition(now);
  if (!['edition','catchup'].includes(current?.mode)) return {state:'no_edition',due};
  if (current.date>due) return {state:'ahead_of_due_date',due};
  if (current.date<due) return {state:'older_edition',due};
  return {state:current.state==='archive_unreadable' ? 'archive_unreadable' : current.mode==='catchup' ? 'current_catchup' : 'current_edition',due};
}

export function delivery(current, archive, build={}) {
  if (!['edition','catchup'].includes(current?.mode)) return 'preview';
  const stamp=build.info?.briefing;
  if (build.mode==='static') {
    return stamp?.date===current.date && (stamp?.mode || 'edition')===current.mode && stamp?.edition_hash===current.edition_hash
      && !!current.edition_hash ? 'included_in_build' : 'not_confirmed_in_build';
  }
  const run=archive?.latest_run;
  if (archive?.current_push) return archive.current_push.state;
  if (run?.date!==current.date) return 'not_recorded';
  return run.push?.state || 'not_recorded';
}

const label = state => ({
  ready:copy('Event context included','含事件背景'), numbers_only:copy('Numeric summary','数字摘要'),
  inputs_unavailable:copy('Inputs unavailable','输入不可用'), archive_unreadable:copy('Archive unreadable','档案不可读'),
  prepared:copy('Inputs saved','已保存输入'), prepare_failed:copy('Preparation failed','准备失败'),
  preparing:copy('Preparing catch-up briefing','正在准备补发简报'),
  ineligible_packet:copy('Input checks failed','输入检查未通过'),
  attempt_recorded:copy('Attempt recorded; completion unconfirmed','已记录尝试，完成情况未确认'),
  not_recorded:copy('No record','暂无记录'), published:copy('Push receipt confirmed','推送回执已核对'),
  publishing:copy('Push attempt recorded','已记录推送尝试'), publish_failed:copy('Push failed','推送失败'),
  finalize_failed:copy('Edition could not be read or finalized','稿件读取或生成失败'),
  receipt_mismatch:copy('Receipt version mismatch','回执版本不匹配'),
  included_in_build:copy('Included in this build','已包含在当前构建'),
  not_confirmed_in_build:copy('Not confirmed in this build','当前构建未确认包含'),
  preview:copy('Validation preview','验收预览'),
  catchup:copy('Catch-up briefing','补发简报'),
  already_available:copy('Saved briefing available','已有可用简报'),
  waiting_for_attribution:copy('Waiting for updated attribution','等待归因数据更新'),
  waiting_for_news:copy('Waiting for news sources','等待新闻源恢复'),
  waiting_for_morning:copy('Waiting for the saved morning packet to publish','等待已保存晨间输入发布'),
  catchup_failed:copy('Catch-up attempt failed; retry pending','补发尝试失败，等待重试'),
}[state] || copy('Unrecognized state','未识别的状态'));

function freshnessHtml(current, now) {
  const {state,due}=freshness(current,now);
  const wording={
    no_edition:copy('No saved briefing is available in this snapshot. The host can prepare one when inputs are available.','当前快照尚无已保存简报，主机可在输入齐备后生成。'),
    ahead_of_due_date:copy('The edition has a future date. Check the reader and host clocks.','稿件日期晚于当前日期，请检查浏览器与主机时钟。'),
    older_edition:copy('This is an earlier briefing. A new edition can be prepared when the host is available; absence alone does not establish a failed run.','当前显示较早的简报。主机可用后可生成新稿，缺少本日稿件本身无法证明运行失败。'),
    current_edition:copy('A saved briefing is available for this date.','本日已有保存的简报。'),
    current_catchup:copy('A catch-up briefing is available for this date. Delivery is checked when the host is available, without a fixed publication deadline.','本日已有补发简报，按主机可用时的实际交付检查，不设固定出刊时刻。'),
    archive_unreadable:copy('The latest edition file could not be read. Earlier editions remain in the archive.','最新晨报文件无法读取，较早稿件仍可从历史记录查看。'),
  }[state];
  return `<p class="brief-freshness hint" data-freshness="${state}">${wording} ${copy('Reference weekday','日期参考')} ${esc(due)} · America/New_York</p>`;
}

function statusHtml(current, archive, build, now) {
  const run=archive?.latest_run;
  const late=current?.mode==='catchup';
  const attempt=archive?.latest_catchup_run;
  const cell=(title,state,stamp)=>`<div><dt>${title}</dt><dd>${esc(label(state))}</dd>${stamp ? `<small>${esc(stamp)}</small>` : ''}</div>`;
  return `${freshnessHtml(current,now)}<details class="brief-run-details"><summary>${copy('Run details','运行详情')} · ${esc(label(delivery(current,archive,build)))}</summary><dl class="brief-status-grid">
    ${cell(late ? copy('Latest catch-up check','最近补发检查') : copy('Last preparation record','最近准备记录'),late ? attempt?.state || 'not_recorded' : run?.prepare?.state || 'not_recorded',late ? attempt?.observed_at : run?.prepare?.started_at)}
    ${cell(copy('Latest available edition','最新可用稿件'),current?.state || 'not_recorded',current?.generated_at)}
    ${cell(copy('Latest available edition delivery','最新可用稿件交付情况'),delivery(current,archive,build),build.mode==='static' ? build.info?.built_at : (archive?.current_push?.finished_at || run?.push?.finished_at))}
    </dl><p class="hint">${copy('Run records observed','运行记录读取于')} ${esc(archive?.observed_at)}${run ? ` · ${copy('Run date','运行日期')} ${esc(run.date)}` : ''}</p>
    ${archive?.latest_catchup_run ? `<p class="hint">${copy('Catch-up check','补发检查')} ${esc(archive.latest_catchup_run.date)}: ${esc(label(archive.latest_catchup_run.state))} · ${esc(archive.latest_catchup_run.observed_at || '')}</p>` : ''}
    <p class="hint">${copy('After login, the catch-up task checks for usable inputs and a saved briefing. There is no fixed publication deadline or consecutive-day quota. The existing New York morning slot is optional. Saved observations cannot establish current scheduler health; a static page cannot see a later failed push.','登录后，补发任务会检查可用输入和已保存稿件。不设固定出刊时刻或连续天数门槛，原有纽约晨间时段保留为可选能力。已保存记录无法证明调度器当前状态，静态页面也看不到之后发生的推送失败。')}</p></details>`;
}

export function briefingBoardHtml(current, archive, build, now=new Date()) {
  if (!archive) return briefingHtml(current);
  const history=[...(archive.history || []),...(archive.catchup_history || [])].sort((a,b)=>b.date.localeCompare(a.date));
  const key=e=>e.mode==='catchup' ? 'catchup:'+e.date : e.date;
  return `<section class="brief-board col gap14"><h2 class="sec">${copy('Briefing archive','简报记录')}</h2>
    <div data-brief-status>${statusHtml(current,archive,build,now)}</div>
    ${current?.available || history.length ? `<label class="brief-history-label">${copy('Read an edition','选择稿件')}
      <select data-brief-history aria-label="${copy('Morning edition archive','晨报历史记录')}">
        <option value="current">${copy('Latest available','当前可用')} ${esc(current?.date || '')}${current?.mode==='catchup' ? ' · '+label('catchup') : current?.mode!=='edition' ? ' · '+label('preview') : ''}</option>
        ${history.filter(e=>key(e)!==key(current || {})).map(e=>`<option value="${esc(key(e))}">${esc(e.date)} · ${e.mode==='catchup' ? esc(label('catchup'))+' · ' : ''}${esc(label(e.state))}</option>`).join('')}
    </select></label>` : ''}
    ${history.length ? `<p class="hint">${copy(`${archive.total_editions || 0} morning editions and ${archive.total_catchups || 0} catch-up briefings archived. Showing up to 20 of each.`,`${archive.total_editions || 0} 期晨报、${archive.total_catchups || 0} 期补发简报，分别展示最近至多 20 期。`)}</p>` : ''}
    <div data-brief-content>${briefingHtml(current)}</div>
  </section>`;
}

import { bindAudio } from './briefing-audio.js';
let activeBoard;
export function bindBriefingBoard(root,current,archive,build) {
  const panel=root.querySelector('.brief-board');
  if (!panel) { activeBoard=null; return; }
  activeBoard={panel,current,archive,build};
  bindAudio(panel);
  panel.querySelector('[data-brief-history]')?.addEventListener('change',event=>{
    const selected=event.target.value==='current' ? current : [...(archive.history || []),...(archive.catchup_history || [])]
      .find(e=>(e.mode==='catchup' ? 'catchup:'+e.date : e.date)===event.target.value);
    panel.querySelectorAll('audio').forEach(player=>player.pause());
    panel.querySelector('[data-brief-content]').innerHTML=briefingHtml(selected);
    // Status always describes the latest available edition, independently of
    // which historical text the reader selected.
  });
}

export function refreshBriefingStatus(now=new Date()) {
  if (!activeBoard?.panel.isConnected) { activeBoard=null; return; }
  const {panel,current,archive,build}=activeBoard;
  const status=panel.querySelector('[data-brief-status]');
  const open=status.querySelector('details')?.open;
  status.innerHTML=statusHtml(current,archive,build,now);
  status.querySelector('details').open=!!open;
}
