from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.frontend_generator import FrontendGenerator
from harness.sandbox_runner import (
    DockerSandboxRunner,
    SANDBOX_STATUSES,
    frontend_ui_component_profile,
)
from harness.verifier import CommandResult


def test_frontend_profile_has_expected_commands_and_browser_matrix():
    profile = frontend_ui_component_profile()

    assert profile.name == "frontend-ui-component"
    assert profile.project_type == "frontend"
    assert [command.name for command in profile.commands] == ["install", "typecheck", "build", "test"]
    assert profile.commands[0].required is True
    assert profile.commands[1].required is False
    assert [viewport.name for viewport in profile.browser.viewports] == ["desktop", "mobile"]
    assert "prepared" in SANDBOX_STATUSES


def test_prepare_run_copies_project_and_writes_docker_assets(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")
    (tmp_path / "app" / "node_modules").mkdir()
    (tmp_path / "app" / "node_modules" / "ignored.txt").write_text("ignore me")
    runner = DockerSandboxRunner(tmp_path)

    sandbox_run = runner.prepare_run("app", patch_text="diff --git a/a b/a\n", run_id="run_test")

    run_dir = Path(sandbox_run.run_dir)
    copied_project = Path(sandbox_run.project_dir)
    assert copied_project.exists()
    assert (copied_project / "package.json").exists()
    assert not (copied_project / "node_modules").exists()
    assert Path(sandbox_run.profile_path).exists()
    assert Path(sandbox_run.dockerfile_path).exists()
    assert Path(sandbox_run.compose_path).exists()
    assert Path(sandbox_run.run_script_path).exists()
    assert (run_dir / "sandbox" / "browser_check.mjs").exists()
    assert (run_dir / "sandbox" / "write_report.mjs").exists()
    assert (run_dir / "patch.diff").read_text() == "diff --git a/a b/a\n"

    profile = json.loads(Path(sandbox_run.profile_path).read_text())
    assert profile["name"] == "frontend-ui-component"
    assert profile["browser"]["viewports"][0]["name"] == "desktop"


def test_prepare_run_manifest_can_be_loaded(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")
    runner = DockerSandboxRunner(tmp_path)
    sandbox_run = runner.prepare_run("app", run_id="run_load")

    loaded = runner.load_run(sandbox_run.run_dir)

    assert loaded.run_id == "run_load"
    assert loaded.profile.name == "frontend-ui-component"
    assert loaded.profile.browser.viewports[1].name == "mobile"


def test_run_uses_docker_compose_and_writes_result(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")
    calls = []

    def fake_docker(command: list[str], cwd: Path, timeout: int) -> CommandResult:
        calls.append({"command": command, "cwd": cwd, "timeout": timeout})
        outputs = cwd / "outputs"
        outputs.mkdir(exist_ok=True)
        (outputs / "verification-report.json").write_text(json.dumps({"build": "passed"}))
        return CommandResult(
            name="docker_sandbox",
            command=command,
            status="passed",
            returncode=0,
            stdout="ok",
        )

    runner = DockerSandboxRunner(tmp_path, runner=fake_docker)
    sandbox_run = runner.prepare_run("app", run_id="run_exec")

    result = runner.run(sandbox_run)

    assert result.status == "passed"
    assert calls[0]["command"][:3] == ["docker", "compose", "-f"]
    assert calls[0]["cwd"] == Path(sandbox_run.run_dir)
    assert result.report_path is not None
    assert Path(result.report_path).exists()
    assert (Path(sandbox_run.outputs_dir) / "sandbox-result.json").exists()


def test_run_reports_failure_when_docker_fails(tmp_path: Path):
    FrontendGenerator(tmp_path).generate_from_natural_language("生成 React 页面", "app")

    def fake_docker(command: list[str], cwd: Path, timeout: int) -> CommandResult:
        return CommandResult(
            name="docker_sandbox",
            command=command,
            status="failed",
            returncode=1,
            stderr="build failed",
        )

    runner = DockerSandboxRunner(tmp_path, runner=fake_docker)
    sandbox_run = runner.prepare_run("app", run_id="run_fail")

    result = runner.run(sandbox_run.run_dir)

    assert result.status == "failed"
    assert result.report_path is None
    assert "build failed" in result.stderr


def test_sandbox_runner_rejects_paths_outside_root(tmp_path: Path):
    runner = DockerSandboxRunner(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        runner.prepare_run(tmp_path.parent)
