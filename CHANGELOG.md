# Changelog

All notable changes to AIO are documented here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
semantic versioning.

## [0.1.3] - 2026-05-31

A hardening release: five focused passes turning "works in a demo" into
"holds up under scrutiny". Test count grew 163 → 205 (+1 skipped). No new
runtime dependencies — everything still uses the Python standard library.

### Added
- **Ranked code search** (`aio/search.py`, `search_code` tool): Okapi BM25 over
  identifier-aware tokens (camelCase / snake_case / dotted names split into
  subwords), chunked across the repo — relevance-ranked retrieval, not just
  exact-name lookup. Optional semantic re-ranking blends cosine similarity from
  a pluggable embedder (`openai_embedder` for OpenAI-compatible `/v1/embeddings`)
  with BM25; BM25 always works offline. Available in plan mode (read-only).
- **Web dashboard authentication** (`aio/security.py`): a per-session access
  token (handed off via the startup URL, pinned as a `SameSite=Strict`,
  `HttpOnly` cookie), a loopback Host allow-list (DNS-rebinding defense), a
  request body-size cap and per-IP rate limiting. New flags `--web-token` and
  `--web-no-auth`. Security headers (`nosniff`, `X-Frame-Options: DENY`) on
  every response.
- **SQLite session store** (`aio/store.py`): indexed listing, FTS5 full-text
  search (with a `LIKE` fallback), atomic upserts, lock-guarded concurrency,
  and a one-time migration that imports legacy `*.json` sessions.
- **Token-backend reporting**: `tokens.active_backend()`; `/api/info` now
  reports `token_backend` (tiktoken vs heuristic) so the estimate's provenance
  is explicit.
- **Frontend tests** (`tests/test_frontend.py`): `node --check` of the
  dashboard JS, a frontend↔backend route contract, tab/panel pairing checks.
- **Provider integration tests** over a real local HTTP server speaking the
  genuine Anthropic and OpenAI wire formats (chat, SSE streaming, 429/
  Retry-After backoff, retry exhaustion, connection errors) — plus a reactive
  autonomy test where the model branches on actual tool observations.

### Changed
- The dashboard is no longer one ~1200-line inline HTML/JS string: it is split
  into real, separately-served static assets (`aio/static/{index.html,app.css,
  app.js}`).
- `web.py` (1257 lines) split into a slim HTTP layer plus `service.py`
  (`AgentService`) and `catalog.py` (the MCP server catalog); `web_ui.py`
  trimmed 1218 → 47 lines.
- Token counting layers tiktoken over the calibrated heuristic with an explicit
  active-backend story.

### Security
- The web dashboard, which auto-approves tool calls, is no longer reachable
  without the access token; previously any local process or browser tab could
  drive it.

### Notes / known limits
- The web dashboard still auto-approves tool calls for an authenticated user
  (no per-tool approval gate yet); no TLS (intended behind loopback or a
  reverse proxy); single-user (no per-user isolation). Real-model end-to-end
  runs still require live API keys and aren't exercised in CI.

## [0.1.2]
- End-to-end autonomy proof test; release tooling via `workflow_dispatch`.

## [0.1.1]
- Vim-mode TUI, OpenTelemetry GenAI spans, MCP catalog and env-var entry,
  providers panel (test connection, usage/cost, budget warnings), multimodal
  attachments and multi-conversation tabs.

## [0.1.0]
- Initial AIO: multi-provider agent loop (Anthropic + OpenAI-compatible),
  file/search/shell/git tools, MCP client, plan mode, checkpoints/rewind,
  sub-agents, hooks, project map + symbol index, web dashboard and curses TUI.
