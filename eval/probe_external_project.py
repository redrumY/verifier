#!/usr/bin/env python3
"""Probe an external full-stack project with the verifier agent harness."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.context_manager import TaskPack
from harness.sandbox_runner import (
    BrowserProfile,
    BrowserViewport,
    DockerSandboxRunner,
    TestCommand,
    TestProfile,
)
from harness.verifier import Verifier


DEFAULT_TASK = (
    "在 Dashboard 增加一个目标统计摘要组件：展示目标总数、最近创建的目标时间，"
    "并保持现有 /api/goals 数据流不变。"
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_project(project_dir: Path, frontend_dir: Path) -> dict[str, Any]:
    root_package = load_json(project_dir / "package.json")
    frontend_package = load_json(frontend_dir / "package.json")
    root_deps = {
        **root_package.get("dependencies", {}),
        **root_package.get("devDependencies", {}),
    }
    frontend_deps = {
        **frontend_package.get("dependencies", {}),
        **frontend_package.get("devDependencies", {}),
    }
    frontend_stack = "unknown"
    if "react-scripts" in frontend_deps:
        frontend_stack = "create-react-app"
    elif "vite" in frontend_deps:
        frontend_stack = "vite"
    backend_stack = "express" if "express" in root_deps else "unknown"
    api_proxy = frontend_package.get("proxy")
    return {
        "project_dir": str(project_dir),
        "frontend_dir": str(frontend_dir),
        "backend_stack": backend_stack,
        "frontend_stack": frontend_stack,
        "root_scripts": root_package.get("scripts", {}),
        "frontend_scripts": frontend_package.get("scripts", {}),
        "api_proxy": api_proxy,
        "integration_shape": "frontend proxy -> Express API" if api_proxy else "unknown",
    }


def build_task_pack(project_summary: dict[str, Any], task: str) -> TaskPack:
    relevant_files = [
        "frontend/src/pages/Dashboard.jsx",
        "frontend/src/features/goals/goalSlice.js",
        "frontend/src/features/goals/goalService.js",
        "frontend/src/components/GoalItem.jsx",
        "frontend/src/index.css",
        "backend/routes/goalRoutes.js",
        "backend/controllers/goalController.js",
    ]
    acceptance = [
        "Dashboard renders a summary section above the goal list.",
        "Summary uses the existing goals array returned from /api/goals.",
        "No backend route changes are required unless the existing contract is insufficient.",
        "Frontend build passes.",
        "Browser verification can open the dashboard route with mock auth/API data.",
        "Existing create/delete goal behavior is not broken.",
    ]
    constraints = [
        "Do not run install/build directly in the source project; use sandbox first.",
        "Do not require a real MongoDB instance for UI verification; prefer mock API data.",
        "Do not put secrets or JWT values into prompt, Git, or logs.",
        "If real backend smoke test is unavailable, record it as an open issue instead of changing UI blindly.",
    ]
    return TaskPack(
        task_id=f"probe_task_{int(time.time())}",
        role="coder",
        objective=task,
        acceptance_criteria=acceptance,
        relevant_files=relevant_files,
        context_refs=[
            "root package.json",
            "frontend package.json",
            "api proxy",
            "goal redux slice",
        ],
        constraints=constraints,
        metadata={
            "project_summary": project_summary,
            "task_type": "ui_api_integration",
        },
    )


def build_profile(project_summary: dict[str, Any]) -> TestProfile:
    browser_enabled = project_summary["frontend_stack"] == "vite"
    browser = BrowserProfile(
        enabled=browser_enabled,
        route="/",
        port=5173 if project_summary["frontend_stack"] == "vite" else 3000,
        check_console_errors=True,
        screenshots=True,
        viewports=[
            BrowserViewport("desktop", 1440, 900),
            BrowserViewport("mobile", 390, 844),
        ],
    )
    return TestProfile(
        name="external-frontend-ui-api-probe",
        project_type="frontend",
        docker_image="mcr.microsoft.com/playwright:v1.49.1-noble",
        commands=[
            TestCommand("install", ["npm", "install"]),
            TestCommand("build", ["npm", "run", "build"]),
            TestCommand("test", ["npm", "run", "test", "--", "--watchAll=false"], required=False),
        ],
        browser=browser,
        timeout_seconds=600,
        metadata={
            "frontend_stack": project_summary["frontend_stack"],
            "api_proxy": project_summary.get("api_proxy"),
            "browser_disabled_reason": None if browser_enabled else (
                "Current DockerSandboxRunner browser step assumes npm run dev; "
                "this project uses create-react-app npm start."
            ),
        },
    )


def probe_project(
    project_dir: str | Path,
    frontend_dir: str | Path = "frontend",
    task: str = DEFAULT_TASK,
    run_id: str | None = None,
    results_dir: str | Path = "data/evaluation/results",
) -> tuple[dict[str, Any], Path]:
    project = Path(project_dir).resolve()
    frontend = (project / frontend_dir).resolve()
    run_id = run_id or f"github_probe_{int(time.time())}"
    results_root = (REPO_ROOT / results_dir / run_id).resolve()
    results_root.mkdir(parents=True, exist_ok=True)

    project_summary = summarize_project(project, frontend)
    task_pack = build_task_pack(project_summary, task)
    profile = build_profile(project_summary)
    verifier = Verifier(project)
    detected_type = verifier.detect_project_type(frontend.relative_to(project))
    sandbox_runner = DockerSandboxRunner(
        project,
        sandbox_root=".agent-probe/sandbox-runs",
    )
    sandbox_run = sandbox_runner.prepare_run(
        frontend.relative_to(project),
        profile=profile,
        run_id=run_id,
    )
    observations = [
        "Project is full-stack: React frontend talks to Express API through package.json proxy.",
        "The feature can be implemented from existing goals state without backend schema changes.",
        "MongoDB is not required for first UI verification if mock API/browser state is used.",
    ]
    if project_summary["frontend_stack"] == "create-react-app":
        observations.append(
            "Current sandbox browser command is Vite-oriented; CRA needs profile-level start command support."
        )
    report = {
        "run_id": run_id,
        "project_summary": project_summary,
        "detected_frontend_type": detected_type,
        "task_pack": {
            **asdict(task_pack),
            "prompt_preview": task_pack.to_prompt(),
        },
        "test_profile": profile.to_dict(),
        "sandbox_run": sandbox_run.to_dict(),
        "observations": observations,
        "agent_execution": {
            "llm_available": False,
            "status": "not_run",
            "reason": "This probe prepares the task package and sandbox input. Run agents/s_full.py with API keys to execute the Coder loop.",
        },
        "next_harness_changes": [
            "Add frontend framework detection to choose CRA/Vite dev command.",
            "Add mock API/browser target inputs for authenticated dashboard routes.",
            "Route verification failures through FailureClassifier before retrying Coder.",
        ],
    }
    output_path = results_root / "agent-probe.json"
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report, output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", help="External project root.")
    parser.add_argument("--frontend-dir", default="frontend")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    report, output_path = probe_project(
        args.project_dir,
        frontend_dir=args.frontend_dir,
        task=args.task,
        run_id=args.run_id,
    )
    print(json.dumps({
        "run_id": report["run_id"],
        "project": report["project_summary"]["project_dir"],
        "frontend_stack": report["project_summary"]["frontend_stack"],
        "backend_stack": report["project_summary"]["backend_stack"],
        "sandbox_run": report["sandbox_run"]["run_dir"],
        "report": str(output_path),
        "observations": report["observations"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
