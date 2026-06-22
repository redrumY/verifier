from __future__ import annotations

import json
from pathlib import Path

from harness.failure_feedback import FailureClassifier


def test_classifier_extracts_typescript_error_location():
    digest = FailureClassifier().classify(
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        profile="frontend-api-profile",
        failed_step="build",
        command="npm run build",
        exit_code=1,
        stderr="src/App.tsx:18:12 - error TS2741: Property 'onClick' is missing.",
    )

    assert digest.error_type == "typescript_error"
    assert digest.retryable is True
    assert digest.affected_files[0].path == "src/App.tsx"
    assert digest.affected_files[0].line == 18
    assert "TypeScript failed" in digest.summary


def test_classifier_marks_api_connection_as_non_retryable_for_coder():
    digest = FailureClassifier().classify(
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        profile="frontend-api-profile",
        failed_step="browser_check",
        command="playwright browser_check",
        exit_code=1,
        console_errors=["TypeError: fetch failed: ECONNREFUSED 127.0.0.1:9999"],
    )

    assert digest.error_type == "api_connection_error"
    assert digest.retryable is False
    assert "mock profile" in digest.suggested_next_action


def test_classifier_writes_digest_json(tmp_path: Path):
    classifier = FailureClassifier()
    digest = classifier.classify(
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        profile="frontend-api-profile",
        failed_step="browser_check",
        command="playwright browser_check",
        exit_code=1,
        stderr="API contract mismatch: schema requires user.name",
    )

    path = classifier.write_digest(digest, tmp_path / "failure-digest.json")
    payload = json.loads(path.read_text())

    assert payload["error_type"] == "api_contract_mismatch"
    assert payload["summary"]


def test_classifier_reads_failed_verification_report(tmp_path: Path):
    stderr = tmp_path / "build.stderr.log"
    stderr.write_text("src/main.tsx:4:2 - error TS2304: Cannot find name 'App'.")
    report = tmp_path / "verification-report.json"
    report.write_text(json.dumps({
        "steps": [
            {
                "name": "build",
                "status": "failed",
                "exit_code": 1,
                "stderr_path": str(stderr),
            }
        ]
    }))

    digest = FailureClassifier().classify_verification_report(
        report,
        run_id="run_1",
        task_id="task_1",
    )

    assert digest is not None
    assert digest.error_type == "typescript_error"
    assert digest.raw_refs == [str(stderr)]
