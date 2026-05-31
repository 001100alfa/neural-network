"""Project-map / codebase awareness (#J).

Builds a compact, dependency-free overview of the working directory — directory
tree (gitignore-aware, capped), language breakdown, and key files (README,
manifests, entry points) — that's injected into the system prompt so the agent
starts every session already oriented, like Claude Code/Cursor.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
    "target", ".next", ".tox", "site-packages", ".gradle",
}
KEY_FILES = [
    "README.md", "README.rst", "README", "pyproject.toml", "setup.py",
    "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "requirements.txt", "CLAUDE.md", "AGENTS.md",
]
EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cs": "C#", ".php": "PHP",
    ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell", ".html": "HTML",
    ".css": "CSS", ".md": "Markdown", ".json": "JSON", ".toml": "TOML",
    ".yaml": "YAML", ".yml": "YAML", ".sql": "SQL",
}


def _gitignore_patterns(root: Path) -> list[str]:
    pats: list[str] = []
    gi = root / ".gitignore"
    if gi.is_file():
        try:
            for line in gi.read_text("utf-8", "replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pats.append(line.rstrip("/"))
        except OSError:
            pass
    return pats


def _ignored(rel: str, name: str, patterns: list[str]) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*"):
            return True
    return False


def build_project_map(workdir: Path, max_files: int = 400, max_tree_lines: int = 120) -> str:
    """Return a compact Markdown project map, or '' if the dir looks empty."""
    root = Path(workdir)
    if not root.is_dir():
        return ""
    patterns = _gitignore_patterns(root)
    langs: dict[str, int] = {}
    tree_lines: list[str] = []
    key_present: list[str] = []
    n_files = 0

    def walk(d: Path, prefix: str, depth: int) -> None:
        nonlocal n_files
        if depth > 3 or n_files >= max_files or len(tree_lines) >= max_tree_lines:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if e.name in IGNORE_DIRS or e.name.startswith("."):
                if e.name not in (".github",):
                    continue
            rel = str(e.relative_to(root))
            if _ignored(rel, e.name, patterns):
                continue
            if e.is_dir():
                if len(tree_lines) < max_tree_lines:
                    tree_lines.append(f"{prefix}{e.name}/")
                walk(e, prefix + "  ", depth + 1)
            else:
                n_files += 1
                lang = EXT_LANG.get(e.suffix.lower())
                if lang:
                    langs[lang] = langs.get(lang, 0) + 1
                if depth <= 2 and len(tree_lines) < max_tree_lines:
                    tree_lines.append(f"{prefix}{e.name}")

    for kf in KEY_FILES:
        if (root / kf).is_file():
            key_present.append(kf)

    walk(root, "", 0)
    if n_files == 0:
        return ""

    top_langs = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:6]
    parts = [f"Working directory: {root}", f"Files (approx): {n_files}"]
    if top_langs:
        parts.append("Languages: " + ", ".join(f"{k} ({v})" for k, v in top_langs))
    if key_present:
        parts.append("Key files: " + ", ".join(key_present))
    if tree_lines:
        parts.append("Structure:\n" + "\n".join(tree_lines[:max_tree_lines]))
    return "\n".join(parts)
