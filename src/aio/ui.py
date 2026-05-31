"""Terminal UI helpers: colours, diffs, tool rendering, and prompts."""

from __future__ import annotations

import difflib
import sys


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"


class UI:
    def __init__(self, color: bool | None = None, quiet: bool = False) -> None:
        self.color = sys.stdout.isatty() if color is None else color
        self.quiet = quiet

    def _c(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"{code}{text}{Color.RESET}"

    def banner(self, provider: str, model: str) -> None:
        if self.quiet:
            return
        title = self._c("AIO", Color.BOLD + Color.CYAN)
        sub = self._c("all-in-one coding agent", Color.DIM)
        print(f"{title} — {sub}")
        print(self._c(f"provider={provider}  model={model}", Color.GREY))
        print(self._c("type /help for commands, /exit to quit", Color.GREY))
        print()

    def info(self, text: str) -> None:
        if not self.quiet:
            print(self._c(text, Color.GREY))

    def warn(self, text: str) -> None:
        print(self._c(text, Color.YELLOW))

    def error(self, text: str) -> None:
        print(self._c(text, Color.RED))

    def assistant(self, text: str) -> None:
        if not text:
            return
        label = self._c("●", Color.MAGENTA)
        print(f"{label} {text}")

    def thinking(self, text: str = "thinking…") -> None:
        if not self.quiet:
            print(self._c(text, Color.DIM))

    def tool_call(self, name: str, args: dict) -> None:
        rendered = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        head = self._c(f"⚙ {name}", Color.BLUE)
        print(f"{head}({self._c(rendered, Color.GREY)})")

    def tool_result(self, text: str, error: bool = False) -> None:
        prefix = self._c("  ✗ ", Color.RED) if error else self._c("  ↳ ", Color.GREEN)
        snippet = text.strip().splitlines()
        shown = snippet[:12]
        for line in shown:
            print(prefix + self._c(line[:200], Color.GREY))
        if len(snippet) > len(shown):
            print(prefix + self._c(f"… (+{len(snippet) - len(shown)} more lines)", Color.GREY))

    def show_diff(self, old: str, new: str, path: str) -> None:
        if self.quiet or old == new:
            return
        diff = difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                print(self._c(line, Color.GREEN))
            elif line.startswith("-") and not line.startswith("---"):
                print(self._c(line, Color.RED))
            elif line.startswith("@@"):
                print(self._c(line, Color.CYAN))
            else:
                print(self._c(line, Color.GREY))

    def confirm(self, name: str, args: dict) -> str:
        """Prompt for approval. Returns 'yes', 'always', or 'no'."""

        self.tool_call(name, args)
        prompt = self._c("  approve? [y]es / [n]o / [a]lways: ", Color.YELLOW)
        try:
            ans = input(prompt).strip().lower()
        except EOFError:
            return "no"
        if ans in ("a", "always"):
            return "always"
        if ans in ("y", "yes"):
            return "yes"
        return "no"

    def confirm_hunk(self, path: str, index: int, total: int, hunk_lines: list[str]) -> bool:
        """Show one diff hunk and ask whether to apply it. Returns True to keep."""
        print(self._c(f"  hunk {index}/{total} in {path}:", Color.CYAN))
        for line in hunk_lines:
            if line.startswith("+"):
                print(self._c("   " + line, Color.GREEN))
            elif line.startswith("-"):
                print(self._c("   " + line, Color.RED))
            else:
                print(self._c("   " + line, Color.GREY))
        try:
            ans = input(self._c("  apply this hunk? [y]es / [n]o: ", Color.YELLOW)).strip().lower()
        except EOFError:
            return True
        return ans in ("", "y", "yes")


def _short(value, limit: int = 60) -> str:
    s = str(value).replace("\n", "\\n")
    return s if len(s) <= limit else s[:limit] + "…"
