"""Scorecard data structures for coding-agent evaluations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalCheck:
    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalTaskResult:
    task_id: str
    task_type: str
    status: str
    attempts: int
    duration_ms: int
    checks: list[EvalCheck] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [check.to_dict() for check in self.checks]
        return payload


@dataclass
class EvalScorecard:
    run_id: str
    git_sha: str
    task_set: str
    summary: dict[str, Any]
    results: list[EvalTaskResult]
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target


def build_scorecard(
    *,
    run_id: str,
    git_sha: str,
    task_set: str,
    results: list[EvalTaskResult],
    artifacts: dict[str, str] | None = None,
) -> EvalScorecard:
    total = len(results)
    passed = sum(1 for result in results if result.status == "passed")
    failed = total - passed
    attempts = [result.attempts for result in results]
    host_dirty = any(
        check.name == "host_clean" and check.status != "passed"
        for result in results
        for check in result.checks
    )
    classified = sum(
        1 for result in results
        if result.task_type == "classify_failure" and result.status == "passed"
    )
    classification_total = sum(1 for result in results if result.task_type == "classify_failure")

    summary = {
        "total_tasks": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "first_try_pass_rate": round(
            sum(1 for result in results if result.status == "passed" and result.attempts == 1) / total,
            4,
        ) if total else 0.0,
        "avg_attempts": round(sum(attempts) / len(attempts), 2) if attempts else 0.0,
        "host_dirty_after_run": host_dirty,
        "failure_classification_rate": round(classified / classification_total, 4)
        if classification_total else None,
    }
    return EvalScorecard(
        run_id=run_id,
        git_sha=git_sha,
        task_set=task_set,
        summary=summary,
        results=results,
        artifacts=artifacts or {},
    )


def markdown_summary(scorecard: EvalScorecard) -> str:
    lines = [
        f"# Evaluation Summary: {scorecard.run_id}",
        "",
        f"- task_set: `{scorecard.task_set}`",
        f"- git_sha: `{scorecard.git_sha}`",
        f"- total_tasks: `{scorecard.summary['total_tasks']}`",
        f"- passed: `{scorecard.summary['passed']}`",
        f"- failed: `{scorecard.summary['failed']}`",
        f"- pass_rate: `{scorecard.summary['pass_rate']}`",
        f"- first_try_pass_rate: `{scorecard.summary['first_try_pass_rate']}`",
        f"- avg_attempts: `{scorecard.summary['avg_attempts']}`",
        f"- host_dirty_after_run: `{scorecard.summary['host_dirty_after_run']}`",
    ]
    if scorecard.summary.get("failure_classification_rate") is not None:
        lines.append(
            f"- failure_classification_rate: `{scorecard.summary['failure_classification_rate']}`"
        )
    lines.extend(["", "## Tasks", ""])
    for result in scorecard.results:
        lines.append(f"- `{result.task_id}` ({result.task_type}): `{result.status}`")
    return "\n".join(lines) + "\n"
