"""Broker factory."""

from __future__ import annotations

from stockbot.config import AppConfig
from stockbot.core.broker.base import Broker
from stockbot.core.broker.simulated import SimulatedBroker


def create_broker(config: AppConfig) -> Broker:
    name = config.broker_name
    if name == "simulated":
        sb = config.simulated_broker
        return SimulatedBroker(
            initial_cash=sb.initial_cash,
            slippage_pct=sb.slippage_pct,
            execution_timing=sb.execution_timing,
            cost_profile=sb.cost_profile,
            spread_bps=sb.spread_bps,
            impact_coeff_bps=sb.impact_coeff_bps,
            adv_shares=sb.adv_shares,
            symbol_costs={k: v.model_dump(exclude_none=True) for k, v in sb.symbol_costs.items()},
        )
    if name in ("robinhood", "robinhood_mcp"):
        from stockbot.config import load_env
        from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker
        from stockbot.core.state.store import StateStore

        lb = config.live_brokers
        return RobinhoodMCPBroker(
            store=StateStore(config.state.db_path),
            mode=config.mode.value,
            account_number=lb.robinhood_account_number,
            mcp_url=load_env().robinhood_mcp_url,
            max_order_notional_usd=lb.max_order_notional_usd,
            manual_approval=lb.manual_approval,
            kill_switch_path=lb.kill_switch_path,
        )
    if name == "alpaca":
        from stockbot.config import load_env
        from stockbot.core.broker.alpaca import AlpacaBroker
        from stockbot.core.state.store import StateStore

        env = load_env()
        lb = config.live_brokers
        return AlpacaBroker(
            store=StateStore(config.state.db_path),
            mode=config.mode.value,
            api_key=env.alpaca_api_key,
            secret_key=env.alpaca_secret_key,
            base_url=env.alpaca_base_url,
            max_order_notional_usd=lb.max_order_notional_usd,
            manual_approval=lb.manual_approval,
            kill_switch_path=lb.kill_switch_path,
        )
    raise ValueError(f"Unknown broker: {name}")
