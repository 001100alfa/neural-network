# AIO — All-in-One Open-Source Coding Agent

A free, open-source terminal coding agent — a Claude Code / Aider / OpenCode
style assistant — that speaks to **many model providers behind one interface**:

| Provider | Models | Key |
|----------|--------|-----|
| **Anthropic** | Claude (Opus / Sonnet / Haiku) | `ANTHROPIC_API_KEY` |
| **OpenAI** | GPT-4o / GPT-4.x etc. | `OPENAI_API_KEY` |
| **OpenRouter** | one key, hundreds of models | `OPENROUTER_API_KEY` |
| **Ollama** | local Llama / Qwen / DeepSeek — 100% free & offline | _none_ |

It bundles everything you need into a single CLI: a real agent loop, file
read/write/edit tools, filename & content search, a shell tool, git tools, an
approval workflow with diff previews, config files, and optional
[MCP](https://modelcontextprotocol.io) server integration — **with zero
third-party runtime dependencies** (pure Python standard library).

## Install

```bash
git clone https://github.com/001100alfa/neural-network.git
cd neural-network
pip install -e .
```

Requires Python **3.11+**.

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

- chat with the agent and watch each step stream in: assistant messages, tool
  calls, tool results, and **colour-coded diffs** for every file edit
- sidebar listing the available tools and the working directory
- switch **provider/model** on the fly and clear the conversation
- small JSON API: `GET /api/info`, `POST /api/chat`, `POST /api/reset`,
  `POST /api/config`

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

## License

MIT
