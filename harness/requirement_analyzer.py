"""Requirement analysis before dynamic task planning."""

from __future__ import annotations

import re
import json
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

    TASK_TYPES = {
        "generate_frontend_project",
        "ui_api_integration",
        "existing_frontend_change",
        "bugfix",
        "test_work",
        "ambiguous_improvement",
        "general_code_change",
    }
    RISK_LEVELS = {"low", "medium", "high"}

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


class LLMRequirementAnalyzer:
    """Use the planner model to produce RequirementSpec JSON, with rule fallback."""

    def __init__(
        self,
        gateway: Any,
        fallback: RequirementAnalyzer | None = None,
        role: str = "planner",
        max_tokens: int = 1600,
    ):
        self.gateway = gateway
        self.fallback = fallback or RequirementAnalyzer()
        self.role = role
        self.max_tokens = max_tokens

    def analyze(self, request: str, repo_facts: RepoFacts) -> RequirementSpec:
        fallback_spec = self.fallback.analyze(request, repo_facts)
        try:
            response = self.gateway.call(
                role=self.role,
                messages=[{"role": "user", "content": self.user_prompt(request, repo_facts)}],
                system=self.system_prompt(),
                max_tokens=self.max_tokens,
                temperature=0.0,
            )
            payload = self._parse_json(self._content_to_text(response.content))
            spec = self._spec_from_payload(payload, fallback_spec)
            spec.metadata.update({
                **fallback_spec.metadata,
                **spec.metadata,
                "analyzer": "llm_structured",
                "llm_request_id": getattr(response, "request_id", None),
                "llm_model": getattr(response, "model", None),
                "llm_provider": getattr(response, "provider", None),
            })
            return spec
        except Exception as exc:
            fallback_spec.metadata.update({
                "analyzer": "rule_based_fallback",
                "llm_error": f"{type(exc).__name__}: {exc}",
            })
            return fallback_spec

    def system_prompt(self) -> str:
        return (
            "You are the requirement analysis component inside a coding agent.\n"
            "Your job is to classify the user's coding request before any code is edited.\n"
            "You must output one valid JSON object only. Do not write Markdown. "
            "Do not include chain-of-thought or hidden reasoning.\n"
            "If the request is too broad or unsafe to execute, set "
            "clarification_needed=true and provide focused questions.\n"
            "Allowed task_type values: generate_frontend_project, ui_api_integration, "
            "existing_frontend_change, bugfix, test_work, ambiguous_improvement, "
            "general_code_change.\n"
            "Allowed risk_level values: low, medium, high."
        )

    def user_prompt(self, request: str, repo_facts: RepoFacts) -> str:
        schema = {
            "task_type": "one allowed task_type",
            "target_area": "dashboard|auth|api|tests|frontend_ui|unknown|other precise module",
            "clarification_needed": False,
            "questions": ["question when clarification_needed is true"],
            "assumptions": ["execution assumptions"],
            "acceptance_criteria": ["observable completion criteria"],
            "constraints": ["scope, safety, sandbox, secret-handling constraints"],
            "risk_level": "low|medium|high",
        }
        return (
            "Classify this coding-agent request and return JSON matching the schema.\n\n"
            f"User request:\n{request.strip()}\n\n"
            "Repository facts:\n"
            f"{json.dumps(repo_facts.to_dict(), ensure_ascii=False, indent=2)}\n\n"
            "Output schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            "Rules:\n"
            "- Prefer ui_api_integration when the request mentions API/interface/backend integration.\n"
            "- Prefer existing_frontend_change when the task modifies an existing UI without backend integration.\n"
            "- Use ambiguous_improvement with clarification_needed=true for broad requests like optimize/improve without a target module.\n"
            "- Never suggest editing secrets, node_modules, dist, coverage, or generated sandbox artifacts.\n"
            "- Keep acceptance criteria verifiable by build/test/browser/sandbox checks."
        )

    def _spec_from_payload(self, payload: dict[str, Any], fallback: RequirementSpec) -> RequirementSpec:
        task_type = self._allowed(
            payload.get("task_type"),
            RequirementAnalyzer.TASK_TYPES,
            fallback.task_type,
        )
        risk_level = self._allowed(
            payload.get("risk_level"),
            RequirementAnalyzer.RISK_LEVELS,
            fallback.risk_level,
        )
        clarification_needed = payload.get("clarification_needed")
        if not isinstance(clarification_needed, bool):
            clarification_needed = fallback.clarification_needed
        return RequirementSpec(
            request=fallback.request,
            task_type=task_type,
            target_area=str(payload.get("target_area") or fallback.target_area),
            clarification_needed=clarification_needed,
            questions=self._string_list(payload.get("questions"), fallback.questions),
            assumptions=self._string_list(payload.get("assumptions"), fallback.assumptions),
            acceptance_criteria=self._string_list(
                payload.get("acceptance_criteria"),
                fallback.acceptance_criteria,
            ),
            constraints=self._string_list(payload.get("constraints"), fallback.constraints),
            risk_level=risk_level,
            metadata={"llm_payload_keys": sorted(payload.keys())},
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(cleaned[start:end + 1])
        if not isinstance(payload, dict):
            raise ValueError("planner response must be a JSON object")
        return payload

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    else:
                        parts.append(str(block))
                elif getattr(block, "type", None) == "text":
                    parts.append(str(getattr(block, "text", "")))
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(content)

    def _allowed(self, value: Any, allowed: set[str], fallback: str) -> str:
        text = str(value or "").strip()
        return text if text in allowed else fallback

    def _string_list(self, value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            return fallback
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or fallback
