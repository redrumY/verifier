import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
