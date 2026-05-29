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
    p.add_argument("--list-tools", action="store_true", help="List tools and exit.")
    p.add_argument("--version", action="version", version=f"aio {__version__}")
    return p


def _make_agent(config, ui: UI, no_mcp: bool):
    provider = build_provider(config)
    registry = default_registry()

    mcp_servers = []
    if not no_mcp and config.mcp_servers:
        mcp_tools, mcp_servers = load_mcp_tools(config.mcp_servers, ui=ui)
        for t in mcp_tools:
            registry.register(t)

    ctx = ToolContext(
        workdir=config.workdir,
        ui=ui,
        auto_approve=config.auto_approve,
        allow_outside_workdir=config.allow_outside_workdir,
    )
    agent = Agent(
        provider=provider,
        tools=registry,
        ctx=ctx,
        ui=ui,
        system_prompt=config.system_prompt,
        max_steps=config.max_steps,
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


def main(argv: list[str] | None = None) -> int:
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

        serve(config, host=args.host, port=args.port)
        return 0

    agent, mcp_servers = _make_agent(config, ui, no_mcp=args.no_mcp)

    try:
        if args.prompt:
            prompt = " ".join(args.prompt)
            try:
                agent.run(prompt)
            except ProviderError as exc:
                ui.error(str(exc))
                return 1
            return 0
        return repl(agent, config, ui)
    finally:
        for server in mcp_servers:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
