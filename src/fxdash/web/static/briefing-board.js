import {getLang} from './i18n.js';
import {briefingHtml} from './context.js';
const copy = (en,zh) => getLang()==='zh' ? zh : en;
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Uses the reader's current clock, never an age frozen at build time. This is a
// timetable comparison, not evidence that the Windows scheduler is running.
export function dueEdition(now=new Date()) {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{
    timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',hourCycle:'h23',
  }).formatToParts(now).map(p=>[p.type,p.value]));
  const day=new Date(`${parts.year}-${parts.month}-${parts.day}T12:00:00Z`);
  if (Number(parts.hour)<9) day.setUTCDate(day.getUTCDate()-1);
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
    no_edition:copy('No formal edition is available in this snapshot.','当前快照尚无正式晨报。'),
    ahead_of_due_date:copy('The edition date is ahead of the timetable. Check the reader and host clocks.','稿件日期早于计划时点，请检查浏览器与主机时钟。'),
    older_edition:copy('The displayed edition is older than the latest scheduled date.','当前显示的晨报早于最近应出刊日期。'),
    current_edition:copy('An edition record is available for the latest scheduled date.','最近应出刊日期已有稿件记录。'),
    current_catchup:copy('A catch-up briefing is available for this date. The 09:00 timing requirement remains unmet.','本日已有补发简报，09:00 按时出刊要求仍未满足。'),
    archive_unreadable:copy('The latest edition file could not be read. Earlier editions remain in the archive.','最新晨报文件无法读取，较早稿件仍可从历史记录查看。'),
  }[state];
  return `<p class="brief-freshness hint" data-freshness="${state}">${wording} ${copy('Latest scheduled date','最近应出刊日期')} ${esc(due)} · 09:00 America/New_York</p>`;
}

function statusHtml(current, archive, build, now) {
  const run=archive?.latest_run;
  const cell=(title,state,stamp)=>`<div><dt>${title}</dt><dd>${esc(label(state))}</dd>${stamp ? `<small>${esc(stamp)}</small>` : ''}</div>`;
  return `${freshnessHtml(current,now)}<details class="brief-run-details"><summary>${copy('Run details','运行详情')} · ${esc(label(delivery(current,archive,build)))}</summary><dl class="brief-status-grid">
    ${cell(copy('Last preparation record','最近准备记录'),run?.prepare?.state || 'not_recorded',run?.prepare?.started_at)}
    ${cell(copy('Last edition record','最近稿件记录'),run?.edition?.state || 'not_recorded',run?.edition?.generated_at)}
    ${cell(copy('Latest available edition delivery','最新可用稿件交付情况'),delivery(current,archive,build),build.mode==='static' ? build.info?.built_at : (archive?.current_push?.finished_at || run?.push?.finished_at))}
    </dl><p class="hint">${copy('Run records observed','运行记录读取于')} ${esc(archive?.observed_at)}${run ? ` · ${copy('Run date','运行日期')} ${esc(run.date)}` : ''}</p>
    ${archive?.latest_catchup_run ? `<p class="hint">${copy('Catch-up check','补发检查')} ${esc(archive.latest_catchup_run.date)}: ${esc(label(archive.latest_catchup_run.state))} · ${esc(archive.latest_catchup_run.observed_at || '')}</p>` : ''}
    <p class="hint">${copy('08:50 capture, 09:00 edition, New York weekdays. After login, a separate task can issue a dated catch-up briefing. These are saved observations, not a scheduler heartbeat. A static page cannot see a later failed push; refresh to check for a newer build.','美东工作日 08:50 采集，09:00 出刊。登录后，独立任务可生成标注日期的补发简报。这里展示已保存记录，无法据此确认调度器仍在运行。静态页面看不到之后发生的推送失败，可刷新检查新构建。')}</p></details>`;
}

export function briefingBoardHtml(current, archive, build, now=new Date()) {
  if (!archive) return briefingHtml(current);
  const history=[...(archive.history || []),...(archive.catchup_history || [])].sort((a,b)=>b.date.localeCompare(a.date));
  const key=e=>e.mode==='catchup' ? 'catchup:'+e.date : e.date;
  return `<section class="brief-board col gap14"><h2 class="sec">${copy('Morning edition desk','晨报记录')}</h2>
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

let activeBoard;
export function bindBriefingBoard(root,current,archive,build) {
  const panel=root.querySelector('.brief-board');
  if (!panel) { activeBoard=null; return; }
  activeBoard={panel,current,archive,build};
  panel.querySelector('[data-brief-history]')?.addEventListener('change',event=>{
    const selected=event.target.value==='current' ? current : [...(archive.history || []),...(archive.catchup_history || [])]
      .find(e=>(e.mode==='catchup' ? 'catchup:'+e.date : e.date)===event.target.value);
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
