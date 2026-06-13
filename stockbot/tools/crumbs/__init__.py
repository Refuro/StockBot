"""Crumbs scanner (Workstream F4) — structural free-money detector.

Scans EDGAR full-text search for tender offers with odd-lot priority clauses
(holders of <=99 shares accepted without proration). Notifier only: positions
are 99 shares by design and placed manually — this is NOT a Strategy and never
touches the trading loop.
"""
