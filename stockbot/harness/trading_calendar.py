"""US equity trading-day check for the daily tick (shot-5 review fix 5-R1).

The scheduled tasks fire 7 days a week, but the market doesn't: on a
non-trading day Tiingo IEX still serves the LAST session's prices, so a
weekend/holiday tick (a) settles Friday's next-bar orders at Friday's own
price — deleting the overnight gap next-bar execution exists to capture —
and (b) re-detects Friday's >=10% crashes under fresh event_ids, giving a
vetoed fade a fresh roll of the dice each dead day. The fix is to not tick:
`paper_run.main` skips non-trading days (override: `--force`).

Holiday table coverage: 2026-2027 (NYSE full-closure days). Beyond the
table the check fails OPEN (weekend-only) with a loud log line — an
unmaintained table must degrade to the old behavior, not silently skip
trading days.
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

# NYSE full-closure holidays (early-close days still trade and still tick).
_US_MARKET_HOLIDAYS: dict[int, frozenset[date]] = {
    2026: frozenset(
        {
            date(2026, 1, 1),    # New Year's Day
            date(2026, 1, 19),   # Martin Luther King Jr. Day
            date(2026, 2, 16),   # Washington's Birthday
            date(2026, 4, 3),    # Good Friday
            date(2026, 5, 25),   # Memorial Day
            date(2026, 6, 19),   # Juneteenth
            date(2026, 7, 3),    # Independence Day (observed; Jul 4 is a Saturday)
            date(2026, 9, 7),    # Labor Day
            date(2026, 11, 26),  # Thanksgiving Day
            date(2026, 12, 25),  # Christmas Day
        }
    ),
    2027: frozenset(
        {
            date(2027, 1, 1),    # New Year's Day
            date(2027, 1, 18),   # Martin Luther King Jr. Day
            date(2027, 2, 15),   # Washington's Birthday
            date(2027, 3, 26),   # Good Friday
            date(2027, 5, 31),   # Memorial Day
            date(2027, 6, 18),   # Juneteenth (observed; Jun 19 is a Saturday)
            date(2027, 7, 5),    # Independence Day (observed; Jul 4 is a Sunday)
            date(2027, 9, 6),    # Labor Day
            date(2027, 11, 25),  # Thanksgiving Day
            date(2027, 12, 24),  # Christmas Day (observed; Dec 25 is a Saturday)
        }
    ),
}


def is_us_trading_day(d: date) -> bool:
    """True iff `d` is a weekday and not a listed NYSE full-closure holiday."""
    if d.weekday() >= 5:
        return False
    holidays = _US_MARKET_HOLIDAYS.get(d.year)
    if holidays is None:
        logger.warning(
            "trading calendar has no holiday table for %d — weekend-only check "
            "(extend _US_MARKET_HOLIDAYS in stockbot/harness/trading_calendar.py)",
            d.year,
        )
        return True
    return d not in holidays
