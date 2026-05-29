"""A zero-dependency web dashboard for the AIO agent.

Serves a single-page dashboard and a small JSON API on top of the same
:class:`aio.agent.Agent` used by the CLI. Built entirely on the Python standard
library (``http.server``). Tool approvals are auto-granted in web mode (there is
no interactive terminal), so it is intended for local/trusted use.
"""

from __future__ import annotations

import difflib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .agent import Agent
from .config import Config
from .providers import ProviderError, build_provider
from .tools import ToolContext, default_registry


class EventUI:
    """A UI implementation that records events instead of printing them.

    Implements the same surface the agent and tools call, so it can be dropped
    in wherever a terminal :class:`aio.ui.UI` is expected.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def drain(self) -> list[dict[str, Any]]:
        out = self.events
        self.events = []
        return out

    # -- methods used by the agent / tools --------------------------------
    def banner(self, *_a, **_k) -> None:  # no-op in web mode
        pass

    def info(self, text: str) -> None:
        self.events.append({"type": "info", "text": text})

    def warn(self, text: str) -> None:
        self.events.append({"type": "warn", "text": text})

    def error(self, text: str) -> None:
        self.events.append({"type": "error", "text": text})

    def thinking(self, text: str = "thinking…") -> None:
        self.events.append({"type": "thinking", "text": text})

    def assistant(self, text: str) -> None:
        self.events.append({"type": "assistant", "text": text})

    def tool_call(self, name: str, args: dict) -> None:
        self.events.append({"type": "tool_call", "name": name, "args": args})

    def tool_result(self, text: str, error: bool = False) -> None:
        self.events.append({"type": "tool_result", "text": text, "error": error})

    def show_diff(self, old: str, new: str, path: str) -> None:
        if old == new:
            return
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(), new.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
            )
        )
        self.events.append({"type": "diff", "path": path, "diff": diff})

    def confirm(self, name: str, args: dict) -> str:
        # No interactive prompt available over HTTP; auto-approve.
        self.tool_call(name, args)
        return "yes"


class AgentService:
    """Thread-safe wrapper around an Agent for the web server."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.ui = EventUI()
        self._lock = threading.Lock()
        self._build_agent()

    def _build_agent(self) -> None:
        ctx = ToolContext(
            workdir=self.config.workdir,
            ui=self.ui,
            auto_approve=True,  # web mode auto-approves tool calls
            allow_outside_workdir=self.config.allow_outside_workdir,
        )
        self.agent = Agent(
            provider=build_provider(self.config),
            tools=default_registry(),
            ctx=ctx,
            ui=self.ui,
            system_prompt=self.config.system_prompt,
            max_steps=self.config.max_steps,
        )

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "model": self.config.active.model,
            "workdir": str(self.config.workdir),
            "tools": [
                {"name": t.name, "description": t.description.splitlines()[0]}
                for t in self.agent.tools
            ],
            "history": len(self.agent.messages),
        }

    def chat(self, message: str) -> dict[str, Any]:
        with self._lock:
            self.ui.drain()
            try:
                final = self.agent.run(message)
            except ProviderError as exc:
                self.ui.error(str(exc))
                final = ""
            return {"events": self.ui.drain(), "final": final}

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self.agent.reset()
            return {"ok": True}

    def configure(self, provider: str | None, model: str | None) -> dict[str, Any]:
        with self._lock:
            from .config import PROVIDER_DEFAULTS

            if provider:
                if provider not in PROVIDER_DEFAULTS:
                    raise ProviderError(f"unknown provider '{provider}'")
                self.config.provider = provider
            if model:
                self.config.active.model = model
            self._build_agent()
            return self.info()


def _make_handler(service: AgentService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # silence default stderr logging
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/info":
                self._json(200, service.info())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            try:
                payload = self._read_json()
                if self.path == "/api/chat":
                    self._json(200, service.chat(payload.get("message", "")))
                elif self.path == "/api/reset":
                    self._json(200, service.reset())
                elif self.path == "/api/config":
                    self._json(200, service.configure(payload.get("provider"), payload.get("model")))
                else:
                    self._json(404, {"error": "not found"})
            except ProviderError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(config: Config, host: str = "127.0.0.1", port: int = 8765) -> None:
    service = AgentService(config)
    httpd = ThreadingHTTPServer((host, port), _make_handler(service))
    url = f"http://{host}:{port}"
    print(f"AIO web dashboard running at {url}")
    print(f"provider={config.provider}  model={config.active.model}  workdir={config.workdir}")
    print("tool calls are auto-approved in web mode. Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        httpd.server_close()


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AIO — Coding Agent Dashboard</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
        --accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--mag:#bc8cff;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column}
  header{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--border);
         background:var(--panel)}
  header h1{font-size:16px;margin:0;letter-spacing:.5px}
  header h1 .tag{color:var(--accent)}
  .badges{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:center}
  select,input,button{background:#0d1117;color:var(--text);border:1px solid var(--border);
        border-radius:6px;padding:6px 10px;font-size:13px}
  button{cursor:pointer}
  button:hover{border-color:var(--accent)}
  .layout{display:flex;flex:1;min-height:0}
  aside{width:240px;border-right:1px solid var(--border);padding:14px;overflow:auto;background:var(--panel)}
  aside h3{font-size:12px;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
  .tool{padding:6px 8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px}
  .tool b{color:var(--mag)}
  .tool small{color:var(--muted);display:block}
  main{flex:1;display:flex;flex-direction:column;min-width:0}
  #log{flex:1;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:12px}
  .msg{max-width:80%;padding:10px 14px;border-radius:10px;white-space:pre-wrap;word-wrap:break-word}
  .user{align-self:flex-end;background:#1f6feb33;border:1px solid #1f6feb55}
  .assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--border)}
  .event{align-self:flex-start;max-width:80%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         font-size:12.5px;border-radius:8px;border:1px solid var(--border);overflow:hidden}
  .event .head{padding:6px 10px;background:#1c2128;color:var(--accent)}
  .event .body{padding:8px 10px;white-space:pre-wrap;color:var(--muted);max-height:260px;overflow:auto}
  .event.err .head{color:var(--red)}
  .diff .add{color:var(--green)} .diff .del{color:var(--red)} .diff .hunk{color:var(--accent)}
  .thinking{color:var(--muted);font-style:italic;align-self:flex-start}
  footer{border-top:1px solid var(--border);padding:12px;display:flex;gap:10px;background:var(--panel)}
  #input{flex:1;resize:none;height:46px;font-size:14px}
  #send{padding:0 22px;background:#238636;border-color:#2ea043;font-weight:600}
  #send:disabled{opacity:.5;cursor:not-allowed}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px}
</style>
</head>
<body>
<header>
  <h1>AIO <span class="tag">●</span> coding agent</h1>
  <div class="badges">
    <span><span class="dot"></span><span id="provider">…</span> / <span id="model">…</span></span>
    <select id="providerSel" title="switch provider">
      <option>anthropic</option><option>openai</option>
      <option>openrouter</option><option>ollama</option>
    </select>
    <input id="modelInput" placeholder="model name" size="18"/>
    <button id="apply">Apply</button>
    <button id="reset">Clear</button>
  </div>
</header>
<div class="layout">
  <aside>
    <h3>Working dir</h3>
    <div id="workdir" style="color:var(--muted);word-break:break-all;margin-bottom:16px"></div>
    <h3>Tools</h3>
    <div id="tools"></div>
  </aside>
  <main>
    <div id="log"></div>
    <footer>
      <textarea id="input" placeholder="Ask the agent to do something… (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send">Send</button>
    </footer>
  </main>
</div>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const send = document.getElementById('send');

function el(cls, text){const d=document.createElement('div');d.className=cls;if(text!=null)d.textContent=text;return d;}
function scroll(){log.scrollTop = log.scrollHeight;}

function addMsg(role, text){ if(!text) return; const d=el('msg '+role, text); log.appendChild(d); scroll(); }
function addThinking(t){ const d=el('thinking', t||'thinking…'); log.appendChild(d); scroll(); return d; }

function addEvent(ev){
  if(ev.type==='assistant'){ addMsg('assistant', ev.text); return; }
  if(ev.type==='thinking'){ return; }
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
}

async function sendMsg(){
  const text=input.value.trim(); if(!text) return;
  addMsg('user', text); input.value=''; send.disabled=true;
  const think=addThinking();
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text})});
    const d=await r.json(); think.remove();
    (d.events||[]).forEach(addEvent);
    if(d.error){ addEvent({type:'error',text:d.error}); }
  }catch(e){ think.remove(); addEvent({type:'error',text:String(e)}); }
  send.disabled=false; input.focus();
}

send.onclick=sendMsg;
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();}});
document.getElementById('reset').onclick=async()=>{await fetch('/api/reset',{method:'POST'});log.innerHTML='';loadInfo();};
document.getElementById('apply').onclick=async()=>{
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({provider:document.getElementById('providerSel').value,
      model:document.getElementById('modelInput').value})});
  loadInfo();
};
loadInfo();
</script>
</body>
</html>
"""
