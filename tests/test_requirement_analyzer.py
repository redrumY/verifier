from __future__ import annotations

from harness.repo_scanner import RepoFacts
from harness.requirement_analyzer import RequirementAnalyzer


def test_requirement_analyzer_blocks_vague_improvement():
    facts = RepoFacts(root="/tmp/project", frontend_stack="vite")

    spec = RequirementAnalyzer().analyze("优化这个项目", facts)

    assert spec.task_type == "ambiguous_improvement"
    assert spec.clarification_needed is True
    assert spec.risk_level == "high"
    assert spec.questions


def test_requirement_analyzer_detects_ui_api_integration():
    facts = RepoFacts(
        root="/tmp/project",
        frontend_stack="create-react-app",
        backend_stack="express",
        api_proxy="http://localhost:5000",
    )

    spec = RequirementAnalyzer().analyze(
        "在 Dashboard 增加目标统计摘要组件并接入 /api/goals 接口",
        facts,
    )

    assert spec.task_type == "ui_api_integration"
    assert spec.target_area == "dashboard"
    assert spec.clarification_needed is False
    assert any("API contract" in item for item in spec.acceptance_criteria)
    assert any("mock" in item.lower() for item in spec.constraints)
