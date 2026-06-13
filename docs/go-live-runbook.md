# Go-Live Runbook — the $500 Robinhood pilot

This is the exact, reversible procedure to take the bot live on the agentic
Robinhood account **••••0804** (cash, `agentic_allowed=true`). It is written so
it can be followed step-by-step on pilot day. Going live is **one config flip**
plus the always-on safety rails — not a new build.

> **Do NOT run this until BOTH are true:** (1) the shadow week has passed
> reviewer sign-off (~Jun 22/23), and (2) the user has funded the agentic
> account with the pilot capital. Until then, leave `broker.live: simulated`.

## The pilot protocol is LAW (never weaken)

- **$500 hard cap** on the account — never topped up mid-pilot.
- **$50 per-order ceiling** (`live_brokers.max_order_notional_usd`, enforced in
  the broker rails).
- **Manual approval on the first 10 orders** (`live_brokers.manual_approval: true`).
- **Kill switch** always available.
- **Broker pinned to ••••0804** — the account guard REFUSES the default
  ••••2954 (`agentic_allowed=false`). This is hard-coded + config-pinned.
- **Pilot P&L gates nothing** — it is a safety/▸behavior pilot, not a return test.

---

## 0. Pre-flight (the morning of go-live)

1. **Token is valid:**
   ```powershell
   .\.venv\Scripts\python.exe -m stockbot.tools.rh_auth status
   ```
   Expect `"expired": false` with several days of `expires_in_seconds`. If
   expired/near-expiry, re-auth:
   ```powershell
   .\.venv\Scripts\python.exe -m stockbot.tools.rh_auth login
   # (fallback if the localhost callback is flaky:)
   .\.venv\Scripts\python.exe scripts\rh_login_manual.py url
   .\.venv\Scripts\python.exe scripts\rh_login_manual.py exchange "<callback-url>"
   ```

2. **Live read path works against the real API** (read-only, places nothing):
   ```powershell
   .\.venv\Scripts\python.exe scripts\rh_readcheck.py
   ```
   Expect `account ••••0804 (agentic_allowed verified)` and a non-zero
   `buying_power` once funded. A crash here means re-check the data-envelope
   parsing (ticket 9a) before going further.

3. **Confirm the account is funded** to the pilot cap and **not above $500**.

---

## 1. Go live — the ONE config change

Edit `config.yaml`:

```yaml
broker:
  live: robinhood   # was: simulated
```

Leave everything else as is:

```yaml
live_brokers:
  # robinhood_account_number comes from ROBINHOOD_ACCOUNT_NUMBER in .env (the AGENTIC account — do NOT change)
  max_order_notional_usd: 50.0           # $50/order ceiling — do NOT raise
  manual_approval: true                  # first-10-orders rule — keep ON
  kill_switch_path: KILL_SWITCH
```

Also set the run mode to `live` when you start the live tick (the live broker
refuses to even construct unless mode == `live`):

```yaml
mode: live
```

That's the entire flip. The rails (account pin, $50 ceiling, manual approval,
kill switch) are already wired and tested; they engage automatically.

---

## 2. The manual-approval flow (first 10 orders)

With `manual_approval: true`, the bot does **not** place orders directly. Each
proposed order is written to the `pending_approval_orders` table with a
pre-minted idempotency `ref_id`, and logs a line telling you to review.

**List what's pending:**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders
```
Example:
```
1 order(s) pending approval:
  <approval_id>  [robinhood_mcp]  BUY  5.0 AAPL  ~$50.00  bot=pilot  requested=...
```

**Approve (places it for real, reusing the pre-minted ref_id):**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders approve <approval_id>
```
This runs `review_equity_order` first (platform contract), then
`place_equity_order` with the SAME `ref_id` minted at queue time — so a crash
mid-place cannot double-submit on retry.

**Reject (places nothing):**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders reject <approval_id> --note "why"
```

Review **every one of the first 10 orders** by hand. The CLI is the human in the
loop; nothing reaches the venue without an explicit `approve`.

---

## 3. The kill switch (engage / disengage)

The kill switch is a file at the repo root (`KILL_SWITCH`). When present, **all
new BUY orders are refused** — both the bot's direct path and BUY approvals via
the CLI. **Approved SELL exits are still allowed** (you can always get out, never
deeper in).

**Engage (stop all new entries immediately):**
```powershell
New-Item KILL_SWITCH        # PowerShell
# (or, on a *nix shell:  touch KILL_SWITCH )
```

**Disengage (resume normal operation):**
```powershell
Remove-Item KILL_SWITCH
```

---

## 4. How to abort the pilot entirely

1. **Engage the kill switch** (above) — stops all new entries at once.
2. **Flip back to simulated** in `config.yaml`:
   ```yaml
   broker:
     live: robinhood   ->   simulated
   ```
   and set `mode` back to `paper`/`backtest`. The live broker will then refuse
   to construct, so no live order can be placed regardless of state.
3. If positions are open and you want out, **approve only the SELL** exits via
   the CLI (allowed even under the kill switch), then disengage nothing else.
4. Withdraw funds from ••••0804 to return to the $0 physical safety floor.

Reversibility: every step above is a config edit or a file create/delete. There
is no migration, no schema change, and no code change required to go live or to
abort.

---

## 5. Rails — where they live (for the reviewer)

| Rail | Enforced in | Test |
|---|---|---|
| account pin (refuse ••••2954) | `RobinhoodMCPBroker._verify_account` | `tests/test_shot9_envelope.py::TestAccountGuardRealShape`, `tests/test_shot4_5a_brokers.py` |
| $50/order ceiling | `LiveBrokerRails.check_rails` | `test_shot4_5a_brokers.py::...notional_ceiling...` |
| kill switch | `LiveBrokerRails.check_rails` + `approve_orders` | `test_shot4_5a_brokers.py::...kill_switch...` |
| manual approval queue | `LiveBrokerRails.queue_for_approval` + `approve_orders` | `test_shot4_5a_brokers.py::TestApprovalCLI`, `scripts/approval_dryrun.py` |
| idempotency ref_id | `LiveBrokerRails.mint_order_ref` / queue | `test_shot4_5a_brokers.py::...persisted_ref_id` |
| `data`-envelope parse | `MCPHTTPClient._unwrap_data_envelope` | `tests/test_shot9_envelope.py` |

To exercise the approval queue end-to-end without a live venue order at any
time, run:
```powershell
.\.venv\Scripts\python.exe scripts\approval_dryrun.py
```
