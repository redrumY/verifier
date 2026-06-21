"""Centralized model routing, budgeting, retry, and audit logging.

The teaching agents in this repository create provider clients directly inside
each chapter. That is fine for learning the loop, but multi-agent systems need
one boundary that owns credentials, model selection, concurrency, and spend.

ModelGateway is intentionally provider-light: Anthropic is the primary target,
OpenAI is supported when an injected or installed client is available, and tests
can use fake clients without network access.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


AgentRole = str

ROLE_ALIASES = {
    "planner_agent": "planner",
    "coder_agent": "coder",
    "tester_agent": "tester",
    "reviewer_agent": "reviewer",
    "summarizer_agent": "summarizer",
}

DEFAULT_ROLE_POLICIES: tuple[str, ...] = (
    "planner",
    "coder",
    "tester",
    "reviewer",
    "summarizer",
    "verifier",
)


class ModelGatewayError(RuntimeError):
    """Base error for gateway failures."""


class TokenBudgetExceeded(ModelGatewayError):
    """Raised when a role would exceed its configured token budget."""


@dataclass(frozen=True)
class ProviderCredentials:
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProviderCredentials":
        src = env or os.environ
        return cls(
            anthropic_api_key=src.get("ANTHROPIC_API_KEY"),
            anthropic_base_url=src.get("ANTHROPIC_BASE_URL"),
            openai_api_key=src.get("OPENAI_API_KEY"),
            openai_base_url=src.get("OPENAI_BASE_URL"),
        )


@dataclass(frozen=True)
class ModelPolicy:
    """Routing and safety policy for one logical agent role."""

    provider: str = "anthropic"
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 8000
    token_budget: int = 200_000
    max_retries: int = 3
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    use_llm: bool = True

    def with_overrides(self, **overrides: Any) -> "ModelPolicy":
        data = asdict(self)
        data.update({k: v for k, v in overrides.items() if v is not None})
        return ModelPolicy(**data)


@dataclass
class GatewayCallRecord:
    request_id: str
    role: str
    provider: str
    model: str
    attempts: int
    input_tokens: int
    output_tokens: int
    reserved_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    status: str
    error: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ModelGatewayResponse:
    request_id: str
    role: str
    provider: str
    model: str
    content: Any
    stop_reason: str | None
    raw: Any
    usage: dict[str, int]
    attempts: int
    estimated_cost_usd: float


def normalize_role(role: str) -> str:
    key = role.strip().lower().replace("-", "_")
    return ROLE_ALIASES.get(key, key)


def estimate_tokens(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return max(1, len(text) // 4)


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, "") or default)
    except ValueError:
        return default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, "") or default)
    except ValueError:
        return default


def default_role_policies(env: Mapping[str, str] | None = None) -> dict[str, ModelPolicy]:
    """Build role -> policy routing from environment variables.

    Important env vars:
      MODEL_ID / STRONG_MODEL_ID
      MEDIUM_MODEL_ID
      CHEAP_MODEL_ID / FALLBACK_MODEL_ID
      PLANNER_MODEL_ID, CODER_MODEL_ID, TESTER_MODEL_ID, REVIEWER_MODEL_ID,
      SUMMARIZER_MODEL_ID for per-role overrides.
      MODEL_PROVIDER or ROLE_MODEL_PROVIDER for provider overrides.
      MODEL_GATEWAY_TOKEN_BUDGET or ROLE_TOKEN_BUDGET for budgets.
    """

    src = env or os.environ
    default_provider = src.get("MODEL_PROVIDER", "anthropic")
    strong = src.get("STRONG_MODEL_ID") or src.get("MODEL_ID", "")
    medium = src.get("MEDIUM_MODEL_ID") or strong
    cheap = src.get("CHEAP_MODEL_ID") or src.get("FALLBACK_MODEL_ID") or medium
    default_budget = _env_int(src, "MODEL_GATEWAY_TOKEN_BUDGET", 200_000)

    def model_for(role: str, fallback: str) -> str:
        return src.get(f"{role.upper()}_MODEL_ID") or fallback

    def provider_for(role: str) -> str:
        return src.get(f"{role.upper()}_MODEL_PROVIDER") or default_provider

    def budget_for(role: str) -> int:
        return _env_int(src, f"{role.upper()}_TOKEN_BUDGET", default_budget)

    def price(role: str, direction: str) -> float:
        key = f"{role.upper()}_{direction.upper()}_COST_PER_MILLION"
        return _env_float(src, key, 0.0)

    return {
        "planner": ModelPolicy(
            provider=provider_for("planner"),
            model=model_for("planner", strong),
            temperature=0.1,
            max_tokens=8000,
            token_budget=budget_for("planner"),
            input_cost_per_million=price("planner", "input"),
            output_cost_per_million=price("planner", "output"),
        ),
        "coder": ModelPolicy(
            provider=provider_for("coder"),
            model=model_for("coder", strong),
            temperature=0.2,
            max_tokens=12000,
            token_budget=budget_for("coder"),
            input_cost_per_million=price("coder", "input"),
            output_cost_per_million=price("coder", "output"),
        ),
        "tester": ModelPolicy(
            provider=provider_for("tester"),
            model=model_for("tester", medium),
            temperature=0.0,
            max_tokens=8000,
            token_budget=budget_for("tester"),
            input_cost_per_million=price("tester", "input"),
            output_cost_per_million=price("tester", "output"),
        ),
        "reviewer": ModelPolicy(
            provider=provider_for("reviewer"),
            model=model_for("reviewer", strong),
            temperature=0.0,
            max_tokens=8000,
            token_budget=budget_for("reviewer"),
            input_cost_per_million=price("reviewer", "input"),
            output_cost_per_million=price("reviewer", "output"),
        ),
        "summarizer": ModelPolicy(
            provider=provider_for("summarizer"),
            model=model_for("summarizer", cheap),
            temperature=0.0,
            max_tokens=2000,
            token_budget=budget_for("summarizer"),
            input_cost_per_million=price("summarizer", "input"),
            output_cost_per_million=price("summarizer", "output"),
        ),
        "verifier": ModelPolicy(
            provider="none",
            model="",
            temperature=0.0,
            max_tokens=0,
            token_budget=0,
            use_llm=False,
        ),
    }


def _usage_from_response(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}

    def get(*names: str) -> int:
        for name in names:
            if isinstance(usage, dict) and name in usage:
                return int(usage.get(name) or 0)
            if hasattr(usage, name):
                return int(getattr(usage, name) or 0)
        return 0

    return {
        "input_tokens": get("input_tokens", "prompt_tokens"),
        "output_tokens": get("output_tokens", "completion_tokens"),
    }


def _response_content(response: Any) -> Any:
    if isinstance(response, dict):
        return response.get("content") or response.get("output") or response
    return getattr(response, "content", response)


def _response_stop_reason(response: Any) -> str | None:
    if isinstance(response, dict):
        return response.get("stop_reason") or response.get("finish_reason")
    return getattr(response, "stop_reason", None)


def _is_retryable_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    markers = (
        "429",
        "529",
        "rate",
        "ratelimit",
        "overloaded",
        "timeout",
        "temporarily",
        "server error",
        "503",
    )
    return any(marker in name or marker in message for marker in markers)


class ModelGateway:
    """Single access point for all LLM calls in a multi-agent harness."""

    def __init__(
        self,
        policies: Mapping[str, ModelPolicy] | None = None,
        clients: Mapping[str, Any] | None = None,
        credentials: ProviderCredentials | None = None,
        max_concurrent: int = 4,
        call_log_path: str | Path | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[], float] = random.random,
    ):
        self.policies = dict(policies or default_role_policies())
        self.clients = dict(clients or {})
        self.credentials = credentials or ProviderCredentials.from_env()
        self._semaphore = threading.BoundedSemaphore(max(1, int(max_concurrent)))
        self._budget_lock = threading.Lock()
        self._reserved_or_used_tokens = {role: 0 for role in self.policies}
        self.call_log_path = Path(call_log_path) if call_log_path else None
        self.sleep_fn = sleep_fn
        self.jitter_fn = jitter_fn
        self.records: list[GatewayCallRecord] = []

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        call_log_path: str | Path | None = None,
        max_concurrent: int | None = None,
    ) -> "ModelGateway":
        src = env or os.environ
        policies = default_role_policies(src)
        credentials = ProviderCredentials.from_env(src)
        clients: dict[str, Any] = {}
        provider_names = {
            p.provider for p in policies.values()
            if p.use_llm and p.provider != "none"
        }

        if "anthropic" in provider_names:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise ModelGatewayError("anthropic package is required") from exc
            kwargs: dict[str, Any] = {}
            if credentials.anthropic_api_key:
                kwargs["api_key"] = credentials.anthropic_api_key
            if credentials.anthropic_base_url:
                kwargs["base_url"] = credentials.anthropic_base_url
            clients["anthropic"] = Anthropic(**kwargs)

        if "openai" in provider_names:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ModelGatewayError("openai package is required for OpenAI policies") from exc
            kwargs = {}
            if credentials.openai_api_key:
                kwargs["api_key"] = credentials.openai_api_key
            if credentials.openai_base_url:
                kwargs["base_url"] = credentials.openai_base_url
            clients["openai"] = OpenAI(**kwargs)

        concurrency = max_concurrent or _env_int(src, "MODEL_GATEWAY_MAX_CONCURRENT", 4)
        return cls(
            policies=policies,
            clients=clients,
            credentials=credentials,
            max_concurrent=concurrency,
            call_log_path=call_log_path,
        )

    def policy_for(self, role: str) -> ModelPolicy:
        normalized = normalize_role(role)
        try:
            return self.policies[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self.policies))
            raise ModelGatewayError(f"Unknown agent role '{role}'. Available: {available}") from exc

    def remaining_budget(self, role: str) -> int:
        normalized = normalize_role(role)
        policy = self.policy_for(normalized)
        if not policy.use_llm:
            return 0
        with self._budget_lock:
            return max(0, policy.token_budget - self._reserved_or_used_tokens.get(normalized, 0))

    def call(
        self,
        role: str,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
        **overrides: Any,
    ) -> ModelGatewayResponse:
        normalized = normalize_role(role)
        policy = self.policy_for(normalized).with_overrides(**overrides)
        if not policy.use_llm or policy.provider == "none":
            raise ModelGatewayError(f"Role '{normalized}' is configured for non-LLM verification")
        if not policy.model:
            raise ModelGatewayError(f"Role '{normalized}' has no model configured")

        request_id = str(uuid.uuid4())[:8]
        prompt_tokens = estimate_tokens({"system": system, "messages": messages, "tools": tools})
        reserved_tokens = prompt_tokens + int(policy.max_tokens or 0)
        self._reserve_budget(normalized, policy, reserved_tokens)

        started = time.monotonic()
        attempts = 0
        last_error = ""
        try:
            with self._semaphore:
                for attempt in range(policy.max_retries + 1):
                    attempts = attempt + 1
                    try:
                        response = self._dispatch(policy, messages, system, tools)
                        usage = _usage_from_response(response)
                        if not usage["input_tokens"]:
                            usage["input_tokens"] = prompt_tokens
                        actual_total = usage["input_tokens"] + usage["output_tokens"]
                        self._settle_budget(normalized, reserved_tokens, actual_total)
                        cost = self._estimate_cost(policy, usage)
                        record = self._record(
                            request_id=request_id,
                            role=normalized,
                            policy=policy,
                            attempts=attempts,
                            usage=usage,
                            reserved_tokens=reserved_tokens,
                            cost=cost,
                            started=started,
                            status="ok",
                        )
                        return ModelGatewayResponse(
                            request_id=request_id,
                            role=normalized,
                            provider=policy.provider,
                            model=policy.model,
                            content=_response_content(response),
                            stop_reason=_response_stop_reason(response),
                            raw=response,
                            usage=usage,
                            attempts=attempts,
                            estimated_cost_usd=record.estimated_cost_usd,
                        )
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                        if attempt >= policy.max_retries or not _is_retryable_error(exc):
                            raise
                        self.sleep_fn(self._retry_delay(attempt))
        except Exception:
            self._release_budget(normalized, reserved_tokens)
            self._record(
                request_id=request_id,
                role=normalized,
                policy=policy,
                attempts=attempts,
                usage={"input_tokens": 0, "output_tokens": 0},
                reserved_tokens=reserved_tokens,
                cost=0.0,
                started=started,
                status="error",
                error=last_error,
            )
            raise

    def _dispatch(
        self,
        policy: ModelPolicy,
        messages: list[dict],
        system: str | None,
        tools: list[dict] | None,
    ) -> Any:
        client = self.clients.get(policy.provider)
        if client is None:
            raise ModelGatewayError(f"No client configured for provider '{policy.provider}'")
        if policy.provider == "anthropic":
            kwargs: dict[str, Any] = {
                "model": policy.model,
                "messages": messages,
                "max_tokens": policy.max_tokens,
                "temperature": policy.temperature,
            }
            if system is not None:
                kwargs["system"] = system
            if tools is not None:
                kwargs["tools"] = tools
            return client.messages.create(**kwargs)
        if policy.provider == "openai":
            return self._dispatch_openai(client, policy, messages, system, tools)
        raise ModelGatewayError(f"Unsupported provider '{policy.provider}'")

    def _dispatch_openai(
        self,
        client: Any,
        policy: ModelPolicy,
        messages: list[dict],
        system: str | None,
        tools: list[dict] | None,
    ) -> Any:
        if hasattr(client, "responses"):
            input_payload = list(messages)
            if system is not None:
                input_payload = [{"role": "system", "content": system}, *input_payload]
            kwargs: dict[str, Any] = {
                "model": policy.model,
                "input": input_payload,
                "max_output_tokens": policy.max_tokens,
                "temperature": policy.temperature,
            }
            if tools is not None:
                kwargs["tools"] = tools
            return client.responses.create(**kwargs)
        if hasattr(client, "chat"):
            chat_messages = list(messages)
            if system is not None:
                chat_messages = [{"role": "system", "content": system}, *chat_messages]
            kwargs = {
                "model": policy.model,
                "messages": chat_messages,
                "max_tokens": policy.max_tokens,
                "temperature": policy.temperature,
            }
            return client.chat.completions.create(**kwargs)
        raise ModelGatewayError("OpenAI client must expose responses or chat.completions")

    def _reserve_budget(self, role: str, policy: ModelPolicy, tokens: int):
        with self._budget_lock:
            current = self._reserved_or_used_tokens.get(role, 0)
            if current + tokens > policy.token_budget:
                raise TokenBudgetExceeded(
                    f"Role '{role}' budget exceeded: requested {tokens}, "
                    f"remaining {max(0, policy.token_budget - current)}"
                )
            self._reserved_or_used_tokens[role] = current + tokens

    def _settle_budget(self, role: str, reserved_tokens: int, actual_tokens: int):
        with self._budget_lock:
            current = self._reserved_or_used_tokens.get(role, 0)
            self._reserved_or_used_tokens[role] = max(0, current - reserved_tokens + actual_tokens)

    def _release_budget(self, role: str, reserved_tokens: int):
        with self._budget_lock:
            current = self._reserved_or_used_tokens.get(role, 0)
            self._reserved_or_used_tokens[role] = max(0, current - reserved_tokens)

    def _retry_delay(self, attempt: int) -> float:
        base = min(0.5 * (2 ** attempt), 32.0)
        return base + self.jitter_fn() * base * 0.25

    def _estimate_cost(self, policy: ModelPolicy, usage: dict[str, int]) -> float:
        return (
            usage["input_tokens"] * policy.input_cost_per_million
            + usage["output_tokens"] * policy.output_cost_per_million
        ) / 1_000_000

    def _record(
        self,
        request_id: str,
        role: str,
        policy: ModelPolicy,
        attempts: int,
        usage: dict[str, int],
        reserved_tokens: int,
        cost: float,
        started: float,
        status: str,
        error: str = "",
    ) -> GatewayCallRecord:
        record = GatewayCallRecord(
            request_id=request_id,
            role=role,
            provider=policy.provider,
            model=policy.model,
            attempts=attempts,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            reserved_tokens=reserved_tokens,
            estimated_cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error=error,
        )
        self.records.append(record)
        if self.call_log_path:
            self.call_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.call_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record
