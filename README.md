# Verifier

Verifier is being refactored from a Claude-Code-style teaching repository into
a verification-first coding-agent harness.

The current product direction is narrow on purpose: generate frontend projects,
run them in an isolated workspace, verify them with deterministic checks, and
feed failures back into the agent loop.

## Current Runtime

```text
User request
  -> agents/s_full.py
  -> harness.ModelGateway
  -> role-based agent calls
  -> tools / task board / subagents
  -> future verifier + sandbox reports
```

The old root-level `s01_*` to `s20_*` course chapters have been removed. The
working entry point is now:

```sh
python agents/s_full.py
```

## ModelGateway

`harness/model_gateway.py` centralizes all LLM access:

- reads provider credentials in one place
- maps agent roles to model policies
- limits concurrent API calls
- enforces per-role token budgets
- retries transient provider failures with backoff
- records token, cost, latency, and status metadata as JSONL

Default role routing:

| Role | Purpose |
| --- | --- |
| `planner` | task decomposition and orchestration |
| `coder` | implementation work |
| `tester` | test design and validation reasoning |
| `reviewer` | diff and risk review |
| `summarizer` | context compaction |
| `verifier` | non-LLM deterministic checks |

Important environment variables:

```sh
ANTHROPIC_API_KEY=...
MODEL_ID=...
STRONG_MODEL_ID=...
MEDIUM_MODEL_ID=...
CHEAP_MODEL_ID=...
MODEL_GATEWAY_MAX_CONCURRENT=4
MODEL_GATEWAY_TOKEN_BUDGET=200000
```

Per-role overrides are also supported, for example `CODER_MODEL_ID` or
`TESTER_TOKEN_BUDGET`.

## Next Extension Points

The next modules should live under `harness/` instead of being added directly to
`agents/s_full.py`:

```text
harness/
  model_gateway.py       # done
  context_manager.py     # task packs, summaries, transcript archive
  frontend_generator.py  # natural language -> Vite/React project
  sandbox_runner.py      # isolated install/build/dev commands
  verifier.py            # build, typecheck, Playwright, reports
```

`agents/s_full.py` should remain the orchestrator entry point while heavy
runtime concerns move into reusable modules.

## Validation

Run:

```sh
python -m pytest -q
```
