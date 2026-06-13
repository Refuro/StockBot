"""Shot-5 evidence helper: dump fade-lane decisions from the scratch demo DB."""

import json
import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/fade_demo/paper.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("--- distinct lanes with decision rows ---")
for r in conn.execute("SELECT DISTINCT bot_id FROM decisions"):
    print("  " + r["bot_id"])

print("--- fade decision rows ---")
rows = conn.execute(
    "SELECT bot_id, action, as_of, inputs_json FROM decisions "
    "WHERE action IN ('fade_buy','fade_vetoed','skipped_fade_gate','fade_hard_stop','fade_time_stop')"
).fetchall()
for r in rows:
    i = json.loads(r["inputs_json"])
    g = i.get("fade_gate", {})
    print(
        f"  {r['bot_id']:18s} {r['action']:18s} {i.get('symbol'):6s} "
        f"drop={i.get('drop_pct'):+.1%} class={g.get('classification')} "
        f"hard_skip={g.get('hard_skip')} veto={g.get('vetoed')} "
        f"counter_pass_logged={'counter_pass' in g} "
        f"flags={g.get('flags')} p={i.get('stated_probability')}"
    )

print("--- theses bucket=crash:fade ---")
for r in conn.execute(
    "SELECT bot_id, symbol, stated_probability, strategy_bucket FROM theses "
    "WHERE strategy_bucket='crash:fade'"
):
    print("  " + json.dumps(dict(r)))

print("--- calibration buckets present ---")
for r in conn.execute("SELECT DISTINCT strategy_bucket FROM calibration_ledger"):
    print("  " + str(r["strategy_bucket"]))
conn.close()
