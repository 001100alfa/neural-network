"""Built-in web fetch tool (#3) — retrieve a URL and return readable text.

Uses only the standard library. HTML is reduced to plain text (scripts/styles
stripped, tags removed, entities unescaped); JSON/text are returned as-is.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from typing import Any

from .base import Tool, ToolContext, ToolError

MAX_BYTES = 2_000_000
MAX_TEXT = 60_000


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|head|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])\s*>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)            # strip remaining tags
    text = html.unescape(raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a URL over HTTP(S) and return its content as readable text "
        "(HTML is converted to plain text). Use to read documentation, issues, "
        "or pages referenced by the user."
    )
    needs_approval = True  # outbound network access
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL to fetch."},
            "max_chars": {"type": "integer", "description": "Truncate output to this many chars."},
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        url = (args.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise ToolError("url must start with http:// or https://")
        limit = min(int(args.get("max_chars", MAX_TEXT)), MAX_TEXT)
        req = urllib.request.Request(
            url, headers={"User-Agent": "aio-agent/1.0 (+web_fetch)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                ctype = resp.headers.get("Content-Type", "")
                data = resp.read(MAX_BYTES)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network
            raise ToolError(f"HTTP {exc.code} fetching {url}")
        except (urllib.error.URLError, ValueError, OSError) as exc:  # pragma: no cover
            raise ToolError(f"could not fetch {url}: {exc}")
        body = data.decode("utf-8", "replace")
        if "html" in ctype.lower() or body.lstrip()[:1] == "<":
            body = _html_to_text(body)
        if len(body) > limit:
            body = body[:limit] + f"\n... (truncated at {limit} chars)"
        return f"[{url}]\n{body}"
