# AIO — All-in-One Open-Source Coding Agent

A free, open-source terminal coding agent — a Claude Code / Aider / OpenCode
style assistant — that speaks to **many model providers behind one interface**:

**10 global providers** out of the box (manage them in the dashboard's
**Providers** tab, or via env vars):

| Provider | Default model | Key env |
|----------|---------------|---------|
| **Anthropic** | claude-sonnet-4-6 | `ANTHROPIC_API_KEY` |
| **OpenAI** | gpt-4o | `OPENAI_API_KEY` |
| **Google Gemini** | gemini-2.0-flash | `GEMINI_API_KEY` |
| **Groq** | llama-3.3-70b-versatile | `GROQ_API_KEY` |
| **Mistral AI** | mistral-large-latest | `MISTRAL_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **xAI (Grok)** | grok-2-latest | `XAI_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| **OpenRouter** | one key, hundreds of models | `OPENROUTER_API_KEY` |
| **Ollama** | local Llama / Qwen / DeepSeek — 100% free & offline | _none_ |

It bundles everything you need: a real agent loop, file read/write/edit tools,
filename & content search, a shell tool, git tools, an approval workflow with
diff previews, config files, and optional
[MCP](https://modelcontextprotocol.io) server integration — **with zero
third-party runtime dependencies** (pure Python standard library).

### One agent, three interfaces ("all-in-one")

The same agent and tools are exposed through every surface a coding assistant
is expected to live in:

| Surface | Category | How |
|---------|----------|-----|
| **Terminal / CLI** | terminal coding agent | `aio "…"` or the `aio` REPL |
| **Web dashboard** | browser app with built-in **IDE/editor** | `aio --web` |
| **VS Code extension** | IDE / editor-integrated | [`editor/vscode/`](editor/vscode/) |

## Install

```bash
git clone https://github.com/001100alfa/neural-network.git
cd neural-network
pip install -e .
```

Requires Python **3.11+**.

## Portable — run on Windows 11 (no install)

Because AIO has **zero runtime dependencies** (pure Python standard library), it
runs straight from the folder — no `pip install`, no virtualenv. You only need
**Python 3.11+** on the machine.

1. Copy the project folder anywhere (USB stick, Desktop, …).
2. Double-click **`run.bat`**.

It launches the local web dashboard and opens your browser at
**http://localhost:8765**. `Ctrl+C` (or closing the window) stops it.

- PowerShell alternative: right-click **`run.ps1`** → *Run with PowerShell*
  (or `powershell -ExecutionPolicy Bypass -File .\run.ps1`).
- macOS/Linux: `./run.sh`
- Pass extra options through the launcher, e.g. a different port, model or
  working directory:

  ```bat
  run.bat --port 9000 -p ollama -C C:\path\to\your\project
  ```

Under the hood the launcher just sets `PYTHONPATH=src` and runs
`python -m aio --web --open`, so nothing is written outside the folder.

### Fully self-contained (no system Python at all)

To ship a bundle that includes its own Python (so it runs on a clean
Windows 11 machine with nothing installed):

1. Double-click **`setup-embedded.bat`** once (needs internet). It downloads the
   official Windows *embeddable* Python into a local **`python\`** folder and
   wires it to the project's `src\`.
2. From then on, **`run.bat`** automatically uses `python\` — no system Python
   required. Copy the whole folder anywhere (USB, another PC) and it just runs,
   even offline.

```bat
setup-embedded.bat                 :: one-time, downloads python\
setup-embedded.bat 3.12.7 amd64    :: pin a specific version / arch
run.bat                            :: uses the bundled python\
```

`run.bat` resolves Python in this order: bundled **`python\`** → the `py`
launcher → `python` on `PATH`. The `python\` folder is git-ignored (it's a
generated runtime, not source).

## Quick start

```bash
# Pick a provider via an API key (auto-detected), or run fully local with Ollama.
export ANTHROPIC_API_KEY=sk-ant-...

# One-shot
aio "add type hints to utils.py and run the tests"

# Interactive REPL
aio

# Browser dashboard (web UI)
aio --web                 # then open http://127.0.0.1:8765

# 100% free & offline with a local model (needs `ollama serve` running)
aio -p ollama -m qwen2.5-coder "explain what this repo does"
```

If no API key is found, AIO falls back to local **Ollama** automatically.

## Usage

```
aio [options] [prompt]

  -p, --provider {anthropic,openai,openrouter,ollama}
  -m, --model NAME        model to use (overrides config)
  -C, --workdir DIR       working directory (default: cwd)
      --config FILE       path to a config TOML
  -y, --yes               auto-approve all tool calls
      --allow-outside     permit file access outside the working dir
      --max-steps N       max agent steps per turn (default 50)
      --no-mcp            don't start configured MCP servers
      --web               launch the browser dashboard instead of the CLI
      --host HOST         web dashboard host (default 127.0.0.1)
      --port PORT         web dashboard port (default 8765)
      --no-color          plain output
      --list-tools        list tools and exit
      --version
```

## Web dashboard

```bash
aio --web --port 8765
# open http://127.0.0.1:8765
```

A single-page dashboard (served by the Python stdlib — no JS build step, no
extra dependencies) that drives the same agent:

- chat with the agent and watch it **stream live, token-by-token**: the reply
  types out word-by-word, and tool calls, tool results and **colour-coded
  diffs** appear as they happen
- **attach images** (📎) and ask about them — multimodal, sent to vision-capable
  models as image content
- **multiple conversation tabs** — run several independent chats at once; each
  keeps its own history (switch / new / close)
- **export / import** a conversation as **Markdown** or **JSON** (import opens
  it in a new tab)
- **search** across saved conversations (title + message text) with snippets
- **drag-and-drop** images onto the chat or **paste** them from the clipboard
- **keyboard shortcuts** — Enter / `Ctrl+Enter` send, `Alt+N` new chat,
  `Alt+W` close chat, `Ctrl/Cmd+,` settings, `Esc` close popovers
- **slash commands** in the input — `/help`, `/new`, `/clear`, `/save`,
  `/export [md|json]`, `/provider <name>`, `/model <name>`, `/theme [light|dark]`
- a **settings** popover (⚙): **light / dark** theme and adjustable **font
  size**, persisted in your browser
- **save / load conversations** — "Save chat" stores the session to disk
  (`~/.config/aio/sessions/`, `AIO_SESSIONS_DIR` to override); pick any saved
  session from the dropdown to reload its full history
- sidebar listing the available tools and the working directory
- switch **provider/model** on the fly and clear the conversation
- a **Providers tab** — a panel for all **10 AI providers**: enter/replace API
  keys (stored locally, `chmod 600`, masked in the UI and never returned by the
  API), set each one's model and base URL, and pick the active provider with one
  click. Keys persist across restarts in `~/.config/aio/keys.json`
  (`AIO_KEYS_FILE` to override). Each card has a **Test connection** button that
  validates the key and **fetches the provider's live model list** (offered as
  autocomplete on the model field). A **usage / cost indicator** shows session
  requests, input/output tokens and an estimated USD cost. Each provider also
  takes an optional **monthly budget ($)** — the card shows month-to-date spend
  and warns when nearing or over the cap.
- an **MCP tab** — add / remove / restart [MCP](https://modelcontextprotocol.io)
  stdio servers from the dashboard; their tools are registered with the agent
  live (also configurable in `.aio.toml`). Ships with **11 open-source servers
  configured out of the box** (filesystem, fetch, memory, sequential-thinking,
  everything, sqlite, postgres, playwright, context7, time, brave-search) —
  listed but **not auto-started** (so launching never spawns 11 processes); hit
  **Start** on the ones you want. A built-in **catalog** (incl. git, github, …)
  lets you add more with "Use"
- a **tools panel** with these tabs that work independently of the model:
  - **Editor** — an in-browser IDE: file tree, **multi-file tabs** (with
    unsaved-change indicators), **line numbers**, in-editor **find & replace**
    (`Ctrl+F`, next/prev + match count, Replace / Replace-all), and
    **syntax highlighting**
    (Python, JS/TS, JSON, HTML, CSS, shell, Markdown — all zero-dependency),
    plus Save / Revert / `Ctrl+S` / `Tab`-indent, sandboxed to the working
    directory. It auto-refreshes open files when the agent edits them.
  - **Terminal** — run `cmd` / `bash` / `sh` commands directly in the working
    directory and see stdout/stderr + exit code
  - **Git** — one-click `status` / `diff` / `staged` / `log` / `add`, plus a
    commit box, with colour-coded diff output
  - **Web server** — start/stop a static file server that serves the working
    directory (handy for previewing built sites), with a clickable link
- small JSON API: `GET /api/info`, `GET|POST /api/providers`,
  `POST /api/providers/test`, `GET /api/usage`, `POST /api/chat`,
  `GET|POST /api/chat/stream` (SSE; POST carries images + conversation id),
  `GET /api/sessions`, `POST /api/sessions/{save,load,delete,export,import,search}`,
  `GET /api/mcp`, `POST /api/mcp/{add,remove,restart}`,
  `POST /api/conversation/close`, `POST /api/reset`, `POST /api/config`,
  `POST /api/exec`, `POST /api/git`, `POST /api/server`,
  `POST /api/fs/{tree,read,write}`

> In web mode tool calls are **auto-approved** (there is no terminal to prompt),
> so run it locally against projects you trust. It binds to `127.0.0.1` by default.

### REPL commands

```
/help              show help
/provider <name>   switch provider on the fly
/model <name>      switch model
/tools             list available tools
/clear             clear conversation history
/yes               toggle auto-approve
/exit              quit
```

## Tools

| Tool | Purpose | Approval |
|------|---------|----------|
| `read_file`  | read a file with line numbers | auto |
| `write_file` | create / overwrite a file (shows a diff) | ✅ confirm |
| `edit_file`  | exact-string replacement (shows a diff) | ✅ confirm |
| `list_dir`   | list a directory | auto |
| `glob`       | find files by pattern (`**/*.py`) | auto |
| `grep`       | regex search across files | auto |
| `run_shell`  | run a shell command | ✅ confirm |
| `git_status` / `git_diff` | inspect the repo | auto |
| `git_commit` | stage & commit | ✅ confirm |

Mutating tools ask for approval before running (`y` / `n` / `a`=always). Use
`-y` / `--yes` or `/yes` to auto-approve. File tools are sandboxed to the
working directory unless `--allow-outside` is set.

## Configuration

Copy [`.aio.toml.example`](.aio.toml.example) to `.aio.toml` (git-ignored) in
your project, or to `~/.config/aio/config.toml` for global settings.

```toml
provider = "anthropic"
auto_approve = false
max_steps = 50

[providers.anthropic]
model = "claude-sonnet-4-6"

[providers.ollama]
model = "qwen2.5-coder"
base_url = "http://localhost:11434/v1"
```

Precedence: **CLI flags → env vars → `./.aio.toml` → `~/.config/aio/config.toml` → defaults**.

### MCP servers

Any [MCP](https://modelcontextprotocol.io) stdio server can be plugged in; its
tools become available to the agent as `<server>__<tool>`:

```toml
[[mcp.servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

## Architecture

```
src/aio/
├── cli.py            # argument parsing + interactive REPL
├── agent.py          # the model<->tools loop
├── config.py         # layered TOML/env/flag configuration
├── ui.py             # colours, diffs, approval prompts
├── web.py            # zero-dependency browser dashboard + JSON API
├── mcp.py            # minimal MCP stdio JSON-RPC client
├── providers/        # unified provider interface
│   ├── base.py       #   Message / ToolCall / AssistantTurn + HTTP
│   ├── anthropic.py  #   native Messages API
│   └── openai_compat.py  # OpenAI / OpenRouter / Ollama
└── tools/            # read, write, edit, glob, grep, shell, git

editor/
└── vscode/           # VS Code extension (IDE/editor integration)
```

The agent normalises every provider to a common `Message`/`ToolCall` shape, so
adding a new backend or tool is a small, self-contained change.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite (no network required) covers tool behaviour, config precedence,
and provider request-building / response-parsing.

CI runs the linter + tests on every push/PR. Pushing a `vX.Y.Z` tag triggers
the **release** workflow, which builds the wheel + sdist and a portable zip and
publishes them as a GitHub Release:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## License

MIT
