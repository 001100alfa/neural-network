// Dashboard side-panels: Terminal (cmd/bash), Git, and the static web
// server. Self-initialising ES module imported by app.js for side effects;
// fully standalone (own DOM refs + fetch handlers, no shared state).

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
function gitBranchOp(action){
  const name=document.getElementById('gitBranch').value.trim();
  if(!name){renderGit('enter a branch name first.');return;}
  git(action,{name});
}
document.getElementById('gitSwitch').onclick=()=>gitBranchOp('switch');
document.getElementById('gitNewBranch').onclick=()=>gitBranchOp('new_branch');

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
