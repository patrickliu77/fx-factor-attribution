'use strict';
const data=JSON.parse(document.getElementById('review-data').textContent);
const el=id=>document.getElementById(id);
const axes={relevance:['related','unrelated','unclear'],redundancy:['unique','duplicate','unclear'],evidence:['supports_event','insufficient','unclear']};
const byId=new Map(data.items.map(i=>[i.id,i]));
const snapshots=new Map(data.snapshots.map(s=>[s.id,s]));
const blank=id=>({id,relevance:null,redundancy:null,evidence:null,duplicate_of:null,notes:'',reviewed_at:null});
let labels=new Map(data.items.map(i=>[i.id,blank(i.id)])), position=0, dirty=false;
const complete=l=>Object.keys(axes).every(k=>l[k]!==null) && (l.redundancy!=='duplicate' || !!l.duplicate_of);
function status(text,error=false) {el('status').textContent=text;el('status').dataset.error=String(error);}
function filtered() {return data.items.filter(i=>(!el('channel').value || i.channel===el('channel').value) && (!el('unfinished').checked || !complete(labels.get(i.id))));}
function option(value,text) {const o=document.createElement('option');o.value=value;o.textContent=text;return o;}
function progress() {
  const count=[...labels.values()].filter(complete).length;
  el('progress').textContent=`${data.items.length} 条候选，${count} 条完成三个维度，${data.items.length-count} 条尚未完成。${data.snapshots.length} 份快照。`;
}
function render() {
  const rows=filtered();position=Math.min(position,Math.max(0,rows.length-1));
  const item=rows[position];
  el('candidate').hidden=!item;el('empty').hidden=!!item;
  el('position').textContent=rows.length ? `${position+1} / ${rows.length}` : '0 / 0';
  el('previous').disabled=position===0;el('next').disabled=position>=rows.length-1;
  progress();if(!item) return;
  el('candidate').dataset.id=item.id;
  const s=item.source, snapshot=snapshots.get(item.snapshot_id), l=labels.get(item.id);
  el('channel-name').textContent=item.channel;
  el('title').textContent=s.title || '缺少标题';
  el('source').textContent=`${s.source || '未标注来源'} · 发布日期 ${s.published || '未提供'}`;
  el('timing').textContent=`抓取于 ${s.observed_at || '未提供'}。检索日期范围 ${snapshot.news_window.start} 至 ${snapshot.news_window.end}。日期精确到日，不代表事件发生时间。`;
  el('query').textContent=`原检索词：${item.query || '未保存'}`;
  el('snippet').textContent=s.summary || '未提供摘要。';
  let safe=false;
  try {const u=new URL(s.url);safe=['http:','https:'].includes(u.protocol) && !!u.hostname && !u.username && !u.password;} catch {}
  el('original').hidden=!safe;
  if(safe) el('original').href=s.url;else el('original').removeAttribute('href');
  for(const k of Object.keys(axes)) el(k).value=l[k] || '';
  el('notes').value=l.notes;
  el('duplicate_of').replaceChildren(option('','请选择对应报道'));
  for(const other of data.items.filter(i=>i.id!==item.id && i.channel===item.channel && i.snapshot_id===item.snapshot_id)) {
    el('duplicate_of').append(option(other.id,`${other.source.title || '缺少标题'} · ${other.source.source || '未标注来源'}`));
  }
  el('duplicate_of').value=l.duplicate_of || '';
  el('duplicate-label').hidden=l.redundancy!=='duplicate';
}
function capture() {
  const id=el('candidate').dataset.id;if(!id) return;
  const l=labels.get(id);
  for(const k of Object.keys(axes)) l[k]=el(k).value || null;
  l.duplicate_of=l.redundancy==='duplicate' ? el('duplicate_of').value || null : null;
  if(l.redundancy!=='duplicate') el('duplicate_of').value='';
  l.notes=el('notes').value;l.reviewed_at=new Date().toISOString();dirty=true;
  el('duplicate-label').hidden=l.redundancy!=='duplicate';progress();
  if(el('unfinished').checked && complete(l)) el('next').disabled=false;
  // Keep the current card in place until navigation, even if it becomes complete.
  status('进度尚未导出。');
}
function validate(input) {
  const keys=(o,wanted)=>o && typeof o==='object' && !Array.isArray(o) && Object.keys(o).sort().join('|')===wanted.sort().join('|');
  if(!keys(input,['schema','dataset_id','reviewer','labels']) || input.schema!==data.schema || input.dataset_id!==data.dataset_id) throw Error('文件不属于当前样本或版本。');
  if(!keys(input.reviewer,['alias','origin']) || typeof input.reviewer.alias!=='string' || input.reviewer.alias.length>64 || ![null,'human','ai_assisted','synthetic'].includes(input.reviewer.origin)) throw Error('复核代号或标签来源无效。');
  if(!Array.isArray(input.labels)) throw Error('标签列表无效。');
  const incoming=new Map();let touched=false;
  for(const l of input.labels) {
    if(!keys(l,['id',...Object.keys(axes),'duplicate_of','notes','reviewed_at']) || !byId.has(l.id) || incoming.has(l.id)) throw Error('候选编号未知或重复。');
    if(Object.entries(axes).some(([k,v])=>![null,...v].includes(l[k])) || typeof l.notes!=='string' || l.notes.length>1200) throw Error('标签值或备注无效。');
    const changed=Object.keys(axes).some(k=>l[k]!==null) || !!l.notes.trim();
    if((changed || l.reviewed_at!==null) && (typeof l.reviewed_at!=='string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(l.reviewed_at) || !Number.isFinite(Date.parse(l.reviewed_at)))) throw Error('标注时间缺失或没有时区。');
    if(l.redundancy==='duplicate') {
      const a=byId.get(l.id),b=byId.get(l.duplicate_of);
      if(!b || a.id===b.id || a.channel!==b.channel || a.snapshot_id!==b.snapshot_id) throw Error('重复报道需选择同一快照和频道内的另一条。');
    } else if(l.duplicate_of!==null) throw Error('只有重复报道可以指定对应条目。');
    incoming.set(l.id,{...l});touched ||= changed;
  }
  for(const id of incoming.keys()) {
    let cursor=id;const seen=new Set();
    while(incoming.get(cursor)?.duplicate_of) {
      if(seen.has(cursor)) throw Error('重复引用形成了循环。');
      seen.add(cursor);cursor=incoming.get(cursor).duplicate_of;
    }
  }
  if(touched && (!input.reviewer.alias.trim() || input.reviewer.origin===null)) throw Error('请填写复核代号并声明标签来源。');
  return incoming;
}
for(const channel of [...new Set(data.items.map(i=>i.channel))].sort()) el('channel').append(option(channel,channel));
for(const k of [...Object.keys(axes),'duplicate_of']) el(k).addEventListener('change',capture);
el('notes').addEventListener('input',capture);
for(const k of ['alias','origin']) el(k).addEventListener('change',()=>{dirty=true;status('复核信息尚未导出。');});
for(const k of ['channel','unfinished']) el(k).addEventListener('change',()=>{position=0;render();});
function navigate(delta) {
  const rows=filtered(), current=rows.findIndex(i=>i.id===el('candidate').dataset.id);
  position=current<0 ? Math.max(0,position+(delta<0 ? -1 : 0)) : Math.max(0,current+delta);
  render();
}
el('previous').addEventListener('click',()=>navigate(-1));
el('next').addEventListener('click',()=>navigate(1));
el('save').addEventListener('click',()=>{
  try {
    const payload={schema:data.schema,dataset_id:data.dataset_id,reviewer:{alias:el('alias').value.trim(),origin:el('origin').value || null},labels:[...labels.values()]};
    validate(payload);
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=`news-labels-${data.dataset_id.slice(0,10)}-${Date.now()}.json`;a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);dirty=false;status('已请求下载进度文件，请确认浏览器下载成功。');
  } catch(e) {status(e.message,true);}
});
el('import').addEventListener('change',async event=>{
  try {
    const file=event.target.files[0];if(!file) return;
    if(file.size>10*1024*1024) throw Error('文件超过 10 MB。');
    const payload=JSON.parse(await file.text()), incoming=validate(payload);
    if(dirty && !confirm('导入会替换尚未导出的当前标注。继续导入？')) return;
    labels=new Map(data.items.map(i=>[i.id,incoming.get(i.id) || blank(i.id)]));
    el('alias').value=payload.reviewer.alias;el('origin').value=payload.reviewer.origin || '';
    position=0;dirty=false;render();status('已导入当前样本的进度。');
  } catch(e) {status(e.message,true);} finally {event.target.value='';}
});
addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
el('binding').textContent=`样本 ${data.dataset_id.slice(0,16)} · ${data.schema}`;
render();
