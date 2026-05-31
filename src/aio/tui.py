"""Full-screen curses TUI with modal (vim-style) editing for the AIO REPL.

The modal editing engine lives in :class:`ViBuffer` (pure, unit-tested); the
curses front-end (:func:`run_tui`) is a thin render/key loop around it and the
agent. Launch with ``aio --tui`` (optionally ``--vim`` to start in NORMAL mode).
"""

from __future__ import annotations


class ViBuffer:
    """A single-line input buffer with a tiny vi command set.

    Modes: ``insert`` and ``normal``. Returns ``"submit"`` from
    :meth:`handle_normal`/:meth:`insert` when the line should be sent.
    """

    def __init__(self, mode: str = "insert") -> None:
        self.text = ""
        self.cursor = 0
        self.mode = mode  # "insert" | "normal"
        self.pending = ""  # multi-key normal commands (d, c, …)

    # -- insert mode ------------------------------------------------------
    def insert(self, ch: str) -> str | None:
        if ch == "\x1b":          # Esc -> normal mode
            self.mode = "normal"
            self.cursor = max(0, self.cursor - 1)
            return None
        if ch in ("\n", "\r"):
            return "submit"
        if ch in ("\x7f", "\b"):  # backspace
            if self.cursor > 0:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
            return None
        self.text = self.text[: self.cursor] + ch + self.text[self.cursor:]
        self.cursor += 1
        return None

    # -- normal mode ------------------------------------------------------
    def handle_normal(self, ch: str) -> str | None:
        p, self.pending = self.pending, ""
        if p == "d":  # dd / dw
            if ch == "d":
                self.text = ""; self.cursor = 0
            elif ch == "w":
                self._delete_word()
            return None
        if p == "c":  # cw / cc
            if ch == "w":
                self._delete_word(); self.mode = "insert"
            elif ch == "c":
                self.text = ""; self.cursor = 0; self.mode = "insert"
            return None

        if ch in ("\n", "\r"):
            return "submit"
        if ch == "i":
            self.mode = "insert"
        elif ch == "a":
            self.mode = "insert"; self.cursor = min(len(self.text), self.cursor + 1)
        elif ch == "A":
            self.mode = "insert"; self.cursor = len(self.text)
        elif ch == "I":
            self.mode = "insert"; self.cursor = 0
        elif ch == "h":
            self.cursor = max(0, self.cursor - 1)
        elif ch == "l":
            self.cursor = min(max(0, len(self.text) - 1), self.cursor + 1)
        elif ch == "0":
            self.cursor = 0
        elif ch == "$":
            self.cursor = max(0, len(self.text) - 1)
        elif ch == "w":
            self.cursor = self._next_word()
        elif ch == "b":
            self.cursor = self._prev_word()
        elif ch == "x":
            if self.cursor < len(self.text):
                self.text = self.text[: self.cursor] + self.text[self.cursor + 1:]
                self.cursor = min(self.cursor, max(0, len(self.text) - 1))
        elif ch in ("d", "c"):
            self.pending = ch
        return None

    def feed(self, ch: str) -> str | None:
        """Route a key to the active mode. Returns 'submit' when done."""
        return self.insert(ch) if self.mode == "insert" else self.handle_normal(ch)

    def take(self) -> str:
        """Return the line and reset the buffer."""
        line = self.text
        self.text = ""; self.cursor = 0; self.pending = ""
        return line

    # -- word motions -----------------------------------------------------
    def _next_word(self) -> int:
        i, n = self.cursor, len(self.text)
        while i < n and not self.text[i].isspace():
            i += 1
        while i < n and self.text[i].isspace():
            i += 1
        return min(i, max(0, n - 1))

    def _prev_word(self) -> int:
        i = self.cursor - 1
        while i > 0 and self.text[i].isspace():
            i -= 1
        while i > 0 and not self.text[i - 1].isspace():
            i -= 1
        return max(0, i)

    def _delete_word(self) -> None:
        end = self._next_word()
        if end <= self.cursor:
            end = len(self.text)
        self.text = self.text[: self.cursor] + self.text[end:]
        self.cursor = min(self.cursor, max(0, len(self.text)))


def run_tui(agent, config, vim: bool = False) -> int:  # pragma: no cover - needs a tty
    """Run the curses TUI. Falls back to a clear message if curses is unavailable."""
    import curses

    def _main(stdscr) -> int:
        curses.curs_set(1)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        transcript: list[str] = [
            f"AIO TUI — {config.provider}/{config.active.model}  "
            f"({'vim: NORMAL' if vim else 'insert'} mode)",
            "Enter to send · Esc for normal mode (h/j/k/l, dw, cc, x, i/a) · /exit to quit",
            "",
        ]
        buf = ViBuffer(mode="normal" if vim else "insert")

        class _ScrUI:
            """Minimal UI surface so the agent can render into the transcript."""
            def banner(self, *a, **k): pass
            def thinking(self, text="thinking…"): pass
            def assistant(self, text):
                if text:
                    transcript.append("● " + text)
            def tool_call(self, name, args):
                transcript.append(f"⚙ {name}({', '.join(f'{k}={v}' for k, v in (args or {}).items())[:60]})")
            def tool_result(self, text, error=False):
                transcript.append(("✗ " if error else "↳ ") + str(text).splitlines()[0][:80])
            def show_diff(self, old, new, path): transcript.append(f"± {path}")
            def info(self, text): transcript.append(str(text))
            def warn(self, text): transcript.append(str(text))
            def error(self, text): transcript.append("error: " + str(text))
            def confirm(self, name, args): return "yes"

        agent.ui = _ScrUI()
        agent.ctx.ui = agent.ui
        agent.stream = False

        def render():
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            body = transcript[-(h - 2):]
            for i, line in enumerate(body):
                stdscr.addnstr(i, 0, line, w - 1)
            mode = "N" if buf.mode == "normal" else "I"
            prompt = f"[{mode}] › {buf.text}"
            stdscr.addnstr(h - 1, 0, prompt, w - 1)
            stdscr.move(h - 1, min(w - 1, len(f"[{mode}] › ") + buf.cursor))
            stdscr.refresh()

        from .providers import ProviderError
        render()
        while True:
            try:
                ch = stdscr.get_wch()
            except (KeyboardInterrupt, curses.error):
                break
            ch = ch if isinstance(ch, str) else chr(ch) if isinstance(ch, int) else "\x1b"
            if buf.feed(ch) == "submit":
                line = buf.take().strip()
                if not line:
                    render(); continue
                if line in ("/exit", "/quit"):
                    break
                transcript.append("› " + line)
                render()
                try:
                    agent.run(line)
                except ProviderError as exc:
                    transcript.append("error: " + str(exc))
            render()
        return 0

    try:
        return curses.wrapper(_main)
    except Exception as exc:  # pragma: no cover
        print(f"TUI unavailable ({exc}); use the plain REPL instead.")
        return 1
