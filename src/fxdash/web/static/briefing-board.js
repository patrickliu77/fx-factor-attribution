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
  if (current?.mode!=='edition') return {state:'no_edition',due};
  if (current.date>due) return {state:'ahead_of_due_date',due};
  if (current.date<due) return {state:'older_edition',due};
  return {state:current.state==='archive_unreadable' ? 'archive_unreadable' : 'current_edition',due};
}

export function delivery(current, archive, build={}) {
  if (current?.mode!=='edition') return 'preview';
  const stamp=build.info?.briefing;
  if (build.mode==='static') {
    return stamp?.date===current.date && stamp?.edition_hash===current.edition_hash
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
  ineligible_packet:copy('Input checks failed','输入检查未通过'),
  attempt_recorded:copy('Attempt recorded; completion unconfirmed','已记录尝试，完成情况未确认'),
  not_recorded:copy('No record','暂无记录'), published:copy('Push receipt confirmed','推送回执已核对'),
  publishing:copy('Push attempt recorded','已记录推送尝试'), publish_failed:copy('Push failed','推送失败'),
  finalize_failed:copy('Edition could not be read or finalized','稿件读取或生成失败'),
  receipt_mismatch:copy('Receipt version mismatch','回执版本不匹配'),
  included_in_build:copy('Included in this build','已包含在当前构建'),
  not_confirmed_in_build:copy('Not confirmed in this build','当前构建未确认包含'),
  preview:copy('Validation preview','验收预览'),
}[state] || copy('Unrecognized state','未识别的状态'));

function freshnessHtml(current, now) {
  const {state,due}=freshness(current,now);
  const wording={
    no_edition:copy('No formal edition is available in this snapshot.','当前快照尚无正式晨报。'),
    ahead_of_due_date:copy('The edition date is ahead of the timetable. Check the reader and host clocks.','稿件日期早于计划时点，请检查浏览器与主机时钟。'),
    older_edition:copy('The displayed edition is older than the latest scheduled date.','当前显示的晨报早于最近应出刊日期。'),
    current_edition:copy('An edition record is available for the latest scheduled date.','最近应出刊日期已有稿件记录。'),
    archive_unreadable:copy('The latest edition file could not be read. Earlier editions remain in the archive.','最新晨报文件无法读取，较早稿件仍可从历史记录查看。'),
  }[state];
  return `<p class="brief-freshness hint" data-freshness="${state}">${wording} ${copy('Latest scheduled date','最近应出刊日期')} ${esc(due)} · 09:00 America/New_York</p>`;
}

function statusHtml(current, archive, build, now) {
  const run=archive?.latest_run;
  const cell=(title,state,stamp)=>`<div><dt>${title}</dt><dd>${esc(label(state))}</dd>${stamp ? `<small>${esc(stamp)}</small>` : ''}</div>`;
  return `${freshnessHtml(current,now)}<dl class="brief-status-grid">
    ${cell(copy('Last preparation record','最近准备记录'),run?.prepare?.state || 'not_recorded',run?.prepare?.started_at)}
    ${cell(copy('Last edition record','最近稿件记录'),run?.edition?.state || 'not_recorded',run?.edition?.generated_at)}
    ${cell(copy('Latest available edition delivery','最新可用稿件交付情况'),delivery(current,archive,build),build.mode==='static' ? build.info?.built_at : (archive?.current_push?.finished_at || run?.push?.finished_at))}
    </dl><p class="hint">${copy('Run records observed','运行记录读取于')} ${esc(archive?.observed_at)}${run ? ` · ${copy('Run date','运行日期')} ${esc(run.date)}` : ''}</p>
    <p class="hint">${copy('08:50 capture, 09:00 edition, New York weekdays. These are saved observations, not a scheduler heartbeat. A static page cannot see a later failed push; refresh to check for a newer build.','美东工作日 08:50 采集，09:00 出刊。这里展示已保存记录，无法据此确认调度器仍在运行。静态页面看不到之后发生的推送失败，可刷新检查新构建。')}</p>`;
}

export function briefingBoardHtml(current, archive, build, now=new Date()) {
  if (!archive) return briefingHtml(current);
  const history=archive.history || [];
  return `<section class="brief-board col gap14"><h2 class="sec">${copy('Morning edition desk','晨报记录')}</h2>
    <div data-brief-status>${statusHtml(current,archive,build,now)}</div>
    <label class="brief-history-label">${copy('Read an edition','选择稿件')}
      <select data-brief-history aria-label="${copy('Morning edition archive','晨报历史记录')}">
        <option value="current">${copy('Latest available','当前可用')} ${esc(current?.date || '')}${current?.mode!=='edition' ? ' · '+label('preview') : ''}</option>
        ${history.filter(e=>e.date!==current?.date).map(e=>`<option value="${esc(e.date)}">${esc(e.date)} · ${esc(label(e.state))}</option>`).join('')}
      </select></label>
    <p class="hint">${history.length ? copy(`Showing the latest ${history.length} of ${archive.total_editions} archived editions. Missing days are not filled in.`,`显示 ${archive.total_editions} 期档案中最近 ${history.length} 期，缺失日期不补写。`) : copy('No formal editions have been archived. Validation previews stay separate.','暂无正式晨报档案，验收预览单独保留。')}</p>
    <div data-brief-content>${briefingHtml(current) || `<p class="hint">${copy('No readable text is available.','暂无可读稿件。')}</p>`}</div>
  </section>`;
}

let activeBoard;
export function bindBriefingBoard(root,current,archive,build) {
  const panel=root.querySelector('.brief-board');
  if (!panel) { activeBoard=null; return; }
  activeBoard={panel,current,archive,build};
  panel.querySelector('[data-brief-history]').addEventListener('change',event=>{
    const selected=event.target.value==='current' ? current : archive.history.find(e=>e.date===event.target.value);
    panel.querySelector('[data-brief-content]').innerHTML=briefingHtml(selected);
    // Status always describes the latest available edition, independently of
    // which historical text the reader selected.
  });
}

export function refreshBriefingStatus(now=new Date()) {
  if (!activeBoard?.panel.isConnected) { activeBoard=null; return; }
  const {panel,current,archive,build}=activeBoard;
  panel.querySelector('[data-brief-status]').innerHTML=statusHtml(current,archive,build,now);
}
