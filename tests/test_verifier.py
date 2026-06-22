from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.frontend_generator import FrontendGenerator
from harness.verifier import BrowserResult, CommandResult, Verifier


def passing_runner(command: list[str], cwd: Path, timeout: int) -> CommandResult:
    name = "install" if command[:2] == ["npm", "install"] else command[-1]
    return CommandResult(name=name, command=command, status="passed", returncode=0, stdout="ok")


def test_verifier_detects_frontend_project(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")
    verifier = Verifier(tmp_path, runner=passing_runner)

    assert verifier.detect_project_type("app") == "frontend"


def test_frontend_verification_plan_includes_expected_steps(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")
    verifier = Verifier(tmp_path, runner=passing_runner)

    plan = verifier.frontend_verification_plan("app")

    assert [step["name"] for step in plan] == ["install", "typecheck", "build", "test", "browser"]
    assert plan[0]["command"] == ["npm", "install"]
    assert plan[1]["command"] == ["npm", "run", "typecheck"]
    assert plan[2]["command"] == ["npm", "run", "build"]
    assert plan[3]["command"] == ["npm", "run", "test"]


def test_verify_frontend_outputs_passed_summary_and_report(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")

    def browser_runner(project_dir: Path, outputs_dir: Path) -> BrowserResult:
        screenshot = outputs_dir / "screenshot.png"
        screenshot.write_text("fake image bytes")
        return BrowserResult(status="passed", url="http://127.0.0.1:5173", screenshot=str(screenshot))

    verifier = Verifier(tmp_path, runner=passing_runner, browser_runner=browser_runner)

    report = verifier.verify("app")

    assert report.install == "passed"
    assert report.typecheck == "passed"
    assert report.build == "passed"
    assert report.tests == "passed"
    assert report.browser == "passed"
    assert report.console_errors == []
    assert report.screenshot is not None
    assert report.summary() == {
        "build": "passed",
        "tests": "passed",
        "browser": "passed",
        "console_errors": [],
        "screenshot": report.screenshot,
    }
    assert report.report_path is not None
    assert Path(report.report_path).exists()


def test_verify_frontend_blocks_later_steps_when_install_fails(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")

    def failing_install(command: list[str], cwd: Path, timeout: int) -> CommandResult:
        if command[:2] == ["npm", "install"]:
            return CommandResult(name="install", command=command, status="failed", returncode=1, stderr="network")
        return passing_runner(command, cwd, timeout)

    verifier = Verifier(tmp_path, runner=failing_install)

    report = verifier.verify("app")

    assert report.install == "failed"
    assert report.typecheck == "skipped"
    assert report.build == "skipped"
    assert report.tests == "skipped"
    assert report.browser == "skipped"
    assert report.browser_result is not None
    assert report.browser_result.skipped_reason == "npm install failed"


def test_browser_console_errors_fail_browser_step(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")

    def browser_runner(project_dir: Path, outputs_dir: Path) -> BrowserResult:
        return BrowserResult(status="failed", console_errors=["ReferenceError: x is not defined"])

    verifier = Verifier(tmp_path, runner=passing_runner, browser_runner=browser_runner)

    report = verifier.verify("app")

    assert report.browser == "failed"
    assert report.console_errors == ["ReferenceError: x is not defined"]


def test_unknown_project_type_is_reported(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    verifier = Verifier(tmp_path, runner=passing_runner)

    report = verifier.verify("empty")

    assert report.project_type == "unknown"
    assert report.browser == "skipped"
    assert report.report_path is not None


def test_verifier_rejects_paths_outside_root(tmp_path: Path):
    verifier = Verifier(tmp_path, runner=passing_runner)

    with pytest.raises(ValueError, match="escapes"):
        verifier.verify(tmp_path.parent)
