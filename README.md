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
  -> harness.ContextManager
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

## ContextManager

`harness/context_manager.py` keeps multi-agent context explicit instead of
letting every subagent inherit the whole parent conversation.

It provides:

- parent and child session state under `.context/sessions/`
- task packs with objective, acceptance criteria, relevant files, and context refs
- transcript archives under `.context/transcripts/`
- child summaries under `.context/summaries/`
- deterministic compaction that keeps a summary plus recent messages
- tool-result trimming so long command output does not explode the prompt

The intended contract is:

```text
Parent agent
  -> creates TaskPack for child
  -> child works in isolated session
  -> child transcript is archived
  -> parent receives only SessionSummary
```

This gives later Planner / Coder / Tester / Reviewer agents a clean handoff
boundary: private working context stays private, while decisions and artifacts
return to the orchestrator.

## Next Extension Points

The next modules should live under `harness/` instead of being added directly to
`agents/s_full.py`:

```text
harness/
  model_gateway.py       # done
  context_manager.py     # done
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
