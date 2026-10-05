import {getLang} from './i18n.js';

const copy = (en, zh) => getLang() === 'zh' ? zh : en;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const TASKS = ['fxdash-live', 'fxdash-narrative', 'fxdash-publish', 'fxdash-briefing', 'fxdash-catchup'];
const dayPattern = /^\d{4}-\d{2}-\d{2}$/;

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

export function operationsState(report, now = new Date()) {
  if (!report || report.schema_version !== 1 || !localClock(now)) return 'not_observed';
  const email = report.email;
  if (!email || typeof email.config_enabled !== 'boolean') return 'unconfirmed';
  if (!email.config_enabled) return 'disabled';
  if (email.configuration_valid === false) return 'attention';
  if (email.configuration_valid !== true) return 'unconfirmed';
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
  if (runtime.audio_backend === 'azure') {
    const speechKeys = ['AZURE_SPEECH_KEY', 'AZURE_SPEECH_REGION'].map(name =>
      report.runtime_observation.credentials?.[name]?.configured);
    if (speechKeys.some(value => value === false)) return 'attention';
    if (speechKeys.some(value => value !== true)) return 'unconfirmed';
  } else if (email.delivery_policy !== 'allow_text' && !['windows'].includes(runtime.audio_backend)) {
    return 'attention';
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

export function briefingOperationsHtml(report, now = new Date()) {
  const state = operationsState(report, now);
  const valid = report?.schema_version === 1;
  const email = valid ? report.email : null;
  const observation = valid ? report.runtime_observation : null;
  const currentObservation = runtimeObservationState(report, now) === 'current';
  const taskValues = TASKS.map(name => observation?.tasks?.[name]);
  const taskLabel = !currentObservation ? copy('No current check', '暂无当前检查')
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
      <div><dt>${copy('Local scheduled tasks', '本机定时任务')}</dt><dd>${esc(taskLabel)}</dd></div>
      <div><dt>${copy('Email credentials', '发信凭据')}</dt><dd>${esc(credentialLabel)}</dd></div>
      <div><dt>${copy('Audio policy', '语音策略')}</dt><dd>${esc(policy)}</dd></div>
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
