"""Reusable harness components for production-style agent experiments."""

from .context_manager import (
    AgentSessionState,
    COMPRESSION_SUMMARY_KEYS,
    COMPRESSION_TRIGGERS,
    CompressionRecord,
    CompressionSummary,
    ContextManager,
    SessionSummary,
    TaskPack,
)
from .model_gateway import (
    DEFAULT_ROLE_POLICIES,
    AgentRole,
    GatewayCallRecord,
    ModelGateway,
    ModelGatewayError,
    ModelGatewayResponse,
    ModelPolicy,
    TokenBudgetExceeded,
    default_role_policies,
)

__all__ = [
    "AgentSessionState",
    "AgentRole",
    "COMPRESSION_SUMMARY_KEYS",
    "COMPRESSION_TRIGGERS",
    "CompressionRecord",
    "CompressionSummary",
    "ContextManager",
    "DEFAULT_ROLE_POLICIES",
    "GatewayCallRecord",
    "ModelGateway",
    "ModelGatewayError",
    "ModelGatewayResponse",
    "ModelPolicy",
    "SessionSummary",
    "TaskPack",
    "TokenBudgetExceeded",
    "default_role_policies",
]
