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
function short(v){v=String(v).replace(/\n/g,'\\n');return v.length>60?v.slice(0,60)+'…':v;}

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
    loadTree(); if(window.__edRefresh) window.__edRefresh(); }
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
  if(sawDiff){ loadTree(); if(window.__edRefresh) window.__edRefresh(); }
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
function parseEnv(s){
  const env={};
  (s||'').split(/[\s\n]+/).forEach(tok=>{ const i=tok.indexOf('=');
    if(i>0){ env[tok.slice(0,i)]=tok.slice(i+1); } });
  return env;
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

// ---- Terminal (cmd / bash) ----
const termOut=document.getElementById('termOut'), termCmd=document.getElementById('termCmd');
async function runTerm(){
  const command=termCmd.value.trim(); if(!command) return;
  const shell=document.getElementById('termShell').value;
  termOut.textContent += '\n$ '+command+'\n'; termCmd.value='';
  termOut.scrollTop=termOut.scrollHeight;
  try{
    const r=await fetch('/api/exec',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({command,shell})});
    const d=await r.json();
    termOut.textContent += (d.output||'');
    const tag=document.createElement('div'); tag.className=d.exit_code===0?'ec0':'ecN';
    tag.textContent='[exit '+d.exit_code+']'; termOut.appendChild(tag);
  }catch(e){ termOut.textContent += 'error: '+e+'\n'; }
  termOut.scrollTop=termOut.scrollHeight;
}
document.getElementById('termRun').onclick=runTerm;
termCmd.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runTerm();}});

// ---- Git ----
const gitOut=document.getElementById('gitOut');
function renderGit(text){
  gitOut.innerHTML='';
  (text||'').split('\n').forEach(line=>{
    let c=''; if(line.startsWith('+')&&!line.startsWith('+++'))c='add';
    else if(line.startsWith('-')&&!line.startsWith('---'))c='del';
    else if(line.startsWith('@@'))c='hunk';
    const ln=document.createElement('div'); ln.className=c; ln.textContent=line; gitOut.appendChild(ln);
  });
}
async function git(action,extra){
  const r=await fetch('/api/git',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({action},extra||{}))});
  const d=await r.json(); renderGit(d.output);
}
document.querySelectorAll('[data-git]').forEach(b=>b.onclick=()=>git(b.dataset.git));
document.getElementById('gitCommit').onclick=()=>{
  const message=document.getElementById('gitMsg').value.trim();
  if(!message){renderGit('enter a commit message first.');return;}
  git('commit',{message}).then(()=>{document.getElementById('gitMsg').value='';});
};

// ---- Web server ----
const srvOut=document.getElementById('srvOut');
function renderSrv(d){
  if(d.running){ srvOut.innerHTML='serving working dir at <a class="link" target="_blank" href="'+d.url+'">'+d.url+'</a>'; }
  else{ srvOut.textContent='server stopped.'; }
}
async function srv(action){
  const port=document.getElementById('srvPort').value;
  const r=await fetch('/api/server',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,port:parseInt(port)||8080})});
  renderSrv(await r.json());
}
document.getElementById('srvStart').onclick=()=>srv('start');
document.getElementById('srvStop').onclick=()=>srv('stop');
document.getElementById('srvStatus').onclick=()=>srv('status');

// ---- Editor (in-browser IDE) : syntax highlighting + multi-file tabs ----
const edFile=document.getElementById('edFile'), edText=document.getElementById('edText'),
      edStatus=document.getElementById('edStatus'), edHL=document.getElementById('edHL'),
      edHLpre=document.getElementById('edHLpre'), edTabs=document.getElementById('edTabs'),
      edNums=document.getElementById('edNums');
const LINE_H=18.75; // 12.5px font * 1.5 line-height

// --- tiny zero-dependency syntax highlighter ---
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const KW={
  python:'def class return if elif else for while import from as with try except finally raise in not and or is None True False lambda yield global nonlocal pass break continue assert del async await self print',
  js:'function return if else for while var let const new class extends import export default from typeof instanceof in of await async yield try catch finally throw switch case break continue this null true false undefined delete void do',
  shell:'if then fi else elif for do done case esac in function while until select export local return'};
function kw(l){return '\\b(?:'+KW[l].trim().split(/\s+/).join('|')+')\\b';}
const STR="'(?:\\\\.|[^'\\\\])*'|\"(?:\\\\.|[^\"\\\\])*\"";
const LANGS={
  python:{comment:'#.*',string:"'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"|"+STR,keyword:kw('python'),number:'\\b\\d[\\d_]*\\.?\\d*\\b'},
  js:{comment:'//.*|/\\*[\\s\\S]*?\\*/',string:STR+"|`(?:\\\\.|[^`\\\\])*`",keyword:kw('js'),number:'\\b\\d[\\d_]*\\.?\\d*\\b'},
  json:{string:'"(?:\\\\.|[^"\\\\])*"',keyword:'\\b(?:true|false|null)\\b',number:'-?\\b\\d[\\d_]*\\.?\\d*(?:[eE][+-]?\\d+)?\\b'},
  css:{comment:'/\\*[\\s\\S]*?\\*/',atrule:'@[\\w-]+',string:STR,number:'-?\\b\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|s|ms|fr|deg)?\\b'},
  html:{comment:'<!--[\\s\\S]*?-->',tag:'</?[a-zA-Z][\\w:-]*|/?>',string:STR},
  shell:{comment:'#.*',string:STR,keyword:kw('shell'),number:'\\b\\d+\\b'},
  md:{heading:'^#{1,6}.*',code:'```[\\s\\S]*?```|`[^`]*`'}};
const EXT={py:'python',pyw:'python',js:'js',jsx:'js',ts:'js',tsx:'js',mjs:'js',json:'json',
  css:'css',scss:'css',html:'html',htm:'html',xml:'html',sh:'shell',bash:'shell',md:'md',markdown:'md'};
function langFor(path){const m=(path||'').match(/\.([A-Za-z0-9]+)$/);return m?EXT[m[1].toLowerCase()]:null;}
const RX={};
function compile(lang){if(RX[lang])return RX[lang];const spec=LANGS[lang];const keys=Object.keys(spec);
  RX[lang]={keys,re:new RegExp(keys.map(k=>'(?<'+k+'>'+spec[k]+')').join('|'),'gms')};return RX[lang];}
function highlight(code,lang){
  if(!lang||!LANGS[lang])return esc(code);
  const {re}=compile(lang); re.lastIndex=0; let out='',last=0,m;
  while((m=re.exec(code))){
    out+=esc(code.slice(last,m.index));
    const g=Object.keys(m.groups).find(k=>m.groups[k]!==undefined);
    out+='<span class="t-'+g+'">'+esc(m[0])+'</span>';
    last=m.index+m[0].length;
    if(m[0].length===0)re.lastIndex++;
  }
  out+=esc(code.slice(last)); return out;
}

// --- multi-file tab state ---
let tabs=[], active=-1;
function activeTab(){return active>=0?tabs[active]:null;}
function updateGutter(){
  const n=(edText.value.match(/\n/g)||[]).length+1;
  let s=''; for(let i=1;i<=n;i++) s+=i+'\n';
  edNums.textContent=s;
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';
}
function render(){
  edHL.innerHTML=highlight(edText.value, active>=0?langFor(tabs[active].path):null);
  edHLpre.scrollTop=edText.scrollTop; edHLpre.scrollLeft=edText.scrollLeft;
  updateGutter();
}
function renderTabs(){
  edTabs.innerHTML='';
  tabs.forEach((t,i)=>{
    const el=document.createElement('div'); el.className='etab'+(i===active?' active':'')+(t.dirty?' dirty':'');
    const name=document.createElement('span'); name.className='name'; name.textContent=t.path.split('/').pop();
    name.title=t.path; name.onclick=()=>activate(i);
    const x=document.createElement('span'); x.className='x'; x.textContent='×';
    x.onclick=(e)=>{e.stopPropagation();closeTab(i);};
    el.appendChild(name); el.appendChild(x); edTabs.appendChild(el);
  });
}
function activate(i){
  active=i; const t=tabs[i];
  edText.value=t.content; edFile.value=t.path;
  edStatus.innerHTML='<span class="muted">'+t.path+'</span>';
  renderTabs(); render(); edText.focus();
}
async function openPath(path){
  if(!path)return;
  const existing=tabs.findIndex(t=>t.path===path);
  if(existing>=0){activate(existing);return;}
  const r=await fetch('/api/fs/read',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path})});
  const d=await r.json();
  if(d.error){edStatus.innerHTML='<span class="ecN">'+d.error+'</span>';return;}
  tabs.push({path,content:d.content,clean:d.content,dirty:false}); activate(tabs.length-1);
}
function closeTab(i){
  if(tabs[i].dirty && !confirm('Discard unsaved changes in '+tabs[i].path+'?'))return;
  tabs.splice(i,1);
  if(tabs.length===0){active=-1;edText.value='';edFile.value='';edStatus.textContent='';renderTabs();render();return;}
  activate(Math.min(i,tabs.length-1));
}
async function saveActive(){
  const t=activeTab(); if(!t){edStatus.textContent='no file open';return;}
  t.content=edText.value;
  const r=await fetch('/api/fs/write',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:t.path,content:t.content})});
  const d=await r.json();
  if(d.error){edStatus.innerHTML='<span class="ecN">'+d.error+'</span>';return;}
  t.clean=t.content; t.dirty=false; renderTabs();
  edStatus.innerHTML='<span class="ok">saved '+t.path+' ('+d.bytes+' bytes)</span>';
}
async function loadTree(){
  const r=await fetch('/api/fs/tree',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await r.json();
  edFile.innerHTML='<option value="">— open a file —</option>';
  (d.files||[]).forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f;edFile.appendChild(o);});
  if(active>=0)edFile.value=tabs[active].path;
}
edFile.onchange=()=>openPath(edFile.value);
document.getElementById('edReload').onclick=loadTree;
document.getElementById('edSave').onclick=saveActive;
document.getElementById('edRevert').onclick=()=>{const t=activeTab();if(!t)return;
  t.content=t.clean;t.dirty=false;edText.value=t.clean;renderTabs();render();
  edStatus.innerHTML='<span class="muted">reverted '+t.path+'</span>';};
edText.addEventListener('input',()=>{const t=activeTab();if(t){t.content=edText.value;
  const d=t.content!==t.clean; if(d!==t.dirty){t.dirty=d;renderTabs();}} render();});
edText.addEventListener('scroll',()=>{edHLpre.scrollTop=edText.scrollTop;edHLpre.scrollLeft=edText.scrollLeft;
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';});
edText.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveActive();}
  if((e.ctrlKey||e.metaKey)&&(e.key==='f'||e.key==='F')){e.preventDefault();openFind();}
  if(e.key==='Tab'){e.preventDefault();const s=edText.selectionStart,en=edText.selectionEnd;
    edText.value=edText.value.slice(0,s)+'    '+edText.value.slice(en);
    edText.selectionStart=edText.selectionEnd=s+4;edText.dispatchEvent(new Event('input'));}
});

// ---- find within the editor (Ctrl/Cmd+F) ----
const edFindBar=document.getElementById('edFindBar'), edFindInput=document.getElementById('edFindInput'),
      edFindCnt=document.getElementById('edFindCnt');
let findMatches=[], findIdx=-1;
function openFind(){
  edFindBar.classList.add('on');
  const sel=edText.value.substring(edText.selectionStart,edText.selectionEnd);
  if(sel && sel.length<60 && !sel.includes('\n')) edFindInput.value=sel;
  edFindInput.focus(); edFindInput.select(); runFind();
}
function closeFind(){ edFindBar.classList.remove('on'); edText.focus(); }
function runFind(){
  const q=edFindInput.value; findMatches=[]; findIdx=-1;
  if(q){ const hay=edText.value.toLowerCase(), needle=q.toLowerCase();
    let i=hay.indexOf(needle);
    while(i!==-1){ findMatches.push(i); i=hay.indexOf(needle, i+Math.max(1,needle.length)); } }
  if(findMatches.length){ findIdx=0; jumpFind(); }
  else { edFindCnt.textContent=q?'0/0':'0/0'; }
}
function jumpFind(){
  if(findIdx<0||!findMatches.length) return;
  const start=findMatches[findIdx], end=start+edFindInput.value.length;
  edText.setSelectionRange(start,end); // visible (greyed) without stealing focus from the find box
  const line=(edText.value.slice(0,start).match(/\n/g)||[]).length;
  edText.scrollTop=Math.max(0, line*LINE_H - edText.clientHeight/2);
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';
  edHLpre.scrollTop=edText.scrollTop;
  edFindCnt.textContent=(findIdx+1)+'/'+findMatches.length;
}
function nextFind(d){ if(!findMatches.length)return; findIdx=(findIdx+d+findMatches.length)%findMatches.length; jumpFind(); }
edFindInput.addEventListener('input', runFind);
edFindInput.addEventListener('keydown', e=>{
  if(e.key==='Enter'){ e.preventDefault(); nextFind(e.shiftKey?-1:1); }
  if(e.key==='Escape'){ e.preventDefault(); closeFind(); }
});
document.getElementById('edFindNext').onclick=()=>nextFind(1);
document.getElementById('edFindPrev').onclick=()=>nextFind(-1);
document.getElementById('edFindClose').onclick=closeFind;

// ---- replace ----
const edReplaceInput=document.getElementById('edReplaceInput');
function replaceOne(){
  if(findIdx<0||!findMatches.length||!edFindInput.value)return;
  const start=findMatches[findIdx], end=start+edFindInput.value.length;
  edText.value=edText.value.slice(0,start)+edReplaceInput.value+edText.value.slice(end);
  edText.dispatchEvent(new Event('input'));   // updates tab/dirty + re-highlight
  runFind();                                  // recompute (jumps to first remaining)
}
function replaceAll(){
  if(!edFindInput.value||!findMatches.length)return;
  let v=edText.value; const flen=edFindInput.value.length, rep=edReplaceInput.value, n=findMatches.length;
  for(let i=findMatches.length-1;i>=0;i--){const s=findMatches[i]; v=v.slice(0,s)+rep+v.slice(s+flen);}
  edText.value=v; edText.dispatchEvent(new Event('input'));
  runFind(); edFindCnt.textContent='replaced '+n;
}
document.getElementById('edReplaceOne').onclick=replaceOne;
document.getElementById('edReplaceAll').onclick=replaceAll;
edReplaceInput.addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();replaceOne();}
  if(e.key==='Escape'){e.preventDefault();closeFind();}
});
// re-read open, unmodified files after the agent edits them on disk
async function refreshOpen(){
  for(const t of tabs){ if(t.dirty)continue;
    const r=await fetch('/api/fs/read',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({path:t.path})});
    const d=await r.json(); if(!d.error){t.content=d.content;t.clean=d.content;}
  }
  if(active>=0){edText.value=tabs[active].content;render();}
}
window.__edRefresh=refreshOpen;
loadTree();

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
