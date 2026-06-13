"""Shot-5 evidence helper: summarize the frontier graph in data/state.db."""

import sqlite3

conn = sqlite3.connect("data/state.db")
conn.row_factory = sqlite3.Row
total = conn.execute("SELECT COUNT(*) c FROM frontier_edges").fetchone()["c"]
print(f"total edges: {total}")
for r in conn.execute(
    "SELECT edge_type, COUNT(*) c FROM frontier_edges GROUP BY edge_type ORDER BY c DESC"
):
    print(f"  {r['edge_type']}: {r['c']}")
named = conn.execute(
    "SELECT COUNT(*) c FROM frontier_edges WHERE counterparty_name IS NOT NULL "
    "AND counterparty_name != '' AND LOWER(counterparty_name) NOT LIKE '%customer%' "
).fetchone()["c"]
named_col = conn.execute(
    "SELECT COUNT(*) c FROM frontier_edges WHERE is_named = 1"
).fetchone()["c"] if any(
    r["name"] == "is_named"
    for r in conn.execute("PRAGMA table_info(frontier_edges)")
) else None
resolved = conn.execute(
    "SELECT COUNT(*) c FROM frontier_edges WHERE counterparty_symbol IS NOT NULL "
    "AND counterparty_symbol != ''"
).fetchone()["c"]
print(f"named (is_named col): {named_col}")
print(f"resolved counterparty tickers: {resolved}")
src = conn.execute("SELECT COUNT(DISTINCT from_symbol) c FROM frontier_edges").fetchone()["c"]
print(f"distinct source symbols: {src}")
conn.close()
