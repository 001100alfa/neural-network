"""Command-line entry point and interactive REPL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import PROVIDER_DEFAULTS, load_config
from .mcp import load_mcp_tools
from .providers import ProviderError, build_provider
from .tools import ToolContext, default_registry
from .ui import UI

SLASH_HELP = """\
Commands:
  /help              show this help
  /provider <name>   switch provider (anthropic, openai, openrouter, ollama)
  /model <name>      switch the model for the current provider
  /tools             list available tools
  /clear             clear the conversation history
  /yes               toggle auto-approve for tool calls
  /exit, /quit       leave
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aio",
        description="All-in-one open-source terminal coding agent "
        "(Anthropic / OpenAI / OpenRouter / Ollama).",
    )
    p.add_argument("prompt", nargs="*", help="Prompt to run once (omit for interactive mode).")
    p.add_argument("-p", "--provider", choices=list(PROVIDER_DEFAULTS), help="Model provider.")
    p.add_argument("-m", "--model", help="Model name (overrides config).")
    p.add_argument("-C", "--workdir", default=".", help="Working directory (default: cwd).")
    p.add_argument("--config", help="Path to a config TOML file.")
    p.add_argument("-y", "--yes", action="store_true", help="Auto-approve all tool calls.")
    p.add_argument("--allow-outside", action="store_true", help="Allow file access outside the workdir.")
    p.add_argument("--max-steps", type=int, help="Max agent steps per turn.")
    p.add_argument("--no-color", action="store_true", help="Disable coloured output.")
    p.add_argument("--no-mcp", action="store_true", help="Do not start configured MCP servers.")
    p.add_argument("--web", action="store_true", help="Launch the browser dashboard instead of the CLI.")
    p.add_argument("--host", default="127.0.0.1", help="Web dashboard host (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=8765, help="Web dashboard port (default 8765).")
    p.add_argument("--open", action="store_true", help="Open the dashboard in the default browser (with --web).")
    p.add_argument("--web-token", default=None,
                   help="Use this access token for the dashboard instead of a random one.")
    p.add_argument("--web-no-auth", action="store_true",
                   help="Disable dashboard authentication (UNSAFE; only on a trusted, isolated host).")
    p.add_argument("--web-auto-approve", action="store_true",
                   help="Auto-approve all tool calls in the dashboard (UNSAFE; skips the approval gate).")
    p.add_argument("--plan", action="store_true", help="Plan mode: read-only; produce a plan, change nothing.")
    p.add_argument("-c", "--continue", dest="cont", action="store_true",
                   help="Resume the most recent CLI conversation.")
    p.add_argument("--output-format", choices=["text", "json"], default="text",
                   help="One-shot output format (json = headless, structured result).")
    p.add_argument("--diff-approve", action="store_true",
                   help="Approve file edits hunk-by-hunk (interactive).")
    p.add_argument("--tui", action="store_true",
                   help="Full-screen curses TUI for the interactive session.")
    p.add_argument("--vim", action="store_true",
                   help="With --tui, start in vim NORMAL mode (modal editing).")
    p.add_argument("--list-tools", action="store_true", help="List tools and exit.")
    p.add_argument("--version", action="version", version=f"aio {__version__}")
    return p


def _make_agent(config, ui: UI, no_mcp: bool, plan_mode: bool = False, per_hunk: bool = False):
    from .hooks import HookRunner

    provider = build_provider(config)
    registry = default_registry()

    mcp_servers: list = []
    if not no_mcp and config.mcp_servers:
        mcp_tools, mcp_servers = load_mcp_tools(config.mcp_servers, ui=ui)
        for t in mcp_tools:
            registry.register(t)

    ctx = ToolContext(
        workdir=config.workdir,
        ui=ui,
        auto_approve=config.auto_approve,
        allow_outside_workdir=config.allow_outside_workdir,
        permissions=dict(config.permissions),
        per_hunk=per_hunk,
    )
    system_prompt = config.system_prompt
    if config.project_map:
        system_prompt += "\n\n# Project map (auto-generated)\n" + config.project_map
    if config.project_memory:
        system_prompt += "\n\n# Project memory (CLAUDE.md / AGENTS.md)\n" + config.project_memory
    agent = Agent(
        provider=provider,
        tools=registry,
        ctx=ctx,
        ui=ui,
        system_prompt=system_prompt,
        max_steps=config.max_steps,
        plan_mode=plan_mode,
        hooks=HookRunner(config.hooks, config.workdir),
        auto_context=config.auto_context,
        auto_context_k=config.auto_context_results,
    )
    return agent, mcp_servers


def _handle_slash(cmd: str, agent: Agent, config, ui: UI) -> bool:
    """Handle a /command. Returns False to signal exit."""

    parts = cmd.strip().split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name in ("/exit", "/quit"):
        return False
    if name == "/help":
        ui.info(SLASH_HELP)
    elif name == "/tools":
        ui.info("\n".join(f"  {t.name:<16} {t.description.splitlines()[0]}" for t in agent.tools))
    elif name == "/clear":
        agent.reset()
        ui.info("conversation cleared.")
    elif name == "/yes":
        agent.ctx.auto_approve = not agent.ctx.auto_approve
        ui.info(f"auto-approve {'on' if agent.ctx.auto_approve else 'off'}.")
    elif name == "/provider":
        if arg not in PROVIDER_DEFAULTS:
            ui.warn(f"unknown provider. choose from: {', '.join(PROVIDER_DEFAULTS)}")
        else:
            config.provider = arg
            try:
                agent.provider = build_provider(config)
                ui.info(f"provider -> {arg} (model={config.active.model})")
            except ProviderError as exc:
                ui.error(str(exc))
    elif name == "/model":
        if not arg:
            ui.info(f"current model: {config.active.model}")
        else:
            config.active.model = arg
            agent.provider = build_provider(config)
            ui.info(f"model -> {arg}")
    else:
        ui.warn(f"unknown command: {name} (try /help)")
    return True


def repl(agent: Agent, config, ui: UI) -> int:
    ui.banner(config.provider, config.active.model)
    while True:
        try:
            line = input(ui._c("› ", "\033[36m"))
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        if line.startswith("/"):
            if not _handle_slash(line, agent, config, ui):
                break
            continue
        try:
            agent.run(line)
        except ProviderError as exc:
            ui.error(str(exc))
        except KeyboardInterrupt:
            ui.warn("\n(interrupted)")
        print()
    return 0


def _enable_windows_ansi() -> None:
    """Enable ANSI escape processing on Windows 10/11 consoles."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11; ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover - best effort, never fatal
        pass


def main(argv: list[str] | None = None) -> int:
    _enable_windows_ansi()
    args = build_parser().parse_args(argv)

    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.yes:
        overrides["auto_approve"] = True
    if args.allow_outside:
        overrides["allow_outside_workdir"] = True
    if args.max_steps is not None:
        overrides["max_steps"] = args.max_steps

    try:
        config = load_config(
            workdir=Path(args.workdir),
            overrides=overrides,
            config_path=Path(args.config) if args.config else None,
        )
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    ui = UI(color=False if args.no_color else None, quiet=False)

    if args.list_tools:
        registry = default_registry()
        for t in registry:
            print(f"{t.name:<16} {t.description.splitlines()[0]}")
        return 0

    if args.web:
        from .web import serve

        serve(config, host=args.host, port=args.port, open_browser=args.open,
              token=args.web_token, require_auth=not args.web_no_auth,
              auto_approve=args.web_auto_approve)
        return 0

    agent, mcp_servers = _make_agent(config, ui, no_mcp=args.no_mcp, plan_mode=args.plan,
                                     per_hunk=args.diff_approve)

    if args.cont:
        n = _load_cli_session(agent)
        ui.info(f"resumed previous conversation ({n} messages).") if n else \
            ui.warn("no previous conversation to resume.")

    try:
        if args.prompt:
            prompt = " ".join(args.prompt)
            if args.output_format == "json":
                return _run_headless_json(agent, prompt)
            try:
                agent.run(prompt)
            except ProviderError as exc:
                ui.error(str(exc))
                return 1
            _save_cli_session(agent)
            return 0
        if args.tui:
            from .tui import run_tui
            rc = run_tui(agent, config, vim=args.vim)
        else:
            rc = repl(agent, config, ui)
        _save_cli_session(agent)
        return rc
    finally:
        for server in mcp_servers:
            server.stop()


def _run_headless_json(agent, prompt: str) -> int:
    """One-shot run that prints a single structured JSON result (headless mode)."""
    import json

    from .web import EventUI

    ui = EventUI()          # records events instead of printing
    agent.ui = ui
    agent.ctx.ui = ui
    agent.stream = False
    error = None
    try:
        final = agent.run(prompt)
    except ProviderError as exc:
        final, error = "", str(exc)
    _save_cli_session(agent)
    result = {
        "ok": error is None,
        "result": final,
        "error": error,
        "usage": agent.run_usage,
        "events": ui.events,
    }
    print(json.dumps(result, indent=2))
    return 0 if error is None else 1


def _cli_session_path() -> "Path":
    import os
    from pathlib import Path

    env = os.environ.get("AIO_SESSIONS_DIR")
    d = Path(env) if env else (Path.home() / ".config" / "aio" / "sessions")
    d.mkdir(parents=True, exist_ok=True)
    return d / "cli-last.json"


def _save_cli_session(agent) -> None:
    import json

    try:
        data = [
            {"role": m.role, "content": m.content,
             "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                            for c in m.tool_calls],
             "tool_call_id": m.tool_call_id, "name": m.name}
            for m in agent.messages
        ]
        _cli_session_path().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _load_cli_session(agent) -> int:
    import json

    from .providers import Message, ToolCall

    p = _cli_session_path()
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    agent.messages = [
        Message(
            role=d.get("role", "user"), content=d.get("content", "") or "",
            tool_calls=[ToolCall(id=c.get("id", ""), name=c.get("name", ""),
                                 arguments=c.get("arguments", {}) or {})
                        for c in d.get("tool_calls", []) or []],
            tool_call_id=d.get("tool_call_id"), name=d.get("name"),
        )
        for d in data
    ]
    return len(agent.messages)


if __name__ == "__main__":
    raise SystemExit(main())
