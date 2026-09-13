import {getLang} from './i18n.js';
const copy=(en,zh)=>getLang()==='zh'?zh:en;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mediaPath=/^media\/briefing\/(edition|catchup)\/\d{4}-\d{2}-\d{2}\/[a-f0-9]{64}\/audio-v1\/(en|zh)\.mp3$/;
const recordingTime=stamp=>{
  const date=new Date(stamp);
  if (!Number.isFinite(date.getTime())) return copy('Time unavailable','时间不可用');
  return new Intl.DateTimeFormat(getLang()==='zh'?'zh-CN':'en-US',{
    timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'
  }).format(date);
};

export function audioHtml(brief) {
  if (!['edition','catchup'].includes(brief?.mode)) return '';
  const data=brief.audio, item=data?.languages?.[getLang()];
  if (!item || item.state!=='ready' || !mediaPath.test(item.url) || !Number.isFinite(item.duration_seconds)) {
    const failed=data?.state==='integrity_failed' || ['failed','integrity_failed'].includes(item?.state);
    return `<p class="hint brief-audio-empty">${failed ? copy('Audio is unavailable for this language. The saved text remains readable.','本语言音频暂不可用，已保存文字仍可阅读。') : copy('No audio is saved for this edition in this language.','本期尚无本语言的已保存音频。')}</p>`;
  }
  const seconds=Math.floor(item.duration_seconds), duration=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
  return `<section class="brief-audio" aria-label="${copy('Audio briefing','音频简报')}">
    <div class="brief-audio-heading"><h3>${copy('Listen to this briefing','收听本期简报')}</h3><span>${esc(duration)} · ${copy('Synthetic voice','合成语音')}</span></div>
    <audio controls preload="none" aria-label="${esc(copy('Briefing dated ','简报日期 ')+brief.date)}" src="${esc(item.url)}"></audio>
    <p class="hint" data-audio-error hidden>${copy('Playback failed. You can still read the transcript below or try downloading the file.','音频播放失败，可阅读下方文字稿或尝试下载文件。')}</p>
    <details class="brief-audio-transcript"><summary>${copy('Transcript and recording details','播报稿与录音信息')}</summary>
      <p class="hint">${copy('Audio generated','音频生成于')} <time datetime="${esc(item.generated_at)}" title="${esc(item.generated_at)}">${esc(recordingTime(item.generated_at))}</time> · ${copy('New York time','纽约时间')} · ${esc(item.voice)} · ${esc(data.script_version)}</p>
      <p class="hint">${copy('This recording reads a saved edition. Its generation date can be later than the briefing date. Research checks are included; no economic-release calendar is connected. Sources appear in this edition’s news notes below.','录音读取已保存稿件，音频生成日期可能晚于简报日期。关注点为研究核验事项，尚未接入经济发布日历。来源见本期下方新闻解读。')}</p>
      <div class="brief-audio-script">${esc(item.transcript)}</div>
      <a class="brief-audio-download" href="${esc(item.url)}" download="fx-briefing-${esc(brief.date)}-${getLang()}.mp3">${copy('Download MP3','下载 MP3')} ↗</a>
    </details></section>`;
}

export function bindAudio(panel) {
  // Delegation also covers an archive selection that replaces the child player.
  panel.addEventListener('play',event=>{
    if (event.target.tagName!=='AUDIO') return;
    panel.querySelectorAll('audio').forEach(player=>{if(player!==event.target)player.pause();});
  },true);
  panel.addEventListener('error',event=>{
    const player=event.target.closest?.('audio');
    if (!player) return;
    const hint=player.closest('.brief-audio')?.querySelector('[data-audio-error]');
    if (hint) hint.hidden=false;
  },true);
}
