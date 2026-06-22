from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from harness.task_planner import TaskPlanner


class FakePlannerGateway:
    def __init__(self):
        self.calls = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            content=json.dumps({
                "task_type": "ui_api_integration",
                "target_area": "dashboard",
                "clarification_needed": False,
                "questions": [],
                "assumptions": ["复用现有 API service"],
                "acceptance_criteria": ["浏览器验证无 console error"],
                "constraints": ["不修改数据库 schema"],
                "risk_level": "medium",
            }, ensure_ascii=False),
            request_id="req_plan",
            model="planner-model",
            provider="fake",
        )


def test_frontend_plan_has_standard_six_tasks(tmp_path: Path):
    planner = TaskPlanner(tmp_path)

    plan = planner.create_frontend_plan("生成一个可运行 React 网页")

    assert plan.metadata["planner_mode"] == "template_fallback"
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


def test_task_planner_creates_dynamic_plan_from_repo_facts(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"start": "node backend/server.js"},
        "dependencies": {"express": "^4.18.0"},
    }))
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(json.dumps({
        "proxy": "http://localhost:5000",
        "scripts": {"start": "react-scripts start", "build": "react-scripts build"},
        "dependencies": {"react": "^18.0.0", "react-scripts": "5.0.1"},
    }))
    (frontend / "src").mkdir()
    (frontend / "src" / "App.js").write_text("export default function App() { return null }")

    planner = TaskPlanner(tmp_path)
    plan = planner.create_dynamic_plan("在 Dashboard 增加目标统计摘要组件并接入 /api/goals 接口")

    assert plan.metadata["planner_mode"] == "dynamic"
    assert plan.metadata["repo_facts"]["frontend_stack"] == "create-react-app"
    assert plan.metadata["repo_facts"]["backend_stack"] == "express"
    assert [task.owner for task in plan.tasks] == ["planner", "planner", "coder", "verifier", "reviewer"]


def test_task_planner_dynamic_plan_blocks_vague_request(tmp_path: Path):
    planner = TaskPlanner(tmp_path)

    plan = planner.create_dynamic_plan("优化这个项目")

    assert plan.metadata["planner_mode"] == "clarification_required"
    assert plan.tasks[0].status == "blocked"
    assert plan.ready_tasks() == []


def test_task_planner_dynamic_plan_can_use_llm_requirement_analyzer(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"start": "node backend/server.js"},
        "dependencies": {"express": "^4.18.0"},
    }))
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(json.dumps({
        "proxy": "http://localhost:5000",
        "scripts": {"start": "react-scripts start", "build": "react-scripts build"},
        "dependencies": {"react": "^18.0.0", "react-scripts": "5.0.1"},
    }))
    (frontend / "src").mkdir()
    (frontend / "src" / "App.js").write_text("export default function App() { return null }")

    gateway = FakePlannerGateway()
    planner = TaskPlanner(tmp_path, gateway=gateway, use_llm_planner=True)
    plan = planner.create_dynamic_plan("在 Dashboard 增加目标统计摘要组件")

    assert gateway.calls
    assert plan.metadata["analyzer"] == "llm_structured"
    assert plan.metadata["requirement_metadata"]["llm_request_id"] == "req_plan"
    assert plan.metadata["task_type"] == "ui_api_integration"
