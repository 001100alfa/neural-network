"""Static configuration data: provider catalog, system prompt, styles, windows.

Split out of ``config.py`` so the loading logic stays small and these large,
rarely-changing tables live on their own. ``config`` re-exports every public
name here, so existing imports (``from aio.config import PROVIDER_DEFAULTS`` …)
keep working.
"""

from __future__ import annotations

from typing import Any

# Built-in per-provider defaults. ``env`` is the environment variable that
# supplies the API key when one is not given in a config file or the key store.
# All providers except "anthropic" speak the OpenAI-compatible chat API.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
    },
    "google": {
        "label": "Google Gemini",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": "GEMINI_API_KEY",
    },
    "groq": {
        "label": "Groq",
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
    },
    "mistral": {
        "label": "Mistral AI",
        "model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
        "env": "MISTRAL_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "env": "DEEPSEEK_API_KEY",
    },
    "xai": {
        "label": "xAI (Grok)",
        "model": "grok-2-latest",
        "base_url": "https://api.x.ai/v1",
        "env": "XAI_API_KEY",
    },
    "together": {
        "label": "Together AI",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "base_url": "https://api.together.xyz/v1",
        "env": "TOGETHER_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "label": "Ollama (local)",
        "model": "qwen2.5-coder",
        "base_url": "http://localhost:11434/v1",
        "env": None,  # local, no key required
    },
}

# Order used when auto-detecting which provider to use from available API keys.
AUTODETECT_ORDER = (
    "anthropic", "openai", "google", "groq", "mistral",
    "deepseek", "xai", "together", "openrouter",
)

DEFAULT_SYSTEM_PROMPT = """\
You are AIO, an all-in-one open-source terminal coding agent operating inside a \
user's project directory. You help with software engineering tasks: reading and \
understanding code, implementing features, fixing bugs, running tests, and using git.

You have tools to read, write, and edit files; navigate by symbol; search by \
filename and content; run shell commands; and use git. Guidelines:
- Orient first: use the project map, search_code (keyword/concept search) and \
  find_symbol to locate definitions, and read_file (optionally with a symbol=) \
  before editing. Make focused, minimal changes.
- For non-trivial multi-step work, keep a plan with write_todos and update it as \
  you go; delegate independent sub-tasks with task / parallel_tasks.
- Prefer multi_edit for several changes to one file (atomic); use rename_symbol \
  for a project-wide identifier rename instead of many edits.
- For file management use the dedicated tools (make_dir, move_path, copy_path, \
  delete_path, archive) — they are platform-independent and safer than shell \
  mv/rm/cp; move/delete of a single file is rewindable.
- To PROVE a web UI works, run it (run_shell/run_background) then capture a \
  screenshot of its URL to give the user visual evidence — verify by tests AND \
  a screenshot when a change is visual.
- edit_file matches old_string EXACTLY, including whitespace. If an edit fails, \
  re-read the file and copy the exact text (the error often points at the cause, \
  e.g. an indentation difference) — do not guess the same string twice.
- ALWAYS verify your work: after editing, run the project's tests/linter/build \
  via run_shell and fix what you broke before finishing. If a tool call fails, \
  read the error, adjust, and retry rather than giving up or guessing.
- Be concise. Explain what you are about to do, then do it.
- Never run destructive commands without a clear reason; the user must approve \
  mutating actions.
- When the task is complete, give a short summary of what changed and how you \
  verified it.
"""

# Named output styles (#7): appended to the system prompt to shape responses.
OUTPUT_STYLES: dict[str, str] = {
    "default": "",
    "concise": "\n\n# Output style: concise\nBe terse. Prefer the shortest correct "
               "answer; minimal prose, no preamble or recap.",
    "explanatory": "\n\n# Output style: explanatory\nExplain your reasoning and the "
                   "trade-offs as you work, so the user learns from the change.",
    "teacher": "\n\n# Output style: teacher\nTeach as you go: define key concepts, note "
               "why each step matters, and suggest what to learn next.",
}

# Known usable context windows (tokens) by model-name substring, longest match
# wins. These are the API context limits; the agent compacts at ~80% of this.
_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude-sonnet-4", 1_000_000),   # 1M-token tier
    ("claude-opus-4", 200_000),
    ("claude-3-5", 200_000),
    ("claude-3", 200_000),
    ("claude", 200_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("o1", 200_000), ("o3", 200_000),
    ("gpt-4", 128_000),
    ("gemini-1.5", 1_000_000), ("gemini", 1_000_000),
    ("qwen", 128_000), ("deepseek", 128_000), ("llama", 128_000),
    ("mistral", 128_000), ("mixtral", 64_000),
]
DEFAULT_CONTEXT_WINDOW = 128_000


def context_window_for(model: str) -> int:
    """Best-effort usable context window (tokens) for a model name."""
    m = (model or "").lower()
    best = (0, DEFAULT_CONTEXT_WINDOW)
    for key, window in _CONTEXT_WINDOWS:
        if key in m and len(key) > best[0]:
            best = (len(key), window)
    return best[1]
