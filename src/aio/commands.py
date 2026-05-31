"""Custom slash commands and @file mentions for the chat input.

Custom commands live as Markdown files under ``.aio/commands/`` (or
``~/.config/aio/commands/``); the file body is the prompt template. ``$ARGUMENTS``
(or ``$1``, ``$2`` …) are substituted from the text after the command name. So
``.aio/commands/review.md`` is invoked as ``/review src/app.py``.

``@path`` mentions in a message are expanded inline to the referenced file's
contents, so users can pull files into context without a tool call.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_MENTION_BYTES = 100_000
_MENTION_RE = re.compile(r"(?<![\w@])@([\w./\-]+)")


def commands_dirs(workdir: Path) -> list[Path]:
    return [workdir / ".aio" / "commands", Path.home() / ".config" / "aio" / "commands"]


def list_commands(workdir: Path) -> dict[str, Path]:
    """Map command name -> file path (project dir takes priority over global)."""
    found: dict[str, Path] = {}
    for d in commands_dirs(workdir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            found.setdefault(f.stem, f)
    return found


def expand_command(workdir: Path, name: str, args: str) -> str | None:
    """Return the command's prompt with arguments substituted, or None if unknown."""
    cmds = list_commands(workdir)
    f = cmds.get(name)
    if f is None:
        return None
    try:
        body = f.read_text("utf-8")
    except OSError:
        return None
    parts = args.split()
    body = body.replace("$ARGUMENTS", args)
    for i, p in enumerate(parts, start=1):
        body = body.replace(f"${i}", p)
    return body


def expand_mentions(workdir: Path, text: str, allow_outside: bool = False) -> str:
    """Replace ``@path`` mentions with the file's contents appended to the message."""
    seen: list[str] = []

    def _check(rel: str) -> Path | None:
        p = (workdir / rel).resolve()
        if not allow_outside:
            wd = workdir.resolve()
            if wd != p and wd not in p.parents:
                return None
        return p if p.is_file() else None

    blocks = []
    for m in _MENTION_RE.finditer(text):
        rel = m.group(1)
        if rel in seen:
            continue
        p = _check(rel)
        if p is None:
            continue
        seen.append(rel)
        try:
            content = p.read_bytes()[:MAX_MENTION_BYTES].decode("utf-8", "replace")
        except OSError:
            continue
        blocks.append(f"\n\n--- contents of {rel} ---\n{content}")
    return text + "".join(blocks)
