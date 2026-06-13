# Operator Guide — running the bot day to day

*Plain-language command reference for the human running this system. For the
one-time go-live switch, see [go-live-runbook.md](go-live-runbook.md); this doc
is everyday operation.*

> **Two rules for every command below:**
> 1. Open **PowerShell** in the project folder: (the repository root)
> 2. Always use the project's own Python: **`.\.venv\Scripts\python.exe`** — not bare
>    `python` (that's the system one and it's missing the libraries; it's what
>    caused the login headaches earlier).

---

## The mental model (read this once)

The bot is **not** a program that's always running. It's a set of **Windows
Scheduled Tasks** that fire at fixed times. The important one is the **5:00 PM
Paper Tick** — that's when every strategy makes its decisions for the day. The
rest just pull data in the morning and keep the dashboard alive.

Nothing is lost if the PC restarts: all state lives on disk in `data\paper.db`.
A missed tick (PC off/asleep) **catches up automatically** the next time the
machine is awake and logged in.

---

## Everyday checks

**Is it healthy? (did today's jobs run?)**
```powershell
Get-ScheduledTask -TaskName "Stockbot*" | Get-ScheduledTaskInfo |
  Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
```
`LastTaskResult = 0` means success. Anything else, check the logs (below).

**The dashboard** (the easiest health check — it has a "Did it run today?" line):
> **http://127.0.0.1:8765** — only opens on this PC (it's local).

**The logs** live in the `logs\` folder, one file per job per day, e.g.
`logs\paper-tick-2026-06-13.log`. To see the latest paper tick:
```powershell
Get-Content (Get-ChildItem logs\paper-tick-*.log | Sort-Object LastWriteTime | Select-Object -Last 1) -Tail 40
```

---

## The live pilot — approving real orders

When the pilot is live, the 5 PM tick **does not place orders directly**. It
**queues** them, and **you approve each one** with this tool (this is the
"first 10 orders" safety rule). Approving happens **here in PowerShell**, *not*
in the Robinhood app.

**See what's waiting:**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders
```
Lists each pending order: id, BUY/SELL, quantity, symbol, ~$ value, which bot.

**Approve one (places it for real):**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders approve <id>
```

**Reject one (skips it):**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.approve_orders reject <id> --note "gapped against the thesis"
```

**When should you approve?** The order is a **regular-hours** order, so if you
approve it at night it just waits for the next market open. **Best practice for
the pilot: approve in the morning, near/after the 8:30 AM open, after a glance at
where the stock is trading.** That way you see any overnight price jump *before*
you commit — and you can simply `reject` it if it gapped hard against the idea.

---

## Emergency stop — the kill switch

To **halt all buying immediately** (e.g. something looks wrong), create a file
named `KILL_SWITCH` in the project folder:
```powershell
New-Item KILL_SWITCH
```
While it exists: **new BUYs are refused, but SELLs/exits still go through** — you
can always get *out* of a position, never deeper in. To resume:
```powershell
Remove-Item KILL_SWITCH
```

---

## Robinhood login (auth)

**Check the login token is alive:**
```powershell
.\.venv\Scripts\python.exe -m stockbot.tools.rh_auth status
```
Shows time-to-expiry. The token lasts ~10 days and a weekly Monday job warns you
if it's dying. (A read-only sanity check that the broker can see your account:
`.\.venv\Scripts\python.exe scripts\rh_readcheck.py`.)

**Re-login if it ever expires** — use the **reliable paste method** (the browser
auto-callback is flaky):
```powershell
# 1) get the link + save the security key:
.\.venv\Scripts\python.exe scripts\rh_login_manual.py url
# 2) open the link, log in, click Allow. The browser lands on a
#    "can't reach localhost" page — that's expected. Copy the FULL address-bar URL.
# 3) hand that URL back to finish:
.\.venv\Scripts\python.exe scripts\rh_login_manual.py exchange "<paste-the-url-here>"
```

---

## Occasionally useful

**Run a paper tick by hand** (normally automatic at 5 PM):
```powershell
.\.venv\Scripts\python.exe -m stockbot.harness.paper_run
```
Add `--force` to run on a weekend/holiday (it normally skips non-trading days).

**Money note — the AI's spend:** the strategies call AI models, capped at ~$5/day.
If the dashboard or thesis flags show **`ensemble_unavailable` / `cross_model_failed`**,
the **GPT second-opinion ran out of funds** — top up OpenAI (and turn on
auto-reload) so the system keeps using both models instead of just one.

---

## "What do I do if…" quick table

| Situation | What's happening / what to do |
|---|---|
| Dashboard won't load | It restarts at logon; if needed, log out and back in, or run `scripts\run_dashboard.ps1`. |
| PC restarted (Windows update) | Fine — state is safe. Just make sure you're **logged in**; the missed tick catches up. |
| A flag says `ensemble_unavailable` | GPT was out of funds that run. Top up OpenAI. Not dangerous, just degraded (Claude-only). |
| A flag says `no_filing_text` / `no_source_doc` | It couldn't fetch the filing, so it **didn't trade** that one. Correct, conservative. |
| A "surprise" looks huge (e.g. +100%) | Actual earnings beat the estimate by that %. For tiny-estimate stocks the % is exaggerated — check the actual-vs-estimate numbers on the dashboard. |
| Something looks wrong with real money | `New-Item KILL_SWITCH` first (stops buys), then look into it. |
| Want to stop the whole pilot | Kill switch + set `broker.live: simulated` in `config.yaml` (see the go-live runbook to reverse a go-live). |

---

*Keep this doc open during the pilot's first week. Once the first 10 manual
approvals feel routine, we can discuss relaxing the manual-approval rule — but
not before.*
