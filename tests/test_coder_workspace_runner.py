from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.coder_workspace_runner import CoderWorkspaceRunner
from harness.task_planner import AgentTask, TaskPlan
from harness.verifier import CommandResult


def passing_runner(command: list[str], cwd: Path, timeout: int) -> CommandResult:
    name = "install" if command[:2] == ["npm", "install"] else command[-1]
    return CommandResult(name=name, command=command, status="passed", returncode=0)


def make_frontend_project(root: Path):
    app = root / "app"
    (app / "src").mkdir(parents=True)
    (app / "package.json").write_text(json.dumps({
        "scripts": {"build": "vite build"},
        "dependencies": {"vite": "^5.0.0", "react": "^18.0.0"},
    }))
    (app / "src" / "App.js").write_text("export default function App() { return 'old' }\n")
    (app / "node_modules").mkdir()
    (app / "node_modules" / "ignored.txt").write_text("ignored")
    return app


def make_plan() -> TaskPlan:
    return TaskPlan(
        goal="Update dashboard component",
        plan_id="test_plan",
        tasks=[
            AgentTask(
                id="task_001",
                owner="planner",
                status="completed",
                title="Inspect repo",
            ),
            AgentTask(
                id="task_002",
                owner="coder",
                status="pending",
                title="Implement UI change",
                description="Change the dashboard copy.",
                depends_on=["task_001"],
                context_refs=["src/App.js"],
                acceptance_criteria=["Build passes", "Diff is scoped"],
            ),
        ],
        metadata={
            "planner_mode": "dynamic",
            "task_type": "existing_frontend_change",
            "constraints": ["Do not edit node_modules"],
        },
    )


def test_coder_workspace_runner_isolates_edit_and_writes_diff(tmp_path: Path):
    app = make_frontend_project(tmp_path)
    plan = make_plan()

    def coder_callback(workspace_dir: Path, task_pack):
        target = workspace_dir / "src" / "App.js"
        target.write_text("export default function App() { return 'new' }\n")
        return {"summary": "updated copy", "task_id": task_pack.task_id}

    runner = CoderWorkspaceRunner(tmp_path, verifier_runner=passing_runner)
    result = runner.run_coder_task(
        plan,
        "task_002",
        source_project_dir="app",
        coder_callback=coder_callback,
        verification_mode="local",
        run_id="run_local",
    )

    assert result.status == "passed"
    assert result.task_status == "completed"
    assert result.files_changed == ["src/App.js"]
    assert "+export default function App() { return 'new' }" in result.diff
    assert (app / "src" / "App.js").read_text() == "export default function App() { return 'old' }\n"
    assert not (Path(result.run.workspace_dir) / "node_modules").exists()
    assert Path(result.run.task_pack_path).exists()
    assert Path(result.diff_path).exists()
    assert plan.task_by_id("task_002").status == "completed"
    assert plan.task_by_id("task_002").result["files_changed"] == ["src/App.js"]


def test_coder_workspace_runner_can_prepare_sandbox_from_workspace(tmp_path: Path):
    make_frontend_project(tmp_path)
    plan = make_plan()

    def coder_callback(workspace_dir: Path, task_pack):
        (workspace_dir / "src" / "App.js").write_text("export default function App() { return 'sandbox' }\n")

    runner = CoderWorkspaceRunner(tmp_path, verifier_runner=passing_runner)
    result = runner.run_coder_task(
        plan,
        "task_002",
        source_project_dir="app",
        coder_callback=coder_callback,
        verification_mode="sandbox",
        run_sandbox=False,
        run_id="run_sandbox_prepare",
    )

    assert result.status == "prepared"
    assert result.task_status == "in_progress"
    assert result.sandbox_run is not None
    sandbox_project = Path(result.sandbox_run["project_dir"])
    assert (sandbox_project / "src" / "App.js").read_text() == (
        "export default function App() { return 'sandbox' }\n"
    )
    assert Path(result.sandbox_run["dockerfile_path"]).exists()
    assert plan.task_by_id("task_002").status == "in_progress"


def test_coder_workspace_runner_rejects_non_coder_task(tmp_path: Path):
    make_frontend_project(tmp_path)
    plan = make_plan()
    runner = CoderWorkspaceRunner(tmp_path)

    with pytest.raises(ValueError, match="not coder"):
        runner.run_coder_task(plan, "task_001", source_project_dir="app", verification_mode="none")
