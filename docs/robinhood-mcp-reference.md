# Robinhood Agentic Trading MCP — API Surface Reference

Captured live 2026-06-11 from an authenticated session. This is ground truth for the Phase 5a `RobinhoodBroker` build (shot 4). Endpoint: `https://agent.robinhood.com/mcp/trading` (MCP-over-HTTP).

## Accounts (verified live)

The OAuth consent created a dedicated agentic sub-account, exactly as the product promised:

| account_number | nickname | type | agentic_allowed | option_level | notes |
|---|---|---|---|---|---|
| ••••2954 | (default) | margin | **false** | option_level_2 | the user's normal account — **trading tools reject it; never target it** |
| **••••0804** | Agentic | cash | **true** | "" (none) | the sandboxed pilot account; **this is the only one our broker may trade** |

- The agentic account is a **cash account** (no margin, no PDT mechanics) — good for the pilot.
- **Funding status confirmed: $0** across every field, buying_power 0. Leave it at $0 until shot-4 shadow mode passes; an unfunded account physically cannot fill, which is our safety floor.
- `agentic_allowed` is the hard gate: `place_equity_order` / `review_equity_order` / `cancel_equity_order` reject any non-agentic account upstream. The broker must hard-code or config-pin `••••0804` and refuse to operate on anything else.

## Auth model — RESOLVED (shot 4, verified live 2026-06-12)

- Auth is **OAuth 2.0 + PKCE** (`code_challenge_method=S256`), browser-consent flow, scope `internal`, resource-bound to the MCP URL.
- **The discovery chain is fully standard MCP auth** (probed unauthenticated, no credentials involved):
  1. `POST /mcp/trading` without a token → `401` with
     `WWW-Authenticate: Bearer resource_metadata="https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"`
  2. Protected-resource metadata → `authorization_servers: ["https://agent.robinhood.com/mcp/trading"]`, `scopes_supported: ["internal"]`
  3. `GET /.well-known/oauth-authorization-server/mcp/trading` →
     - `authorization_endpoint`: `https://robinhood.com/oauth`
     - `token_endpoint`: `https://api.robinhood.com/oauth2/token/`
     - `registration_endpoint`: `https://agent.robinhood.com/oauth/trading/register`
     - `grant_types_supported`: `["authorization_code", "refresh_token"]` — **refresh tokens exist**
     - `token_endpoint_auth_methods_supported`: `["none"]` — public client + PKCE, no secret
- **Dynamic client registration is OPEN** (RFC 7591): a plain `POST` to the registration endpoint with `redirect_uris` returned `200` + a fresh `client_id` (verified live). We register our OWN client — no reuse of Claude's client_id, no scraping.
- **Implementation (shot 4):** option 1 — the broker speaks MCP directly from Python. `stockbot/core/broker/rh_oauth.py` (discovery + DCR + token store + auto-refresh) and `python -m stockbot.tools.rh_auth login` (one-time browser consent → `secrets/robinhood_token.json`, restricted permissions, gitignored). `rh_auth status` / `rh_auth refresh` for ops. A dead refresh raises a clear re-auth error naming the login command.
- **Token lifetime: RESOLVED at first login (2026-06-13).** Observed token response: `expires_in ≈ 845,723 s` (**~9.8 days**), `token_type: Bearer`, `refresh_token: present`, `scope: internal`. The access token is long-lived (~9–10 days) and refreshable; the weekly Monday `rh_auth status` task is comfortably inside the window. Auto-refresh fires 120 s before nominal expiry; a 401 mid-call triggers one refresh + one retry.
- **Login gotcha (2026-06-13):** the localhost-callback capture proved flaky in practice (callback timing / stale-tab `state` mismatches across repeated attempts). Robust fallback: `scripts/rh_login_manual.py` — `url` prints the consent link and saves the PKCE verifier; after consent the browser lands on a harmless "can't reach localhost" page, then `exchange "<callback-url>"` mints the token from the pasted `code`. Same token store/format as `rh_auth login`. The interactive `rh_auth login` callback timeout was also raised 300 s → 1800 s.

## Tool surface (33 tools) — what maps to our `Broker` interface

**Equities (the pilot uses only these):**
- `get_accounts` → account discovery (no args). Returns the table above.
- `get_portfolio(account_number)` → authoritative buying_power + value breakdown. **Use this for equity/cash, NOT get_accounts** (the guide explicitly says get_accounts buying power is unreliable).
- `get_equity_positions(account_number)` → symbol, quantity, average cost, hold breakdown. **This is our reconciliation source** (positions from the venue, not memory — satisfies the 0.1b reconciliation rule).
- `get_equity_quotes(symbols[])` → real-time quote + official last-session close. ≤20 symbols or `closes` is omitted.
- `get_equity_tradability(account_number, symbols[])` → per-session eligibility + fractional, ≤10 symbols. Call before placing.
- `review_equity_order(...)` → **simulate**, returns quote + pre-trade alerts (buying power, PDT, halt). No money moves.
- `place_equity_order(...)` → **real order, real money.** See order semantics below.
- `get_equity_orders(account_number, ...)` → order status/fills; filter by `state`, `symbol`, `created_at_gte`, `placed_agent='agentic'`.
- `cancel_equity_order(account_number, order_id)`.

**Not used by the pilot (exist on the endpoint):** options suite (`place_option_order`, `get_option_chains`, quotes, positions, etc. — the beta has expanded past equities-only), indexes, watchlists, `search`. The proportionality strategy is equities-long-only; ignore all of these in shot 4.

## Order placement semantics (for the broker mapping)

`place_equity_order` required: `account_number`, `symbol`, `side` (buy/sell), `type`. Then:

- **type:** `market`, `limit`, `stop_market`, `stop_limit`. `limit_price` required for limit/stop_limit; `stop_price` for stop_market/stop_limit.
- **sizing:** exactly one of `quantity` (shares; fractional only on market + regular_hours) OR `dollar_amount` (USD notional; market only). Our $50/trade pilot maps naturally to `dollar_amount="50.00"` market, or a marketable limit at the ask for price protection (the review tool's own guidance prefers the latter).
- **time_in_force:** `gfd` (default) or `gtc`.
- **market_hours:** `regular_hours` (default) / `extended_hours` / `all_day_hours`. Fractional + dollar-based only fill in regular_hours.
- **ref_id (idempotency):** pass a fresh UUID per logical order; re-send the SAME ref_id on transient-failure retries — upstream dedupes by it. **The broker MUST set ref_id** (don't rely on the server fallback) so a network retry can't double-place. Map ref_id ↔ our internal order id in the state DB.
- **Review-first is the platform default:** `place_equity_order`'s own contract says call `review_equity_order` first and get explicit confirmation unless explicitly told to skip. This dovetails with our manual-approval-on rule for the first 10 pilot orders.

## Mapping to the existing `Broker` interface (3-mode spine)

| Our `Broker` method | Robinhood tool |
|---|---|
| submit_order (BUY/SELL) | review_equity_order → place_equity_order (with ref_id) |
| get_portfolio | get_portfolio(account_number) |
| sync_positions / reconcile | get_equity_positions(account_number) |
| order status / fills | get_equity_orders(..., placed_agent='agentic') |
| cancel | cancel_equity_order |
| (quote, if needed) | get_equity_quotes |

The live `DataFeed` (shot 3, ticket 2d) can keep using Tiingo for daily prices; `get_equity_quotes` is available as a cross-check / live mark at order time.

## Open items — status after shot 4
1. **Token lifetime + refresh** — mechanism RESOLVED (see Auth model above: refresh_token grant supported, auto-refresh implemented). The concrete `expires_in` value awaits the user's one-time `rh_auth login`; record it here then.
2. **Fractional eligibility on the cash agentic account** — ANSWERED (live `get_equity_tradability` on account ••••0804, 2026-06-12): per-symbol `fractional_tradability: "tradable"` (e.g. F, AAPL), `account_type_tradability: "tradable"` for `individual`. Fractional + dollar orders still place only in `regular_hours`. The broker uses integer/fractional `quantity` market or marketable-limit orders, not `dollar_amount`.
3. **Settlement (cash account):** unchanged — the broker reads buying_power from `get_portfolio` each tick (implemented: `RobinhoodMCPBroker.get_portfolio` uses the portfolio tool, never `get_accounts`).

## Shot-4 live verification (2026-06-12, via the authenticated MCP session)
- `get_accounts` → both accounts, ••••0804 `agentic_allowed=true`, cash; ••••2954 `agentic_allowed=false` (never targeted).
- `get_portfolio(••••0804)` → `total_value=0`, `buying_power=0.0000` — the $0 safety floor confirmed.
- `get_equity_positions(••••0804)` → empty.
- `get_equity_quotes(AAPL,F,SIRI)` → live quotes + official prior closes.
- `review_equity_order(••••0804, F, buy, limit 3 @ 14.88)` → simulation round-tripped: quote data + `EQUITY_NOT_ENOUGH_BP` alert (`depositAmount $44.64`). Nothing placed; `place_equity_order` was never invoked this shot.
