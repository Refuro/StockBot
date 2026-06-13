"""LLM router (Ticket 2c) — tiered runtime models behind one spend-capped door.

Every LLM call in the trading system goes through `LLMRouter.complete`:
- spend-capped (`SpendTracker` raises before the call that would bust the cap),
- token-logged (input/output tokens + running cost on every call),
- model id returned on every response so decision rows can record it.

Tiers (from config.yaml `llm:`):
- triage      -> cheap model (Haiku) — kills obvious junk
- strong      -> Opus — the real read
- cross_check -> GPT — independent second opinion for the ensemble

The router never sizes orders and never touches the risk engine; it returns
text. Deterministic code does everything else.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from stockbot.config import EnvSettings, load_env

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    TRIAGE = "triage"
    STRONG = "strong"
    CROSS_CHECK = "cross_check"


# $ per 1M input/output tokens — used for cap enforcement and spend logs,
# never for billing. Unknown models fall back to the most expensive row so the
# cap errs on the safe side.
#
# STANDING RULE (2c-F2): estimates may run HIGH (caps bite early — safe), never
# knowingly LOW. Recalibrate only from CLEAN dashboard deltas (fully settled),
# via scripts/reconcile_llm_spend.py — the monthly ops habit.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # Verified public list price; controlled test 2026-06-12 is consistent.
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # 2c-F2 recalibration (2026-06-12 controlled test, reports/
    # spend-reconciliation-test.json): tracker estimated $2.0893 opus while the
    # CLEAN Anthropic dashboard delta was $0.81 total; netting out haiku at its
    # verified list price ($0.1113) leaves ~$0.70 real opus vs $2.09 estimated
    # => implied real ≈ (5.0, 25.1)/MTok. The old (15, 75) row was legacy
    # Opus-4.1-era pricing, ~3x high. Set 20% ABOVE implied real per the
    # standing rule.
    "claude-opus-4-8": (6.00, 30.00),
    # Reconciled against the user's real billing dashboard twice (2026-06-12):
    # the original guess of (2.50, 10.00) under-counted ~2.3x; the first
    # correction (5.00, 30.00) still ran ~25-30% low on a live slice ($0.61
    # estimated vs ~$0.80 billed). Padded to err on the EXPENSIVE side so
    # spend caps bind early. NOTE (2c-F2): the 2026-06-12 controlled test
    # showed 2.24x HIGH against a lag-truncated OpenAI reading ($0.37,
    # incomplete) — UNUSABLE for recalibration. That earlier "2.3x correction"
    # was itself contaminated (failed/truncated-call era) and will likely
    # partially revert. Do NOT touch this row until the user supplies the
    # SETTLED June OpenAI dashboard reading.
    "gpt-5.5": (7.00, 40.00),
}
_FALLBACK_PRICE = (15.00, 75.00)

# Extra completion-token budget for OpenAI reasoning models (gpt-5.x): hidden
# reasoning tokens are billed as completion tokens and spend first; without
# headroom the visible JSON truncates (2c-F1). Cost exposure is bounded by the
# SpendTracker either way (worst case ≈ $0.14/call at gpt-5.5 output prices).
REASONING_HEADROOM_TOKENS = 4_000

# Runtime model ids per tier (claude ids must be full snapshot ids where known).
TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.TRIAGE: "claude-haiku-4-5-20251001",
    ModelTier.STRONG: "claude-opus-4-8",
    ModelTier.CROSS_CHECK: "gpt-5.5",
}
TIER_PROVIDERS: dict[ModelTier, str] = {
    ModelTier.TRIAGE: "anthropic",
    ModelTier.STRONG: "anthropic",
    ModelTier.CROSS_CHECK: "openai",
}


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    inp, outp = PRICE_PER_MTOK.get(model_id, _FALLBACK_PRICE)
    return (input_tokens * inp + output_tokens * outp) / 1_000_000.0


@dataclass(frozen=True)
class LLMResponse:
    model_id: str
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LLMSpendCapExceeded(RuntimeError):
    """The configured LLM budget is spent; no further calls are allowed."""


@dataclass
class SpendTracker:
    """Cumulative spend across all calls routed through one router instance."""

    cap_usd: float
    label: str = "llm"
    spent_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, float] = field(default_factory=dict)

    def check(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise LLMSpendCapExceeded(
                f"{self.label}: spend cap ${self.cap_usd:.2f} reached (${self.spent_usd:.4f})"
            )

    def add(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        cost = estimate_cost_usd(model_id, input_tokens, output_tokens)
        self.spent_usd += cost
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.by_model[model_id] = self.by_model.get(model_id, 0.0) + cost
        logger.info(
            "%s call %d: model=%s in=%d out=%d cost=$%.4f cum=$%.4f/cap $%.2f",
            self.label, self.calls, model_id, input_tokens, output_tokens,
            cost, self.spent_usd, self.cap_usd,
        )
        return cost

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "cap_usd": self.cap_usd,
            "spent_usd": round(self.spent_usd, 4),
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_model": {k: round(v, 4) for k, v in self.by_model.items()},
        }


class LLMClient(Protocol):
    model_id: str

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> LLMResponse:
        ...


class AnthropicLLMClient:
    def __init__(self, model_id: str, api_key: str) -> None:
        self.model_id = model_id
        self._api_key = api_key

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> LLMResponse:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            # Newer Anthropic models (Opus 4.8+) deprecated `temperature`.
            if "temperature" not in str(exc):
                raise
            kwargs.pop("temperature", None)
            resp = client.messages.create(**kwargs)
        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = getattr(resp, "usage", None)
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        return LLMResponse(
            model_id=self.model_id, text=text, input_tokens=inp, output_tokens=out,
            cost_usd=estimate_cost_usd(self.model_id, inp, out),
        )


class OpenAILLMClient:
    def __init__(self, model_id: str, api_key: str) -> None:
        self.model_id = model_id
        self._api_key = api_key

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> LLMResponse:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # gpt-5.x rejects `max_tokens` and non-default temperature. They are
        # also reasoning models: hidden reasoning tokens consume the completion
        # budget BEFORE any visible text, so the caller's `max_tokens` (meant
        # as visible output) needs headroom or responses truncate mid-JSON and
        # the cross-check silently degrades to single-model (ticket 2c-F1 —
        # observed live: 9/11 responses cut at exactly the old 700 ceiling).
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_completion_tokens": max_tokens + REASONING_HEADROOM_TOKENS,
        }
        # JSON mode forbids truncated/garnished output; OpenAI rejects the flag
        # unless the conversation mentions JSON, hence the guard.
        if "json" in f"{system or ''} {prompt}".lower():
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            logger.warning(
                "%s hit the completion-token ceiling — visible output likely truncated",
                self.model_id,
            )
        text = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        return LLMResponse(
            model_id=self.model_id, text=text, input_tokens=inp, output_tokens=out,
            cost_usd=estimate_cost_usd(self.model_id, inp, out),
        )


class StubLLMClient:
    """Deterministic offline client for tests: returns canned JSON by substring match."""

    def __init__(self, model_id: str, responses: list[tuple[str, str]] | None = None,
                 default: str = "{}") -> None:
        self.model_id = model_id
        self._responses = responses or []
        self._default = default
        self.prompts_seen: list[str] = []

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> LLMResponse:
        self.prompts_seen.append(prompt)
        text = next((resp for needle, resp in self._responses if needle in prompt), self._default)
        return LLMResponse(
            model_id=self.model_id, text=text,
            input_tokens=len(prompt) // 4, output_tokens=len(text) // 4,
            cost_usd=estimate_cost_usd(self.model_id, len(prompt) // 4, len(text) // 4),
        )


class LLMRouter:
    """Routes tiered prompts through one shared spend tracker."""

    def __init__(self, clients: dict[ModelTier, LLMClient], tracker: SpendTracker) -> None:
        self._clients = clients
        self.tracker = tracker

    def available(self, tier: ModelTier) -> bool:
        return tier in self._clients

    def model_for(self, tier: ModelTier) -> str | None:
        client = self._clients.get(tier)
        return client.model_id if client else None

    def complete(
        self, tier: ModelTier, prompt: str, *, system: str | None = None, max_tokens: int = 1024
    ) -> LLMResponse:
        client = self._clients.get(tier)
        if client is None:
            raise ValueError(f"No client configured for tier {tier.value}")
        self.tracker.check()  # raise BEFORE spending if the cap is already gone
        resp = client.complete(prompt, system=system, max_tokens=max_tokens)
        self.tracker.add(resp.model_id, resp.input_tokens, resp.output_tokens)
        return resp


def build_router(
    *,
    cap_usd: float,
    label: str = "llm",
    env: EnvSettings | None = None,
    clients: dict[ModelTier, LLMClient] | None = None,
) -> LLMRouter:
    """Build a router from configured API keys; tiers without keys are absent.

    Pass `clients` to inject stubs in tests (the tracker still applies).
    """
    tracker = SpendTracker(cap_usd=cap_usd, label=label)
    if clients is not None:
        return LLMRouter(clients, tracker)
    env = env or load_env()
    built: dict[ModelTier, LLMClient] = {}
    for tier, model_id in TIER_MODELS.items():
        provider = TIER_PROVIDERS[tier]
        if provider == "anthropic" and env.anthropic_api_key:
            built[tier] = AnthropicLLMClient(model_id, env.anthropic_api_key)
        elif provider == "openai" and env.openai_api_key:
            built[tier] = OpenAILLMClient(model_id, env.openai_api_key)
    return LLMRouter(built, tracker)


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse the first JSON object out of an LLM response (handles code fences)."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
