"""Dynamic task graph planner for existing-repo coding-agent work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .repo_scanner import RepoFacts
from .requirement_analyzer import RequirementSpec
from .task_planner import AgentTask, TaskPlan


@dataclass(frozen=True)
class TaskTemplate:
    task_type: str
    owner: str
    title: str
    description: str
    depends_on: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()


class DynamicTaskPlanner:
    """Build a dependency-aware TaskPlan from requirement and repo facts."""

    def create_plan(
        self,
        request: str,
        requirement: RequirementSpec,
        repo_facts: RepoFacts,
    ) -> TaskPlan:
        if requirement.clarification_needed:
            return self._clarification_plan(request, requirement, repo_facts)

        templates = self._templates(requirement, repo_facts)
        tasks = [
            AgentTask(
                id=f"task_{index:03d}",
                owner=template.owner,
                status="pending",
                title=template.title,
                description=template.description,
                depends_on=[f"task_{int(dep):03d}" if dep.isdigit() else dep for dep in template.depends_on],
                context_refs=list(dict.fromkeys([*template.context_refs, *repo_facts.relevant_files[:8]])),
                acceptance_criteria=list(dict.fromkeys([
                    *template.acceptance_criteria,
                    *requirement.acceptance_criteria,
                ])),
            )
            for index, template in enumerate(templates, start=1)
        ]
        return TaskPlan(
            goal=request,
            tasks=tasks,
            plan_id="dynamic_task_plan",
            metadata=self._metadata(requirement, repo_facts, mode="dynamic"),
        )

    def _clarification_plan(
        self,
        request: str,
        requirement: RequirementSpec,
        repo_facts: RepoFacts,
    ) -> TaskPlan:
        task = AgentTask(
            id="task_001",
            owner="planner",
            status="blocked",
            title="等待用户澄清需求",
            description="The request is too broad to execute safely. Ask focused clarification questions.",
            context_refs=repo_facts.relevant_files[:8],
            acceptance_criteria=[
                "User chooses a concrete optimization target.",
                "Scope and verification method are explicit before code edits.",
            ],
            result={
                "questions": requirement.questions,
                "safe_default_plan": [
                    "Scan repository structure.",
                    "Propose 2-3 low-risk candidate tasks.",
                    "Wait for user approval before edits.",
                ],
            },
        )
        return TaskPlan(
            goal=request,
            tasks=[task],
            plan_id="clarification_required_plan",
            metadata=self._metadata(requirement, repo_facts, mode="clarification_required"),
        )

    def _templates(self, requirement: RequirementSpec, repo_facts: RepoFacts) -> list[TaskTemplate]:
        task_type = requirement.task_type
        base = [
            TaskTemplate(
                task_type="repo_inspection",
                owner="planner",
                title="扫描仓库结构和相关代码路径",
                description="Confirm framework, scripts, data flow, and files relevant to the request.",
                context_refs=tuple(repo_facts.relevant_files[:8]),
                acceptance_criteria=(
                    "Relevant files and commands are identified.",
                    "Assumptions and risks are recorded.",
                ),
            )
        ]
        if task_type == "generate_frontend_project":
            return self._frontend_generation_templates()
        if task_type == "ui_api_integration":
            return [
                *base,
                TaskTemplate(
                    task_type="api_contract",
                    owner="planner",
                    title="定义 UI 与 API contract",
                    description="Document endpoint, response shape, mock data, and states to verify.",
                    depends_on=("1",),
                    context_refs=("api_contract.json", "mock_responses.json"),
                    acceptance_criteria=(
                        "API contract or existing service boundary is explicit.",
                        "Mock success/empty/error data is available for verification.",
                    ),
                ),
                TaskTemplate(
                    task_type="code_edit",
                    owner="coder",
                    title="实现 UI + API 联调改动",
                    description="Edit the smallest set of frontend files needed for the requested behavior.",
                    depends_on=("2",),
                    acceptance_criteria=("Patch is limited to relevant UI/API files.",),
                ),
                TaskTemplate(
                    task_type="sandbox_verification",
                    owner="verifier",
                    title="在 sandbox 中执行 build 和浏览器验证",
                    description="Run profile-driven install/build/test/browser checks outside the source project.",
                    depends_on=("3",),
                    acceptance_criteria=(
                        "Build/test/browser report is produced.",
                        "Source project remains clean after verification.",
                    ),
                ),
                TaskTemplate(
                    task_type="review",
                    owner="reviewer",
                    title="审查 diff、验证报告和剩余风险",
                    description="Review changed files, verification artifacts, and open issues before final handoff.",
                    depends_on=("4",),
                    acceptance_criteria=("Final report names changed files, tests, screenshots, and open issues.",),
                ),
            ]
        if task_type in {"existing_frontend_change", "bugfix", "test_work", "general_code_change"}:
            return [
                *base,
                TaskTemplate(
                    task_type="code_edit",
                    owner="coder",
                    title=self._edit_title(task_type),
                    description="Make a scoped code change based on the inspected files and acceptance criteria.",
                    depends_on=("1",),
                    acceptance_criteria=("Patch is small, relevant, and reviewable.",),
                ),
                TaskTemplate(
                    task_type="verification",
                    owner="verifier",
                    title="运行匹配项目类型的验证",
                    description="Run deterministic commands selected from repo facts and test profile.",
                    depends_on=("2",),
                    acceptance_criteria=("Verification report is produced.",),
                ),
                TaskTemplate(
                    task_type="review",
                    owner="reviewer",
                    title="审查结果并输出最终报告",
                    description="Review diff and verification report before marking the task complete.",
                    depends_on=("3",),
                    acceptance_criteria=("Risks, tests, and open issues are explicit.",),
                ),
            ]
        return base

    def _frontend_generation_templates(self) -> list[TaskTemplate]:
        return [
            TaskTemplate(
                task_type="spec",
                owner="planner",
                title="解析用户自然语言，生成项目规格",
                description="Convert the user request into a frontend project spec.",
                acceptance_criteria=("Create spec.json",),
            ),
            TaskTemplate(
                task_type="project_generation",
                owner="coder",
                title="生成完整前端工程",
                description="Create Vite/React/TypeScript project files from spec.json.",
                depends_on=("1",),
                context_refs=("spec.json",),
                acceptance_criteria=("package.json exists", "src/App.tsx exists"),
            ),
            TaskTemplate(
                task_type="verification",
                owner="verifier",
                title="安装依赖、构建并验证浏览器打开",
                description="Run install/build/browser checks and collect artifacts.",
                depends_on=("2",),
                acceptance_criteria=("npm run build succeeds", "page opens without console errors"),
            ),
            TaskTemplate(
                task_type="report",
                owner="reviewer",
                title="输出最终报告",
                description="Summarize generated files, verification result, and known issues.",
                depends_on=("3",),
                acceptance_criteria=("Final report includes URL or dist path",),
            ),
        ]

    def _edit_title(self, task_type: str) -> str:
        return {
            "existing_frontend_change": "实现前端 UI 改动",
            "bugfix": "修复复现到的问题",
            "test_work": "补充或修复测试",
            "general_code_change": "实现受控代码改动",
        }.get(task_type, "实现代码改动")

    def _metadata(self, requirement: RequirementSpec, repo_facts: RepoFacts, mode: str) -> dict[str, Any]:
        return {
            "planner_mode": mode,
            "task_type": requirement.task_type,
            "target_area": requirement.target_area,
            "risk_level": requirement.risk_level,
            "assumptions": requirement.assumptions,
            "questions": requirement.questions,
            "constraints": requirement.constraints,
            "repo_facts": repo_facts.to_dict(),
            "workflow": "repo scan -> requirement analysis -> dynamic task graph -> validation -> execution",
        }
