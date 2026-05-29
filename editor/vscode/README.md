# AIO Coding Agent — VS Code extension

Editor integration for the [AIO](../../README.md) all-in-one coding agent. It
drives the same `aio` CLI that ships with this repository, so the terminal
agent, the web dashboard, and the editor all share one implementation.

## Commands

| Command | What it does |
|---------|--------------|
| **AIO: Ask the agent…** | prompt for a task and run it via `aio` in an integrated terminal |
| **AIO: Ask about the selection** | right-click in the editor to send the selected code as context |
| **AIO: Open web dashboard** | launch `aio --web` and embed the dashboard in a side webview |

## Requirements

The `aio` CLI must be installed and on your `PATH`:

```bash
pip install -e .        # from the repo root
```

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `aio.command` | `aio` | the executable to invoke |
| `aio.provider` | _(auto)_ | `anthropic` / `openai` / `openrouter` / `ollama` |
| `aio.dashboardPort` | `8765` | port for the embedded dashboard |

## Run it locally (development)

1. `npm install -g @vscode/vsce` (only needed to package a `.vsix`)
2. Open this `editor/vscode` folder in VS Code and press **F5** to launch an
   Extension Development Host.
3. Run any **AIO:** command from the Command Palette.
