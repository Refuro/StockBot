"""Consumer-model providers for Flow Lens sampling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from stockbot.config import EnvSettings, load_env


@dataclass(frozen=True)
class ConsumerModelSpec:
    model_id: str
    provider: str
    model: str


@dataclass(frozen=True)
class ModelCompletion:
    model_id: str
    model_version: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


# Rough public list prices ($ per 1M input / output tokens) for spend visibility.
# Estimates only — used for the daily log line, never for billing.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, outp = PRICE_PER_MTOK.get(model, (1.00, 5.00))
    return (input_tokens * inp + output_tokens * outp) / 1_000_000.0


def available_providers(env: EnvSettings | None = None) -> set[str]:
    """Providers we hold working keys + a client implementation for."""
    env = env or load_env()
    out: set[str] = set()
    if env.openai_api_key:
        out.add("openai")
    if env.anthropic_api_key:
        out.add("anthropic")
    return out


DEFAULT_CONSUMER_MODELS: tuple[ConsumerModelSpec, ...] = (
    ConsumerModelSpec("chatgpt", "openai", "gpt-4o-mini"),
    ConsumerModelSpec("claude", "anthropic", "claude-haiku-4-5"),
    ConsumerModelSpec("grok", "xai", "grok-2-latest"),
    ConsumerModelSpec("gemini", "google", "gemini-2.0-flash"),
)


class ConsumerModelClient(ABC):
    @abstractmethod
    def complete(self, spec: ConsumerModelSpec, prompt: str) -> ModelCompletion:
        """Run one retail-style prompt against a consumer model."""


class OpenAIConsumerClient(ConsumerModelClient):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, spec: ConsumerModelSpec, prompt: str) -> ModelCompletion:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        resp = client.chat.completions.create(
            model=spec.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        content = resp.choices[0].message.content or ""
        version = getattr(resp, "model", spec.model) or spec.model
        usage = getattr(resp, "usage", None)
        return ModelCompletion(
            model_id=spec.model_id,
            model_version=version,
            content=content,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


class AnthropicConsumerClient(ConsumerModelClient):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, spec: ConsumerModelSpec, prompt: str) -> ModelCompletion:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=spec.model,
            max_tokens=800,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [block.text for block in resp.content if block.type == "text"]
        content = "".join(parts)
        version = getattr(resp, "model", spec.model) or spec.model
        usage = getattr(resp, "usage", None)
        return ModelCompletion(
            model_id=spec.model_id,
            model_version=version,
            content=content,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


class StubConsumerClient(ConsumerModelClient):
    """Deterministic offline responses for tests and dry runs."""

    def __init__(self, responses: dict[tuple[str, str], str] | None = None) -> None:
        self._responses = responses or {}

    def complete(self, spec: ConsumerModelSpec, prompt: str) -> ModelCompletion:
        # Key by model_id + first 40 chars of prompt for fixture lookup.
        key = (spec.model_id, prompt[:40])
        content = self._responses.get(key)
        if content is None:
            content = self._default_content(spec.model_id, prompt)
        return ModelCompletion(
            model_id=spec.model_id,
            model_version=f"{spec.model}-stub",
            content=content,
        )

    @staticmethod
    def _default_content(model_id: str, prompt: str) -> str:
        base = {
            "chatgpt": "Retail buzz: $AAPL, MSFT, and NVDA dominate chatter.",
            "claude": "Watchlist names: AMD, NVDA, and SMCI for AI exposure.",
            "grok": "Trending: TSLA, GME, and AMC on social feeds.",
            "gemini": "Small caps to watch: PLTR, SOFI, and RIVN.",
        }
        return base.get(model_id, f"[{model_id}] No configured stub for: {prompt[:30]}...")


class MultiProviderClient(ConsumerModelClient):
    """Routes each consumer model to the configured backend."""

    def __init__(self, env: EnvSettings | None = None) -> None:
        env = env or load_env()
        self._openai = OpenAIConsumerClient(env.openai_api_key) if env.openai_api_key else None
        self._anthropic = AnthropicConsumerClient(env.anthropic_api_key) if env.anthropic_api_key else None
        self._stub = StubConsumerClient()

    def complete(self, spec: ConsumerModelSpec, prompt: str) -> ModelCompletion:
        if spec.provider == "openai" and self._openai is not None:
            return self._openai.complete(spec, prompt)
        if spec.provider == "anthropic" and self._anthropic is not None:
            return self._anthropic.complete(spec, prompt)
        # Grok/Gemini and missing keys fall back to stub until dedicated clients land.
        return self._stub.complete(spec, prompt)


def build_consumer_client(
    *,
    offline: bool = False,
    stub_responses: dict[tuple[str, str], str] | None = None,
    env: EnvSettings | None = None,
) -> ConsumerModelClient:
    if offline or stub_responses is not None:
        return StubConsumerClient(stub_responses)
    return MultiProviderClient(env)
