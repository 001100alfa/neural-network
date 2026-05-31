import { short, esc, parseEnv } from './util.js';
import './editor.js';   // self-initialising in-browser editor (side-effect import)
import './panels.js';   // terminal / git / web-server panels (side-effect import)

// Security hygiene: the startup URL carries a one-time ?token=. The server has
// already pinned it as a SameSite cookie by the time this runs, so strip it
// from the address bar/history (keeps it out of screenshots and back/forward).
if (location.search.includes('token=')) {
  try { history.replaceState(null, '', location.pathname); } catch (e) {}
}

const log = document.getElementById('log');
const input = document.getElementById('input');
const send = document.getElementById('send');

function el(cls, text){const d=document.createElement('div');d.className=cls;if(text!=null)d.textContent=text;return d;}
function scroll(){log.scrollTop = log.scrollHeight;}

function addMsg(role, text){ if(!text) return; const d=el('msg '+role, text); log.appendChild(d); scroll(); }
function addThinking(t){ const d=el('thinking', t||'thinking…'); log.appendChild(d); scroll(); return d; }

// streaming: build the assistant bubble token-by-token
let curStream=null;
function appendToken(t){
  if(!curStream){ curStream=el('msg assistant'); curStream.textContent=''; log.appendChild(curStream); }
  curStream.textContent += t; scroll();
}
function endStream(){ curStream=null; }

function renderTodos(todos){
  const bar=document.getElementById('todoBar'); bar.innerHTML='';
  if(!todos || !todos.length){ bar.classList.remove('on'); return; }
  todos.forEach(t=>{ const row=el('t');
    const mark=t.status==='completed'?'✔':(t.status==='in_progress'?'▶':'○');
    const s=document.createElement('span'); s.className='s'; s.textContent=mark;
    const c=document.createElement('span');
    c.className=t.status==='completed'?'done':(t.status==='in_progress'?'cur':'');
    c.textContent=t.content; row.appendChild(s); row.appendChild(c); bar.appendChild(row); });
  bar.classList.add('on');
}
function addEvent(ev){
  if(ev.type==='thinking'){ return; }
  if(ev.type==='token'){ appendToken(ev.text); return; }
  if(ev.type==='todos'){ renderTodos(ev.todos); return; }
  endStream();  // any non-token event finalises the streamed bubble
  if(ev.type==='assistant'){ addMsg('assistant', ev.text); return; }
  if(ev.type==='tool_call'){
    const wrap=el('event'); wrap.appendChild(Object.assign(el('head'),
      {textContent:'⚙ '+ev.name+'('+Object.entries(ev.args||{}).map(([k,v])=>k+'='+short(v)).join(', ')+')'}));
    log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='tool_result'){
    const wrap=el('event'+(ev.error?' err':'')); wrap.appendChild(Object.assign(el('head'),{textContent: ev.error?'✗ error':'↳ result'}));
    wrap.appendChild(Object.assign(el('body'),{textContent: ev.text})); log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='diff'){
    const wrap=el('event diff'); wrap.appendChild(Object.assign(el('head'),{textContent:'± '+ev.path}));
    const body=el('body'); ev.diff.split('\n').forEach(line=>{
      let c=''; if(line.startsWith('+')&&!line.startsWith('+++'))c='add';
      else if(line.startsWith('-')&&!line.startsWith('---'))c='del';
      else if(line.startsWith('@@'))c='hunk';
      const ln=document.createElement('div'); ln.className=c; ln.textContent=line; body.appendChild(ln);
    });
    wrap.appendChild(body); log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='info'||ev.type==='warn'||ev.type==='error'){
    const wrap=el('event'+(ev.type==='error'?' err':'')); wrap.appendChild(Object.assign(el('head'),{textContent:ev.type}));
    wrap.appendChild(Object.assign(el('body'),{textContent:ev.text})); log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='tool_approval'){ renderApproval(ev); return; }
  if(ev.type==='tool_approval_resolved'){
    const box=document.getElementById('appr-'+ev.id);
    if(box){ box.querySelector('.appr-actions').remove();
      box.appendChild(Object.assign(el('body'),{textContent:'decision: '+ev.decision})); }
    return;
  }
}
function renderApproval(ev){
  const wrap=el('event appr'); wrap.id='appr-'+ev.id;
  wrap.appendChild(Object.assign(el('head'),
    {textContent:'⚠ approve '+ev.name+'('+Object.entries(ev.args||{}).map(([k,v])=>k+'='+short(v)).join(', ')+')'}));
  const act=el('appr-actions');
  const mk=(label,decision)=>{ const b=document.createElement('button'); b.textContent=label;
    b.onclick=()=>{ act.querySelectorAll('button').forEach(x=>x.disabled=true);
      fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:ev.id,decision})}); }; return b; };
  act.appendChild(mk('Approve','yes'));
  act.appendChild(mk('Always','always'));
  act.appendChild(mk('Deny','no'));
  wrap.appendChild(act); log.appendChild(wrap); scroll();
}
async function loadInfo(){
  const r=await fetch('/api/info'); const d=await r.json();
  document.getElementById('provider').textContent=d.provider;
  document.getElementById('model').textContent=d.model;
  document.getElementById('workdir').textContent=d.workdir;
  document.getElementById('providerSel').value=d.provider;
  document.getElementById('modelInput').value=d.model;
  document.getElementById('tools').innerHTML='';
  d.tools.forEach(t=>{const x=el('tool');x.innerHTML='<b>'+t.name+'</b><small>'+t.description+'</small>';
    document.getElementById('tools').appendChild(x);});
  document.getElementById('memBadge').classList.toggle('on', !!d.memory);
  document.getElementById('planBtn').classList.toggle('on', !!d.plan_mode);
  document.getElementById('thinkBtn').classList.toggle('on', (d.thinking_tokens||0)>0);
  if(d.output_style){ const ss=document.getElementById('styleSel'); if(ss) ss.value=d.output_style; }
}
document.getElementById('thinkBtn').onclick=async()=>{
  const on=document.getElementById('thinkBtn').classList.contains('on');
  await fetch('/api/thinking',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tokens:on?0:8000})});
  loadInfo();
};
document.getElementById('planBtn').onclick=async()=>{
  const on=!document.getElementById('planBtn').classList.contains('on');
  await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({on})});
  loadInfo();
};
document.getElementById('rewindBtn').onclick=async()=>{
  const r=await fetch('/api/checkpoints'); const d=await r.json();
  if(!(d.checkpoints||[]).length){ sysMsg('nothing to rewind'); return; }
  const last=d.checkpoints[d.checkpoints.length-1];
  if(!confirm('Undo last change to '+last.path+'?')) return;
  const rr=await fetch('/api/rewind',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const rd=await rr.json();
  if(rd.ok){ sysMsg('rewound: '+(rd.undone||[]).join(', '));
    if(window.__edReloadTree) window.__edReloadTree(); if(window.__edRefresh) window.__edRefresh(); }
  else sysMsg('rewind: '+(rd.error||'failed'));
};

// ---- conversation tabs (multiple concurrent chats) ----
let convs={}, activeConv=null, convSeq=0;
function newConv(title){ const id='c'+(++convSeq); convs[id]={title:title||('Chat '+convSeq), html:''}; return id; }
function renderConvTabs(){
  const bar=document.getElementById('convTabs'); bar.innerHTML='';
  Object.keys(convs).forEach(id=>{
    const t=el('ctab'+(id===activeConv?' active':''));
    const nm=document.createElement('span'); nm.textContent=convs[id].title; nm.onclick=()=>switchConv(id);
    t.appendChild(nm);
    if(Object.keys(convs).length>1){ const x=document.createElement('span'); x.className='x'; x.textContent='×';
      x.onclick=(e)=>{e.stopPropagation(); closeConv(id);}; t.appendChild(x); }
    bar.appendChild(t);
  });
  const add=document.createElement('button'); add.className='newconv'; add.textContent='+ New chat';
  add.onclick=()=>{ switchConv(newConv()); }; bar.appendChild(add);
}
function switchConv(id){
  if(activeConv && convs[activeConv]) convs[activeConv].html=log.innerHTML;
  activeConv=id; curStream=null; log.innerHTML=(convs[id]&&convs[id].html)||''; renderConvTabs(); scroll();
}
async function closeConv(id){
  try{ await fetch('/api/conversation/close',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:id})}); }catch(_){}
  delete convs[id];
  if(activeConv===id){ const rest=Object.keys(convs); switchConv(rest[0]||newConv()); }
  else renderConvTabs();
}

// ---- image attachments (multimodal) ----
let pending=[];        // images (sent as vision content)
let pendingFiles=[];   // non-image documents (inlined as text)
function renderAttachments(){
  const bar=document.getElementById('attachBar'); bar.innerHTML='';
  pending.forEach((p,i)=>{ const th=el('thumb'); const im=document.createElement('img');
    im.src='data:'+p.media_type+';base64,'+p.data; th.appendChild(im);
    const x=document.createElement('span'); x.className='x'; x.textContent='×';
    x.onclick=()=>{pending.splice(i,1);renderAttachments();}; th.appendChild(x); bar.appendChild(th); });
  pendingFiles.forEach((p,i)=>{ const th=el('thumb'); th.style.fontSize='10px'; th.style.padding='3px';
    th.textContent='📄 '+(p.name||'file');
    const x=document.createElement('span'); x.className='x'; x.textContent='×';
    x.onclick=()=>{pendingFiles.splice(i,1);renderAttachments();}; th.appendChild(x); bar.appendChild(th); });
}
function addFiles(files){
  [...files].forEach(f=>{
    const r=new FileReader();
    const isImg = f.type && f.type.indexOf('image/')===0;
    r.onload=()=>{ const b64=String(r.result).split(',')[1]||'';
      if(isImg) pending.push({media_type:f.type||'image/png',data:b64,name:f.name||'pasted'});
      else pendingFiles.push({name:f.name||'file',data:b64});
      renderAttachments(); };
    r.readAsDataURL(f); });
}
document.getElementById('attachBtn').onclick=()=>document.getElementById('fileInput').click();
document.getElementById('fileInput').addEventListener('change',(e)=>{ addFiles(e.target.files); e.target.value=''; });
// drag & drop onto the chat area
['dragover','drop'].forEach(ev=>document.getElementById('log').addEventListener(ev,e=>{e.preventDefault();}));
document.getElementById('log').addEventListener('drop',e=>{ if(e.dataTransfer&&e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
const mainEl=document.querySelector('main');
['dragover','drop'].forEach(ev=>mainEl.addEventListener(ev,e=>{e.preventDefault(); mainEl.classList.toggle('dragging', ev==='dragover');}));
mainEl.addEventListener('drop',e=>{ mainEl.classList.remove('dragging'); if(e.dataTransfer&&e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
mainEl.addEventListener('dragleave',()=>mainEl.classList.remove('dragging'));
// paste images from the clipboard
document.addEventListener('paste',e=>{ const items=(e.clipboardData||{}).items||[];
  const imgs=[...items].filter(it=>it.type&&it.type.indexOf('image/')===0).map(it=>it.getAsFile()).filter(Boolean);
  if(imgs.length){ addFiles(imgs); } });
function addUserMsg(text, imgs){
  const d=el('msg user'); if(text) d.textContent=text;
  if(imgs && imgs.length){ const box=el('imgs'); imgs.forEach(p=>{const im=document.createElement('img');
    im.src='data:'+p.media_type+';base64,'+p.data; box.appendChild(im);}); d.appendChild(box); }
  log.appendChild(d); scroll();
}

function sysMsg(text){ const d=el('event'); d.appendChild(Object.assign(el('head'),{textContent:text})); log.appendChild(d); scroll(); }
let customCommands=[];
async function loadCommands(){ try{ const r=await fetch('/api/commands'); customCommands=(await r.json()).commands||[]; }catch(_){} }
async function runSlash(text){
  const [cmd, ...rest]=text.slice(1).split(/\s+/); const arg=rest.join(' ').trim();
  const lc=(cmd||'').toLowerCase();
  // custom commands (.aio/commands/*.md) are handled server-side: send as a message
  if(customCommands.includes(lc) && !['help','new','clear','save','export','provider','model','theme'].includes(lc)){
    return sendMsgText(text);
  }
  switch(lc){
    case 'help': sysMsg('built-in: /new /clear /save /export [md|json] /provider /model /theme /help'
      +(customCommands.length?('  ·  custom: '+customCommands.map(c=>'/'+c).join(' ')):'')); break;
    case 'new': switchConv(newConv()); break;
    case 'clear': document.getElementById('reset').click(); break;
    case 'save': document.getElementById('saveSession').click(); break;
    case 'export': exportConv((arg||'md').toLowerCase()==='json'?'json':'md'); break;
    case 'provider':
      if(arg){ document.getElementById('providerSel').value=arg; await fetch('/api/config',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:arg})}); loadInfo(); sysMsg('provider → '+arg); }
      break;
    case 'model':
      if(arg){ await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({model:arg})}); loadInfo(); sysMsg('model → '+arg); }
      break;
    case 'theme':{ const s=getSettings(); s.theme=(arg==='light'?'light':'dark'); saveSettings(s); sysMsg('theme → '+s.theme); break; }
    default: sysMsg('unknown command: /'+cmd+' (try /help)');
  }
}

async function sendMsg(){
  const text=input.value.trim();
  const hasAtt = pending.length>0 || pendingFiles.length>0;
  if(!text && !hasAtt) return;
  if(text.startsWith('/') && !hasAtt){ input.value=''; await runSlash(text); input.focus(); return; }
  input.value=''; await sendMsgText(text);
}
async function sendMsgText(text){
  const imgs=pending.map(p=>({media_type:p.media_type,data:p.data}));
  const docs=pendingFiles.map(p=>({name:p.name,data:p.data}));
  const labelImgs=pending.slice(); const labelDocs=pendingFiles.map(p=>p.name);
  addUserMsg(text + (labelDocs.length?('\n📄 '+labelDocs.join(', ')):''), labelImgs);
  pending=[]; pendingFiles=[]; renderAttachments();
  send.disabled=true; const think=addThinking();
  let sawDiff=false, gotFirst=false;
  try{
    const resp=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text, images:imgs, files:docs, conv:activeConv})});
    const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
    for(;;){
      const {value,done}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const frame=buf.slice(0,i); buf=buf.slice(i+2);
        const dl=frame.split('\n').find(l=>l.startsWith('data:')); if(!dl) continue;
        let ev; try{ ev=JSON.parse(dl.slice(5).trim()); }catch(_){ continue; }
        if(!gotFirst){ think.remove(); gotFirst=true; }
        if(ev.type==='done'){ if(ev.usage) renderUsage(ev.usage); break; }
        if(ev.type==='diff') sawDiff=true;
        if(ev.type!=='thinking') addEvent(ev);
      }
    }
  }catch(e){ if(!gotFirst) think.remove(); addEvent({type:'error',text:String(e)}); }
  endStream(); send.disabled=false; input.focus();
  if(sawDiff){ if(window.__edReloadTree) window.__edReloadTree(); if(window.__edRefresh) window.__edRefresh(); }
}

send.onclick=sendMsg;
input.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();}                 // Enter / Ctrl+Enter: send
  if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendMsg();}
});
// global keyboard shortcuts (chosen to avoid clobbering browser defaults)
document.addEventListener('keydown',e=>{
  if(e.altKey && (e.key==='n'||e.key==='N')){ e.preventDefault(); switchConv(newConv()); input.focus(); }
  else if(e.altKey && (e.key==='w'||e.key==='W')){ e.preventDefault(); if(activeConv) closeConv(activeConv); }
  else if((e.ctrlKey||e.metaKey) && e.key===','){ e.preventDefault(); document.getElementById('settingsPanel').classList.toggle('on'); }
  else if(e.key==='Escape'){ document.getElementById('settingsPanel').classList.remove('on');
    const sr=document.getElementById('searchResults'); if(sr) sr.classList.remove('on'); }
});
document.getElementById('reset').onclick=async()=>{
  await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv})});
  log.innerHTML=''; if(convs[activeConv]) convs[activeConv].html=''; loadInfo();
};
document.getElementById('apply').onclick=async()=>{
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({provider:document.getElementById('providerSel').value,
      model:document.getElementById('modelInput').value})});
  loadInfo();
};

// ---- sessions: save / load conversation history ----
const sessionSel=document.getElementById('sessionSel');
async function loadSessions(){
  const r=await fetch('/api/sessions'); const d=await r.json();
  sessionSel.innerHTML='<option value="">sessions… ('+(d.sessions||[]).length+')</option>';
  (d.sessions||[]).forEach(s=>{const o=document.createElement('option');o.value=s.id;
    o.textContent=s.title+' · '+s.count+' msg'; sessionSel.appendChild(o);});
}
function renderHistory(msgs){
  log.innerHTML='';
  (msgs||[]).forEach(m=>{
    if(m.role==='user'){ addUserMsg(m.content, (m.images||[])); }
    else if(m.role==='assistant'){
      if(m.content) addMsg('assistant', m.content);
      (m.tool_calls||[]).forEach(tc=>addEvent({type:'tool_call',name:tc.name,args:tc.arguments}));
    } else if(m.role==='tool'){ addEvent({type:'tool_result',text:m.content}); }
  });
}
document.getElementById('saveSession').onclick=async()=>{
  const r=await fetch('/api/sessions/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv})});
  const d=await r.json(); await loadSessions();
  if(d.id) sessionSel.value=d.id;
  if(d.title && convs[activeConv]){ convs[activeConv].title=d.title; renderConvTabs(); }
};
document.getElementById('loadSession').onclick=async()=>{
  const id=sessionSel.value; if(!id) return;
  const r=await fetch('/api/sessions/load',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id, conv:activeConv})});
  const d=await r.json();
  if(d.ok){ renderHistory(d.messages); if(d.title && convs[activeConv]){convs[activeConv].title=d.title; renderConvTabs();} }
};
loadSessions();

// ---- export / import a conversation ----
function download(filename, content, mime){
  const blob=new Blob([content],{type:mime||'text/plain'}); const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download=filename; document.body.appendChild(a);
  a.click(); a.remove(); URL.revokeObjectURL(url);
}
async function exportConv(fmt){
  const r=await fetch('/api/sessions/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv, format:fmt})});
  const d=await r.json(); download(d.filename, d.content, d.mime);
}
document.getElementById('exportMd').onclick=()=>exportConv('md');
document.getElementById('exportJson').onclick=()=>exportConv('json');
document.getElementById('importBtn').onclick=()=>document.getElementById('importInput').click();
document.getElementById('importInput').addEventListener('change',(e)=>{
  const f=e.target.files[0]; if(!f) return; const rd=new FileReader();
  rd.onload=async()=>{ let data; try{ data=JSON.parse(String(rd.result)); }catch(err){ alert('Not valid JSON'); return; }
    const r=await fetch('/api/sessions/import',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({data})});
    const d=await r.json();
    if(d.ok){ const id=newConv(d.title||'imported'); switchConv(id); renderHistory(d.messages);
      convs[id].html=log.innerHTML; }
  };
  rd.readAsText(f); e.target.value='';
});

// ---- settings: theme + font size (persisted in localStorage) ----
function getSettings(){ try{ return JSON.parse(localStorage.getItem('aio_settings')||'{}'); }catch(_){ return {}; } }
function applySettings(){
  const s=getSettings(); const theme=s.theme||'dark'; const fz=s.font||14;
  document.body.classList.toggle('light', theme==='light');
  document.documentElement.style.setProperty('--fz', fz+'px');
  const ts=document.getElementById('themeSel'); if(ts) ts.value=theme;
  const fv=document.getElementById('fzVal'); if(fv) fv.textContent=fz;
}
function saveSettings(s){ localStorage.setItem('aio_settings', JSON.stringify(s)); applySettings(); }
document.getElementById('gearBtn').onclick=()=>document.getElementById('settingsPanel').classList.toggle('on');
document.getElementById('themeSel').onchange=(e)=>{ const s=getSettings(); s.theme=e.target.value; saveSettings(s); };
document.getElementById('styleSel').onchange=async(e)=>{
  await fetch('/api/style',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({style:e.target.value})});
};
document.getElementById('fzMinus').onclick=()=>{ const s=getSettings(); s.font=Math.max(11,(s.font||14)-1); saveSettings(s); };
document.getElementById('fzPlus').onclick=()=>{ const s=getSettings(); s.font=Math.min(22,(s.font||14)+1); saveSettings(s); };
applySettings();

// ---- search across saved conversations ----
const searchInput=document.getElementById('searchInput');
const searchResults=document.getElementById('searchResults');
async function loadSessionById(id, title){
  const r=await fetch('/api/sessions/load',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id, conv:activeConv})});
  const d=await r.json();
  if(d.ok){ renderHistory(d.messages);
    if((title||d.title)&&convs[activeConv]){convs[activeConv].title=(title||d.title); renderConvTabs();} }
}
let searchTimer=null;
function placeSearch(){ const r=searchInput.getBoundingClientRect(); searchResults.style.left=r.left+'px'; }
searchInput.addEventListener('input',()=>{ clearTimeout(searchTimer); searchTimer=setTimeout(doSearch,250); });
async function doSearch(){
  const q=searchInput.value.trim();
  if(!q){ searchResults.classList.remove('on'); return; }
  const r=await fetch('/api/sessions/search',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q})});
  const d=await r.json(); searchResults.innerHTML=''; placeSearch();
  if(!(d.results||[]).length){ searchResults.innerHTML='<div class="sr"><small>no matches</small></div>'; }
  (d.results||[]).forEach(s=>{ const it=el('sr');
    it.innerHTML='<b>'+esc(s.title)+'</b> <small>· '+s.matches+' match(es) · '+s.count+' msg</small>'
      +'<br><small>'+esc(s.snippet||'')+'</small>';
    it.onclick=()=>{ searchResults.classList.remove('on'); searchInput.value=''; loadSessionById(s.id, s.title); };
    searchResults.appendChild(it); });
  searchResults.classList.add('on');
}
document.addEventListener('click',(e)=>{ if(!searchResults.contains(e.target) && e.target!==searchInput)
  searchResults.classList.remove('on'); });


// ---- MCP panel ----
const mcpList=document.getElementById('mcpList');
async function loadMcp(){
  const r=await fetch('/api/mcp'); const d=await r.json(); mcpList.innerHTML='';
  if(!(d.servers||[]).length){ mcpList.appendChild(el('muted','No MCP servers configured. Add one below.')); }
  (d.servers||[]).forEach(s=>{
    const c=el('pcard'); const t=el('ptitle');
    t.appendChild(el('pdot'+(s.running?' ok':'')));
    const nm=document.createElement('b'); nm.textContent=s.name; t.appendChild(nm);
    const sp=document.createElement('span'); sp.style.flex='1'; t.appendChild(sp);
    const badge=el('badge'); badge.textContent=(s.running?'running · ':'stopped · ')+s.tools+' tools'; t.appendChild(badge);
    c.appendChild(t);
    const cmd=el('muted', s.command+' '+(s.args||[]).join(' ')); cmd.style.fontSize='11px'; c.appendChild(cmd);
    const acts=el('pacts');
    if(!s.running){ const st=document.createElement('button'); st.textContent='Start';
      st.onclick=async()=>{ st.disabled=true; st.textContent='starting…';
        await fetch('/api/mcp/start',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:s.name})}); loadMcp(); loadInfo(); };
      acts.appendChild(st); }
    const rm=document.createElement('button'); rm.textContent='Remove';
    rm.onclick=async()=>{ await fetch('/api/mcp/remove',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:s.name})}); loadMcp(); loadInfo(); };
    acts.appendChild(rm); c.appendChild(acts); mcpList.appendChild(c);
  });
  mcpCatalogAll=d.catalog||[]; applyCatFilter();
}
let mcpCatalogAll=[];
function applyCatFilter(){
  const q=(document.getElementById('mcpCatFilter').value||'').toLowerCase().trim();
  const list=q ? mcpCatalogAll.filter(s=>(s.name+' '+(s.desc||'')).toLowerCase().includes(q)) : mcpCatalogAll;
  renderCatalog(list);
}
document.getElementById('mcpCatFilter').addEventListener('input', applyCatFilter);
function renderCatalog(cat){
  const box=document.getElementById('mcpCatalog'); box.innerHTML='';
  if(!cat.length){ box.appendChild(el('muted','no servers match the filter')); return; }
  cat.forEach(s=>{
    const row=el('mcat'); const info=el('info');
    const title=document.createElement('div');
    title.innerHTML='<b>'+esc(s.name)+'</b>'+(s.installed?' <span class="ins">✓ added</span>':'')
      +(s.env_hint?' <span class="key" title="needs env var">'+esc(s.env_hint)+'</span>':'');
    info.appendChild(title);
    info.appendChild(Object.assign(el('d'),{textContent:s.desc||''}));
    info.appendChild(Object.assign(el('cmd'),{textContent:s.command+' '+(s.args||[]).join(' ')}));
    const use=document.createElement('button'); use.textContent='Use →';
    use.onclick=()=>{
      document.getElementById('mcpName').value=s.name;
      document.getElementById('mcpCmd').value=s.command;
      document.getElementById('mcpArgs').value=(s.args||[]).join(' ');
      const envEl=document.getElementById('mcpEnv');
      envEl.value = s.env_hint ? (s.env_hint+'=') : '';
      document.getElementById('mcpName').scrollIntoView({block:'nearest'});
      (s.env_hint ? envEl : document.getElementById('mcpArgs')).focus();
    };
    row.appendChild(info); row.appendChild(use); box.appendChild(row);
  });
}
document.getElementById('mcpAdd').onclick=async()=>{
  const name=document.getElementById('mcpName').value.trim();
  const command=document.getElementById('mcpCmd').value.trim();
  const args=document.getElementById('mcpArgs').value.trim();
  const env=parseEnv(document.getElementById('mcpEnv').value);
  if(!name||!command) return;
  const body={name, command, args};
  if(Object.keys(env).length) body.env=env;
  await fetch('/api/mcp/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  document.getElementById('mcpName').value=''; document.getElementById('mcpCmd').value='';
  document.getElementById('mcpArgs').value=''; document.getElementById('mcpEnv').value='';
  loadMcp(); loadInfo();
};
document.getElementById('mcpRestart').onclick=async()=>{
  await fetch('/api/mcp/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); loadMcp(); loadInfo();
};
const mcpTabBtn=document.querySelector('.tabs button[data-tab="mcp"]');
if(mcpTabBtn) mcpTabBtn.addEventListener('click', loadMcp);

// start with one conversation
switchConv(newConv('Chat 1'));
loadCommands();

// ---- tabs ----
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById('tab-'+b.dataset.tab).classList.add('active');
});



// ---- Providers / API keys panel ----
const provList=document.getElementById('provList');
const usageBar=document.getElementById('usageBar');
let provDlSeq=0;
function fmt(n){ return (n||0).toLocaleString(); }
function renderUsage(u){
  if(!u){ return; }
  const cost = u.cost_known ? ('$'+(u.est_cost_usd||0).toFixed(4)) : ('~$'+(u.est_cost_usd||0).toFixed(4)+' (partial)');
  let html='usage — requests <b>'+fmt(u.requests)+'</b> · in <b>'+fmt(u.input_tokens)
    +'</b> tok · out <b>'+fmt(u.output_tokens)+'</b> tok · est. cost <b>'+cost+'</b>';
  if((u.cache_read||0)+(u.cache_write||0)>0){
    html+='<br>cache — read <b>'+fmt(u.cache_read)+'</b> · written <b>'+fmt(u.cache_write)+'</b> tok';
  }
  if(u.budget_warning){ html+='<br><span class="bwarn">⚠ '+u.budget_warning+'</span>'; }
  usageBar.innerHTML=html;
}
async function refreshUsage(){ try{ const r=await fetch('/api/usage'); renderUsage(await r.json()); }catch(_){} }
async function loadProviders(){
  const r=await fetch('/api/providers'); const d=await r.json();
  provList.innerHTML='';
  d.providers.forEach(p=>provList.appendChild(provCard(p)));
  renderUsage(d.usage);
}
function provCard(p){
  const card=el('pcard'+(p.active?' active':''));
  const t=el('ptitle');
  t.appendChild(el('pdot'+(p.active?' on':(p.configured?' ok':''))));
  const nm=document.createElement('b'); nm.textContent=p.label; t.appendChild(nm);
  const sp=document.createElement('span'); sp.style.flex='1'; t.appendChild(sp);
  const badge=el('badge'+(p.active?' act':''));
  badge.textContent=p.active?'active':(p.configured?(p.source||'set'):(p.needs_key?'no key':'local'));
  t.appendChild(badge); card.appendChild(t);

  let keyInput=null;
  if(p.needs_key){
    keyInput=document.createElement('input'); keyInput.type='password'; keyInput.autocomplete='off';
    keyInput.placeholder = p.configured ? ('saved '+p.key_masked+' — type new to replace') : ('API key  ('+(p.env||'')+')');
    card.appendChild(keyInput);
  } else {
    const note=el('muted','local runtime — no API key required'); note.style.fontSize='11px'; card.appendChild(note);
  }
  // model field backed by a datalist that "Test" fills with the live model list
  const modelInput=document.createElement('input'); modelInput.value=p.model||''; modelInput.placeholder='model';
  const dl=document.createElement('datalist'); dl.id='dl_'+p.name+'_'+(provDlSeq++); modelInput.setAttribute('list', dl.id);
  card.appendChild(modelInput); card.appendChild(dl);

  const adv=el('padv','▸ advanced (base URL)');
  const advBody=el('padv-body');
  const baseInput=document.createElement('input'); baseInput.value=p.base_url||''; baseInput.placeholder='base URL';
  advBody.appendChild(baseInput);
  adv.onclick=()=>{const on=advBody.classList.toggle('on'); adv.textContent=(on?'▾':'▸')+' advanced (base URL)';};
  card.appendChild(adv); card.appendChild(advBody);

  // monthly budget + spend
  const budgetInput=document.createElement('input'); budgetInput.type='number';
  budgetInput.step='0.01'; budgetInput.min='0';
  budgetInput.value = p.budget_usd ? p.budget_usd : '';
  budgetInput.placeholder='monthly budget $ (0 = none)';
  card.appendChild(budgetInput);
  if(p.budget_usd>0 || p.month_spent_usd>0){
    const spent=el('ptest '+(p.over_budget?'err':(p.near_budget?'err':'muted')));
    spent.textContent = p.budget_usd>0
      ? ('this month: $'+(p.month_spent_usd||0).toFixed(4)+' / $'+p.budget_usd.toFixed(2)
          +(p.over_budget?'  ⚠ over budget':(p.near_budget?'  ⚠ nearing':'')))
      : ('this month: $'+(p.month_spent_usd||0).toFixed(4));
    card.appendChild(spent);
  }

  const status=el('ptest muted',''); card.appendChild(status);

  const acts=el('pacts');
  const testBtn=document.createElement('button'); testBtn.textContent='Test';
  testBtn.onclick=()=>testProvider(p.name, keyInput, modelInput, baseInput, budgetInput, dl, status, testBtn);
  const saveBtn=document.createElement('button'); saveBtn.textContent='Save';
  saveBtn.onclick=()=>saveProvider(p.name, keyInput, modelInput, baseInput, budgetInput, false);
  const useBtn=document.createElement('button'); useBtn.textContent=p.active?'In use':'Use';
  useBtn.disabled=!!p.active;
  useBtn.onclick=()=>saveProvider(p.name, keyInput, modelInput, baseInput, budgetInput, true);
  acts.appendChild(testBtn); acts.appendChild(saveBtn); acts.appendChild(useBtn);
  card.appendChild(acts);
  return card;
}
async function testProvider(name, keyInput, modelInput, baseInput, budgetInput, dl, status, btn){
  // Persist any typed key/base first so the test uses current values.
  if((keyInput && keyInput.value) || (baseInput && baseInput.value)){
    await saveProviderQuiet(name, keyInput, modelInput, baseInput, budgetInput);
  }
  status.className='ptest muted'; status.textContent='testing…'; btn.disabled=true;
  try{
    const r=await fetch('/api/providers/test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({provider:name})});
    const d=await r.json();
    if(d.ok){
      status.className='ptest ok'; status.textContent='✓ connected — '+d.count+' models';
      dl.innerHTML=''; (d.models||[]).forEach(m=>{const o=document.createElement('option');o.value=m;dl.appendChild(o);});
    } else {
      status.className='ptest err'; status.textContent='✗ '+(d.error||'failed').split('\n')[0].slice(0,140);
    }
  }catch(e){ status.className='ptest err'; status.textContent='✗ '+e; }
  btn.disabled=false;
}
function provBody(name, keyInput, modelInput, baseInput, budgetInput, makeActive){
  const body={provider:name, model:modelInput.value, base_url:baseInput.value};
  if(keyInput && keyInput.value) body.api_key=keyInput.value;
  if(budgetInput && budgetInput.value!=='') body.budget=parseFloat(budgetInput.value)||0;
  if(makeActive) body.make_active=true;
  return body;
}
async function saveProviderQuiet(name, keyInput, modelInput, baseInput, budgetInput){
  await fetch('/api/providers',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(provBody(name, keyInput, modelInput, baseInput, budgetInput, false))});
}
async function saveProvider(name, keyInput, modelInput, baseInput, budgetInput, makeActive){
  await fetch('/api/providers',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(provBody(name, keyInput, modelInput, baseInput, budgetInput, makeActive))});
  await loadProviders();
  loadInfo();
}
const provTabBtn=document.querySelector('.tabs button[data-tab="providers"]');
if(provTabBtn) provTabBtn.addEventListener('click', loadProviders);
loadProviders();

loadInfo();
