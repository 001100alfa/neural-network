# Benchmarks

AIO ships an outcome-based eval harness (`aio-eval` / `python -m aio.eval`) that
runs the agent on golden coding tasks and checks the result by running the
workspace's own tests. This is how we keep the "does it actually solve tasks?"
question honest instead of vibe-based.

## Running

```bash
# Offline: deterministic reference solvers (proves the harness + tool loop).
python -m aio.eval --json results-reference.json

# Live: measure a real model's reasoning (set a provider key).
AIO_EVAL_LIVE=1 ANTHROPIC_API_KEY=sk-... python -m aio.eval --json results-live.json
```

The scorecard prints pass/total, per-case tool calls, tokens and timing, and
`--json` writes a machine-readable record you can commit and diff over time.

## Cases (current golden suite)

| Case | Capability exercised |
|------|----------------------|
| `fix-divide-by-zero` | locate + fix a runtime bug, verify with tests |
| `implement-missing-function` | implement a missing function from its test |
| `fix-logic-bug` | correct faulty logic (off-by-one) |
| `implement-across-two-files` | implement a symbol imported by another file |
| `navigate-then-fix` | `find_symbol` → `read_file(symbol=)` → edit → verify |
| `fix-two-bugs-two-files` | fix two independent bugs across two files in one run |

## Results

**Reference (offline, deterministic solvers)** — proves the harness, tools and
checks; **not** a measure of model quality:

| Mode | Passed | Pass rate |
|------|--------|-----------|
| reference | 6 / 6 | 1.00 |

**Live (real model)** — _not yet recorded in CI (no API key/budget in CI)._ Run
the live command above and paste the `results-live.json` summary here. This is
the number that backs any "Claude-Code-level" claim; until it's filled in, that
claim is unproven and should not be made.

## Honest limitations

- The golden suite is small and synthetic; it is **not** SWE-bench. Wiring a
  real SWE-bench-lite subset (clone repo, apply task, run the project's tests)
  is the next step to a credible number.
- Reference-solver runs only prove mechanics. Treat offline `pass_rate` as a
  smoke test, not a quality score.
