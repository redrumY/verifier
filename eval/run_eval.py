#!/usr/bin/env python3
"""Run a small deterministic evaluation suite for the coding-agent harness."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.scorecard import EvalCheck, EvalTaskResult, build_scorecard, markdown_summary
from harness.failure_feedback import FailureClassifier
from harness.frontend_generator import FrontendGenerator
from harness.sandbox_runner import DockerSandboxRunner
from harness.verifier import BrowserResult, CommandResult, Verifier


def passing_runner(command: list[str], cwd: Path, timeout: int) -> CommandResult:
    name = "install" if command[:2] == ["npm", "install"] else command[-1]
    return CommandResult(
        name=name,
        command=command,
        status="passed",
        returncode=0,
        stdout=f"fixture runner passed: {' '.join(command)}",
    )


def passing_browser_runner(project_dir: Path, outputs_dir: Path) -> BrowserResult:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    screenshot = outputs_dir / "screenshot.png"
    screenshot.write_text("fixture screenshot placeholder", encoding="utf-8")
    return BrowserResult(
        status="passed",
        url="http://127.0.0.1:5173",
        screenshot=str(screenshot),
        console_errors=[],
    )


class EvaluationRunner:
    """Execute fixture-level coding-agent evaluation tasks."""

    def __init__(
        self,
        root: str | Path,
        results_dir: str | Path = "data/evaluation/results",
        workspaces_dir: str | Path = "data/evaluation/workspaces",
        artifacts_dir: str | Path = "data/evaluation/artifacts",
        use_real_docker: bool = False,
    ):
        self.root = Path(root).resolve()
        self.results_dir = self._resolve(results_dir)
        self.workspaces_dir = self._resolve(workspaces_dir)
        self.artifacts_dir = self._resolve(artifacts_dir)
        self.use_real_docker = use_real_docker
        for directory in (self.results_dir, self.workspaces_dir, self.artifacts_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.classifier = FailureClassifier()

    def run_task_set(self, task_set_path: str | Path, run_id: str | None = None):
        task_set_file = self._resolve(task_set_path)
        task_set = json.loads(task_set_file.read_text(encoding="utf-8"))
        run_id = run_id or f"eval_{int(time.time())}"
        results = [self.run_task(task, run_id) for task in task_set["tasks"]]
        scorecard = build_scorecard(
            run_id=run_id,
            git_sha=self._git_sha(),
            task_set=task_set.get("name", task_set_file.stem),
            results=results,
            artifacts={
                "task_set": str(task_set_file),
                "results_dir": str(self.results_dir / run_id),
            },
        )
        run_dir = self.results_dir / run_id
        scorecard_path = scorecard.write(run_dir / "scorecard.json")
        summary_path = run_dir / "summary.md"
        summary_path.write_text(markdown_summary(scorecard), encoding="utf-8")
        return scorecard, scorecard_path, summary_path

    def run_task(self, task: dict[str, Any], run_id: str) -> EvalTaskResult:
        task_type = task["type"]
        start = time.monotonic()
        if task_type == "generate_frontend_project":
            result = self._run_generate_frontend_project(task, run_id)
        elif task_type == "sandbox_prepare":
            result = self._run_sandbox_prepare(task, run_id)
        elif task_type == "classify_failure":
            result = self._run_classify_failure(task, run_id)
        else:
            result = EvalTaskResult(
                task_id=task["id"],
                task_type=task_type,
                status="failed",
                attempts=1,
                duration_ms=0,
                checks=[EvalCheck("known_task_type", "failed", {"type": task_type})],
            )
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _run_generate_frontend_project(self, task: dict[str, Any], run_id: str) -> EvalTaskResult:
        workspace = self._workspace(run_id, task["id"])
        generator = FrontendGenerator(workspace)
        generated = generator.generate_from_natural_language(
            task["prompt"],
            "app",
            name=task.get("name"),
        )
        validation = generator.validate_minimum_delivery("app")
        verifier = Verifier(
            workspace,
            outputs_dir=workspace / "outputs",
            runner=passing_runner,
            browser_runner=passing_browser_runner,
        )
        report = verifier.verify("app")
        checks = [
            EvalCheck("minimum_delivery", "passed" if validation["ok"] else "failed", validation),
            EvalCheck("build", report.build, {"report_path": report.report_path}),
            EvalCheck("browser", report.browser, {"screenshot": report.screenshot}),
            EvalCheck(
                "console_errors",
                "passed" if not report.console_errors else "failed",
                {"console_errors": report.console_errors},
            ),
        ]
        return EvalTaskResult(
            task_id=task["id"],
            task_type=task["type"],
            status=self._status(checks),
            attempts=1,
            duration_ms=0,
            checks=checks,
            artifacts={
                "project_dir": generated.project_dir,
                "verification_report": report.report_path or "",
            },
            metrics={"generated_files": len(generated.files)},
        )

    def _run_sandbox_prepare(self, task: dict[str, Any], run_id: str) -> EvalTaskResult:
        workspace = self._workspace(run_id, task["id"])
        generator = FrontendGenerator(workspace)
        generator.generate_from_natural_language(task["prompt"], "app", name=task.get("name"))
        source_dir = workspace / "app"
        before = self._file_snapshot(source_dir)

        def fake_docker(command: list[str], cwd: Path, timeout: int) -> CommandResult:
            outputs = cwd / "outputs"
            outputs.mkdir(exist_ok=True)
            (outputs / "verification-report.json").write_text(
                json.dumps({
                    "install": "passed",
                    "typecheck": "passed",
                    "build": "passed",
                    "tests": "passed",
                    "browser": "passed",
                    "console_errors": [],
                    "steps": [],
                }, indent=2),
                encoding="utf-8",
            )
            return CommandResult(
                name="docker_sandbox",
                command=command,
                status="passed",
                returncode=0,
                stdout="fake docker passed",
            )

        runner = DockerSandboxRunner(
            workspace,
            runner=None if self.use_real_docker else fake_docker,
        )
        sandbox_run = runner.prepare_run("app", run_id=f"{run_id}_{task['id']}")
        execution = runner.run(sandbox_run)
        after = self._file_snapshot(source_dir)
        checks = [
            EvalCheck("sandbox_manifest", "passed" if Path(sandbox_run.profile_path).exists() else "failed"),
            EvalCheck("sandbox_execution", execution.status),
            EvalCheck("host_clean", "passed" if before == after else "failed", {
                "before_count": len(before),
                "after_count": len(after),
            }),
            EvalCheck(
                "sandbox_artifacts",
                "passed" if execution.report_path and Path(execution.report_path).exists() else "failed",
                {"report_path": execution.report_path},
            ),
        ]
        return EvalTaskResult(
            task_id=task["id"],
            task_type=task["type"],
            status=self._status(checks),
            attempts=1,
            duration_ms=0,
            checks=checks,
            artifacts={
                "sandbox_run": sandbox_run.run_dir,
                "sandbox_result": str(Path(sandbox_run.outputs_dir) / "sandbox-result.json"),
            },
        )

    def _run_classify_failure(self, task: dict[str, Any], run_id: str) -> EvalTaskResult:
        failure = task["failure"]
        digest = self.classifier.classify(
            run_id=run_id,
            task_id=task["id"],
            attempt=1,
            profile=failure.get("profile", "frontend-api-profile"),
            failed_step=failure["failed_step"],
            command=failure.get("command", failure["failed_step"]),
            exit_code=failure.get("exit_code"),
            stdout=failure.get("stdout", ""),
            stderr=failure.get("stderr", ""),
            console_errors=failure.get("console_errors", []),
            raw_refs=failure.get("raw_refs", []),
        )
        expected = task.get("expected", {})
        digest_dir = self.artifacts_dir / run_id / task["id"]
        digest_path = self.classifier.write_digest(digest, digest_dir / "failure-digest.json")
        checks = [
            EvalCheck(
                "error_type",
                "passed" if digest.error_type == expected.get("error_type") else "failed",
                {"actual": digest.error_type, "expected": expected.get("error_type")},
            ),
            EvalCheck(
                "digest_has_summary",
                "passed" if bool(digest.summary) else "failed",
                {"summary": digest.summary},
            ),
            EvalCheck(
                "digest_is_bounded",
                "passed" if len(json.dumps(digest.to_dict(), ensure_ascii=False)) < 4000 else "failed",
                {"bytes": len(json.dumps(digest.to_dict(), ensure_ascii=False))},
            ),
        ]
        return EvalTaskResult(
            task_id=task["id"],
            task_type=task["type"],
            status=self._status(checks),
            attempts=1,
            duration_ms=0,
            checks=checks,
            artifacts={"failure_digest": str(digest_path)},
            metrics={"error_type": digest.error_type, "retryable": digest.retryable},
        )

    def _workspace(self, run_id: str, task_id: str) -> Path:
        path = self.workspaces_dir / run_id / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _status(self, checks: list[EvalCheck]) -> str:
        return "passed" if all(check.status in {"passed", "skipped"} for check in checks) else "failed"

    def _file_snapshot(self, directory: Path) -> list[str]:
        return sorted(
            str(path.relative_to(directory))
            for path in directory.rglob("*")
            if path.is_file()
        )

    def _git_sha(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return "unknown"
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes project root: {path}") from exc
        return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks",
        default="data/evaluation/tasks/frontend_ui_v1.json",
        help="Path to evaluation task set JSON.",
    )
    parser.add_argument("--run-id", default=None, help="Optional stable run id.")
    parser.add_argument(
        "--use-real-docker",
        action="store_true",
        help="Run DockerSandboxRunner against real Docker instead of the fixture runner.",
    )
    args = parser.parse_args(argv)
    runner = EvaluationRunner(REPO_ROOT, use_real_docker=args.use_real_docker)
    scorecard, scorecard_path, summary_path = runner.run_task_set(args.tasks, run_id=args.run_id)
    print(json.dumps({
        "run_id": scorecard.run_id,
        "scorecard": str(scorecard_path),
        "summary": str(summary_path),
        "pass_rate": scorecard.summary["pass_rate"],
        "passed": scorecard.summary["passed"],
        "total": scorecard.summary["total_tasks"],
    }, indent=2, ensure_ascii=False))
    return 0 if scorecard.summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
