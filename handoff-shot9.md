# Shot 9 Handoff — Go-live readiness (5c-prep): make the $500 pilot a config flip, not a build

End-to-end build brief, **shot 9 only**, for **one capable agent** (the broker is sensitive live-money code; it's one cohesive workstream — don't split). Reviewer audits at the end. Bar set by `reports/shot1..8-report.md`.

**Why this shot exists:** Robinhood auth is now DONE (token valid live, ~9.8-day lifetime + refresh; agentic account ••••0804 confirmed cash/`agentic_allowed`). But a read-only `get_accounts` test on 2026-06-13 surfaced a **go-live blocker**, and the shadow week clears ~Jun 22/23 — after which the user funds $500 and the pilot starts under the pre-committed protocol. This shot fixes the blocker, proves the live read path, and pre-stages go-live so it's a flip-and-approve, not a new build.

**The pilot protocol is LAW (do not weaken anything):** $500 hard cap (never topped mid-pilot), $50/order ceiling, manual approval on the first 10 orders, kill switch, broker pinned to agentic account ••••0804 (NEVER the default ••••2954), pilot P&L gates nothing. This shot PREPARES go-live; it does NOT execute it.

## Required reading
1. `docs/robinhood-mcp-reference.md` — auth model, token lifetime (now recorded), tool surface, the `data`-envelope note.
2. `stockbot/core/broker/robinhood_mcp.py` — `RobinhoodMCPBroker` + `MCPHTTPClient`; the read methods with the bug.
3. `stockbot/core/broker/live_rails.py` — the rails (kill switch, $50 ceiling, manual-approval queue). Do not weaken.
4. `reports/shot8-report.md` — Frontier disposition (relevant only to ticket 9d).

## The blocker, precisely (reviewer-diagnosed 2026-06-13)
The live MCP wraps **every** tool response in a `data` envelope:
```json
{ "data": { "accounts": [ {"account_number":"••••0804","type":"cash","agentic_allowed":true, ...} ] }, "guide": "..." }
```
But the broker's reads expect the payload at the top level — e.g. `_verify_account` does `accounts.get("accounts", accounts)`, `get_portfolio` does `portfolio.get("buying_power")`, `_venue_positions` does `raw.get("positions", raw)`. Against the real envelope these get `None`/the whole dict → the account guard **crashes** (`'str' object has no attribute 'get'`) and portfolio/positions silently read **$0/empty**. The unit tests passed because they mocked the *un*wrapped shape.

## Scope — build ONLY this

### Ticket 9a — fix the response-envelope parsing (the blocker)
- Unwrap the `data` envelope **once**, centrally, in `MCPHTTPClient.call_tool` (return `result["data"]` when the response is `{"data": ...}`, else the result as-is) — so every existing `.get("accounts")` / `.get("buying_power")` / `.get("positions")` call works unchanged. Centralize rather than patching each method.
- **Audit ALL `call_tool` consumers** for the same assumption: `_verify_account`, `get_portfolio`, `_venue_positions`, AND the write path (`place_equity_order`, `review_equity_order`, `cancel_equity_order`, order-status reads). Confirm each parses the unwrapped shape. Note the `guide` key is sibling to `data` (advisory text) — discard it.
- **Regression tests with the REAL shapes** (not the old mocked ones): use the actual envelopes — `get_accounts` `{"data":{"accounts":[{...••••0804..., "agentic_allowed":true},{...••••2954..., "agentic_allowed":false}]}}`, `get_portfolio`, `get_equity_positions`, and an order-placement response. Assert: the account guard accepts ••••0804 and REFUSES ••••2954; get_portfolio reads buying_power/equity; positions parse. Keep the existing rails tests green; never weaken a rail.

### Ticket 9b — live read-path verification (read-only, no orders)
- A small CLI/script (e.g. `scripts/rh_readcheck.py`) that runs the broker's real read path against the live API on ••••0804: account guard → `get_portfolio` → positions, printing a clean human-readable result (mask the account number to ••••0804; show cash/equity, position count). **Read-only — it must place NO order.**
- Run it; paste the output in the report as live proof the fix works against the real API (this is the verification shot-4 claimed but didn't actually have). If anything else is `data`-wrapped differently than expected, fix it here and note it.

### Ticket 9c — go-live runbook + manual-approval dry run (no live order)
- Write `docs/go-live-runbook.md`: the exact, reversible steps to go live — the one config change (`broker.live: simulated → robinhood`), how to engage/disengage the kill switch (`New-Item KILL_SWITCH` / delete), the manual-approval flow (`python -m stockbot.tools.approve_orders` to list/approve/reject), the first-10-orders rule, and how to abort. Plain enough that the user can follow it on pilot day.
- **Exercise the manual-approval queue end-to-end WITHOUT a live venue order:** with `manual_approval` on, drive a single $50 order through the broker in a mode that writes to `pending_approval_orders` instead of placing it, then show `approve_orders` lists it and approve/reject updates it. (Use the simulated/dry path or a stubbed MCP — the point is proving the queue + CLI work, not hitting Robinhood.) This validates the rail the pilot's safety depends on. Confirm `pending_approval_orders` / `broker_order_refs` tables materialize (the shot-4 review flagged they were absent from paper.db — they belong to the live DB; show they appear here).

### Ticket 9d — turn the Frontier paper lanes ON (small)
- Flip `frontier.enabled: true` in `config.yaml` so the two frontier lanes accumulate the forward sector-neutral sign-accuracy sample (mechanical, no LLM, prediction-only → **zero capital risk**, default was OFF only to land safely). Confirm the next paper tick runs all lanes and the dashboard `frontier:*` rows appear. Do NOT change the existing **down-only** frontier logic or constants (shot 8 froze them); 9e only ADDS an up-side alongside them.

### Ticket 9e — Frontier up-propagation (additive, pre-registered, LOWEST priority — do AFTER 9a–9c are solid)
**Why:** every shot-8 prediction is DOWN, which a long-only cash account can't trade. The cheap, long-expressible test: does a **positive** anchor shock propagate (big customer/supplier has a great day → the dependent filer rises)? If it does, we can trade it with a normal BUY — no shorting, no margin, no options.
- **Pre-register NEW constants** (frozen before scoring, separate from the down-only block; do NOT edit shot 8's frozen down-only constants or its measurement): an up-shock threshold (mirror the 5% magnitude, direction UP), an up direction map (`customer_concentration → up`, `sole_supplier → up`), and **separate buckets/trade-types** (e.g. `frontier:customer_concentration_up`) so up and down are scored independently and never commingled.
- **Up predictions are LONG-tradeable:** an up-signal places a small real (paper) BUY through `submit_and_record` (risk-gated, ≤3% like the down side's sizing) and resolves on the same sector-neutral excess. Down predictions stay prediction-only (unchanged). The mech twin mirrors the up side too.
- **Measure both ways:** extend the F-0/backtest to report up-side signal frequency and per-bucket sign-accuracy beside the down side (same PIT, same sector-neutralization). Expect it to also be thin — report honestly; this is about turning on a free *forward* data stream, not a significance claim today.
- **Strictly additive & isolated:** must NOT touch the broker code (9a–9c), the rails, or the ride/fade/shadow lanes; must NOT alter shot 8's down-only numbers (they stay reproducible). If 9e risks destabilizing the go-live work, STOP and leave it for a follow-up — 9a–9c are the gate, 9e is a bonus.

### Ticket R1 (carry-forwards, optional, non-blocking)
- gpt-5.5 price row: still deferred to the user's settled July OpenAI reading — leave `router.py`.
- Shot-7 precision carry-forwards (validator "% of {segment}'s revenue" check, dedupe 1-char typo tolerance) — only if quick; isolated to `validator.py`/`dedupe.py`; do not re-mutate the graph.

## Hard rules
1. **NO live trade, NO funding, and leave `broker.live: simulated`** — go-live is gated on shadow review passing (reviewer, ~Jun 22/23) + the user funding $500. This shot prepares; it does not flip the switch.
2. Never weaken a rail (kill switch, $50 ceiling, manual approval, account pin to ••••0804). The account guard must still REFUSE ••••2954.
3. Don't disturb the ride/fade/shadow lanes or their semantics; frontier lanes stay mechanical/prediction-only as shot 8 froze them. Existing tests stay green.
4. 9b is read-only; the 9c queue exercise places no real venue order. Zero real orders this shot.
5. Honesty: if the live read-path check reveals more envelope/shape surprises, fix and report them — the whole point is to find them now, not on pilot day.

## Completion report (`reports/shot9-report.md`)
Files+tests (before/after + suite tail); 9a fix description + the real-shape regression tests (account guard accepts ••••0804 / refuses ••••2954, portfolio/positions parse, order parsing audited); 9b live read-path output (masked); 9c runbook link + manual-approval queue exercise evidence (`pending_approval_orders` populated, `approve_orders` lists/approves); 9d frontier lanes enabled + dashboard rows; 9e up-propagation pre-registered constants + up-side F-0/sign-accuracy (or a note that it was deferred to keep the go-live work clean); rider status; deviations; open questions.

## Not in scope (stated so it isn't attempted)
- 3e/3f ADR-lag bucket, and Frontier counterparty-ticker resolution (the thin-signal lever) — deferred to a later shot; Frontier's first buckets aren't proven yet.
- The actual go-live flip / first $50 trade — that's the user + reviewer on/after ~Jun 22/23, following the runbook this shot produces.
