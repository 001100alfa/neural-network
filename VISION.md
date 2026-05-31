# AIO — product direction (proposal)

> Ruthless-mentor framing: the code is no longer weak, but "all-in-one coding
> agent" is generic — it competes head-on with Cursor / Claude Code / Cline /
> Aider and wins at nothing. This document proposes the one thing AIO can be
> *best* at, derived from what's already been built rather than invented.

## The signature

**The coding agent that runs anywhere and proves what it did.**

Not "another AI IDE." AIO is the agent you can drop into a place the cloud IDEs
can't go, point at *your* model, and walk away with a tamper-evident record of
every action it took.

## Why this, and why us

Everything already built points here — this is AIO's latent identity, not a pivot:

| Built | Becomes the signature |
|-------|-----------------------|
| Zero runtime dependencies, single `pip install`, Windows-portable | **Runs anywhere** — air-gapped, locked-down, CI, a USB stick |
| Anthropic / OpenAI / OpenRouter / **local Ollama** behind one interface | **Your model, your data** — no mandatory cloud, no vendor lock |
| Token auth, Host pinning, OS sandbox, tool-approval gate | **Safe to run unattended** in an environment you don't fully trust |
| Hash-chained, secret-redacted **audit log** + OpenTelemetry | **Proves what it did** — every tool call recorded, tamper-evident |
| Self-hostable web + CLI + TUI, no build step | **You own the deployment** — no SaaS, no telemetry-home |

Cursor / Claude Code are cloud-tied, proprietary, heavy, and opaque about agent
actions. None of them can be dropped into an air-gapped regulated box, run
against a local model, and hand a compliance team a verifiable action log. That
gap is real and AIO already has the pieces.

## Who it's for (hypothesis — needs validation)

- **Regulated / air-gapped engineering** (finance, health, gov, defense) that
  *cannot* send code to a cloud IDE.
- **Security-conscious enterprises** that need auditability and self-hosting.
- **CI / automation** where an agent runs unattended and its actions must be
  reviewable after the fact.

## What we would STOP doing

Stop widening the all-in-one surface (more demo-grade panels). Depth on the
signature only: provider-agnostic execution, sandbox, and the audit/proof story.

## Concrete next steps (only after the direction is validated)

1. **Audit viewer**: surface the hash-chained log in the dashboard + an
   `/api/audit` endpoint + offline chain verification (`aio audit --verify`).
2. **Air-gapped proof**: a documented, tested offline run against local Ollama
   with zero outbound network (assert no egress).
3. **Compliance export**: signed (HMAC) audit bundle for a run.

## Open questions (the answers decide if this is right)

1. Who is the *first* user — a regulated team, a solo dev, a platform/CI?
2. What is the painful *moment* AIO solves that the alternatives don't?
3. If AIO didn't exist, what would that user reach for instead — and why is that
   worse for them?

Until these are answered with a real user, this is a hypothesis, not a plan.
