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

## ContextManager

`harness.context_manager.ContextManager` is the shared runtime boundary for
multi-agent context isolation. It stores parent and child sessions separately,
creates minimal task packs for child agents, archives full transcripts, and
returns compact summaries to the parent session.

Example:

```python
from harness.context_manager import ContextManager

contexts = ContextManager(".")
parent = contexts.create_session("planner", "Build a frontend project")
task_pack = contexts.build_task_pack(
    "coder",
    "Create the Vite app",
    acceptance_criteria=["npm run build succeeds"],
    relevant_files=["package.json", "src/App.tsx"],
    parent_session_id=parent.session_id,
)
child = contexts.create_child_session(task_pack)

summary = contexts.complete_session(
    child.session_id,
    result="implemented",
    decisions=["used Vite + React"],
    files_changed=["package.json", "src/App.tsx"],
)
```

Only the `SessionSummary` is attached back to the parent. The child transcript
stays archived under `.context/transcripts/`, which keeps the parent prompt
small while preserving auditability.

## TaskPlanner

`harness.task_planner.TaskPlanner` creates the standard frontend-generation task
graph. The lead agent can plan before writing code, while each task records its
owner, status, dependencies, context references, acceptance criteria, and result.

The default workflow is:

```text
task_001 spec generation
  -> task_002 Vite project generation
  -> task_003 install/build
  -> task_004 browser verification
  -> task_005 repair from logs
  -> task_006 final report
```

## FrontendGenerator

`harness.frontend_generator.FrontendGenerator` turns a natural language request
into a complete Vite/React/TypeScript project scaffold. It writes `spec.json`,
`package.json`, `index.html`, `src/App.tsx`, `src/main.tsx`, styles, and Vite/TS
configuration, then exposes `validate_minimum_delivery()` for deterministic file
and script checks.

## Verifier

`harness.verifier.Verifier` detects the project type and runs the matching
verification workflow. Frontend projects run npm install, typecheck, build, test,
and browser verification. The browser step uses Playwright when available, records
console errors, and writes screenshots and a JSON verification report under
`outputs/`.
