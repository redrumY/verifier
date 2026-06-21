from __future__ import annotations

import json
import threading
import time
import types
from pathlib import Path

import pytest

from harness.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelPolicy,
    TokenBudgetExceeded,
    default_role_policies,
)


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=5):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, content="ok", stop_reason="end_turn", input_tokens=10, output_tokens=5):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeMessages:
    def __init__(self, responses=None, fail_first=False):
        self.calls = []
        self.responses = list(responses or [FakeResponse()])
        self.fail_first = fail_first

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("429 rate limit")
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResponse()


class FakeAnthropic:
    def __init__(self, messages):
        self.messages = messages


def make_gateway(policy: ModelPolicy, fake_messages: FakeMessages, **kwargs) -> ModelGateway:
    return ModelGateway(
        policies={"planner": policy},
        clients={"anthropic": FakeAnthropic(fake_messages)},
        sleep_fn=lambda _seconds: None,
        jitter_fn=lambda: 0.0,
        **kwargs,
    )


def test_default_role_policies_route_roles_from_env():
    policies = default_role_policies({
        "MODEL_ID": "claude-strong",
        "MEDIUM_MODEL_ID": "claude-medium",
        "FALLBACK_MODEL_ID": "claude-cheap",
        "CODER_MODEL_ID": "claude-coder",
        "MODEL_GATEWAY_TOKEN_BUDGET": "12345",
    })

    assert policies["planner"].model == "claude-strong"
    assert policies["coder"].model == "claude-coder"
    assert policies["tester"].model == "claude-medium"
    assert policies["reviewer"].temperature == 0.0
    assert policies["summarizer"].model == "claude-cheap"
    assert policies["verifier"].use_llm is False
    assert policies["planner"].token_budget == 12345


def test_call_uses_role_policy_and_writes_audit_log(tmp_path: Path):
    fake_messages = FakeMessages([
        FakeResponse(content=[types.SimpleNamespace(type="text", text="done")],
                     input_tokens=25, output_tokens=7)
    ])
    policy = ModelPolicy(
        provider="anthropic",
        model="claude-strong",
        temperature=0.1,
        max_tokens=100,
        token_budget=1000,
        input_cost_per_million=3.0,
        output_cost_per_million=15.0,
    )
    log_path = tmp_path / "model-calls.jsonl"
    gateway = make_gateway(policy, fake_messages, call_log_path=log_path)

    response = gateway.call(
        "planner_agent",
        messages=[{"role": "user", "content": "make a plan"}],
        system="system prompt",
        tools=[{"name": "read_file"}],
    )

    call = fake_messages.calls[0]
    assert call["model"] == "claude-strong"
    assert call["temperature"] == 0.1
    assert call["max_tokens"] == 100
    assert call["system"] == "system prompt"
    assert call["tools"] == [{"name": "read_file"}]
    assert response.role == "planner"
    assert response.usage == {"input_tokens": 25, "output_tokens": 7}
    assert response.estimated_cost_usd == pytest.approx((25 * 3.0 + 7 * 15.0) / 1_000_000)

    logged = json.loads(log_path.read_text().splitlines()[0])
    assert logged["role"] == "planner"
    assert logged["status"] == "ok"
    assert logged["attempts"] == 1


def test_retryable_errors_are_retried():
    fake_messages = FakeMessages(fail_first=True)
    policy = ModelPolicy(
        provider="anthropic",
        model="claude",
        max_tokens=100,
        max_retries=2,
        token_budget=1000,
    )
    gateway = make_gateway(policy, fake_messages)

    response = gateway.call("planner", [{"role": "user", "content": "hello"}])

    assert response.attempts == 2
    assert len(fake_messages.calls) == 2
    assert gateway.records[-1].status == "ok"


def test_token_budget_blocks_request_before_provider_call():
    fake_messages = FakeMessages()
    policy = ModelPolicy(provider="anthropic", model="claude", max_tokens=1000, token_budget=10)
    gateway = make_gateway(policy, fake_messages)

    with pytest.raises(TokenBudgetExceeded):
        gateway.call("planner", [{"role": "user", "content": "hello"}])

    assert fake_messages.calls == []


def test_concurrency_limit_serializes_provider_calls():
    active = 0
    max_active = 0
    lock = threading.Lock()

    class SlowMessages:
        def create(self, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return FakeResponse(input_tokens=1, output_tokens=1)

    policy = ModelPolicy(provider="anthropic", model="claude", max_tokens=10, token_budget=1000)
    gateway = ModelGateway(
        policies={"planner": policy},
        clients={"anthropic": FakeAnthropic(SlowMessages())},
        max_concurrent=1,
    )

    threads = [
        threading.Thread(target=lambda: gateway.call("planner", [{"role": "user", "content": "x"}]))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
    assert len(gateway.records) == 4


def test_verifier_role_is_non_llm_by_default():
    gateway = ModelGateway(policies=default_role_policies({"MODEL_ID": "claude"}))

    with pytest.raises(ModelGatewayError, match="non-LLM verification"):
        gateway.call("verifier", [{"role": "user", "content": "run tests"}])
