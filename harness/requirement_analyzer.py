"""Requirement analysis before dynamic task planning."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .repo_scanner import RepoFacts


@dataclass
class RequirementSpec:
    request: str
    task_type: str
    target_area: str
    clarification_needed: bool
    questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RequirementAnalyzer:
    """Rule-based analyzer with the same interface an LLM planner can later fill."""

    VAGUE_PATTERNS = (
        "优化",
        "改进",
        "提升",
        "完善",
        "弄好",
        "搞一下",
        "make it better",
        "improve",
        "optimize",
    )

    def analyze(self, request: str, repo_facts: RepoFacts) -> RequirementSpec:
        normalized = re.sub(r"\s+", " ", request.strip())
        lowered = normalized.lower()
        task_type = self._task_type(normalized, lowered, repo_facts)
        target_area = self._target_area(normalized, lowered)
        clarification_needed = self._needs_clarification(normalized, lowered, task_type)
        questions = self._questions(task_type, target_area) if clarification_needed else []
        assumptions = self._assumptions(task_type, repo_facts)
        acceptance = self._acceptance(task_type, target_area)
        constraints = self._constraints(task_type, repo_facts)
        return RequirementSpec(
            request=normalized,
            task_type=task_type,
            target_area=target_area,
            clarification_needed=clarification_needed,
            questions=questions,
            assumptions=assumptions,
            acceptance_criteria=acceptance,
            constraints=constraints,
            risk_level=self._risk_level(task_type, clarification_needed),
            metadata={
                "frontend_stack": repo_facts.frontend_stack,
                "backend_stack": repo_facts.backend_stack,
                "repo_warnings": repo_facts.warnings,
            },
        )

    def _task_type(self, request: str, lowered: str, repo_facts: RepoFacts) -> str:
        if any(word in request for word in ("生成", "创建", "新建")) and any(
            word in lowered for word in ("react", "前端", "网页", "页面", "app")
        ) and repo_facts.frontend_stack == "unknown":
            return "generate_frontend_project"
        if any(word in lowered for word in ("api", "接口", "联调", "backend", "后端")):
            return "ui_api_integration"
        if any(word in request for word in ("组件", "页面", "UI", "ui", "按钮", "表单")):
            return "existing_frontend_change"
        if any(word in lowered for word in ("bug", "fix", "error", "报错", "修复", "失败")):
            return "bugfix"
        if any(word in lowered for word in ("test", "测试", "coverage")):
            return "test_work"
        if any(pattern in lowered for pattern in self.VAGUE_PATTERNS):
            return "ambiguous_improvement"
        return "general_code_change"

    def _target_area(self, request: str, lowered: str) -> str:
        if any(word in lowered for word in ("dashboard", "看板", "仪表盘")):
            return "dashboard"
        if any(word in lowered for word in ("login", "register", "auth", "登录", "注册", "认证")):
            return "auth"
        if any(word in lowered for word in ("api", "接口", "联调")):
            return "api"
        if any(word in lowered for word in ("test", "测试")):
            return "tests"
        if any(word in lowered for word in ("ui", "组件", "页面")):
            return "frontend_ui"
        return "unknown"

    def _needs_clarification(self, request: str, lowered: str, task_type: str) -> bool:
        if not request:
            return True
        if task_type == "ambiguous_improvement":
            concrete_markers = ("dashboard", "api", "接口", "组件", "页面", "bug", "测试", "build")
            return not any(marker in lowered for marker in concrete_markers)
        if len(request) < 8 and task_type == "general_code_change":
            return True
        return False

    def _questions(self, task_type: str, target_area: str) -> list[str]:
        questions = [
            "你希望优化 UI、性能、代码结构、测试覆盖，还是修复具体 bug？",
            "是否允许修改后端接口或数据库 schema？",
            "完成标准是 build/test 通过，还是还需要浏览器截图验证？",
        ]
        if target_area == "unknown":
            questions.insert(0, "目标页面或模块是哪一个？")
        if task_type == "ambiguous_improvement":
            questions.append("是否先让我扫描仓库并提出 2-3 个低风险候选任务？")
        return questions

    def _assumptions(self, task_type: str, repo_facts: RepoFacts) -> list[str]:
        assumptions = [
            "先在 sandbox 或隔离副本里验证，不直接污染源项目。",
            "默认不写入真实 secret，不修改本地 .env。",
        ]
        if task_type in {"ui_api_integration", "existing_frontend_change"}:
            assumptions.append("优先复用现有前端状态和 API contract。")
        if repo_facts.backend_stack != "unknown":
            assumptions.append("第一轮 UI 验证不依赖真实后端数据库，必要时使用 mock API。")
        return assumptions

    def _acceptance(self, task_type: str, target_area: str) -> list[str]:
        common = [
            "相关项目 build 通过。",
            "改动范围和风险在最终报告中说明。",
        ]
        if task_type == "ui_api_integration":
            return [
                "UI 使用明确的 API contract 或现有 service 层。",
                "loading/success/empty/error 状态有可验证路径。",
                "浏览器验证无 console error。",
                *common,
            ]
        if task_type == "existing_frontend_change":
            return [
                "目标 UI 在相关页面可见。",
                "移动端和桌面端布局不明显破坏。",
                *common,
            ]
        if task_type == "bugfix":
            return [
                "复现路径或失败日志被记录。",
                "修复后同一验证步骤通过。",
                *common,
            ]
        if task_type == "generate_frontend_project":
            return [
                "package.json、入口文件和页面文件存在。",
                "npm install / build / browser verify 可以执行。",
            ]
        return common

    def _constraints(self, task_type: str, repo_facts: RepoFacts) -> list[str]:
        constraints = [
            "不要提交 node_modules、dist、coverage 或 sandbox 产物。",
            "如果需求不清楚，先 block/提问，不要扩大范围乱改。",
        ]
        if repo_facts.backend_stack != "unknown":
            constraints.append("除非任务明确要求，不修改认证、数据库连接或后端 schema。")
        if task_type == "ui_api_integration":
            constraints.append("真实后端不可用时，使用 mock 验证 UI 状态并记录 open issue。")
        return constraints

    def _risk_level(self, task_type: str, clarification_needed: bool) -> str:
        if clarification_needed:
            return "high"
        if task_type in {"ui_api_integration", "bugfix"}:
            return "medium"
        return "low"
