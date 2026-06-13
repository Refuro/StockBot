"""Proportionality Engine — Phases 2 (ride side) + 2.5 (fade side).

One strategy class, one question per shock: was the market's reaction
proportionate to the fundamental dollar change?
  reaction < change  -> underreaction -> RIDE (the PEAD trade)
  reaction ~= change -> proportionate -> NO TRADE (the default)
  reaction > change  -> overreaction  -> FADE (the triage trade)

Ledger buckets: (event_type x direction), e.g. "earnings:ride", "crash:fade".
"""
