from __future__ import annotations

import json
import types

from harness.repo_scanner import RepoFacts
from harness.requirement_analyzer import LLMRequirementAnalyzer, RequirementAnalyzer


class FakeGateway:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.content, Exception):
            raise self.content
        return types.SimpleNamespace(
            content=self.content,
            request_id="req_123",
            model="planner-model",
            provider="fake",
        )


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


def test_llm_requirement_analyzer_uses_structured_prompt():
    facts = RepoFacts(
        root="/tmp/project",
        frontend_stack="create-react-app",
        backend_stack="express",
        api_proxy="http://localhost:5000",
        relevant_files=["frontend/src/pages/Dashboard.jsx"],
    )
    gateway = FakeGateway(json.dumps({
        "task_type": "ui_api_integration",
        "target_area": "dashboard",
        "clarification_needed": False,
        "questions": [],
        "assumptions": ["复用现有 goals service"],
        "acceptance_criteria": ["Dashboard 显示目标总数"],
        "constraints": ["不修改数据库 schema"],
        "risk_level": "medium",
    }, ensure_ascii=False))

    spec = LLMRequirementAnalyzer(gateway).analyze(
        "在 Dashboard 增加目标统计摘要组件并接入 /api/goals 接口",
        facts,
    )

    assert spec.task_type == "ui_api_integration"
    assert spec.target_area == "dashboard"
    assert spec.acceptance_criteria == ["Dashboard 显示目标总数"]
    assert spec.metadata["analyzer"] == "llm_structured"
    assert spec.metadata["llm_request_id"] == "req_123"

    call = gateway.calls[0]
    assert call["role"] == "planner"
    assert "one valid JSON object only" in call["system"]
    assert "Repository facts" in call["messages"][0]["content"]
    assert "frontend/src/pages/Dashboard.jsx" in call["messages"][0]["content"]


def test_llm_requirement_analyzer_falls_back_on_bad_response():
    facts = RepoFacts(root="/tmp/project", frontend_stack="vite")
    gateway = FakeGateway("not json")

    spec = LLMRequirementAnalyzer(gateway).analyze("优化这个项目", facts)

    assert spec.task_type == "ambiguous_improvement"
    assert spec.clarification_needed is True
    assert spec.metadata["analyzer"] == "rule_based_fallback"
    assert "llm_error" in spec.metadata
