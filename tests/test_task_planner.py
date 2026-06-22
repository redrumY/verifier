from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.task_planner import TaskPlanner


def test_frontend_plan_has_standard_six_tasks(tmp_path: Path):
    planner = TaskPlanner(tmp_path)

    plan = planner.create_frontend_plan("生成一个可运行 React 网页")

    assert [task.id for task in plan.tasks] == [
        "task_001",
        "task_002",
        "task_003",
        "task_004",
        "task_005",
        "task_006",
    ]
    assert plan.tasks[0].owner == "planner"
    assert plan.tasks[1].owner == "coder"
    assert plan.tasks[2].owner == "verifier"
    assert plan.tasks[1].depends_on == ["task_001"]
    assert plan.tasks[1].context_refs == ["spec.json"]
    assert plan.ready_tasks()[0].id == "task_001"


def test_task_plan_blocks_tasks_until_dependencies_complete(tmp_path: Path):
    planner = TaskPlanner(tmp_path)
    plan = planner.create_frontend_plan("生成一个可运行 React 网页")

    with pytest.raises(ValueError, match="blocked"):
        planner.update_task(plan, "task_002", status="in_progress")

    planner.update_task(plan, "task_001", status="completed", result={"spec_path": "spec.json"})
    ready = plan.ready_tasks()

    assert [task.id for task in ready] == ["task_002"]
    planner.update_task(plan, "task_002", status="in_progress")
    assert plan.task_by_id("task_002").status == "in_progress"


def test_task_plan_persists_json_shape(tmp_path: Path):
    planner = TaskPlanner(tmp_path)
    plan = planner.create_frontend_plan("生成一个可运行 React 网页")

    path = planner.save_plan(plan)
    payload = json.loads(path.read_text())
    loaded = planner.load_plan(path)

    assert payload["tasks"][0]["id"] == "task_001"
    assert payload["tasks"][0]["owner"] == "planner"
    assert payload["tasks"][0]["status"] == "pending"
    assert payload["tasks"][0]["depends_on"] == []
    assert payload["tasks"][0]["context_refs"] == []
    assert payload["tasks"][0]["result"] is None
    assert loaded.goal == plan.goal


def test_task_planner_rejects_paths_outside_root(tmp_path: Path):
    planner = TaskPlanner(tmp_path)
    plan = planner.create_frontend_plan("生成一个可运行 React 网页")

    with pytest.raises(ValueError, match="escapes"):
        planner.save_plan(plan, tmp_path.parent / "plan.json")
