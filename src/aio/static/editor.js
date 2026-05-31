// In-browser editor (IDE): multi-file tabs, syntax highlighting, find/replace.
// Self-initialising ES module imported by app.js for its side effects; it
// exposes window.__edReloadTree / __edRefresh so the chat code can refresh
// open files after the agent edits them on disk.
import { highlight, langFor, esc } from './util.js';

// ---- Editor (in-browser IDE) : syntax highlighting + multi-file tabs ----
const edFile=document.getElementById('edFile'), edText=document.getElementById('edText'),
      edStatus=document.getElementById('edStatus'), edHL=document.getElementById('edHL'),
      edHLpre=document.getElementById('edHLpre'), edTabs=document.getElementById('edTabs'),
      edNums=document.getElementById('edNums');
const LINE_H=18.75; // 12.5px font * 1.5 line-height

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
window.__edReloadTree=loadTree; window.__edRefresh=refreshOpen;
loadTree();
