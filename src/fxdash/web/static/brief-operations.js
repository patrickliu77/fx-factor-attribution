import {getLang} from './i18n.js';

const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const TASKS = ['fxdash-live', 'fxdash-briefing', 'fxdash-catchup'];
const EXECUTION_TASKS = ['fxdash-briefing', 'fxdash-catchup'];
const executionExit = {failed:1, attention_required:2, submitted_pending:0, pending:0,
  disabled:0, confirmed:0, idle:0, not_observed:2};
// Ready, running, not yet run, an event trigger and a queued invocation do not
// establish an execution failure. Terminated and unscheduled tasks need attention.
const schedulerInformation = new Set([0, 0x41300, 0x41301, 0x41303, 0x41308, 0x41325]);
const dayPattern = /^\d{4}-\d{2}-\d{2}$/;
const awareStamp = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  ? Date.parse(value) : NaN;
const exitCode = value => Number.isInteger(value) && value >= -0x80000000 && value <= 0xFFFFFFFF;

function localClock(now) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', hourCycle: 'h23', weekday: 'short',
  }).formatToParts(now).map(part => [part.type, part.value]));
  return {date: `${parts.year}-${parts.month}-${parts.day}`, hour: Number(parts.hour),
    weekend: ['Sat', 'Sun'].includes(parts.weekday)};
}

export function runtimeObservationState(report, now = new Date()) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) return 'unconfirmed';
  const observation = report?.runtime_observation;
  if (!observation || observation.state === 'not_observed') return 'not_observed';
  if (!['current', 'stale'].includes(observation.state)) return 'unconfirmed';
  const stamp = observation.observed_at;
  const observed = typeof stamp === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(stamp)
    ? Date.parse(stamp) : NaN;
  const age = (now.getTime() - observed) / 3600000;
  if (!Number.isFinite(age) || age < 0) return 'unconfirmed';
  return observation.state === 'stale' || age > 26 ? 'stale' : 'current';
}

function executionView(report, name, now) {
  const clock = localClock(now);
  const execution = report?.task_execution;
  if (!clock || execution?.date !== clock.date) return {state:'not_observed'};
  const row = execution.tasks?.[name];
  if (!row || row.state === 'not_observed' && row.freshness === 'missing') return {state:'not_observed'};
  const observed = awareStamp(row.observed_at);
  const current = Number.isFinite(observed) && observed <= now.getTime()
    && localClock(new Date(observed))?.date === clock.date && row.freshness === 'current';
  if (row.state === 'unreadable') return {state:'unreadable', current};
  if (!current || row.kind !== name.slice(7) || !exitCode(row.exit_code) || !exitCode(row.dispatch_exit_code)
      || !Object.hasOwn(executionExit, row.delivery_state)) return {state:'unconfirmed'};
  const expected = row.dispatch_exit_code ? 'dispatch_failed' : row.delivery_state;
  if (row.state !== expected || row.exit_code !== (row.dispatch_exit_code || executionExit[row.delivery_state]))
    return {state:'unconfirmed'};
  return {state:row.state, current:true, observed_at:observed, exit_code:row.exit_code};
}

function schedulerFailure(report, name, now) {
  if (runtimeObservationState(report, now) !== 'current') return false;
  const task = report.runtime_observation.tasks?.[name];
  const last = awareStamp(task?.last_run_at);
  if (task?.state === 'unreadable' || !exitCode(task?.last_result) || task.last_result < 0 || schedulerInformation.has(task.last_result)
      || !Number.isFinite(last) || last > now.getTime()
      || last > awareStamp(report.runtime_observation.observed_at)
      || localClock(new Date(last))?.date !== localClock(now)?.date) return false;
  const execution = executionView(report, name, now);
  // A later completed worker observation can supersede an earlier scheduler probe.
  return !(execution.current && execution.exit_code === 0
    && execution.observed_at > awareStamp(report.runtime_observation.observed_at));
}

export function operationsState(report, now = new Date()) {
  if (!report || report.schema_version !== 1 || !localClock(now)) return 'not_observed';
  const email = report.email;
  if (!email || typeof email.config_enabled !== 'boolean') return 'unconfirmed';
  if (!email.config_enabled) return 'disabled';
  if (email.configuration_valid === false) return 'attention';
  if (email.configuration_valid !== true) return 'unconfirmed';
  if (TASKS.some(name => {
    const execution = executionView(report, name, now);
    return execution.current && (execution.state === 'unreadable' || execution.exit_code !== 0)
      || schedulerFailure(report, name, now);
  })) return 'attention';
  const observation = runtimeObservationState(report, now);
  if (observation !== 'current') return observation;
  const runtime = report.runtime_observation.runtime;
  if (runtime?.state !== 'ready' || runtime.persistent !== true || runtime.dependencies_ready !== true)
    return 'attention';
  if (email.credential_configured === false) return 'attention';
  if (email.credential_configured !== true) return 'unconfirmed';
  const prerequisites = [report.runtime_observation.tools?.git?.available,
    report.runtime_observation.tools?.ffprobe?.available,
    report.runtime_observation.credentials?.BANXICO_TOKEN?.configured];
  if (prerequisites.some(value => value === false)) return 'attention';
  if (prerequisites.some(value => value !== true)) return 'unconfirmed';
  if (email.delivery_policy !== 'allow_text') {
    if (runtime.audio_backend === 'azure') {
      const speechKeys = ['AZURE_SPEECH_KEY', 'AZURE_SPEECH_REGION'].map(name =>
        report.runtime_observation.credentials?.[name]?.configured);
      if (speechKeys.some(value => value === false)) return 'attention';
      if (speechKeys.some(value => value !== true)) return 'unconfirmed';
    } else if (runtime.audio_backend !== 'windows') return 'attention';
  }
  const tasks = report.runtime_observation.tasks;
  if (!tasks || TASKS.some(name => tasks[name]?.registered !== true || tasks[name]?.enabled !== true))
    return 'attention';
  const clock = localClock(now);
  if (clock.weekend) return 'weekend';
  if (clock.hour < 9) return 'before_window';
  const today = email.today;
  if (!today || today.date !== clock.date) return 'waiting';
  const states = ['en', 'zh'].map(lang => today.languages?.[lang]?.state);
  if (states.some(state => ['review_required', 'unreadable', 'unconfirmed', 'integrity_failed'].includes(state)))
    return 'attention';
  return states.every(state => ['submitted', 'provider_confirmed'].includes(state)) ? 'submitted' : 'waiting';
}

const stateLabel = state => ({
  not_observed: copy('Runtime not checked', '运行环境尚未检查'),
  unconfirmed: copy('Status cannot be confirmed', '状态未能确认'),
  stale: copy('Runtime check is out of date', '运行环境检查已过期'),
  attention: copy('Delivery needs attention', '投递需要检查'),
  disabled: copy('Email is disabled', '邮件未启用'),
  weekend: copy('No scheduled weekend email', '周末无需定时发送'),
  before_window: copy('Before the delivery window', '尚未到发送时段'),
  submitted: copy('Today’s emails submitted', '今日邮件已提交'),
  waiting: copy('Today’s delivery is pending', '等待今日投递'),
}[state] || copy('Status unavailable', '状态不可用'));

const receiptLabel = state => ({
  submitted: copy('Submitted to the email service', '已提交邮件服务商'),
  provider_confirmed: copy('Service reports delivery', '服务商报告已送达'),
  review_required: copy('Manual review required', '需要人工核查'),
  creating: copy('Creation attempt recorded', '已记录创建尝试'),
  submitting: copy('Submission attempt recorded', '已记录提交尝试'),
  not_recorded: copy('No record', '暂无记录'),
  unreadable: copy('Receipt unreadable', '回执不可读'),
  unconfirmed: copy('Receipt cannot be confirmed', '回执未能确认'),
}[state] || copy('Status unavailable', '状态不可用'));

function receiptHtml(row, lang) {
  const receipt = row?.languages?.[lang];
  const mode = receipt?.content_mode;
  const content = mode === 'text_only' ? copy('Text only', '文字版')
    : mode === 'audio' ? copy('Text and audio link', '文字与语音链接') : '';
  const confirmation = receipt?.confirmation?.state === 'provider_confirmed'
    ? copy('Service reports delivery; the send claim is retained', '服务商报告送达，原发送记录保留')
    : receipt?.confirmation?.state === 'provider_sent'
      ? copy('Service reports sent; delivery is not confirmed', '服务商报告已发送，送达尚未确认') : '';
  return `<div><dt>${lang === 'en' ? copy('English email', '英文邮件') : copy('Chinese email', '中文邮件')}</dt>
    <dd>${esc(receiptLabel(receipt?.state || 'not_recorded'))}</dd>
    ${content ? `<small>${esc(content)}</small>` : ''}
    ${confirmation ? `<small>${esc(confirmation)}</small>` : ''}</div>`;
}

function executionHtml(report, name, now) {
  const execution = executionView(report, name, now);
  const label = schedulerFailure(report, name, now) ? copy('Scheduler reported an error today', '定时任务今日报告异常')
    : ({
      dispatch_failed: copy('Task execution failed', '任务执行失败'),
      failed: copy('Delivery check failed', '投递检查失败'),
      attention_required: copy('Task needs review', '任务需要核查'),
      submitted_pending: copy('Submission recorded; delivery pending', '已记录提交，送达待确认'),
      pending: copy('Task completed; delivery pending', '任务已结束，投递待完成'),
      disabled: copy('Task completed; email disabled', '任务已结束，邮件未启用'),
      confirmed: copy('Task completed; service reports delivery', '任务已结束，服务商报告送达'),
      idle: copy('Task checked; no delivery due', '任务已检查，无需投递'),
      not_observed: execution.current ? copy('Task outcome needs review', '任务结果需要核查')
        : copy('No completed task recorded today', '今日暂无任务完成记录'),
      unreadable: copy('Task result unreadable', '任务结果不可读'),
      unconfirmed: copy('Task result cannot be confirmed', '任务结果未能确认'),
    }[execution.state] || copy('Task result cannot be confirmed', '任务结果未能确认'));
  return `<div><dt>${name === 'fxdash-briefing' ? copy('Today’s morning task', '今日晨报任务')
    : copy('Today’s catch-up task', '今日补报任务')}</dt><dd>${esc(label)}</dd></div>`;
}

export function briefingOperationsHtml(report, now = new Date()) {
  const state = operationsState(report, now);
  const valid = report?.schema_version === 1;
  const email = valid ? report.email : null;
  const observation = valid ? report.runtime_observation : null;
  const currentObservation = runtimeObservationState(report, now) === 'current';
  const taskValues = TASKS.map(name => observation?.tasks?.[name]);
  const taskLabel = !currentObservation ? copy('No current check', '暂无当前检查')
    : TASKS.some(name => schedulerFailure(report, name, now)) ? copy('Task errors recorded today', '今日记录任务异常')
    : taskValues.every(task => task?.registered === true && task?.enabled === true)
      ? copy('Registered and enabled', '已注册并启用')
      : taskValues.some(task => task?.registered === false) ? copy('Tasks are missing', '任务未注册')
      : taskValues.some(task => task?.enabled === false) ? copy('Tasks are disabled', '任务未启用')
      : copy('Status unknown', '状态未知');
  const configured = email?.credential_configured;
  const credentialLabel = !currentObservation || configured == null ? copy('Not checked', '尚未检查')
    : configured ? copy('Configured; account not verified', '已配置，账户尚未核验')
    : copy('Email credentials are missing', '发信凭据缺失');
  const history = Array.isArray(email?.history) ? email.history : [];
  const latest = history.filter(row => dayPattern.test(row?.date || '')).sort((a, b) => b.date.localeCompare(a.date))[0];
  const today = email?.today;
  const row = today?.date === localClock(now)?.date && ['en', 'zh'].some(lang =>
    typeof today.languages?.[lang]?.state === 'string' && today.languages[lang].state !== 'not_recorded')
    ? today : latest;
  const observed = typeof observation?.observed_at === 'string' && Number.isFinite(Date.parse(observation.observed_at))
    ? observation.observed_at : null;
  const policy = email?.delivery_policy === 'allow_text' ? copy('Verified text can be sent without audio', '可发送已验证文字版')
    : email?.delivery_policy === 'require_audio' ? copy('Verified bilingual audio required', '要求双语音频验证通过')
    : copy('Unknown', '未知');
  return `<details class="brief-operations" data-operations-state="${state}">
    <summary><span>${copy('Briefing delivery', '简报投递状态')}</span><small>${esc(stateLabel(state))}</small></summary>
    <dl class="brief-status-grid">
      <div><dt>${copy('Email setting', '邮件设置')}</dt><dd>${email?.config_enabled === true ? copy('Enabled', '已开启')
        : email?.config_enabled === false ? copy('Disabled', '已关闭') : copy('Unknown', '未知')}</dd></div>
      <div><dt>${copy('Daily delivery tasks', '每日投递任务')}</dt><dd>${esc(taskLabel)}</dd></div>
      <div><dt>${copy('Email credentials', '发信凭据')}</dt><dd>${esc(credentialLabel)}</dd></div>
      <div><dt>${copy('Audio policy', '语音策略')}</dt><dd>${esc(policy)}</dd></div>
      ${EXECUTION_TASKS.map(name => executionHtml(valid ? report : null, name, now)).join('')}
      ${receiptHtml(row, 'en')}${receiptHtml(row, 'zh')}
    </dl>
    ${row ? `<p class="hint">${copy('Receipt date', '回执日期')} ${esc(row.date)}</p>` : ''}
    <p class="hint">${copy('Submitted means the email service accepted the request. Service-reported delivery does not prove inbox arrival.',
      '已提交表示邮件服务商接受了请求；服务商报告送达仍不证明进入收件箱。')}</p>
    <p class="hint">${observed ? `${copy('Runtime checked', '运行环境检查于')} ${esc(observed)}. ` : ''}${copy(
      'This view uses saved observations. A static build cannot observe a later delivery failure.',
      '此处使用保存的检查结果，静态页面无法观察之后发生的投递失败。')}</p>
  </details>`;
}

let activeOperations;
export function bindBriefingOperations(root, report) {
  const panel = root.querySelector('.brief-operations');
  activeOperations = panel ? {panel, report} : null;
}

export function refreshBriefingOperations(now = new Date()) {
  if (!activeOperations?.panel.isConnected) { activeOperations = null; return; }
  const {panel, report} = activeOperations;
  const open = panel.open;
  const focused = panel.contains(document.activeElement);
  const holder = document.createElement('div');
  holder.innerHTML = briefingOperationsHtml(report, now);
  const replacement = holder.firstElementChild;
  replacement.open = open;
  panel.replaceWith(replacement);
  if (focused) replacement.querySelector('summary').focus();
  activeOperations.panel = replacement;
}
