"""Shared test setup."""

import os

# The broker reads its agentic-account pin from the environment at import time.
# Pin a fake so tests are deterministic and never see the operator's real number
# (pytest loads this file before any test module imports the broker).
os.environ["ROBINHOOD_ACCOUNT_NUMBER"] = "000000804"
