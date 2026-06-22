"""Run coder tasks inside isolated workspaces before verification."""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .context_manager import ContextManager, TaskPack
from .sandbox_runner import (
    DockerSandboxRunner,
    SandboxExecutionResult,
    SandboxRun,
    TestProfile,
    frontend_ui_component_profile,
)
from .task_planner import AgentTask, TaskPlan, TaskPlanner
from .verifier import CommandResult, VerificationReport, Verifier


CoderCallback = Callable[[Path, TaskPack], Any]


@dataclass
class CoderWorkspaceRun:
    run_id: str
    task_id: str
    source_project_dir: str
    run_dir: str
    baseline_dir: str
    workspace_dir: str
    outputs_dir: str
    task_pack_path: str
    task_prompt_path: str
    diff_path: str
    task_pack: TaskPack
    status: str = "prepared"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_pack"] = asdict(self.task_pack)
        return payload


@dataclass
class CoderWorkspaceResult:
    run: CoderWorkspaceRun
    status: str
    task_status: str
    diff_path: str
    diff: str = ""
    files_changed: list[str] = field(default_factory=list)
    coder_result: Any = None
    verification_mode: str = "none"
    verification: dict[str, Any] = field(default_factory=dict)
    sandbox_run: dict[str, Any] | None = None
    sandbox_result: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["run"] = self.run.to_dict()
        return payload


class CoderWorkspaceRunner:
    """Prepare an isolated workspace, run a coder callback, diff, and verify."""

    DEFAULT_IGNORE = {
        ".git",
        ".agent-probe",
        ".context",
        ".logs",
        ".sandbox",
        ".tasks",
        ".workspaces",
        ".next",
        ".vite",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "outputs",
    }

    def __init__(
        self,
        root: str | Path,
        context_manager: ContextManager | None = None,
        task_planner: TaskPlanner | None = None,
        workspace_root: str | Path = ".workspaces/coder-runs",
        verifier_runner: Callable[[list[str], Path, int], CommandResult] | None = None,
        docker_runner: Callable[[list[str], Path, int], CommandResult] | None = None,
    ):
        self.root = Path(root)
        self.context_manager = context_manager or ContextManager(self.root)
        self.task_planner = task_planner or TaskPlanner(self.root)
        self.workspace_root = self._resolve(workspace_root)
        self.verifier_runner = verifier_runner
        self.docker_runner = docker_runner
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def run_coder_task(
        self,
        plan: TaskPlan,
        task_id: str,
        source_project_dir: str | Path = ".",
        patch_text: str | None = None,
        coder_callback: CoderCallback | None = None,
        verification_mode: str = "local",
        run_sandbox: bool = False,
        profile: TestProfile | None = None,
        parent_session_id: str | None = None,
        run_id: str | None = None,
        update_plan: bool = True,
    ) -> CoderWorkspaceResult:
        task = plan.task_by_id(task_id)
        if task.owner != "coder":
            raise ValueError(f"task {task_id} is owned by {task.owner}, not coder")
        if verification_mode not in {"none", "local", "sandbox"}:
            raise ValueError(f"unsupported verification_mode: {verification_mode}")

        if update_plan:
            self.task_planner.update_task(plan, task_id, status="in_progress")

        task_pack = self.build_task_pack(plan, task, parent_session_id=parent_session_id)
        run = self.prepare_workspace(
            source_project_dir=source_project_dir,
            task_pack=task_pack,
            run_id=run_id,
        )

        status = "passed"
        task_status = "completed"
        coder_result: Any = None
        verification: dict[str, Any] = {}
        sandbox_run_payload = None
        sandbox_result_payload = None
        error = ""

        try:
            if patch_text:
                self._apply_patch(Path(run.workspace_dir), patch_text)
            if coder_callback:
                coder_result = coder_callback(Path(run.workspace_dir), task_pack)

            diff = self._write_diff(run)
            files_changed = self._files_changed(diff)

            if verification_mode == "local":
                report = self._verify_local(run)
                verification = report.to_dict()
                if not self._verification_passed(report):
                    status = "failed"
                    task_status = "failed"
            elif verification_mode == "sandbox":
                sandbox_run, sandbox_result = self._verify_sandbox(
                    run,
                    profile=profile,
                    run_sandbox=run_sandbox,
                )
                sandbox_run_payload = sandbox_run.to_dict()
                sandbox_result_payload = sandbox_result.to_dict() if sandbox_result else None
                verification = {
                    "mode": "sandbox",
                    "status": sandbox_result.status if sandbox_result else "prepared",
                    "run_dir": sandbox_run.run_dir,
                    "report_path": sandbox_result.report_path if sandbox_result else None,
                }
                if sandbox_result and sandbox_result.status != "passed":
                    status = "failed"
                    task_status = "failed"
                elif sandbox_result is None:
                    status = "prepared"
                    task_status = "in_progress"
            else:
                verification = {"mode": "none", "status": "skipped"}

            run.status = status
            result = CoderWorkspaceResult(
                run=run,
                status=status,
                task_status=task_status,
                diff_path=run.diff_path,
                diff=diff,
                files_changed=files_changed,
                coder_result=coder_result,
                verification_mode=verification_mode,
                verification=verification,
                sandbox_run=sandbox_run_payload,
                sandbox_result=sandbox_result_payload,
            )
        except Exception as exc:
            status = "failed"
            task_status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            self._write_diff(run)
            result = CoderWorkspaceResult(
                run=run,
                status=status,
                task_status=task_status,
                diff_path=run.diff_path,
                verification_mode=verification_mode,
                error=error,
            )

        self._write_result(result)
        if update_plan:
            self.task_planner.update_task(
                plan,
                task_id,
                status=task_status,
                result=result.to_dict(),
                context_refs=[run.diff_path, run.task_pack_path],
            )
        return result

    def build_task_pack(
        self,
        plan: TaskPlan,
        task: AgentTask,
        parent_session_id: str | None = None,
    ) -> TaskPack:
        constraints = list(plan.metadata.get("constraints", []))
        constraints.extend([
            "Work only inside the isolated workspace.",
            "Do not write secrets or modify generated dependency directories.",
            "Keep the patch scoped to the task acceptance criteria.",
        ])
        objective = f"{task.title}: {task.description}".strip(": ")
        return self.context_manager.build_task_pack(
            role=task.owner,
            objective=objective,
            task_id=task.id,
            parent_session_id=parent_session_id,
            acceptance_criteria=task.acceptance_criteria,
            relevant_files=task.context_refs,
            context_refs=task.context_refs,
            constraints=list(dict.fromkeys(constraints)),
            metadata={
                "plan_id": plan.plan_id,
                "goal": plan.goal,
                "planner_mode": plan.metadata.get("planner_mode"),
                "task_type": plan.metadata.get("task_type"),
                "workspace_isolation": "copy",
            },
        )

    def prepare_workspace(
        self,
        source_project_dir: str | Path,
        task_pack: TaskPack,
        run_id: str | None = None,
    ) -> CoderWorkspaceRun:
        source = self._resolve(source_project_dir)
        if not source.is_dir():
            raise FileNotFoundError(f"source project not found: {source_project_dir}")

        run_id = run_id or f"coder_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        run_dir = self.workspace_root / run_id
        baseline_dir = run_dir / "baseline"
        workspace_dir = run_dir / "workspace"
        outputs_dir = run_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        self._copy_project(source, baseline_dir)
        self._copy_project(source, workspace_dir)

        task_pack_path = run_dir / "task-pack.json"
        task_prompt_path = run_dir / "task-prompt.txt"
        diff_path = outputs_dir / "workspace.diff"
        task_pack_path.write_text(
            json.dumps(asdict(task_pack), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        task_prompt_path.write_text(task_pack.to_prompt(), encoding="utf-8")

        run = CoderWorkspaceRun(
            run_id=run_id,
            task_id=task_pack.task_id,
            source_project_dir=str(source),
            run_dir=str(run_dir),
            baseline_dir=str(baseline_dir),
            workspace_dir=str(workspace_dir),
            outputs_dir=str(outputs_dir),
            task_pack_path=str(task_pack_path),
            task_prompt_path=str(task_prompt_path),
            diff_path=str(diff_path),
            task_pack=task_pack,
        )
        (run_dir / "coder-workspace-run.json").write_text(
            json.dumps(run.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return run

    def _verify_local(self, run: CoderWorkspaceRun) -> VerificationReport:
        run_dir = Path(run.run_dir)
        verifier = Verifier(
            run_dir,
            outputs_dir=Path("outputs") / "local-verifier",
            runner=self.verifier_runner,
        )
        return verifier.verify("workspace")

    def _verify_sandbox(
        self,
        run: CoderWorkspaceRun,
        profile: TestProfile | None,
        run_sandbox: bool,
    ) -> tuple[SandboxRun, SandboxExecutionResult | None]:
        run_dir = Path(run.run_dir)
        sandbox_runner = DockerSandboxRunner(
            run_dir,
            sandbox_root=Path("outputs") / "sandbox-runs",
            runner=self.docker_runner,
        )
        sandbox_run = sandbox_runner.prepare_run(
            "workspace",
            profile=profile or frontend_ui_component_profile(),
            run_id=f"{run.run_id}_sandbox",
        )
        if not run_sandbox:
            return sandbox_run, None
        return sandbox_run, sandbox_runner.run(sandbox_run)

    def _apply_patch(self, workspace_dir: Path, patch_text: str):
        patch_path = workspace_dir.parent / "coder.patch"
        patch_path.write_text(patch_text, encoding="utf-8")
        completed = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "git apply failed").strip())

    def _write_diff(self, run: CoderWorkspaceRun) -> str:
        run_dir = Path(run.run_dir)
        diff_path = Path(run.diff_path)
        completed = subprocess.run(
            ["git", "diff", "--no-index", "--", "baseline", "workspace"],
            cwd=run_dir,
            capture_output=True,
            text=True,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError((completed.stderr or completed.stdout or "git diff failed").strip())
        diff = completed.stdout
        diff_path.write_text(diff, encoding="utf-8")
        return diff

    def _files_changed(self, diff: str) -> list[str]:
        files = []
        for line in diff.splitlines():
            if not line.startswith("+++ b/workspace/"):
                continue
            path = line.removeprefix("+++ b/workspace/")
            if path != "/dev/null":
                files.append(path)
        return list(dict.fromkeys(files))

    def _verification_passed(self, report: VerificationReport) -> bool:
        statuses = [
            report.install,
            report.typecheck,
            report.build,
            report.tests,
            report.browser,
        ]
        return "failed" not in statuses

    def _write_result(self, result: CoderWorkspaceResult):
        outputs_dir = Path(result.run.outputs_dir)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "coder-workspace-result.json").write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _copy_project(self, source: Path, target: Path):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=self._ignore)

    def _ignore(self, directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in self.DEFAULT_IGNORE:
                ignored.add(name)
                continue
            if fnmatch.fnmatch(name, "*.log"):
                ignored.add(name)
        return ignored

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
