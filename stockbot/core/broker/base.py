"""Broker abstract interface — order execution regardless of mode."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from stockbot.types import Fill, OrderRequest, OrderResult, PortfolioSnapshot


class Broker(ABC):
    @abstractmethod
    def submit_order(self, request: OrderRequest, as_of: datetime) -> OrderResult:
        """Submit an order and return fill or rejection."""

    @abstractmethod
    def get_portfolio(self, as_of: datetime) -> PortfolioSnapshot:
        """Return current portfolio state."""

    @abstractmethod
    def get_fills(self) -> list[Fill]:
        """Return all fills recorded in this session."""

    @abstractmethod
    def sync_positions(self) -> PortfolioSnapshot:
        """Reconcile in-memory state against the source of truth and return it.

        This is the source-of-truth contract for resuming a run with zero desync:
        - Simulated brokers restore cash + positions from the last persisted snapshot.
        - Live brokers (Phase 5) pull authoritative positions/cash from the venue.

        Callers should treat the returned snapshot as authoritative after this call.
        """
