"""Structured task planning for frontend-generation agent workflows."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TASK_STATUSES = {"pending", "in_progress", "completed", "failed", "blocked"}


@dataclass
class AgentTask:
    id: str
    owner: str
    status: str
    title: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    goal: str
    tasks: list[AgentTask]
    plan_id: str = "frontend_generation_plan"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tasks"] = [task.to_dict() for task in self.tasks]
        return payload

    def task_by_id(self, task_id: str) -> AgentTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"unknown task id: {task_id}")

    def ready_tasks(self) -> list[AgentTask]:
        completed = {task.id for task in self.tasks if task.status == "completed"}
        return [
            task for task in self.tasks
            if task.status == "pending" and all(dep in completed for dep in task.depends_on)
        ]


class TaskPlanner:
    """Build and persist a dependency-aware task graph for frontend work."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.plans_dir = self.root / ".tasks" / "plans"
        self.plans_dir.mkdir(parents=True, exist_ok=True)

    def create_frontend_plan(self, user_request: str) -> TaskPlan:
        tasks = [
            AgentTask(
                id="task_001",
                owner="planner",
                status="pending",
                title="解析用户自然语言，生成项目规格",
                description="Convert the user request into a frontend project spec.",
                context_refs=[],
                acceptance_criteria=[
                    "Create spec.json",
                    "Capture app goal, pages, features, style, and acceptance criteria",
                ],
            ),
            AgentTask(
                id="task_002",
                owner="coder",
                status="pending",
                title="生成完整前端工程",
                description="Create Vite/React/TypeScript project files from spec.json.",
                depends_on=["task_001"],
                context_refs=["spec.json"],
                acceptance_criteria=[
                    "package.json exists",
                    "src/App.tsx exists",
                    "src/main.tsx exists",
                    "index.html exists",
                ],
            ),
            AgentTask(
                id="task_003",
                owner="verifier",
                status="pending",
                title="安装依赖并构建",
                description="Install dependencies and run the production build.",
                depends_on=["task_002"],
                context_refs=["package.json", "spec.json"],
                acceptance_criteria=["npm install succeeds", "npm run build succeeds"],
            ),
            AgentTask(
                id="task_004",
                owner="verifier",
                status="pending",
                title="浏览器打开页面验证",
                description="Open the generated app in a browser and verify it renders.",
                depends_on=["task_003"],
                context_refs=["dist/", "spec.json"],
                acceptance_criteria=[
                    "page opens without console errors",
                    "desktop and mobile viewport render key content",
                ],
            ),
            AgentTask(
                id="task_005",
                owner="coder",
                status="pending",
                title="根据失败日志修复",
                description="Use build or browser verification logs to fix generated code.",
                depends_on=["task_003", "task_004"],
                context_refs=["build.log", "browser-report.json", "spec.json"],
                acceptance_criteria=["all previous failing checks pass"],
            ),
            AgentTask(
                id="task_006",
                owner="reviewer",
                status="pending",
                title="输出最终报告",
                description="Summarize generated files, verification result, URL/dist path, and known issues.",
                depends_on=["task_004", "task_005"],
                context_refs=["spec.json", "browser-report.json", "dist/"],
                acceptance_criteria=["final report includes URL or dist path", "open issues are explicit"],
            ),
        ]
        return TaskPlan(
            goal=user_request,
            tasks=tasks,
            metadata={
                "workflow": "natural language -> spec -> Vite project -> install/build -> browser verify -> repair -> report",
            },
        )

    def save_plan(self, plan: TaskPlan, path: str | Path | None = None) -> Path:
        target = self._resolve(path or self.plans_dir / f"{plan.plan_id}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def load_plan(self, path: str | Path | None = None) -> TaskPlan:
        target = self._resolve(path or self.plans_dir / "frontend_generation_plan.json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        tasks = [AgentTask(**task) for task in payload["tasks"]]
        return TaskPlan(
            goal=payload["goal"],
            tasks=tasks,
            plan_id=payload.get("plan_id", "frontend_generation_plan"),
            created_at=payload.get("created_at", time.time()),
            metadata=payload.get("metadata", {}),
        )

    def update_task(
        self,
        plan: TaskPlan,
        task_id: str,
        status: str | None = None,
        owner: str | None = None,
        result: Any = None,
        context_refs: list[str] | None = None,
    ) -> AgentTask:
        task = plan.task_by_id(task_id)
        if status is not None:
            if status not in TASK_STATUSES:
                raise ValueError(f"invalid task status: {status}")
            if status == "in_progress":
                self._ensure_dependencies_completed(plan, task)
            task.status = status
        if owner is not None:
            task.owner = owner
        if result is not None:
            task.result = result
        if context_refs is not None:
            task.context_refs = list(dict.fromkeys([*task.context_refs, *context_refs]))
        return task

    def _ensure_dependencies_completed(self, plan: TaskPlan, task: AgentTask):
        incomplete = [
            dep for dep in task.depends_on
            if plan.task_by_id(dep).status != "completed"
        ]
        if incomplete:
            raise ValueError(f"task {task.id} is blocked by incomplete dependencies: {incomplete}")

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        root = self.root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path escapes project root: {path}") from exc
        return resolved
