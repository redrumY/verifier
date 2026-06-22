"""Structured failure feedback for verifier and sandbox outputs."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


FAILURE_ERROR_TYPES = {
    "dependency_install_error",
    "typescript_error",
    "build_error",
    "unit_test_failure",
    "browser_console_error",
    "browser_render_error",
    "api_connection_error",
    "api_contract_mismatch",
    "timeout",
    "sandbox_infra_error",
    "unknown_error",
}


@dataclass
class AffectedFile:
    path: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FailureEvidence:
    kind: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FailureDigest:
    run_id: str
    task_id: str
    attempt: int
    profile: str
    failed_step: str
    command: str
    exit_code: int | None
    error_type: str
    retryable: bool
    affected_files: list[AffectedFile] = field(default_factory=list)
    summary: str = ""
    evidence: list[FailureEvidence] = field(default_factory=list)
    raw_refs: list[str] = field(default_factory=list)
    suggested_next_action: str = ""
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["affected_files"] = [item.to_dict() for item in self.affected_files]
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


class FailureClassifier:
    """Classify deterministic tool failures into a small Coder-facing digest."""

    TS_LOCATION_RE = re.compile(
        r"(?P<path>[\w./@-]+\.(?:ts|tsx|js|jsx)):(?P<line>\d+):(?P<column>\d+)"
    )

    def __init__(self, max_evidence_chars: int = 600):
        self.max_evidence_chars = max_evidence_chars

    def classify(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt: int,
        profile: str,
        failed_step: str,
        command: str | list[str],
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
        console_errors: list[str] | None = None,
        raw_refs: list[str] | None = None,
    ) -> FailureDigest:
        command_text = " ".join(command) if isinstance(command, list) else command
        console_errors = console_errors or []
        raw_refs = raw_refs or []
        text = "\n".join([stdout, stderr, *console_errors])
        error_type = self._error_type(failed_step, text)
        affected_files = self._affected_files(text)
        evidence_text = self._evidence_text(text)
        summary = self._summary(error_type, failed_step, affected_files, evidence_text)
        retryable = self._retryable(error_type)

        digest = FailureDigest(
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            profile=profile,
            failed_step=failed_step,
            command=command_text,
            exit_code=exit_code,
            error_type=error_type,
            retryable=retryable,
            affected_files=affected_files[:3],
            summary=summary,
            evidence=[FailureEvidence(kind="log_excerpt", text=evidence_text)] if evidence_text else [],
            raw_refs=raw_refs,
            suggested_next_action=self._suggested_next_action(error_type, affected_files),
            confidence=self._confidence(error_type, affected_files, evidence_text),
        )
        return digest

    def classify_verification_report(
        self,
        report_path: str | Path,
        *,
        run_id: str,
        task_id: str,
        attempt: int = 1,
        profile: str = "frontend-ui-component",
    ) -> FailureDigest | None:
        path = Path(report_path)
        report = json.loads(path.read_text(encoding="utf-8"))
        steps = report.get("steps", [])
        for step in steps:
            if step.get("status") != "failed":
                continue
            stdout = self._read_optional(step.get("stdout_path"))
            stderr = self._read_optional(step.get("stderr_path"))
            return self.classify(
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                profile=profile,
                failed_step=step.get("name", "unknown"),
                command=step.get("command", step.get("name", "unknown")),
                exit_code=step.get("exit_code"),
                stdout=stdout,
                stderr=stderr,
                raw_refs=[
                    ref for ref in [step.get("stdout_path"), step.get("stderr_path")]
                    if ref
                ],
            )
        console_errors = report.get("console_errors") or []
        if report.get("browser") == "failed" or console_errors:
            return self.classify(
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                profile=profile,
                failed_step="browser_check",
                command="playwright browser_check",
                exit_code=1,
                console_errors=console_errors,
                raw_refs=["browser-report.json"],
            )
        return None

    def write_digest(self, digest: FailureDigest, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(digest.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def _error_type(self, failed_step: str, text: str) -> str:
        lowered = text.lower()
        if "cannot connect to the docker daemon" in lowered or "docker" in lowered and "image" in lowered:
            return "sandbox_infra_error"
        if "timeout" in lowered or "timed out" in lowered:
            return "timeout"
        if failed_step == "install" or "npm err!" in lowered and "install" in lowered:
            return "dependency_install_error"
        if re.search(r"error\s+TS\d+", text) or "typescript" in lowered:
            return "typescript_error"
        if "contract" in lowered and ("mismatch" in lowered or "schema" in lowered):
            return "api_contract_mismatch"
        if "econnrefused" in lowered or "networkerror" in lowered or "fetch failed" in lowered:
            return "api_connection_error"
        if failed_step in {"test", "unit_test"} or "assertionerror" in lowered or "expected" in lowered and "received" in lowered:
            return "unit_test_failure"
        if failed_step in {"browser", "browser_check"} and text.strip():
            return "browser_console_error"
        if failed_step == "build":
            return "build_error"
        return "unknown_error"

    def _affected_files(self, text: str) -> list[AffectedFile]:
        seen: set[tuple[str, int | None, int | None]] = set()
        files: list[AffectedFile] = []
        for match in self.TS_LOCATION_RE.finditer(text):
            item = (
                match.group("path"),
                int(match.group("line")),
                int(match.group("column")),
            )
            if item not in seen:
                seen.add(item)
                files.append(AffectedFile(path=item[0], line=item[1], column=item[2]))
        if files:
            return files
        generic_file = re.search(r"([\w./@-]+\.(?:ts|tsx|js|jsx|css|json))", text)
        if generic_file:
            return [AffectedFile(path=generic_file.group(1))]
        return []

    def _evidence_text(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        interesting = [
            line for line in lines
            if any(token in line.lower() for token in (
                "error",
                "failed",
                "exception",
                "econnrefused",
                "mismatch",
                "timeout",
                "ts",
            ))
        ]
        selected = interesting[:5] or lines[-5:]
        excerpt = "\n".join(selected)
        return excerpt[:self.max_evidence_chars]

    def _summary(
        self,
        error_type: str,
        failed_step: str,
        affected_files: list[AffectedFile],
        evidence_text: str,
    ) -> str:
        location = ""
        if affected_files:
            first = affected_files[0]
            if first.line is not None:
                location = f" at {first.path}:{first.line}:{first.column}"
            else:
                location = f" in {first.path}"
        if error_type == "typescript_error":
            return f"TypeScript failed during {failed_step}{location}."
        if error_type == "api_contract_mismatch":
            return f"API contract mismatch detected during {failed_step}{location}."
        if error_type == "api_connection_error":
            return "API connection failed; verify backend availability or switch to mock profile."
        if error_type == "browser_console_error":
            return f"Browser verification reported console/runtime errors{location}."
        if error_type == "sandbox_infra_error":
            return "Sandbox infrastructure failed before project verification completed."
        if error_type == "dependency_install_error":
            return "Dependency installation failed."
        if error_type == "unit_test_failure":
            return f"Unit tests failed during {failed_step}{location}."
        if error_type == "timeout":
            return f"Verification timed out during {failed_step}."
        if evidence_text:
            return f"{failed_step} failed: {evidence_text.splitlines()[0]}"
        return f"{failed_step} failed with {error_type}."

    def _suggested_next_action(self, error_type: str, affected_files: list[AffectedFile]) -> str:
        target = affected_files[0].path if affected_files else "the relevant source file"
        if error_type == "typescript_error":
            return f"Fix the TypeScript error in {target}, then rerun build verification."
        if error_type == "api_contract_mismatch":
            return "Align UI field access with api_contract.json or update the contract through review."
        if error_type == "api_connection_error":
            return "Check backend availability; if unavailable, rerun with mock profile before editing UI code."
        if error_type == "browser_console_error":
            return f"Fix the runtime error in {target} and rerun browser verification."
        if error_type == "sandbox_infra_error":
            return "Do not edit business code; fix Docker/sandbox environment first."
        if error_type == "dependency_install_error":
            return "Inspect package.json and lockfile/dependency compatibility."
        if error_type == "unit_test_failure":
            return "Use the failing assertion to decide whether code or test expectations are wrong."
        if error_type == "timeout":
            return "Check for hanging dev server, test deadlock, or insufficient timeout."
        return "Inspect raw_refs and route the task to Coder or Reviewer."

    def _retryable(self, error_type: str) -> bool:
        return error_type not in {"sandbox_infra_error", "api_connection_error", "timeout"}

    def _confidence(
        self,
        error_type: str,
        affected_files: list[AffectedFile],
        evidence_text: str,
    ) -> float:
        if error_type == "unknown_error":
            return 0.35
        score = 0.72
        if affected_files:
            score += 0.12
        if evidence_text:
            score += 0.08
        return min(score, 0.95)

    def _read_optional(self, path: str | None) -> str:
        if not path:
            return ""
        target = Path(path)
        if not target.exists():
            return ""
        return target.read_text(encoding="utf-8", errors="replace")[-20000:]
