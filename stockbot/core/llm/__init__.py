"""LLM router — tiered, spend-capped, token-logged (Ticket 2c)."""

from stockbot.core.llm.router import (
    LLMClient,
    LLMResponse,
    LLMRouter,
    LLMSpendCapExceeded,
    ModelTier,
    SpendTracker,
    StubLLMClient,
    build_router,
    parse_json_object,
)

__all__ = [
    "LLMClient",
    "LLMResponse",
    "LLMRouter",
    "LLMSpendCapExceeded",
    "ModelTier",
    "SpendTracker",
    "StubLLMClient",
    "build_router",
    "parse_json_object",
]
