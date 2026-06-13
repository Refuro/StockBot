"""Configuration loader — single source for MODE and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from stockbot.types import ExecutionMode


class SymbolCostConfig(BaseModel):
    spread_bps: float | None = None
    adv_shares: float | None = None
    impact_coeff_bps: float | None = None


class SimulatedBrokerConfig(BaseModel):
    initial_cash: float = 100_000.0
    slippage_pct: float = 0.003
    execution_timing: str = "next_bar"  # same_bar | next_bar
    cost_profile: str = "flat"  # flat | small_cap | robinhood_retail
    spread_bps: float = 20.0
    impact_coeff_bps: float = 15.0
    adv_shares: float = 1_000_000.0
    symbol_costs: dict[str, SymbolCostConfig] = Field(default_factory=dict)


class RiskConfig(BaseModel):
    max_position_pct: float = 0.05
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.08
    max_orders_per_day: int = 20


class LLMConfig(BaseModel):
    cheap_tier: str = "claude-haiku-4-5"
    strong_tier: str = "claude-opus-4-8"
    cross_check: str = "gpt-5.5"


class StateConfig(BaseModel):
    db_path: str = "data/stockbot.db"


class HistoricalDataConfig(BaseModel):
    prices_path: str = "fixtures/historical/prices.csv"
    events_path: str = "fixtures/historical/events.csv"


class HelloWorldConfig(BaseModel):
    symbol: str = "FAKE"
    quantity: float = 10
    side: str = "buy"


class FlowLensConfig(BaseModel):
    """Synthetic retail-crowd logger (Workstream F1)."""

    enabled: bool = True
    consensus_threshold: float = 0.5
    consumer_models: list[dict[str, str]] = Field(default_factory=list)


class LiveBrokersConfig(BaseModel):
    """Phase 5a — live broker rails. The pilot protocol values are LAW:
    $50/trade ceiling, manual approval ON (first 10 orders), kill switch."""

    # The AGENTIC account (never the default acct). From .env, not committed.
    robinhood_account_number: str = Field(
        default_factory=lambda: os.environ.get("ROBINHOOD_ACCOUNT_NUMBER", "")
    )
    max_order_notional_usd: float = 50.0
    manual_approval: bool = True
    kill_switch_path: str = "KILL_SWITCH"


class ShadowConfig(BaseModel):
    """Phase 5b — shadow lane on the daily paper tick."""

    enabled: bool = False
    bot_id: str = "proportionality_shadow"


class FadeLaneConfig(BaseModel):
    """Phase 2.5c — fade side on the daily paper tick.

    When enabled, the gated lane grows fade entries (one engine, two trade
    types) and the `crash_fade_mech` control twin joins the tick (fades every
    detected >=10% crash at constant 0.55 — the brain-vs-coin race).
    """

    enabled: bool = False
    mech_bot_id: str = "crash_fade_mech"
    universe_metadata: str = "stockbot/data/universe_expanded.metadata.json"
    # Shot 11: the additive crash_fade_v2 lane (anti-sycophancy counter-pass v2).
    # Default OFF in code; enabled in config.yaml. When on it shares the gated
    # lane's per-crash rubric ensemble (no double spend) and races v1 + the coin.
    v2_enabled: bool = False
    v2_bot_id: str = "crash_fade_v2"


class FrontierLaneConfig(BaseModel):
    """Phase 3c/3d — Frontier propagation engine on the daily paper tick.

    When enabled, two ADDITIVE lanes join the tick (default OFF, so every
    existing ride/fade/shadow lane stays bit-identical):
    - `frontier`      — graph-driven propagation: on an observable anchor shock,
                        predict the PIT-valid linked filers in the graph's
                        direction; applies the max-concurrent selection cap.
    - `frontier_mech` — the control twin: EVERY observable anchor shock on
                        EVERY PIT-valid linked name, no selection cap, constant
                        0.55, same exits (graph-vs-coin).
    Both are prediction-and-resolution lanes (long-only stack can't short the
    DOWN prediction) bucketed `frontier:customer_concentration` /
    `frontier:sole_supplier`, which advance or die independently.
    """

    enabled: bool = False
    bot_id: str = "frontier"
    mech_bot_id: str = "frontier_mech"
    universe_metadata: str = "stockbot/data/universe_expanded.metadata.json"
    graph_db: str = "data/state.db"  # where frontier_edges lives (not paper.db)


class AppConfig(BaseModel):
    mode: ExecutionMode = ExecutionMode.BACKTEST
    datafeed: dict[str, str] = Field(
        default_factory=lambda: {
            "backtest": "fake_replay",
            "paper": "fake_live",
            "live": "fake_live",
        }
    )
    broker: dict[str, str] = Field(
        default_factory=lambda: {
            "backtest": "simulated",
            "paper": "simulated",
            "live": "simulated",
        }
    )
    simulated_broker: SimulatedBrokerConfig = Field(default_factory=SimulatedBrokerConfig)
    historical_data: HistoricalDataConfig = Field(default_factory=HistoricalDataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    hello_world: HelloWorldConfig = Field(default_factory=HelloWorldConfig)
    flow_lens: FlowLensConfig = Field(default_factory=FlowLensConfig)
    live_brokers: LiveBrokersConfig = Field(default_factory=LiveBrokersConfig)
    shadow: ShadowConfig = Field(default_factory=ShadowConfig)
    fade: FadeLaneConfig = Field(default_factory=FadeLaneConfig)
    frontier: FrontierLaneConfig = Field(default_factory=FrontierLaneConfig)

    @property
    def datafeed_name(self) -> str:
        return self.datafeed[self.mode.value]

    @property
    def broker_name(self) -> str:
        return self.broker[self.mode.value]


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"  # live host = Phase-5c change
    robinhood_mcp_url: str = "https://agent.robinhood.com/mcp/trading"
    tiingo_api_key: str = ""
    alphavantage_api_key: str = ""
    sec_user_agent: str = "Stockbot/0.1 (research@localhost)"


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Load config.yaml and merge with environment variables."""
    load_dotenv()
    path = Path(config_path)
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    if "mode" in raw and isinstance(raw["mode"], str):
        raw["mode"] = ExecutionMode(raw["mode"])
    return AppConfig.model_validate(raw)


def load_env() -> EnvSettings:
    load_dotenv()
    return EnvSettings()
