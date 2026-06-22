from __future__ import annotations

from harness.dynamic_task_planner import DynamicTaskPlanner
from harness.repo_scanner import RepoFacts
from harness.requirement_analyzer import RequirementAnalyzer


def test_dynamic_planner_creates_ui_api_task_graph():
    facts = RepoFacts(
        root="/tmp/project",
        frontend_stack="create-react-app",
        backend_stack="express",
        api_proxy="http://localhost:5000",
        relevant_files=[
            "frontend/src/pages/Dashboard.jsx",
            "frontend/src/features/goals/goalService.js",
            "backend/routes/goalRoutes.js",
        ],
    )
    request = "在 Dashboard 增加目标统计摘要组件并接入 /api/goals 接口"
    requirement = RequirementAnalyzer().analyze(request, facts)

    plan = DynamicTaskPlanner().create_plan(request, requirement, facts)

    assert plan.metadata["planner_mode"] == "dynamic"
    assert plan.metadata["task_type"] == "ui_api_integration"
    assert len(plan.tasks) == 5
    assert [task.owner for task in plan.tasks] == ["planner", "planner", "coder", "verifier", "reviewer"]
    assert plan.tasks[1].title == "定义 UI 与 API contract"
    assert plan.tasks[2].depends_on == ["task_002"]
    assert "frontend/src/pages/Dashboard.jsx" in plan.tasks[2].context_refs
    assert any("loading/success/empty/error" in item for item in plan.tasks[2].acceptance_criteria)


def test_dynamic_planner_blocks_ambiguous_request():
    facts = RepoFacts(root="/tmp/project", frontend_stack="vite")
    request = "优化这个项目"
    requirement = RequirementAnalyzer().analyze(request, facts)

    plan = DynamicTaskPlanner().create_plan(request, requirement, facts)

    assert plan.plan_id == "clarification_required_plan"
    assert plan.metadata["planner_mode"] == "clarification_required"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].status == "blocked"
    assert plan.ready_tasks() == []
    assert plan.tasks[0].result["questions"]
