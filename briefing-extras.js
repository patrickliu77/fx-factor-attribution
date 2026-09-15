import {getLang} from './i18n.js';
const copy=(en,zh)=>getLang()==='zh'?zh:en;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sources={BLS:'https://www.bls.gov/schedule/news_release/bls.ics',BEA:'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics'};

export function calendarHtml(context) {
  if (!context || !context.date) return '';
  const fmt=new Intl.DateTimeFormat(getLang()==='zh'?'zh-CN':'en-US',{
    timeZone:'America/New_York',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const events=(context.events || []).filter(e=>sources[e.source]===e.source_url && Number.isFinite(Date.parse(e.scheduled_at)));
  const observed=Date.parse(context.observed_at);
  const row=e=>`<li><time datetime="${esc(e.scheduled_at)}">${esc(fmt.format(new Date(e.scheduled_at)))}</time><div><a href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a><span>${esc(e.source)} · ${Date.parse(e.scheduled_at)<=observed ? copy('Scheduled time passed; release not checked','预定时刻已过，未核验实际发布') : copy('Scheduled','预定发布')}</span></div></li>`;
  if (!events.length) return `<details class="brief-calendar brief-calendar-empty"><summary>${copy('Economic calendar','经济日历')}</summary>
    <div class="calendar-empty-body"><p>${copy('No usable release times in this snapshot.','这份快照暂无可用发布时间。')}</p>
      <p>${copy('Limited to US BLS and BEA schedules. Missing items do not mean there are no releases.','仅覆盖美国 BLS 与 BEA 日程，缺少条目不代表当天没有经济数据发布。')}</p>
      <p>${esc(context.date)}${context.state!=='available' ? ' / '+copy('Some sources unavailable','部分来源不可用') : ''}</p>
      <p>${copy('Observed','读取于')} ${esc(context.observed_at)}</p></div></details>`;
  return `<section class="brief-calendar"><h3>${copy('US release schedule','美国发布日程')} <small>${esc(context.date)}</small></h3>
    ${context.state!=='available' ? `<p class="calendar-coverage">${copy('Limited coverage','覆盖不全')}</p>` : ''}
    <ul>${events.slice(0,3).map(row).join('')}</ul>
    <details><summary>${copy('Coverage and remaining releases','覆盖范围与其余日程')}</summary>
    <p>${copy('New York time. US BLS and BEA schedules only; central-bank meetings and other countries are not covered. Scheduled dates can change. No actual values or consensus forecasts are included.','时间为纽约时间，仅覆盖美国 BLS 与 BEA，不包含央行会议及其他国家。预定时间可能调整，不含公布值和市场预期。')}</p>
    ${context.state!=='available' ? `<p>${copy('Some schedule sources are unavailable or incomplete.','部分日历来源不可用或覆盖不全。')}</p>` : ''}
    ${events.length>3 ? `<ul>${events.slice(3).map(row).join('')}</ul>` : ''}
    <p>${copy('Observed','读取于')} ${esc(context.observed_at)}</p></details></section>`;
}

function formURL(value) {
  try {const u=new URL(value);return u.protocol==='https:' && /^[a-z0-9-]+\.sibforms\.com$/.test(u.hostname)
    && /^\/serve\/[A-Za-z0-9_-]+={0,2}$/.test(u.pathname) && !u.username && !u.password && !u.search && !u.hash && !u.port ? u.href : null;
  } catch {return null;}
}

export function subscriptionHtml(settings) {
  const url=formURL(settings?.forms?.[getLang()]);
  if (settings?.enabled!==true || !url) return '';
  return `<details class="brief-subscribe"><summary class="subscribe-cta"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 5h18v14H3zM3 6l9 7 9-7"/></svg><span>${copy('Get the briefing by email','通过邮箱接收简报')}</span></summary>
    <div class="subscribe-panel"><h2>${copy('Your daily FX briefing','每天一份外汇简报')}</h2>
    <p>${copy('Text and an audio link, targeting 09:00 New York time on weekdays. Delivery may be later when the host is offline.','工作日发送文字与语音链接，目标为纽约时间 09:00，主机离线时可能延后。')}</p>
    <p>${copy('Brevo manages your email address and confirmation. Subscribe only after confirming by email; unsubscribe from any briefing.','邮箱由 Brevo 管理，点击确认邮件后订阅生效，每封简报均可退订。')}</p>
    <div class="subscribe-actions"><button type="button" class="btn" data-load-subscription="${esc(url)}">${copy('Open signup form','打开订阅表单')}</button>
    <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${copy('Open in a new tab','在新标签页打开')} ↗</a>
    </div><div data-subscription-frame></div></div></details>`;
}

let subscriptionEvents;
export function bindSubscription(root) {
  subscriptionEvents?.abort();
  const panel=root.querySelector('.brief-subscribe');
  if (!panel) return;
  subscriptionEvents=new AbortController();
  const {signal}=subscriptionEvents;
  const close=()=>{panel.open=false;panel.querySelector('summary').focus();};
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}},{signal});
  document.addEventListener('pointerdown',event=>{
    if (!panel.isConnected) {subscriptionEvents.abort();return;}
    if (panel.open && !panel.contains(event.target)) panel.open=false;
  },{signal});
  panel.querySelector('[data-load-subscription]')?.addEventListener('click',event=>{
    const button=event.currentTarget,url=formURL(button.dataset.loadSubscription);
    if (!url) return;
    const holder=button.closest('.brief-subscribe').querySelector('[data-subscription-frame]');
    if (!holder.firstChild) {
      const frame=document.createElement('iframe');
      frame.src=url;frame.title=copy('Email confirmation signup form','邮箱确认订阅表单');
      frame.referrerPolicy='no-referrer';
      frame.setAttribute('sandbox','allow-forms allow-scripts allow-same-origin');
      holder.append(frame);
    }
    button.hidden=true;
  },{signal});
}
