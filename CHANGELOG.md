# Changelog

All notable changes to AIO are documented here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
semantic versioning.

## [0.1.4] - 2026-05-31

A second, deeper hardening wave. Test count grew 205 → 261 (+1 skipped), with a
new **mypy** gate and a CI **coverage gate (85%)**. Still zero runtime
dependencies.

### Added
- **Tool-approval gate** (`security.ApprovalBroker`): side-effecting tools
  require explicit approval over the live stream (Approve/Always/Deny in the
  dashboard); read-only tools bypass it; fail-closed on the non-streaming path.
- **Conversation durability**: live tabs auto-persist to SQLite and are restored
  on restart; versioned schema migrations + WAL.
- **RAG-lite**: the most relevant code (BM25) is auto-injected into the agent's
  context each turn.
- **Eval harness** (`aio-eval`): outcome-based golden suite (5 cases) that runs
  against a real model via `AIO_EVAL_LIVE=1`, with deterministic offline mode.
- **Observability**: structured JSON request logs, `/health`, `/metrics`
  (Prometheus), `X-AIO-API-Version` + `/api/v1` alias.
- **TLS** serving (`--web-tls-cert/--web-tls-key`) and a **Dockerfile**.
- Secret **redaction** (logs/exports/audit), a catastrophic-command
  **guardrail**, agent **budgets** (`max_tool_calls`/`deadline_s`), and a
  **hash-chained, rotating audit log**.
- Frontend ES modules: `util.js` (pure logic, node:test) and `editor.js`, plus
  a Playwright end-to-end test.

### Changed
- `AgentService` decomposed across typed mixins
  (`service_sessions`/`providers`/`panels` over `service_base`); `web.py` slimmed.
- Token counting reports its backend (`tokens.active_backend()`); `/api/info`
  exposes it.
- mypy added as a real gate (all surfaced type errors fixed); CI runs under a
  coverage floor.

### Security
- The dashboard's auto-approve hole is closed: an authenticated user's
  side-effecting tools now go through the approval gate by default
  (`--web-auto-approve` to opt out).

### Known limits
- `app.js` remains a large DOM-glue module (full SPA/build intentionally
  avoided); eval CI uses reference solvers (real-model quality needs a live
  key); guardrail/redaction are regex-based (not a sandbox); audit chain is
  unsigned; single-user; `/api/v1` is an alias (no behavioural versioning yet).

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
