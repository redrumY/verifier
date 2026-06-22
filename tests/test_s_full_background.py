import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from harness.task_planner import AgentTask, TaskPlan


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "agents" / "s_full.py"


def load_s_full_module(temp_cwd: Path):
    fake_anthropic = types.ModuleType("anthropic")

    class FakeAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = types.SimpleNamespace(create=None)

    fake_dotenv = types.ModuleType("dotenv")
    setattr(fake_anthropic, "Anthropic", FakeAnthropic)
    setattr(fake_dotenv, "load_dotenv", lambda override=True: None)

    previous_anthropic = sys.modules.get("anthropic")
    previous_dotenv = sys.modules.get("dotenv")
    previous_cwd = Path.cwd()
    spec = importlib.util.spec_from_file_location("s_full_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)

    sys.modules["anthropic"] = fake_anthropic
    sys.modules["dotenv"] = fake_dotenv
    try:
        os.chdir(temp_cwd)
        os.environ.setdefault("MODEL_ID", "test-model")
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(previous_cwd)
        if previous_anthropic is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = previous_anthropic
        if previous_dotenv is None:
            sys.modules.pop("dotenv", None)
        else:
            sys.modules["dotenv"] = previous_dotenv


class BackgroundManagerTests(unittest.TestCase):
    def test_check_returns_running_placeholder_when_result_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            manager = module.BackgroundManager()
            manager.tasks["abc123"] = {
                "status": "running",
                "command": "sleep 1",
                "result": None,
            }

            self.assertEqual(manager.check("abc123"), "[running] (running)")

    def test_call_model_routes_through_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))

            class FakeGateway:
                def __init__(self):
                    self.calls = []

                def call(self, **kwargs):
                    self.calls.append(kwargs)
                    return types.SimpleNamespace(raw="raw-response")

            fake_gateway = FakeGateway()
            module.GATEWAY = fake_gateway

            result = module.call_model(
                "coder",
                [{"role": "user", "content": "write code"}],
                system="system",
                tools=[{"name": "bash"}],
                max_tokens=123,
            )

            self.assertEqual(result, "raw-response")
            self.assertEqual(fake_gateway.calls[0]["role"], "coder")
            self.assertEqual(fake_gateway.calls[0]["system"], "system")
            self.assertEqual(fake_gateway.calls[0]["tools"], [{"name": "bash"}])
            self.assertEqual(fake_gateway.calls[0]["max_tokens"], 123)

    def test_teammate_roles_map_to_gateway_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))

            self.assertEqual(module.gateway_role_for_teammate("QA tester"), "tester")
            self.assertEqual(module.gateway_role_for_teammate("security reviewer"), "reviewer")
            self.assertEqual(module.gateway_role_for_teammate("frontend engineer"), "coder")

    def test_run_subagent_records_summary_in_parent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            parent = module.CONTEXTS.create_session("planner", "parent task")

            module.call_model = lambda *args, **kwargs: types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="subagent done")],
                stop_reason="end_turn",
            )

            result = module.run_subagent(
                "inspect frontend files",
                parent_session_id=parent.session_id,
            )

            parent_messages = module.CONTEXTS.get_messages(parent.session_id)
            serialized = json.dumps(parent_messages)
            self.assertEqual(result, "subagent done")
            self.assertIn("agent_summary", serialized)
            self.assertIn("subagent done", serialized)

    def test_auto_compact_writes_fixed_context_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            session = module.CONTEXTS.create_session("planner", "用户要生成一个可运行 React 网页")
            summary = {
                "goal": "用户要生成一个可运行 React 网页",
                "decisions": ["使用 Vite + React + TS"],
                "files_changed": ["package.json"],
                "open_issues": ["还没跑浏览器验证"],
                "next_actions": ["npm run build"],
            }

            module.call_model = lambda *args, **kwargs: types.SimpleNamespace(
                content=[types.SimpleNamespace(text=json.dumps(summary, ensure_ascii=False))]
            )

            compacted = module.auto_compact(
                [{"role": "user", "content": "make a React app"}],
                session_id=session.session_id,
                trigger="phase_transition",
            )

            stored_summary = module.CONTEXTS.get_messages(session.session_id)[0]["content"]["summary"]
            self.assertEqual(stored_summary, summary)
            self.assertIn('"goal": "用户要生成一个可运行 React 网页"', compacted[0]["content"])
            self.assertIn("phase_transition", compacted[0]["content"])

    def test_frontend_task_planning_tool_writes_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))

            output = module.handle_plan_frontend_tasks("生成一个可运行 React 网页")
            payload = json.loads(output)

            self.assertTrue(Path(payload["plan_path"]).exists())
            self.assertEqual(payload["planner_mode"], "dynamic")
            self.assertEqual(payload["plan"]["tasks"][0]["id"], "task_001")
            self.assertEqual(payload["plan"]["tasks"][0]["owner"], "planner")
            self.assertEqual(payload["ready_tasks"][0]["id"], "task_001")

    def test_frontend_generation_tool_creates_minimum_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))

            output = module.handle_generate_frontend_project(
                "生成一个有数据看板的 React 页面",
                project_dir="generated/app",
            )
            payload = json.loads(output)
            project_dir = Path(payload["project"]["project_dir"])

            self.assertTrue((project_dir / "package.json").exists())
            self.assertTrue((project_dir / "src/App.tsx").exists())
            self.assertTrue((project_dir / "src/main.tsx").exists())
            self.assertTrue((project_dir / "index.html").exists())
            self.assertTrue(payload["validation"]["ok"])

    def test_verify_project_tool_returns_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            project_dir = Path(tmp) / "app"
            project_dir.mkdir()
            (project_dir / "package.json").write_text(json.dumps({
                "scripts": {"build": "vite build", "test": "npm run typecheck"},
                "dependencies": {"vite": "^6.0.0"},
            }))

            class FakeVerifier:
                def verify(self, project_dir, project_type=None):
                    command = types.SimpleNamespace(to_dict=lambda: {
                        "name": "build",
                        "status": "passed",
                    })
                    return types.SimpleNamespace(
                        project_type="frontend",
                        report_path="outputs/verification-report.json",
                        commands=[command],
                        summary=lambda: {
                            "build": "passed",
                            "tests": "passed",
                            "browser": "passed",
                            "console_errors": [],
                            "screenshot": "outputs/screenshot.png",
                        },
                    )

            module.PROJECT_VERIFIER = FakeVerifier()
            payload = json.loads(module.handle_verify_project("app"))

            self.assertEqual(payload["build"], "passed")
            self.assertEqual(payload["tests"], "passed")
            self.assertEqual(payload["browser"], "passed")
            self.assertEqual(payload["console_errors"], [])
            self.assertEqual(payload["screenshot"], "outputs/screenshot.png")

    def test_prepare_and_run_docker_sandbox_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            module.FRONTEND_GENERATOR.generate_from_natural_language("生成 React 页面", "app")

            prepared = json.loads(module.handle_prepare_docker_sandbox("app", run_id="run_tool"))
            self.assertEqual(prepared["run_id"], "run_tool")
            self.assertTrue(Path(prepared["dockerfile_path"]).exists())
            self.assertTrue(Path(prepared["compose_path"]).exists())

            class FakeSandboxRunner:
                def run(self, run_dir):
                    return types.SimpleNamespace(to_dict=lambda: {
                        "run_id": "run_tool",
                        "status": "passed",
                        "run_dir": str(run_dir),
                        "report_path": "outputs/verification-report.json",
                    })

            module.SANDBOX_RUNNER = FakeSandboxRunner()
            result = json.loads(module.handle_run_docker_sandbox(prepared["run_dir"]))

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["run_id"], "run_tool")

    def test_run_coder_workspace_task_tool_updates_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_s_full_module(Path(tmp))
            plan = TaskPlan(
                goal="update UI",
                plan_id="workspace_tool_plan",
                tasks=[
                    AgentTask(id="task_001", owner="planner", status="completed", title="inspect"),
                    AgentTask(
                        id="task_002",
                        owner="coder",
                        status="pending",
                        title="edit UI",
                        depends_on=["task_001"],
                    ),
                ],
            )
            plan_path = module.FRONTEND_TASK_PLANNER.save_plan(plan, ".tasks/plans/workspace_tool_plan.json")

            class FakeCoderWorkspaceRunner:
                def run_coder_task(self, plan, **kwargs):
                    plan.task_by_id(kwargs["task_id"]).status = "completed"
                    return types.SimpleNamespace(to_dict=lambda: {
                        "status": "passed",
                        "task_status": "completed",
                        "diff_path": "outputs/workspace.diff",
                    })

            module.CODER_WORKSPACE_RUNNER = FakeCoderWorkspaceRunner()
            payload = json.loads(module.handle_run_coder_workspace_task(
                "task_002",
                plan_path=str(plan_path),
                verification_mode="none",
            ))

            self.assertEqual(payload["result"]["status"], "passed")
            self.assertEqual(payload["plan"]["tasks"][1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
