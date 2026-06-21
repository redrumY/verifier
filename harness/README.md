# Harness Runtime Modules

The course chapters intentionally keep most mechanisms in single files so each
lesson is easy to read. Production-style extensions need a different boundary:
shared runtime modules that can be reused by the comprehensive agent, frontend
generation, sandbox execution, and verification.

## ModelGateway

`harness.model_gateway.ModelGateway` is the first shared runtime module. It
centralizes model access for multi-agent work:

- reads provider credentials from one place
- routes logical roles to model policies
- limits concurrent provider calls
- enforces per-role token budgets
- retries transient provider failures with backoff
- writes JSONL audit records with token and cost metadata

Default role routing:

| Role | Default model source | Purpose |
| --- | --- | --- |
| `planner` | `STRONG_MODEL_ID` or `MODEL_ID` | break work into tasks |
| `coder` | `CODER_MODEL_ID` or strong model | implement code |
| `tester` | `TESTER_MODEL_ID` or `MEDIUM_MODEL_ID` | design and run tests |
| `reviewer` | strong model | inspect diffs and risks |
| `summarizer` | `CHEAP_MODEL_ID` or `FALLBACK_MODEL_ID` | compact context |
| `verifier` | no LLM | execute deterministic checks |

Example:

```python
from harness.model_gateway import ModelGateway

gateway = ModelGateway.from_env(call_log_path=".logs/model-calls.jsonl")

response = gateway.call(
    "coder",
    messages=[{"role": "user", "content": "Implement the button."}],
    system="You are the coder agent.",
)
```

The verifier role is intentionally configured as non-LLM by default. Frontend
builds, browser checks, and sandbox runs should be deterministic tool work
first; LLM review can consume the resulting report later.
