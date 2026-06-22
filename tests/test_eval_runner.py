from __future__ import annotations

import json
from pathlib import Path

from eval.run_eval import EvaluationRunner
from eval.scorecard import EvalCheck, EvalTaskResult, build_scorecard


def test_scorecard_summarizes_pass_rate_and_dirty_flag():
    scorecard = build_scorecard(
        run_id="eval_test",
        git_sha="abc123",
        task_set="fixture",
        results=[
            EvalTaskResult(
                task_id="one",
                task_type="generate_frontend_project",
                status="passed",
                attempts=1,
                duration_ms=1,
                checks=[EvalCheck("build", "passed")],
            ),
            EvalTaskResult(
                task_id="two",
                task_type="sandbox_prepare",
                status="failed",
                attempts=2,
                duration_ms=1,
                checks=[EvalCheck("host_clean", "failed")],
            ),
        ],
    )

    assert scorecard.summary["total_tasks"] == 2
    assert scorecard.summary["passed"] == 1
    assert scorecard.summary["pass_rate"] == 0.5
    assert scorecard.summary["avg_attempts"] == 1.5
    assert scorecard.summary["host_dirty_after_run"] is True


def test_evaluation_runner_executes_frontend_task_set(tmp_path: Path):
    task_set = tmp_path / "tasks.json"
    task_set.write_text(json.dumps({
        "name": "mini",
        "tasks": [
            {
                "id": "generate",
                "type": "generate_frontend_project",
                "prompt": "生成一个 CRM 页面"
            },
            {
                "id": "sandbox",
                "type": "sandbox_prepare",
                "prompt": "生成一个 sandbox 页面"
            },
            {
                "id": "failure",
                "type": "classify_failure",
                "failure": {
                    "failed_step": "build",
                    "command": "npm run build",
                    "exit_code": 1,
                    "stderr": "src/App.tsx:1:1 - error TS2304: Cannot find name 'React'."
                },
                "expected": {
                    "error_type": "typescript_error"
                }
            }
        ],
    }))
    runner = EvaluationRunner(tmp_path)

    scorecard, scorecard_path, summary_path = runner.run_task_set(task_set, run_id="eval_test")

    assert scorecard.summary["total_tasks"] == 3
    assert scorecard.summary["passed"] == 3
    assert scorecard.summary["pass_rate"] == 1.0
    assert scorecard_path.exists()
    assert summary_path.exists()
    payload = json.loads(scorecard_path.read_text())
    assert payload["results"][0]["task_id"] == "generate"
