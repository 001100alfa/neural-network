"""Curated catalog of open-source MCP servers for coding & project work.

Kept separate from :mod:`aio.web` so the dashboard server logic stays focused
on request handling. ``web.py`` re-exports these names for compatibility.
"""

from __future__ import annotations

from typing import Any

MCP_CATALOG: list[dict[str, Any]] = [
    {"name": "filesystem", "desc": "Read/write files within a directory",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    {"name": "git", "desc": "Inspect & operate on a local git repository",
     "command": "uvx", "args": ["mcp-server-git", "--repository", "."]},
    {"name": "github", "desc": "GitHub repos, issues, PRs, code search",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env_hint": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    {"name": "fetch", "desc": "Fetch a URL and convert it to Markdown",
     "command": "uvx", "args": ["mcp-server-fetch"]},
    {"name": "memory", "desc": "Persistent knowledge-graph memory across sessions",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    {"name": "sequential-thinking", "desc": "Structured step-by-step reasoning scratchpad",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
    {"name": "everything", "desc": "Reference server exercising all MCP features (testing)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"]},
    {"name": "sqlite", "desc": "Query/inspect a SQLite database",
     "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "./app.db"]},
    {"name": "postgres", "desc": "Read-only access to a PostgreSQL database",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"]},
    {"name": "playwright", "desc": "Drive a real browser (Microsoft Playwright MCP)",
     "command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
    {"name": "context7", "desc": "Up-to-date library/API documentation (Upstash Context7)",
     "command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
    {"name": "time", "desc": "Current time & timezone conversion",
     "command": "uvx", "args": ["mcp-server-time"]},
    {"name": "brave-search", "desc": "Web search via the Brave Search API",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
     "env_hint": "BRAVE_API_KEY"},
    {"name": "gitlab", "desc": "GitLab projects, issues, merge requests",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-gitlab"],
     "env_hint": "GITLAB_PERSONAL_ACCESS_TOKEN"},
    {"name": "slack", "desc": "Read/post Slack messages & channels",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
     "env_hint": "SLACK_BOT_TOKEN"},
    {"name": "sentry", "desc": "Inspect Sentry issues & stack traces",
     "command": "npx", "args": ["-y", "@sentry/mcp-server@latest"],
     "env_hint": "SENTRY_AUTH_TOKEN"},
    {"name": "notion", "desc": "Read/write Notion pages & databases",
     "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
     "env_hint": "NOTION_TOKEN"},
    {"name": "docker", "desc": "Manage Docker containers & images",
     "command": "uvx", "args": ["docker-mcp"]},
    {"name": "puppeteer", "desc": "Browser automation & screenshots (Puppeteer)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]},
    {"name": "google-drive", "desc": "Search & read Google Drive files (OAuth setup)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-gdrive"]},
    {"name": "redis", "desc": "Read/write a Redis instance",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-redis", "redis://localhost:6379"]},
    {"name": "kubernetes", "desc": "Inspect & manage a Kubernetes cluster",
     "command": "npx", "args": ["-y", "mcp-server-kubernetes"]},
    {"name": "mongodb", "desc": "Query a MongoDB database",
     "command": "npx", "args": ["-y", "mongodb-mcp-server"],
     "env_hint": "MDB_MCP_CONNECTION_STRING"},
    {"name": "obsidian", "desc": "Read/search an Obsidian vault",
     "command": "uvx", "args": ["mcp-obsidian"], "env_hint": "OBSIDIAN_API_KEY"},
    {"name": "figma", "desc": "Read Figma designs/components",
     "command": "npx", "args": ["-y", "figma-developer-mcp", "--stdio"],
     "env_hint": "FIGMA_API_KEY"},
]

# Servers configured out of the box (when nothing else is set): the coding /
# project catalog minus git+github. They are listed as "configured" but NOT
# auto-started (autostart=False) so launching never spawns 11 npx/uvx processes;
# each gets a Start button in the panel.
_DEFAULT_MCP_NAMES = {
    "filesystem", "fetch", "context7", "brave-search", "playwright",
    "sqlite", "postgres", "memory", "sequential-thinking", "time", "everything",
}
DEFAULT_MCP_SERVERS: list[dict[str, Any]] = [
    {"name": c["name"], "command": c["command"], "args": list(c["args"]), "autostart": False}
    for c in MCP_CATALOG if c["name"] in _DEFAULT_MCP_NAMES
]
