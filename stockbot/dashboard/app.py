"""Dashboard v3 — stdlib HTTP server, read-only over the paper DB.

Strategy-organized IA: home scoreboard, per-strategy drill-downs, and global
Execution / Ops / Lens pages. The global ops strip appears on every page.

Zero new write paths: SQLite is opened read-only; logs/sidecar/alert JSON are
artifacts the ops jobs already produce. Run with:

    python -m stockbot.dashboard.app --db data/paper.db --port 8765
"""

from __future__ import annotations

import argparse
import html
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from stockbot.config import load_config
from stockbot.dashboard import data as d
from stockbot.dashboard import glossary as g
from stockbot.dashboard import ops as o
from stockbot.dashboard import verdicts as v
from stockbot.harness.trading_calendar import is_us_trading_day

# Display timezone is US Central. All stored data stays UTC; we only
# convert at render time. Change this one constant to re-base every displayed
# timestamp on the dashboard.
DISPLAY_TZ_NAME = "America/Chicago"
DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_NAME)

# |consensus EPS| below this (dollars) makes the surprise % misleading: a few
# cents of beat balloons into a giant percent. Such cells get a 'tiny est.' mark.
SMALL_CONSENSUS_USD = 0.10

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 1.5rem auto; max-width: 1100px; color: #1a1a2e; }
h1 { font-size: 1.35rem; } h2 { font-size: 1.05rem; margin-top: 1.8rem; border-bottom: 1px solid #eee; padding-bottom: 0.2rem; }
h3 { font-size: 0.95rem; margin-bottom: 0.3rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; margin-bottom: 0.6rem; }
th, td { border: 1px solid #ddd; padding: 0.3rem 0.5rem; text-align: right; }
th { background: #f4f4f8; } td:first-child, th:first-child { text-align: left; }
.ok { color: #0a7d36; font-weight: 600; } .no { color: #b3261e; font-weight: 600; }
.warn { color: #9a6700; font-weight: 600; }
svg { background: #fafafc; border: 1px solid #eee; }
.muted { color: #777; font-size: 0.82rem; }
.chip { display: inline-block; padding: 0.25rem 0.7rem; border-radius: 1rem; margin-right: 0.5rem;
        font-size: 0.82rem; font-weight: 600; }
.chip.ok { background: #e2f3e8; color: #0a7d36; }
.chip.failed { background: #fbe4e2; color: #b3261e; }
.chip.pending { background: #f0f0f3; color: #777; }
.chip.running { background: #fdf3d7; color: #9a6700; }
.flag { display: inline-block; background: #fdf3d7; color: #9a6700; border-radius: 0.3rem;
        padding: 0 0.3rem; font-size: 0.75rem; margin-left: 0.2rem; }
a { color: #3a5fcd; text-decoration: none; } a:hover { text-decoration: underline; }
.rationale { text-align: left; max-width: 420px; font-size: 0.8rem; }
.kv { font-size: 0.85rem; } .kv b { display: inline-block; min-width: 11rem; }
pre { background: #f7f7fa; border: 1px solid #eee; padding: 0.6rem; font-size: 0.78rem;
      overflow-x: auto; white-space: pre-wrap; }
.novel { background: #e2f3e8; }
.summary-strip { background: #f7f7fa; border: 1px solid #e0e0e8; border-radius: 0.4rem;
                 padding: 0.8rem 1rem; margin: 1rem 0 1.2rem; font-size: 0.88rem; line-height: 1.5; }
.summary-strip p { margin: 0.4rem 0; }
.phase-journey { font-size: 0.88rem; margin: 0.6rem 0 1rem; }
.phase-journey span { cursor: help; }
.verdict { background: #eef2fb; border-left: 3px solid #3a5fcd; padding: 0.5rem 0.7rem;
           margin: 0.6rem 0; font-size: 0.86rem; }
details.explainer { margin: 0.4rem 0 0.8rem; font-size: 0.84rem; }
details.explainer summary { cursor: pointer; color: #3a5fcd; font-weight: 600; }
.rel { color: #777; font-size: 0.82rem; }
.fake-money { color: #777; font-size: 0.82rem; }
/* --- shot 10 facelift: tabular numerals, zebra striping, tidier chrome --- */
td { font-variant-numeric: tabular-nums; }
td:not(:first-child) { font-feature-settings: "tnum"; }
table tr:nth-child(even) td { background: #fbfbfd; }
table tr:hover td { background: #eef2fb; }
th { position: sticky; top: 0; font-weight: 600; letter-spacing: 0.01em; }
h3 { color: #2a2a45; margin-top: 1rem; }
.eps { color: #777; font-size: 0.72rem; white-space: nowrap; }
.tz-note { margin: 0.1rem 0 0.6rem; }
.tiny-est { background: #fde7cf; color: #9a6700; border-radius: 0.3rem;
            padding: 0 0.3rem; font-size: 0.7rem; margin-left: 0.25rem; cursor: help; }
.flag-legend { background: #fbfbfd; border: 1px solid #e6e6ee; border-radius: 0.4rem;
               padding: 0.5rem 0.8rem; margin: 0.5rem 0 1rem; font-size: 0.8rem; }
.flag-legend h4 { margin: 0 0 0.4rem; font-size: 0.82rem; color: #555; font-weight: 600; }
.flag-legend dl { display: grid; grid-template-columns: max-content 1fr;
                  gap: 0.25rem 0.7rem; margin: 0; }
.flag-legend dt { text-align: left; }
.flag-legend dd { margin: 0; color: #444; }
.ensemble-warn { background: #fdf3d7; border: 1px solid #ecd9a0; border-radius: 0.4rem;
                 color: #7a5200; padding: 0.5rem 0.8rem; margin: 0.6rem 0; font-size: 0.86rem; }
/* --- shot 13: AI debate view + gate scoreboard + Tier-2 trackers --- */
.debate-box { background: #f7f7fa; border: 1px solid #e0e0e8; border-radius: 0.35rem;
              padding: 0.6rem 0.9rem; margin: 0.5rem 0; }
.debate-box h4 { font-size: 0.88rem; font-weight: 700; color: #2a2a45;
                 margin: 0 0 0.35rem; border-bottom: 1px solid #e8e8f0;
                 padding-bottom: 0.2rem; }
.debate-quote { background: #fffdf5; border-left: 3px solid #9a6700;
                padding: 0.5rem 0.75rem; margin: 0.35rem 0; font-size: 0.83rem;
                white-space: pre-wrap; line-height: 1.5; }
.debate-entered { background: #e2f3e8; border-left: 3px solid #0a7d36;
                   padding: 0.45rem 0.75rem; margin: 0.4rem 0; font-size: 0.88rem; }
.debate-vetoed  { background: #fbe4e2; border-left: 3px solid #b3261e;
                   padding: 0.45rem 0.75rem; margin: 0.4rem 0; font-size: 0.88rem; }
.debate-model { display: inline-block; margin-right: 0.15rem; }
.debate-model-disagree { background: #fdf3d7; border-radius: 0.25rem; padding: 0.05rem 0.35rem; }
.pbar { height: 0.6rem; background: #e8e8f0; border-radius: 0.3rem;
        margin: 0.25rem 0; overflow: hidden; display: inline-block; width: 120px; }
.pbar-fill { height: 100%; border-radius: 0.3rem; background: #3a5fcd; }
.pbar-fill.ok   { background: #0a7d36; }
.pbar-fill.warn { background: #9a6700; }
.sb-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.3rem 0;
          font-size: 0.86rem; flex-wrap: wrap; }
.sb-label { font-weight: 600; min-width: 9rem; display: inline-block; }
.sb-tag { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 0.3rem;
          font-size: 0.78rem; font-weight: 700; }
.sb-tag.passing { background: #e2f3e8; color: #0a7d36; }
.sb-tag.failing { background: #fbe4e2; color: #b3261e; }
.sb-tag.early   { background: #f0f0f3; color: #777; }
.sb-tag.measuring { background: #fdf3d7; color: #9a6700; }
/* --- dash v3: nav, ops strip, scoreboard cards --- */
.nav-header { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: baseline;
               margin: 0.6rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid #e0e0e8;
               font-size: 0.88rem; }
.nav-header a { font-weight: 600; }
.nav-header a.active { color: #1a1a2e; text-decoration: underline; }
.ops-strip { background: #f7f7fa; border: 1px solid #e0e0e8; border-radius: 0.4rem;
             padding: 0.7rem 1rem; margin: 0 0 1.2rem; font-size: 0.86rem; line-height: 1.55; }
.ops-strip p { margin: 0.35rem 0; }
.engine-section { margin: 1.2rem 0; }
.engine-section summary { cursor: pointer; font-weight: 700; font-size: 1rem; color: #2a2a45;
                          margin-bottom: 0.5rem; }
.sb-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 0.75rem; margin: 0.5rem 0 1rem; }
.sb-card { border: 1px solid #e0e0e8; border-radius: 0.45rem; padding: 0.75rem 0.9rem;
           background: #fff; display: block; color: inherit; }
.sb-card:hover { border-color: #3a5fcd; background: #fafbff; text-decoration: none; }
.sb-card h3 { margin: 0 0 0.35rem; font-size: 0.95rem; color: #2a2a45; }
.sb-card .standing { font-size: 0.84rem; margin: 0.25rem 0; }
.sb-card .gate-line { font-size: 0.82rem; color: #555; margin: 0.2rem 0; }
"""


@dataclass
class DashboardPaths:
    db_path: str
    ops_db_path: str
    logs_dir: str = "logs"
    sidecar_path: str = "data/paper_strategy_state.json"
    crumbs_json: str = "data/crumbs_alerts.json"
    assumed_slippage_pct: float | None = None
    refresh_seconds: int = 60
    kill_switch_path: str = "KILL_SWITCH"
    rh_token_path: str = "secrets/robinhood_token.json"


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    return html.escape(str(value))


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _fmt_usd(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _th(label: str, col_key: str) -> str:
    """Column header with glossary tooltip; visible label unchanged."""
    tip = g.COLUMN_TOOLTIPS.get(col_key, "")
    title = f" title='{_esc(tip)}'" if tip else ""
    return f"<th{title}>{_esc(label)}</th>"


def _fmt_flags(flags: list[str]) -> str:
    parts = []
    for f in flags:
        tip = g.flag_text(f)
        parts.append(f"<span class='flag' title='{_esc(tip)}'>{_esc(f)}</span>")
    return "".join(parts)


def _flag_legend(present_flags: set[str] | None = None) -> str:
    """Always-visible plain-English legend (not hover-only) for the flags.

    Shows the union of the documented at-minimum set (`g.LEGEND_FLAGS`) and any
    flags actually present in the DB, normalized so ``forced_flow:foo`` collapses
    to ``forced_flow``. Text comes from `glossary.py` so the legend and the hover
    tooltips can never drift.
    """
    ordered: list[str] = list(g.LEGEND_FLAGS)
    for f in sorted(present_flags or set()):
        base = g.flag_base(f)
        if base not in ordered:
            ordered.append(base)
    items = "".join(
        f"<dt><span class='flag'>{_esc(b)}</span></dt><dd>{_esc(g.flag_text(b))}</dd>"
        for b in ordered
    )
    return (
        "<div class='flag-legend'><h4>What the flags mean</h4>"
        f"<dl>{items}</dl></div>"
    )


def _surprise_cell(
    surprise_pct: float | None,
    *,
    actual_eps: float | None = None,
    consensus_eps: float | None = None,
) -> str:
    """Earnings-surprise cell: signed % with the raw actual-vs-consensus EPS
    underneath (when known) and a 'tiny est.' marker for small denominators."""
    if surprise_pct is None:
        return "<td>—</td>"
    cls = "ok" if surprise_pct > 0 else ("no" if surprise_pct < 0 else "")
    pct = f"{surprise_pct * 100:+.1f}%"
    mark = ""
    if consensus_eps is not None and abs(consensus_eps) < SMALL_CONSENSUS_USD:
        mark = (
            "<span class='tiny-est' title='tiny estimate (|consensus| &lt; $0.10) — "
            "the % overstates the real beat'>tiny est.</span>"
        )
    detail = ""
    if actual_eps is not None and consensus_eps is not None:
        detail = (
            f"<br><span class='eps'>act ${actual_eps:.2f} vs est ${consensus_eps:.2f}</span>"
        )
    return f"<td class='{cls}'>{pct}{mark}{detail}</td>"


def _panel_explainer(key: str) -> str:
    body = g.PANEL_EXPLAINERS.get(key, "")
    if not body:
        return ""
    return (
        f"<details class='explainer'><summary>What am I looking at?</summary>{body}</details>"
    )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_central(value: str | datetime | None) -> datetime | None:
    """Parse a stored (UTC) timestamp and convert it to the display timezone.

    Naive timestamps are assumed UTC (everything is stored UTC by design); the
    only conversion the dashboard ever does is at render time.
    """
    if isinstance(value, datetime):
        ts: datetime | None = value
    else:
        if value and len(value) > 26:
            value = value[:26]
        ts = _parse_ts(value)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(DISPLAY_TZ)


def _fmt_central(value: str | datetime | None, *, with_time: bool = True) -> str:
    """Render a UTC-stored timestamp in US Central with a CDT/CST label,
    e.g. ``2026-06-11 10:00 PM CDT``. Returns ``—`` when unparseable."""
    dt = _to_central(value)
    if dt is None:
        return "—"
    if not with_time:
        return dt.strftime("%Y-%m-%d")
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%Y-%m-%d')} {hour}:{dt.strftime('%M %p %Z')}"


def _fmt_relative(ts: datetime | None, now: datetime | None = None) -> str:
    """Human-readable relative time, e.g. '2 h ago'."""
    if ts is None:
        return ""
    ref = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta = ref - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 90:
        return "just now"
    mins = secs // 60
    if mins < 90:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 48:
        return f"{hours} h ago"
    days = hours // 24
    if days < 14:
        return f"{days} d ago"
    return f"{days // 7} wk ago"


def _fmt_ts(value: str | None, now: datetime | None = None) -> str:
    """Absolute timestamp (US Central) plus relative annotation."""
    if not value:
        return "—"
    ts = _parse_ts(value[:26] if len(value) > 26 else value)
    short = _fmt_central(value)
    rel = _fmt_relative(ts, now)
    if rel:
        return f"{_esc(short)} <span class='rel'>({rel})</span>"
    return _esc(short)


def _next_us_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_us_trading_day(n):
        n += timedelta(days=1)
    return n


def _weekday_name(d: date) -> str:
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()]


def _effective_job_chip(
    job: o.JobStatus,
    paths: DashboardPaths,
    today: date,
) -> tuple[str, str, str]:
    """Chip state, optional exit-code suffix, and extra title text for a scheduled job."""
    state = job.state
    detail = f" exit {job.exit_code}" if job.state == "failed" else ""
    title_extra = ""
    if (
        job.log_prefix == "paper-tick"
        and job.state == "failed"
        and d.paper_tick_completed_today(paths.db_path, today=today)
    ):
        state = "ok"
        detail = ""
        title_extra = " — scheduled run failed; recovered by re-run"
    return state, detail, title_extra


def _tick_status_line(paths: DashboardPaths, today: date) -> str:
    """Last tick + next expected tick, holiday-aware."""
    if not d.db_exists(paths.db_path):
        return "No paper tick recorded yet — waiting for the first successful paper tick."

    # Report the last tick that actually produced data, not merely the last run
    # row — a crashed tick still writes one (2026-08 outage).
    productive = d.last_productive_tick(paths.db_path, mode="paper")
    last_bit = "no paper tick recorded yet"
    if productive:
        last_bit = f"last completed tick {_fmt_ts(productive)}"
        prod_ts = _parse_ts(productive[:26] if len(productive) > 26 else productive)
        if prod_ts is not None:
            stale = (today - prod_ts.astimezone(d._DISPLAY_TZ).date()).days
            if stale > 3:
                last_bit += (
                    f" <span class='no'>({stale} days ago — race is STALLED)</span>"
                )

    if not is_us_trading_day(today):
        nxt = _next_us_trading_day(today)
        return (
            f"{last_bit}. <strong>Market closed today</strong> — "
            f"next expected tick {_weekday_name(nxt)} (paper tick ~17:00 ET on trading days)."
        )

    paper_job = next((j for j in o.job_statuses(paths.logs_dir, today=today)
                      if j.log_prefix == "paper-tick"), None)
    db_ok_today = d.paper_tick_completed_today(paths.db_path, today=today)
    if db_ok_today or (paper_job and paper_job.ran_today and paper_job.state == "ok"):
        recovery = ""
        if db_ok_today and paper_job and paper_job.ran_today and paper_job.state == "failed":
            recovery = " <span class='muted'>(scheduled run failed — recovered by re-run)</span>"
        return f"{last_bit}. Today's paper tick <span class='ok'>completed</span>.{recovery}"
    if paper_job and paper_job.ran_today and paper_job.state == "failed":
        return f"{last_bit}. Today's paper tick <span class='no'>FAILED</span> — see logs."
    if paper_job and paper_job.ran_today and paper_job.state == "running":
        return f"{last_bit}. Paper tick <span class='warn'>running now</span>."
    return (
        f"{last_bit}. Next expected tick today ~17:00 ET "
        f"(after US market close on trading days)."
    )


def _rh_auth_status(token_path: str | Path) -> dict[str, Any]:
    """Read-only Robinhood OAuth token presence/expiry (no network)."""
    p = Path(token_path)
    if not p.is_file():
        return {"state": "missing", "detail": "no token file — run rh_auth login before pilot"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"state": "invalid", "detail": "token file unreadable"}
    expires_at = data.get("expires_at")
    if expires_at is None:
        return {"state": "present", "detail": "token on disk (no expiry recorded)"}
    try:
        exp = float(expires_at)
    except (TypeError, ValueError):
        return {"state": "present", "detail": "token on disk"}
    now = datetime.now(timezone.utc).timestamp()
    if now >= exp:
        return {"state": "expired", "detail": f"expired {_fmt_central(datetime.fromtimestamp(exp, tz=timezone.utc))}"}
    return {
        "state": "ok",
        "detail": f"valid until {_fmt_central(datetime.fromtimestamp(exp, tz=timezone.utc))}",
    }


def _kill_switch_status(kill_switch_path: str | Path) -> dict[str, str]:
    engaged = Path(kill_switch_path).exists()
    if engaged:
        return {"state": "engaged", "detail": "KILL_SWITCH file present — all new live orders refused"}
    return {"state": "clear", "detail": "kill switch clear (no KILL_SWITCH file)"}


def _brain_bot_for_ensemble(db_path: str) -> str | None:
    """First scoreboard strategy's brain lane for ensemble-health checks."""
    for strat in d.strategies(db_path):
        if not strat.get("scoreboard"):
            continue
        for lane in strat.get("lanes") or []:
            if lane.get("role") == "brain":
                return lane["bot_id"]
    return None


def _global_nav(current: str = "/") -> str:
    links = [
        ("/", "Scoreboard"),
        ("/execution", "Execution"),
        ("/ops", "Ops"),
        ("/lens", "Lens"),
        ("/glossary", "Glossary"),
        ("/debates", "AI debates"),
    ]
    parts = ["<nav class='nav-header'>"]
    for href, label in links:
        cls = " class='active'" if current == href else ""
        parts.append(f"<a href='{href}'{cls}>{_esc(label)}</a>")
    parts.append("</nav>")
    return "".join(parts)


def _global_ops_strip(
    paths: DashboardPaths,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> str:
    """Compact ops/health strip — on every page."""
    day = today or date.today()
    parts = ["<div class='ops-strip'>"]

    statuses = o.job_statuses(paths.logs_dir, today=day)
    chips: list[str] = []
    for job in statuses:
        state, detail, title_extra = _effective_job_chip(job, paths, day)
        label = {"ok": "&#10003;", "failed": "&#10007;", "pending": "&#8943;", "running": "&#8635;"}[
            state
        ]
        chips.append(
            f"<span class='chip {state}' title='scheduled {job.scheduled_time}{title_extra}'>"
            f"{label} {_esc(job.name)}{detail}</span>"
        )
    parts.append(f"<p><b>Scheduled jobs today</b> {' '.join(chips)}</p>")
    parts.append(f"<p><b>Paper tick</b> {_tick_status_line(paths, day)}</p>")

    spend = o.daily_spend(paths.logs_dir, today=day)
    cap = f" / cap {_fmt_usd(spend.paper_llm_cap_usd)}" if spend.paper_llm_cap_usd else ""
    spend_bits = [
        f"paper LLM {_fmt_usd(spend.paper_llm_usd)}{cap}" if spend.paper_llm_usd is not None
        else "paper LLM —",
        f"Flow Lens {_fmt_usd(spend.flow_lens_usd)}" if spend.flow_lens_usd is not None
        else "Flow Lens —",
        f"<strong>total {_fmt_usd(spend.total_usd)}</strong>",
    ]
    parts.append(f"<p><b>Today's LLM/API spend</b> {' · '.join(spend_bits)}</p>")

    ks = _kill_switch_status(paths.kill_switch_path)
    ks_cls = "no" if ks["state"] == "engaged" else "ok"
    parts.append(
        f"<p><b>Kill switch</b> <span class='{ks_cls}'>{_esc(ks['detail'])}</span></p>"
    )

    auth = _rh_auth_status(paths.rh_token_path)
    auth_cls = {"ok": "ok", "present": "warn", "missing": "muted", "expired": "no", "invalid": "no"}.get(
        auth["state"], "muted"
    )
    parts.append(
        f"<p><b>Robinhood auth</b> <span class='{auth_cls}'>{_esc(auth['detail'])}</span></p>"
    )

    if d.db_exists(paths.db_path):
        brain_id = _brain_bot_for_ensemble(paths.db_path)
        if brain_id:
            try:
                health = d.ensemble_health(paths.db_path, bot_id=brain_id, mode="paper")
            except Exception:  # noqa: BLE001
                health = {"degraded": False}
            if health.get("degraded"):
                when = _fmt_central(health.get("as_of")) if health.get("as_of") else "the last run"
                parts.append(
                    "<p class='ensemble-warn'>&#9888; <strong>AI ensemble health:</strong> "
                    "GPT cross-check unavailable on the last gated run "
                    f"({_esc(str(health.get('flagged', 0)))} of {_esc(str(health.get('checked', 0)))} "
                    f"theses on {_esc(when)}) — Claude-only, capped at 0.60.</p>"
                )
            else:
                parts.append(
                    "<p class='muted'>AI ensemble health: GPT cross-check OK on the last gated run.</p>"
                )
    parts.append("</div>")
    return "".join(parts)


def _page_shell(
    title: str,
    body: str,
    paths: DashboardPaths,
    *,
    nav_path: str = "/",
    refresh_seconds: int | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> str:
    refresh = (
        f"<meta http-equiv='refresh' content='{refresh_seconds}'>" if refresh_seconds else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title>{refresh}<style>{_STYLE}</style></head><body>"
        f"<h1>{_esc(title)} <span class='muted'>paper DB: {_esc(paths.db_path)}"
        f" · auto-refresh {refresh_seconds or '—'}s</span></h1>"
        f"<p class='muted tz-note'>All times shown in US Central ({DISPLAY_TZ_NAME}, "
        "CDT/CST). Stored data is UTC.</p>"
        f"{_global_nav(nav_path)}"
        f"{_global_ops_strip(paths, today=today, now=now)}"
        f"{body}</body></html>"
    )


def _status_pill(status: str) -> str:
    labels = {
        "too_early": ("too early", "early"),
        "measuring": ("measuring", "measuring"),
        "passing": ("passing", "passing"),
        "failing": ("failing", "failing"),
    }
    text, cls = labels.get(status, (status, "early"))
    return f"<span class='sb-tag {cls}'>{_esc(text)}</span>"


def _gate_progress_html(card: dict[str, Any]) -> str:
    gate = card.get("gate") or {}
    gate_kind = card.get("gate_kind") or ""
    progress = gate.get("progress") or 0.0
    pct_w = min(max(float(progress), 0.0), 1.0) * 100
    fill_cls = "ok" if card.get("status") == "passing" else ("warn" if card.get("status") == "measuring" else "")
    bar = (
        f"<span class='pbar'><span class='pbar-fill {fill_cls}' "
        f"style='width:{pct_w:.0f}%'></span></span>"
    )
    if gate_kind == "brier":
        val = gate.get("value")
        brier = f"{val:.3f}" if val is not None else "—"
        return (
            f"<div class='gate-line'>{bar} n={gate.get('sample_n', 0)}/{gate.get('needed', 10)} "
            f"· Brier {brier} vs ≤{gate.get('bar', 0.25)}</div>"
        )
    if gate_kind == "fade_signals":
        rev = gate.get("reversion_rate")
        rev_s = _fmt_pct(rev, 1) if rev is not None else "—"
        net = gate.get("net_pl")
        net_s = _fmt_usd(net) if net is not None else "—"
        evaluated = gate.get("evaluated", 0)
        return (
            f"<div class='gate-line'>{bar} {gate.get('sample_n', 0)}/{gate.get('needed', 40)} fades resolved "
            f"· reversion {rev_s} vs {_fmt_pct(gate.get('reversion_bar', 0.55), 0)} "
            f"· brain net {net_s} "
            f"<span class='muted'>({evaluated} crashes evaluated)</span></div>"
        )
    hit = gate.get("value")
    hit_s = _fmt_pct(hit, 1) if hit is not None else "—"
    return (
        f"<div class='gate-line'>{bar} n={gate.get('sample_n', 0)}/{gate.get('needed', 30)} "
        f"· sign accuracy {hit_s} vs {_fmt_pct(gate.get('bar', 0.55), 0)}</div>"
    )


def _standing_line(standing: dict[str, Any]) -> str:
    edge = standing.get("edge")
    if edge is None:
        return "Brain vs coin: no closed trades yet"
    b_pl = standing.get("brain_pl")
    c_pl = standing.get("control_pl")
    b_s = _fmt_usd(b_pl) if b_pl is not None else "—"
    c_s = _fmt_usd(c_pl) if c_pl is not None else "—"
    rel = "ahead" if edge > 0 else ("behind" if edge < 0 else "tied with")
    return f"Realized P/L — brain {b_s} vs coin {c_s} — brain {rel} by {_fmt_usd(abs(edge))}"


def _scoreboard_card_html(card: dict[str, Any], db_path: str) -> str:
    sid = card["strategy_id"]
    gate_kind = _gate_kind_for(sid, db_path)
    return (
        f"<a class='sb-card' href='/strategy/{_esc(sid)}'>"
        f"<h3>{_esc(card['label'])} {_status_pill(card['status'])}</h3>"
        f"<div class='standing'>{_esc(_standing_line(card.get('standing') or {}))}</div>"
        f"{_gate_progress_html({**card, 'gate_kind': gate_kind})}"
        "</a>"
    )


def _gate_kind_for(strategy_id: str, db_path: str) -> str:
    for s in d.strategies(db_path):
        if s["strategy_id"] == strategy_id:
            return s["gate_kind"]
    return "sign_accuracy"


def _lane_stats_for_summary(paths: DashboardPaths) -> list[str]:
    """One line per scoreboard strategy: brain-vs-coin standing."""
    if not d.db_exists(paths.db_path):
        return ["No lane data yet — the race starts with the first paper tick."]
    sb = d.scoreboard(paths.db_path)
    lines: list[str] = []
    for card in sb.get("strategies") or []:
        standing = card.get("standing") or {}
        label = card.get("label") or card.get("strategy_id")
        edge = standing.get("edge")
        if edge is None:
            lines.append(f"<strong>{_esc(label)}</strong>: no closed trades yet.")
            continue
        lines.append(
            f"<strong>{_esc(label)}</strong>: {_esc(_standing_line(standing))}"
        )
    return lines or ["No scoreboard strategies registered yet."]


def _summary_strip(
    paths: DashboardPaths,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> str:
    """Top-of-page plain-English summary for non-traders."""
    day = today or date.today()
    lane_lines = _lane_stats_for_summary(paths)
    tick_line = _tick_status_line(paths, day)

    # Count the lanes that have actually ticked (distinct paper bot_ids with
    # snapshots), so the headline never drifts as lanes come online (fade twin,
    # shadow, Frontier…) or contradicts the head-to-head race view below.
    try:
        active = sum(1 for lane in d.list_bot_lanes(paths.db_path) if lane.mode == "paper")
    except Exception:  # noqa: BLE001 - summary must render even on a fresh/empty DB
        active = 0
    count_word = {0: "No", 1: "One", 2: "Two", 3: "Three", 4: "Four",
                  5: "Five", 6: "Six"}.get(active, str(active))
    lane_noun = "strategy lane" if active == 1 else "strategy lanes"

    parts = [
        "<div class='summary-strip'>",
        f"<p><strong>What is this?</strong> {count_word} {lane_noun} paper-trading "
        "<strong>$100k each of fake money</strong> on real market data, to find out "
        "which approach works before any real money is used. The core race is the "
        "<strong>AI-gated lane vs a mechanical coin-flip lane</strong>; extra lanes "
        "(shadow, control twins, and the Frontier engine) join the race as each "
        "strategy comes online.</p>",
        "<p><strong>Is real money at risk?</strong> No. Gates before real capital: "
        "shadow week clean → calibrated bucket (Brier ≤ 0.25, 10+ samples) → "
        "$500 pilot → only then scaling.</p>",
        f"<p><strong>Who's winning?</strong> {' · '.join(lane_lines)}</p>",
        f"<p><strong>Did it run today?</strong> {tick_line}</p>",
        "</div>",
    ]
    return "".join(parts)


def _phase_journey() -> str:
    h = g.PHASE_HOVER
    return (
        "<p class='phase-journey'>"
        f"<span title='{_esc(h['backtest'])}'>backtest ✅</span> → "
        f"<span title='{_esc(h['paper'])}'>paper ▶</span> → "
        f"<span title='{_esc(h['pilot'])}'>$500 pilot ⏳</span> → "
        f"<span title='{_esc(h['scaled'])}'>scaled ⬜</span>"
        "</p>"
    )


def _signed_pct_cell(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "<td>—</td>"
    cls = "ok" if value > 0 else ("no" if value < 0 else "")
    return f"<td class='{cls}'>{value * 100:+.{digits}f}%</td>"


def equity_sparkline(points: list[dict], width: int = 420, height: int = 90) -> str:
    """Inline SVG polyline of one equity curve."""
    values = [p["total_equity"] for p in points]
    if len(values) < 2:
        return '<span class="muted">not enough snapshots</span>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (width - 8) / (len(values) - 1)
    coords = " ".join(
        f"{4 + i * step:.1f},{height - 6 - (v - lo) / span * (height - 12):.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" role="img">'
        f'<polyline points="{coords}" fill="none" stroke="#3a5fcd" stroke-width="1.5"/></svg>'
    )


def race_overlay_svg(
    curves: dict[str, list[dict]], width: int = 640, height: int = 160
) -> str:
    """Both lanes' % return from their own start, overlaid on one chart."""
    colors = ["#3a5fcd", "#b3261e", "#0a7d36"]
    series: list[tuple[str, list[float], str]] = []
    for idx, (label, points) in enumerate(curves.items()):
        values = [p["total_equity"] for p in points]
        if len(values) < 2 or values[0] == 0:
            continue
        rets = [v / values[0] - 1.0 for v in values]
        series.append((label, rets, colors[idx % len(colors)]))
    if not series:
        return '<span class="muted">not enough snapshots for the race chart yet</span>'
    lo = min(min(r) for _, r, _ in series)
    hi = max(max(r) for _, r, _ in series)
    span = (hi - lo) or 1.0
    parts = [f'<svg width="{width}" height="{height}" role="img">']
    if lo <= 0 <= hi:
        y0 = height - 6 - (0 - lo) / span * (height - 12)
        parts.append(
            f'<line x1="4" y1="{y0:.1f}" x2="{width - 4}" y2="{y0:.1f}" '
            f'stroke="#ccc" stroke-dasharray="3,3"/>'
        )
    for label, rets, color in series:
        step = (width - 8) / (len(rets) - 1)
        coords = " ".join(
            f"{4 + i * step:.1f},{height - 6 - (r - lo) / span * (height - 12):.1f}"
            for i, r in enumerate(rets)
        )
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        )
    parts.append("</svg>")
    legend = " ".join(
        f"<span style='color:{color}'>&#9632; {_esc(label)} ({rets[-1] * 100:+.2f}%)</span>"
        for label, rets, color in series
    )
    return "".join(parts) + f"<br><span class='muted'>{legend}</span>"


def calibration_svg(bins: list[dict], size: int = 200) -> str:
    """Stated probability (x) vs realized hit rate (y), diagonal = perfect."""
    pad = 14
    inner = size - 2 * pad

    def x(p: float) -> float:
        return pad + p * inner

    def y(p: float) -> float:
        return size - pad - p * inner

    parts = [
        f'<svg width="{size}" height="{size}" role="img">',
        f'<line x1="{x(0):.0f}" y1="{y(0):.0f}" x2="{x(1):.0f}" y2="{y(1):.0f}" '
        f'stroke="#bbb" stroke-dasharray="4,3"/>',
        f'<rect x="{pad}" y="{pad}" width="{inner}" height="{inner}" fill="none" stroke="#ddd"/>',
    ]
    for b in bins:
        r = 3 + 2 * math.sqrt(b["count"])
        parts.append(
            f'<circle cx="{x(b["mean_stated"]):.1f}" cy="{y(b["hit_rate"]):.1f}" '
            f'r="{min(r, 14):.1f}" fill="#3a5fcd" fill-opacity="0.65">'
            f'<title>p&#8776;{b["mean_stated"]:.2f} hit {b["hit_rate"]:.2f} (n={b["count"]})</title>'
            f"</circle>"
        )
    parts.append("</svg>")
    return "".join(parts)


# Pre-registered fade entry threshold (mirrors fade.FADE_PROBABILITY_ENTRY_THRESHOLD).
# Defined here so app.py stays independent of the bot module.
_FADE_ENTRY_THRESHOLD = 0.55

_MODEL_DEBATE_SHORT: dict[str, str] = {
    "claude-opus-4-8": "Opus",
    "gpt-5.5": "GPT",
}


def _model_debate_short_name(model_id: str) -> str:
    """Short label for per-model rubric lines in the debate view."""
    mid = str(model_id or "").strip()
    if not mid:
        return "—"
    return _MODEL_DEBATE_SHORT.get(mid, mid.split("-")[0].capitalize() if mid else mid)


def _classification_css(classification: str) -> str:
    if classification == "overreaction":
        return "ok"
    if classification == "proportionate":
        return "warn"
    return "no"


def _per_model_rubric_html(fg: dict[str, Any], flags: list[str]) -> str:
    """Per-model rubric breakdown when persisted; empty string for older rows."""
    model_probs = fg.get("model_probabilities") or {}
    if not model_probs:
        return ""
    model_classes = fg.get("model_classifications") or {}
    class_values = {
        str(model_classes.get(mid, "")).strip()
        for mid in model_probs
        if str(model_classes.get(mid, "")).strip()
    }
    models_disagree = len(class_values) > 1 or "ensemble_disagreement" in flags
    chunks: list[str] = []
    for model_id in sorted(model_probs.keys()):
        prob = model_probs[model_id]
        classification = str(model_classes.get(model_id) or "—")
        short = _model_debate_short_name(model_id)
        cls_css = _classification_css(classification)
        disagree_cls = " debate-model-disagree" if models_disagree else ""
        chunks.append(
            f"<span class='debate-model{disagree_cls}'>"
            f"<b>{_esc(short)}</b> {_fmt(prob, 2)} "
            f"(<span class='{cls_css}'>{_esc(classification)}</span>)</span>"
        )
    return (
        "<p class='kv'><b>Per-model rubric</b> "
        + " &middot; ".join(chunks)
        + "</p>"
    )


def _debate_section(
    fg: dict[str, Any],
    *,
    prompt_version: str | None = None,
) -> str:
    """HTML for the AI Debate section on a fade thesis drill-down page.

    ``fg`` is ``inputs.fade_gate`` from the entry decision.  Handles gracefully:
    empty dict (old/mech row), hard-skip, ensemble_unavailable, and counter-pass
    errors.
    """
    parts = ["<h2>AI Debate</h2>"]
    if not fg:
        parts.append(
            "<p class='muted'>No fade gate data stored — this entry did not run "
            "the AI fade pipeline (mechanical twin or pre-pipeline row).</p>"
        )
        return "".join(parts)

    flags = list(fg.get("flags") or [])
    cp = fg.get("counter_pass") or {}
    ensemble_prob = fg.get("ensemble_probability")
    classification = str(fg.get("classification") or "—")

    if "ensemble_unavailable" in flags or "cross_model_failed" in flags:
        parts.append(
            "<p class='ensemble-warn'>&#9888; <strong>Degraded run:</strong> "
            "GPT was unavailable; Claude-only, probability capped at 0.60.</p>"
        )

    # --- 1. Rubric round ---
    parts.append("<div class='debate-box'>")
    parts.append("<h4>1 &middot; Rubric round</h4>")
    if fg.get("hard_skip"):
        reasons = ", ".join(fg.get("hard_skip_reasons") or [])
        parts.append(
            f"<p class='warn'>&#9940; <strong>Hard skip</strong>: {_esc(reasons)} — "
            "binary-resolution situations (pending trial, fraud, going-concern, "
            "bankruptcy) are never faded by design.</p>"
        )
    elif "triaged_out" in flags:
        parts.append(
            "<p class='muted'>Haiku triage filter rejected this event before "
            "full scoring — the filing did not substantively explain the drop.</p>"
        )
    elif ensemble_prob is not None:
        disagree_note = ""
        if "ensemble_disagreement" in flags:
            tip = _esc(g.flag_text("ensemble_disagreement"))
            disagree_note = (
                f" <span class='flag' title='{tip}'>ensemble_disagreement</span>"
                " <span class='muted'>(models diverged &gt;0.15; lower number kept)</span>"
            )
        cls = _classification_css(classification)
        parts.append(
            "<p class='kv'>"
            f"<b>Classification</b> <span class='{cls}'>{_esc(classification)}</span><br>"
            f"<b>Ensemble probability</b> {_fmt(ensemble_prob, 2)}{disagree_note}<br>"
            f"<b>Entry threshold</b> {_FADE_ENTRY_THRESHOLD} (pre-registered — "
            "overreaction + p &ge; 0.55 + no veto required to enter)</p>"
        )
        per_model = _per_model_rubric_html(fg, flags)
        if per_model:
            parts.append(per_model)
    else:
        parts.append("<p class='muted'>Rubric score not available (old row or no doc).</p>")

    other_flags = [
        f for f in flags
        if f not in {
            "ensemble_disagreement", "ensemble_unavailable", "cross_model_failed",
            "counter_pass_veto", "counter_pass_failed", "counter_pass_unparseable",
            "counter_pass_same_model", "triaged_out", "hard_skip_binary_resolution",
        }
    ]
    if other_flags:
        parts.append(f"<p>Additional flags: {_fmt_flags(other_flags)}</p>")
    parts.append("</div>")

    # --- 2. Devil's advocate ---
    argument = str(cp.get("argument", "")).strip()
    strongest = str(cp.get("strongest_point", "")).strip()
    counter_model = str(cp.get("counter_model_id", "")).strip()

    if argument or strongest:
        parts.append("<div class='debate-box'>")
        parts.append(
            "<h4>2 &middot; Devil&rsquo;s advocate &mdash; "
            "&ldquo;The case that the crash is CORRECT&rdquo;</h4>"
        )
        if argument:
            parts.append(f"<div class='debate-quote'>{_esc(argument)}</div>")
        else:
            parts.append("<p class='muted'>Argument text not stored.</p>")
        if strongest:
            parts.append(f"<p><b>Strongest point:</b> {_esc(strongest)}</p>")
        if counter_model:
            parts.append(
                f"<p class='muted' style='font-size:0.79rem'>"
                f"Argued by: {_esc(counter_model)}</p>"
            )
        parts.append("</div>")
    elif not fg.get("hard_skip") and "triaged_out" not in flags:
        # Threshold not cleared — counter-pass doesn't run below 0.55
        parts.append(
            "<div class='debate-box'><h4>2 &middot; Devil&rsquo;s advocate</h4>"
            "<p class='muted'>Counter-pass did not run — ensemble probability was "
            "below the 0.55 entry threshold, so the adversarial step was skipped.</p>"
            "</div>"
        )

    # --- 3. Re-read & verdict ---
    revised_prob = cp.get("revised_probability")
    final_prob = cp.get("final_probability")
    reread_rationale = str(cp.get("reread_rationale", "")).strip()
    vetoed = bool(cp.get("vetoed", fg.get("vetoed", False)))
    reread_model = str(cp.get("reread_model_id", "")).strip()

    if "revised_probability" in cp or "final_probability" in cp or cp.get("error"):
        parts.append("<div class='debate-box'>")
        parts.append("<h4>3 &middot; Re-read &amp; verdict</h4>")
        if reread_rationale:
            parts.append(f"<p><b>Re-read rationale:</b> {_esc(reread_rationale)}</p>")
        # v2-only fields (reread had to engage before the number)
        for label, key in (
            ("Counter-point acknowledged", "reread_strongest_point"),
            ("Rebuttal", "reread_rebuttal"),
        ):
            val = str(cp.get(key, "")).strip()
            if val:
                parts.append(f"<p><b>{label}:</b> {_esc(val)}</p>")
        if revised_prob is not None:
            parts.append(
                f"<p><b>Revised probability:</b> {_fmt(revised_prob, 2)}</p>"
            )
        if final_prob is not None:
            parts.append(
                f"<p><b>Final probability:</b> {_fmt(final_prob, 2)} "
                "<span class='muted'>(= min(ensemble, revised)) "
                f"vs threshold {_FADE_ENTRY_THRESHOLD}</span></p>"
            )
        if cp.get("error"):
            err_msg = str(cp.get("reason") or cp.get("error", "")).strip()
            parts.append(
                f"<div class='debate-vetoed'><strong>VETOED (pipeline error)</strong>"
                f" &mdash; {_esc(err_msg)}</div>"
            )
        elif vetoed and "counter_pass_veto" in flags:
            fp_str = _fmt(final_prob, 2) if final_prob is not None else "—"
            parts.append(
                f"<div class='debate-vetoed'><strong>VETOED</strong> &mdash; "
                f"final probability {fp_str} &lt; {_FADE_ENTRY_THRESHOLD} threshold. "
                "Trade blocked by devil&rsquo;s advocate.</div>"
            )
        elif not vetoed and final_prob is not None:
            parts.append(
                f"<div class='debate-entered'><strong>ENTERED</strong> &mdash; "
                f"final probability {_fmt(final_prob, 2)} &ge; "
                f"{_FADE_ENTRY_THRESHOLD} threshold. Trade executed.</div>"
            )
        if reread_model:
            parts.append(
                f"<p class='muted' style='font-size:0.79rem'>"
                f"Re-read by: {_esc(reread_model)}</p>"
            )
        parts.append("</div>")

    # --- 4. Provenance ---
    pv = prompt_version or "—"
    if counter_model or reread_model or pv != "—":
        parts.append("<div class='debate-box'>")
        parts.append("<h4>4 &middot; Provenance</h4>")
        prov: list[str] = []
        if counter_model:
            prov.append(f"<b>Counter-model (argued):</b> {_esc(counter_model)}")
        if reread_model:
            prov.append(f"<b>Re-read model:</b> {_esc(reread_model)}")
        prov.append(
            f"<b>Prompt version:</b> {_esc(pv)} "
            "<span class='muted'>(fade-v1 vs fade-v2 debates show separately "
            "in the veto-value tally once resolved)</span>"
        )
        if "counter_pass_same_model" in flags:
            prov.append(
                "<span class='flag'>counter_pass_same_model</span> "
                "<span class='muted'>"
                "(GPT was unavailable; primary model argued against itself)</span>"
            )
        parts.append(f"<p class='kv'>{'<br>'.join(prov)}</p>")
        parts.append("</div>")

    return "".join(parts)


# ---------------------------------------------------------------------------
# panels
# ---------------------------------------------------------------------------


def _panel_ops_health(paths: DashboardPaths, *, today: date | None = None) -> str:
    day = today or date.today()
    statuses = o.job_statuses(paths.logs_dir, today=day)
    parts = ["<h2>1 · Ops health</h2>", _panel_explainer("ops"), "<p>"]
    for job in statuses:
        state, detail, title_extra = _effective_job_chip(job, paths, day)
        label = {
            "ok": "&#10003;", "failed": "&#10007;", "pending": "&#8943;", "running": "&#8635;",
        }[state]
        parts.append(
            f"<span class='chip {state}' title='scheduled {job.scheduled_time}{title_extra}'>"
            f"{label} {_esc(job.name)}{detail}</span>"
        )
    parts.append("</p>")

    tick_line = "no paper tick recorded yet"
    if d.db_exists(paths.db_path):
        run = d.last_run_info(paths.db_path, mode="paper")
        if run:
            ts = run["finished_at"] or run["started_at"]
            tick_line = f"last paper tick: {_fmt_ts(ts)} ({run['status']}) — run {run['run_id'][:8]}"
    paper_job = next((j for j in statuses if j.log_prefix == "paper-tick"), None)
    if paper_job and paper_job.ran_today and paper_job.state == "failed":
        if d.paper_tick_completed_today(paths.db_path, today=day):
            tick_line += (
                "  <span class='muted'>(scheduled run failed — recovered by re-run)</span>"
            )
        else:
            tick_line += "  <span class='no'>(today's tick FAILED — see log)</span>"

    spend = o.daily_spend(paths.logs_dir)
    cap = f" / cap {_fmt_usd(spend.paper_llm_cap_usd)}" if spend.paper_llm_cap_usd else ""
    spend_bits = [
        f"paper LLM {_fmt_usd(spend.paper_llm_usd)}{cap}" if spend.paper_llm_usd is not None
        else "paper LLM —",
        f"Flow Lens {_fmt_usd(spend.flow_lens_usd)}" if spend.flow_lens_usd is not None
        else "Flow Lens —",
        f"<strong>total {_fmt_usd(spend.total_usd)}</strong>",
    ]
    parts.append(
        f"<p class='kv'><b>Last paper tick</b> {tick_line}<br>"
        f"<b>Today's LLM/API spend</b> {' · '.join(spend_bits)}</p>"
    )

    if d.db_exists(paths.db_path):
        try:
            brain_id = _brain_bot_for_ensemble(paths.db_path)
            health = (
                d.ensemble_health(paths.db_path, bot_id=brain_id, mode="paper")
                if brain_id
                else {"degraded": False}
            )
        except Exception:  # noqa: BLE001 - the strip must render on any DB state
            health = {"degraded": False}
        if health.get("degraded"):
            when = _fmt_central(health.get("as_of")) if health.get("as_of") else "the last run"
            parts.append(
                "<p class='ensemble-warn'>&#9888; <strong>AI ensemble health:</strong> "
                "the GPT cross-check was unavailable on the last gated run "
                f"({_esc(str(health.get('flagged', 0)))} of {_esc(str(health.get('checked', 0)))} "
                f"theses on {_esc(when)}) — graded by Claude only and capped at 0.60 confidence. "
                "Check OpenAI funding.</p>"
            )
        else:
            parts.append(
                "<p class='muted'>AI ensemble health: GPT cross-check OK on the last gated run.</p>"
            )
    return "".join(parts)


def _panel_positions(paths: DashboardPaths, bot_ids: list[str]) -> str:
    parts = ["<h2>Open positions</h2>", _panel_explainer("positions")]
    if not d.db_exists(paths.db_path):
        parts.append("<p class='muted'>No paper DB yet — created by the first successful paper tick.</p>")
        return "".join(parts)
    sidecar = o.load_sidecar(paths.sidecar_path)
    any_rows = False
    for bot_id in bot_ids:
        lane = d.open_positions(
            paths.db_path, bot_id, "paper", o.open_trades_for(sidecar, bot_id)
        )
        if lane["as_of"] is None:
            continue
        parts.append(
            f"<h3>{_esc(bot_id)} <span class='muted'>as of {_esc(_fmt_central(lane['as_of']))} · "
            f"cash {_fmt_usd(lane['cash'])} · equity {_fmt_usd(lane['total_equity'])} "
            f"<span class='fake-money'>(fake $)</span></span></h3>"
        )
        if not lane["positions"]:
            parts.append("<p class='muted'>flat — no open positions</p>")
            continue
        any_rows = True
        parts.append(
            "<table><tr>"
            f"{_th('symbol', 'symbol')}{_th('qty', 'qty')}{_th('mark', 'mark')}"
            f"{_th('P&L $', 'pnl_usd')}{_th('ret from entry', 'ret_from_entry')}"
            f"{_th('to hard stop', 'to_hard_stop')}{_th('to trail stop', 'to_trail_stop')}"
            f"{_th('to profit-take', 'to_profit_take')}{_th('time-stop in', 'time_stop_in')}"
            f"{_th('thesis', 'thesis')}</tr>"
        )
        for p in lane["positions"]:
            pt = "taken" if p["profit_taken"] else _fmt_pct(p["to_profit_take_pct"])
            days = (
                f"{p['days_to_time_stop']:.1f}d" if p["days_to_time_stop"] is not None else "—"
            )
            thesis = (
                f"<a href='/thesis/{_esc(p['thesis_id'])}'>view</a>" if p["thesis_id"] else "—"
            )
            pnl_cls = "ok" if p["unrealized_pnl"] > 0 else ("no" if p["unrealized_pnl"] < 0 else "")
            parts.append(
                f"<tr><td>{_esc(p['symbol'])}</td><td>{p['quantity']:g}</td>"
                f"<td>{_fmt(p['mark'], 2)}</td>"
                f"<td class='{pnl_cls}'>{p['unrealized_pnl']:+,.2f}</td>"
                f"{_signed_pct_cell(p['return_from_entry'])}"
                f"<td>{_fmt_pct(p['to_hard_stop_pct'])}</td>"
                f"<td>{_fmt_pct(p['to_trail_stop_pct'])}</td>"
                f"<td>{pt}</td><td>{days}</td><td>{thesis}</td></tr>"
            )
        parts.append("</table>")
    if not any_rows:
        parts.append(
            "<p class='muted'>Distances: hard stop −8% from entry · trail 7% off high-water · "
            "profit-take +15% (half) · time-stop 14d.</p>"
        )
    else:
        parts.append(
            "<p class='muted'>Rules: hard stop −8% from entry · trail 7% off high-water · "
            "profit-take 50% at +15% · time-stop 14d. Distance = room left before the rule fires.</p>"
        )
    return "".join(parts)


def _panel_theses(paths: DashboardPaths, *, bot_ids: list[str] | None = None) -> str:
    parts = ["<h2>Thesis browser</h2>", _panel_explainer("theses")]
    if not d.db_exists(paths.db_path):
        parts.append("<p class='muted'>No paper DB yet.</p>")
        parts.append(_flag_legend(set()))
        return "".join(parts)
    present_flags: set[str] = set()
    theses: list[dict[str, Any]] = []
    if bot_ids:
        for bid in bot_ids:
            theses.extend(d.list_theses(paths.db_path, bot_id=bid, limit=50))
        theses.sort(key=lambda t: t["created_at"], reverse=True)
        theses = theses[:50]
    else:
        theses = d.list_theses(paths.db_path, limit=50)
    if theses:
        parts.append(
            "<table><tr>"
            f"{_th('created', 'created')}{_th('lane', 'lane')}{_th('symbol', 'symbol')}"
            f"{_th('bucket', 'bucket')}{_th('surprise', 'surprise')}"
            f"{_th('stated p', 'stated_p')}{_th('quality', 'quality')}"
            f"{_th('flags', 'flags')}{_th('outcome', 'outcome')}{_th('rationale', 'rationale')}"
            "</tr>"
        )
        for t in theses:
            present_flags.update(t["flags"] or [])
            outcome = (
                "<span class='muted'>open</span>" if t["outcome"] is None
                else ("<span class='ok'>win</span>" if t["outcome"] else "<span class='no'>loss</span>")
            )
            rationale = t["rationale"]
            short = rationale if len(rationale) <= 140 else rationale[:137] + "…"
            parts.append(
                f"<tr><td><a href='/thesis/{_esc(t['thesis_id'])}'>{_esc(_fmt_central(t['created_at']))}</a></td>"
                f"<td>{_esc(t['bot_id'])}</td><td>{_esc(t['symbol'])}</td>"
                f"<td>{_esc(t['strategy_bucket'])}</td>"
                f"{_surprise_cell(t['surprise_pct'], actual_eps=t['actual_eps'], consensus_eps=t['consensus_eps'])}"
                f"<td>{_fmt(t['stated_probability'], 2)}</td>"
                f"<td>{_fmt(t['quality_score'], 2)}</td><td>{_fmt_flags(t['flags'])}</td>"
                f"<td>{outcome}</td><td class='rationale'>{_esc(short)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>No theses recorded yet.</p>")

    skips = d.gate_skips(paths.db_path, limit=15)
    if skips:
        parts.append("<h3>Recent quality-gate vetoes</h3>")
        parts.append(
            "<table><tr>"
            f"{_th('as of', 'as_of')}{_th('symbol', 'symbol')}{_th('surprise', 'surprise')}"
            f"{_th('quality', 'quality')}{_th('stated p', 'stated_p')}{_th('flags', 'flags')}"
            f"{_th('triage reason', 'triage_reason')}</tr>"
        )
        for s in skips:
            present_flags.update(s["flags"] or [])
            parts.append(
                f"<tr><td>{_esc(_fmt_central(s['as_of']))}</td><td>{_esc(s['symbol'])}</td>"
                f"{_surprise_cell(s['surprise_pct'], actual_eps=s.get('actual_eps'), consensus_eps=s.get('consensus_eps'))}"
                f"<td>{_fmt(s['quality_score'], 2)}</td><td>{_fmt(s['stated_probability'], 2)}</td>"
                f"<td>{_fmt_flags(s['flags'])}</td>"
                f"<td class='rationale'>{_esc(s['triage_reason'])}</td></tr>"
            )
        parts.append("</table>")

    # Always-visible flag legend (sourced from glossary.py), covering every flag
    # in the DB plus the documented at-minimum set. Sits under the tables above.
    parts.append(_flag_legend(present_flags))

    fills = d.recent_fills(paths.db_path, limit=20)
    if fills:
        parts.append("<h3>Recent fills (drill down to the thesis)</h3>")
        parts.append(
            "<table><tr>"
            f"{_th('filled at', 'filled_at')}{_th('lane', 'lane')}{_th('symbol', 'symbol')}"
            f"{_th('side', 'side')}{_th('qty', 'qty')}{_th('price', 'price')}"
            f"{_th('slippage', 'slippage')}{_th('thesis', 'thesis')}</tr>"
        )
        for f in fills:
            link = (
                f"<a href='/thesis/{_esc(f['thesis_id'])}'>view</a>" if f["thesis_id"] else "—"
            )
            parts.append(
                f"<tr><td>{_esc(_fmt_central(f['filled_at']))}</td><td>{_esc(f['bot_id'])}</td>"
                f"<td>{_esc(f['symbol'])}</td><td>{_esc(f['side'])}</td>"
                f"<td>{f['quantity']:g}</td><td>{_fmt(f['price'], 2)}</td>"
                f"<td>{_fmt_pct(f['slippage_pct'])}</td><td>{link}</td></tr>"
            )
        parts.append("</table>")
    return "".join(parts)


def _panel_race_strategy(race: dict[str, Any], strategy_id: str) -> str:
    parts = ["<h2>Brain-vs-coin race</h2>", _panel_explainer("race")]
    race_kind = race.get("race_kind") or "pairwise"
    curves = race.get("curves") or {}
    if race.get("curves_note"):
        parts.append(f"<p class='muted'>{_esc(race['curves_note'])}</p>")

    if race_kind == "sign_accuracy":
        summary = race.get("summary") or {}
        brain = summary.get("brain") or {}
        parts.append(
            "<p class='kv'>"
            f"<b>Sign accuracy</b> {_fmt_pct(brain.get('hit_rate'), 1)} "
            f"(n={brain.get('sample_n', 0)}, bar {_fmt_pct(brain.get('bar'), 0)})"
            "</p>"
        )
        if curves:
            parts.append(race_overlay_svg(curves))
        parts.append("<p class='muted'>Frontier down is predict-only — no short book.</p>")
        return "".join(parts)

    if race_kind == "three_way":
        if curves:
            parts.append(race_overlay_svg(curves))
        parts.append(
            "<table><tr>"
            f"{_th('lane', 'lane')}{_th('resolved trades', 'resolved_trades')}"
            f"{_th('hit rate', 'hit_rate')}{_th('mean net/trade', 'mean_net_trade')}</tr>"
        )
        for key in ("v1", "v2", "coin"):
            s = race.get("summary", {}).get(key) or {}
            parts.append(
                f"<tr><td>{_esc(s.get('bot_id', key))}</td><td>{s.get('resolved_trades', 0)}</td>"
                f"<td>{_fmt_pct(s.get('hit_rate'), 1)}</td><td>{_fmt_pct(s.get('mean_net_return'))}</td></tr>"
            )
        parts.append("</table>")
        events = race.get("events") or []
        if events:
            parts.append("<h3>Per-event (latest 30)</h3>")
            parts.append(
                "<table><tr>"
                f"{_th('resolved', 'resolved')}{_th('symbol', 'symbol')}"
                f"{_th('v1 net', 'gated_net')}{_th('v2 net', 'mech_net')}{_th('coin net', 'cum_mech')}</tr>"
            )
            for e in events[-30:]:
                parts.append(
                    f"<tr><td>{_esc(_fmt_central(str(e['as_of'])))}</td><td>{_esc(e.get('symbol') or '—')}</td>"
                    f"{_signed_pct_cell(e.get('v1'))}{_signed_pct_cell(e.get('v2'))}"
                    f"{_signed_pct_cell(e.get('coin'))}</tr>"
                )
            parts.append("</table>")
        return "".join(parts)

    gated_id = race.get("gated_bot_id")
    mech_id = race.get("mech_bot_id")
    if gated_id and mech_id and curves:
        parts.append(race_overlay_svg({gated_id: curves.get(gated_id, []), mech_id: curves.get(mech_id, [])}))
    parts.append(
        "<table><tr>"
        f"{_th('lane', 'lane')}{_th('resolved trades', 'resolved_trades')}"
        f"{_th('hit rate', 'hit_rate')}{_th('mean net/trade', 'mean_net_trade')}</tr>"
    )
    for key in ("gated", "mech"):
        s = race.get("summary", {}).get(key) or {}
        parts.append(
            f"<tr><td>{_esc(s.get('bot_id', key))}</td><td>{s.get('resolved_trades', 0)}</td>"
            f"<td>{_fmt_pct(s.get('hit_rate'), 1)}</td><td>{_fmt_pct(s.get('mean_net_return'))}</td></tr>"
        )
    parts.append("</table>")
    events = race.get("events") or []
    if events:
        parts.append("<h3>Per-event head-to-head (latest 30)</h3>")
        parts.append(
            "<table><tr>"
            f"{_th('resolved', 'resolved')}{_th('symbol', 'symbol')}"
            f"{_th('gated net', 'gated_net')}{_th('mech net', 'mech_net')}"
            f"{_th('cum gated', 'cum_gated')}{_th('cum mech', 'cum_mech')}</tr>"
        )
        for e in events[-30:]:
            gated = (
                "<td class='warn'>gate veto</td>" if e.get("gate_skipped") and e.get("gated") is None
                else _signed_pct_cell(e.get("gated"))
            )
            parts.append(
                f"<tr><td>{_esc(_fmt_central(str(e['as_of'])))}</td><td>{_esc(e.get('symbol') or '—')}</td>"
                f"{gated}{_signed_pct_cell(e.get('mech'))}"
                f"{_signed_pct_cell(e.get('cum_gated'))}{_signed_pct_cell(e.get('cum_mech'))}</tr>"
            )
    else:
        parts.append("<p class='muted'>No resolved round trips yet.</p>")

    if gated_id and mech_id:
        verdict = v.race_verdict(race, gated_bot_id=gated_id, mech_bot_id=mech_id)
        parts.append(f"<p class='verdict'>{_esc(verdict)}</p>")
    return "".join(parts)


def _panel_race(paths: DashboardPaths) -> str:
    """Legacy alias — earnings ride race for tests that still call the panel directly."""
    if not d.db_exists(paths.db_path):
        return _panel_race_strategy({"summary": {}, "curves": {}}, "earnings_ride")
    race = d.race(paths.db_path, "earnings_ride", mode="paper")
    return _panel_race_strategy(race, "earnings_ride")


def _strategy_bot_ids(db_path: str, strategy_id: str) -> list[str]:
    for s in d.strategies(db_path):
        if s["strategy_id"] == strategy_id:
            return [lane["bot_id"] for lane in s["lanes"]]
    return []


def _panel_calibration(paths: DashboardPaths, *, strategy_id: str | None = None) -> str:
    parts = ["<h2>Calibration</h2>", _panel_explainer("calibration")]
    if not d.db_exists(paths.db_path):
        parts.append("<p class='muted'>No paper DB yet.</p>")
        for line in v.calibration_verdicts([]):
            parts.append(f"<p class='verdict'>{_esc(line)}</p>")
        return "".join(parts)

    if strategy_id:
        detail = d.strategy_detail(paths.db_path, strategy_id, mode="paper")
        bot_ids = set(_strategy_bot_ids(paths.db_path, strategy_id))
        rows = [
            r for r in d.calibration_by_strategy_bucket(paths.db_path)
            if r["bot_id"] in bot_ids
        ]
        curves = detail.get("calibration") or []
    else:
        rows = d.calibration_by_strategy_bucket(paths.db_path)
        curves = d.calibration_curves(paths.db_path)

    if rows:
        parts.append(
            "<table><tr>"
            f"{_th('bot', 'bot')}{_th('bucket', 'bucket')}{_th('samples', 'samples')}"
            f"{_th('mean Brier', 'mean_brier')}{_th('hit rate', 'hit_rate')}"
            f"{_th('calibrated?', 'calibrated')}</tr>"
        )
        for r in rows:
            gate = "<td class='ok'>YES</td>" if r["calibrated"] else "<td class='no'>no</td>"
            parts.append(
                f"<tr><td>{_esc(r['bot_id'])}</td><td>{_esc(r['strategy_bucket'])}</td>"
                f"<td>{r['count']}</td><td>{_fmt(r['mean_brier'])}</td>"
                f"<td>{_fmt_pct(r['hit_rate'], 1)}</td>{gate}</tr>"
            )
        parts.append("</table>")
        parts.append(
            "<p class='muted'>Gate: &#8805;10 resolved samples and mean Brier &#8804; 0.25. "
            "Paper trades regardless; real capital only for calibrated buckets.</p>"
        )
    else:
        parts.append("<p class='muted'>No calibration rows yet.</p>")

    for curve in curves:
        if not curve["bins"]:
            continue
        parts.append(
            f"<h3>{_esc(curve['bot_id'])} · {_esc(curve['strategy_bucket'])} "
            f"<span class='muted'>({curve['samples']} samples)</span></h3>"
        )
        parts.append(calibration_svg(curve["bins"]))
        parts.append(
            "<table><tr>"
            f"{_th('stated-p bin', 'stated_p_bin')}{_th('n', 'n')}"
            f"{_th('mean stated', 'mean_stated')}{_th('realized hit rate', 'realized_hit_rate')}"
            "</tr>"
        )
        for b in curve["bins"]:
            parts.append(
                f"<tr><td>{b['bin_low']:.2f}–{b['bin_high']:.2f}</td><td>{b['count']}</td>"
                f"<td>{_fmt(b['mean_stated'], 2)}</td><td>{_fmt_pct(b['hit_rate'], 1)}</td></tr>"
            )
        parts.append("</table>")

    for line in v.calibration_verdicts(rows):
        parts.append(f"<p class='verdict'>{_esc(line)}</p>")
    return "".join(parts)


def _panel_lens(paths: DashboardPaths) -> str:
    parts = ["<h2>Flow Lens &amp; Crumbs</h2>", _panel_explainer("signals")]

    feed = o.crumbs_feed_status(paths.crumbs_json)
    scan_at = _fmt_central(feed.get("last_scan_at")) if feed.get("last_scan_at") else "—"
    state = feed.get("state") or "absent"
    if state == "absent":
        parts.append(
            "<h3>Crumbs — odd-lot tender alerts</h3>"
            "<p class='muted'>Didn't run yet — no scan output file.</p>"
        )
    elif state == "stale":
        parts.append(
            f"<h3>Crumbs — odd-lot tender alerts</h3>"
            f"<p class='warn'>Last scan {_esc(scan_at)} — feed is stale "
            f"({feed.get('age_hours', '?')} h old).</p>"
        )
    elif state == "ran_empty":
        parts.append(
            f"<h3>Crumbs — odd-lot tender alerts</h3>"
            f"<p class='muted'>Last scan {_esc(scan_at)} — none found.</p>"
        )
    else:
        parts.append(
            f"<h3>Crumbs — odd-lot tender alerts</h3>"
            f"<p class='muted'>Last scan {_esc(scan_at)} — {feed.get('alert_count', 0)} alert(s).</p>"
        )
        alerts = feed.get("alerts") or []
        if alerts:
            parts.append(
                "<table><tr>"
                f"{_th('filed', 'filed')}{_th('company', 'company')}{_th('form', 'form')}"
                f"{_th('matched', 'matched')}{_th('filing', 'filing')}</tr>"
            )
            for a in alerts[:50]:
                url = str(a.get("filing_url", ""))
                link = f"<a href='{_esc(url)}'>EDGAR</a>" if url.startswith("http") else "—"
                parts.append(
                    f"<tr><td>{_esc(a.get('filed_at', '—'))}</td><td>{_esc(a.get('company', '—'))}</td>"
                    f"<td>{_esc(a.get('form', '—'))}</td><td>{_esc(a.get('matched_phrase', '—'))}</td>"
                    f"<td>{link}</td></tr>"
                )
            parts.append("</table>")

    parts.append("<h3>Flow Lens — consensus / novelty</h3>")
    if d.db_exists(paths.ops_db_path):
        board = d.flow_lens_board(paths.ops_db_path)
        if board["rows"]:
            parts.append(f"<p class='muted'>logged {_esc(board['logged_date'])}</p>")
            parts.append(
                "<table><tr>"
                f"{_th('symbol', 'symbol')}{_th('consensus', 'consensus')}{_th('novel?', 'novel')}"
                f"{_th('models', 'models')}{_th('prompts', 'prompts')}</tr>"
            )
            for r in board["rows"]:
                cls = " class='novel'" if r["novelty_flag"] else ""
                novel = "<span class='ok'>NEW</span>" if r["novelty_flag"] else "—"
                parts.append(
                    f"<tr{cls}><td>{_esc(r['symbol'])}</td><td>{_fmt(r['consensus_score'], 2)}</td>"
                    f"<td>{novel}</td><td>{_esc(', '.join(r['models']))}</td>"
                    f"<td>{r['prompt_count']}</td></tr>"
                )
            parts.append("</table>")
        else:
            parts.append("<p class='muted'>No Flow Lens consensus rows yet.</p>")
    else:
        parts.append(f"<p class='muted'>Ops DB not found at {_esc(paths.ops_db_path)}.</p>")
    return "".join(parts)


def _panel_signals(paths: DashboardPaths) -> str:
    """Alias for lens page content."""
    return _panel_lens(paths)


def _panel_closed_trades(trades: list[dict[str, Any]], *, title: str = "Closed trades") -> str:
    parts = [f"<h2>{_esc(title)}</h2>"]
    if not trades:
        parts.append("<p class='muted'>No closed round trips yet.</p>")
        return "".join(parts)
    parts.append(
        "<table><tr>"
        f"{_th('exit', 'filled_at')}{_th('lane', 'lane')}{_th('symbol', 'symbol')}"
        f"{_th('qty', 'qty')}{_th('entry', 'price')}{_th('exit px', 'price')}"
        f"{_th('realized P&L', 'pnl_usd')}{_th('hold days', 'time_stop_in')}"
        f"{_th('exit reason', 'thesis')}{_th('thesis', 'thesis')}</tr>"
    )
    for t in trades:
        pl = t.get("realized_pl")
        pl_cls = "ok" if pl and pl > 0 else ("no" if pl and pl < 0 else "")
        thesis = (
            f"<a href='/thesis/{_esc(t['thesis_id'])}'>view</a>" if t.get("thesis_id") else "—"
        )
        parts.append(
            f"<tr><td>{_esc(_fmt_central(t.get('exit_at')))}</td>"
            f"<td>{_esc(t.get('bot_id', '—'))}</td><td>{_esc(t.get('symbol', '—'))}</td>"
            f"<td>{t.get('quantity', 0):g}</td>"
            f"<td>{_fmt(t.get('entry_price'), 2)}</td><td>{_fmt(t.get('exit_price'), 2)}</td>"
            f"<td class='{pl_cls}'>{_fmt_usd(pl)}</td>"
            f"<td>{t.get('hold_days', '—')}</td>"
            f"<td>{_esc(t.get('exit_reason', '—'))}</td><td>{thesis}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def _panel_lanes_table(lanes: list[dict[str, Any]]) -> str:
    parts = ["<h2>Lanes</h2>"]
    parts.append(
        "<table><tr><th>bot_id</th><th>role</th><th>tradeable</th><th>active</th><th>buckets</th></tr>"
    )
    for lane in lanes:
        active = "<span class='ok'>yes</span>" if lane.get("active") else "<span class='muted'>no</span>"
        trade = "yes" if lane.get("tradeable") else ("predict-only" if lane.get("predict_only") else "no")
        buckets = ", ".join(lane.get("buckets") or []) or "—"
        parts.append(
            f"<tr><td>{_esc(lane['bot_id'])}</td><td>{_esc(lane['role'])}</td>"
            f"<td>{_esc(trade)}</td><td>{active}</td><td>{_esc(buckets)}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def _panel_fade_debates_index(db_path: str) -> str:
    parts = [
        "<h2>Fade decisions</h2>",
        _panel_explainer("fade_decisions"),
        "<p class='muted'>Recent fade decisions for this strategy. "
        "<a href='/debates'>Full debates index</a> · click a row for the full debate drill-down.</p>",
    ]
    debates = d.fade_debates_index(db_path, limit=25)
    if not debates:
        parts.append("<p class='muted'>No fade decisions recorded yet.</p>")
        return "".join(parts)
    parts.append(
        "<table><tr>"
        f"{_th('date', 'created')}{_th('symbol', 'symbol')}{_th('action', 'action')}"
        f"{_th('ensemble p', 'stated_p')}{_th('outcome', 'outcome')}{_th('debate', 'thesis')}</tr>"
    )
    for deb in debates:
        action = "ENTERED" if deb["entered"] else ("VETOED" if deb["vetoed"] else deb.get("action", "—"))
        cls = "ok" if deb["entered"] else ("no" if deb["vetoed"] else "warn")
        outcome_val = deb.get("outcome")
        if outcome_val is None:
            outcome_cell = "<span class='muted'>open</span>"
        elif int(outcome_val) == 1:
            outcome_cell = "<span class='ok'>win</span>"
        else:
            outcome_cell = "<span class='no'>loss</span>"
        tid = deb.get("thesis_id") or ""
        link = f"<a href='/thesis/{_esc(tid)}'>view debate</a>" if tid else "—"
        parts.append(
            f"<tr><td>{_esc(_fmt_central(deb['as_of']))}</td>"
            f"<td>{_esc(deb.get('symbol') or '—')}</td>"
            f"<td class='{cls}'>{_esc(action)}</td>"
            f"<td>{_fmt(deb.get('ensemble_probability'), 2)}</td>"
            f"<td>{outcome_cell}</td><td>{link}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def _panel_model_disagreement(paths: DashboardPaths) -> str:
    """Tier 2 — AI-value trackers: model disagreement + veto-value tally."""
    parts = [
        "<h2>9 &middot; AI-value trackers</h2>",
        "<p class='muted'>Pairs with the debate view; informs fade-v2 prompt tuning. "
        "Most buckets will read &ldquo;not enough data yet&rdquo; until ~July.</p>",
    ]
    if not d.db_exists(paths.db_path):
        parts.append("<p class='muted'>No paper DB yet.</p>")
        return "".join(parts)

    # Model disagreement
    parts.append("<h3>Model disagreement (Opus vs GPT on rubric)</h3>")
    try:
        dis = d.model_disagreement_stats(paths.db_path)
    except Exception:  # noqa: BLE001
        dis = None
    if dis and dis["total"] > 0:
        parts.append(
            "<p class='kv'>"
            f"<b>Fade decisions analysed</b> {dis['total']}<br>"
            f"<b>Disagreed (&gt;0.15 probability gap)</b> "
            f"{dis['disagreements']} ({_fmt_pct(dis['disagree_rate'])})<br>"
            f"<b>GPT unavailable (single-model)</b> "
            f"{dis['ensemble_unavailable']} ({_fmt_pct(dis['unavail_rate'])})</p>"
        )
        if dis["total"] < 20:
            parts.append(
                f"<p class='muted'>Too early to draw conclusions (n={dis['total']}). "
                "Who-was-right-when-they-disagreed needs resolved outcomes (~July).</p>"
            )
        else:
            parts.append(
                "<p class='muted'>Who-was-right-when-they-disagreed: needs resolved "
                "outcomes; will populate once fades start settling.</p>"
            )
    else:
        parts.append(
            "<p class='muted'>No fade decisions yet — disagreement stats will appear here.</p>"
        )

    # Veto-value tally
    parts.append("<h3>Veto-value tally</h3>")
    try:
        veto = d.veto_value_tally(paths.db_path)
    except Exception:  # noqa: BLE001
        veto = None
    if veto and veto["total_vetoed"] > 0:
        parts.append(
            "<p class='kv'>"
            f"<b>Total vetoed fades</b> {veto['total_vetoed']}<br>"
            f"<b>Resolved vetoes</b> {veto['total_resolved']}</p>"
        )
        if not veto["meaningful"]:
            parts.append(
                f"<p class='muted'>Not enough resolved vetoes yet "
                f"(need &ge;10, have {veto['total_resolved']}). "
                "Will read sensibly once fades settle (~July).</p>"
            )
        for version in sorted(veto["by_version"]):
            stats = veto["by_version"][version]
            if stats["resolved"] > 0:
                win_rate = stats["would_have_won"] / stats["resolved"]
                parts.append(
                    f"<p class='kv'><b>{_esc(version)}</b> &mdash; "
                    f"{stats['resolved']} resolved vetoes: "
                    f"{stats['would_have_won']} would have won "
                    f"({_fmt_pct(win_rate)} of resolved)</p>"
                )
            else:
                parts.append(
                    f"<p class='muted'>{_esc(version)}: {stats['total']} vetoed, "
                    "none resolved yet.</p>"
                )
    else:
        parts.append(
            "<p class='muted'>No vetoed fades yet — veto-value tally will appear here.</p>"
        )
    return "".join(parts)


def _panel_lanes_and_slippage(paths: DashboardPaths) -> str:
    """v1 panels kept: equity curve per lane + realized-vs-assumed slippage."""
    parts = ["<h2>Equity curves (all lanes)</h2>", _panel_explainer("lanes")]
    if not d.db_exists(paths.db_path):
        parts.append("<p class='muted'>No snapshots persisted yet.</p>")
        return "".join(parts)
    lanes = d.list_bot_lanes(paths.db_path)
    if not lanes:
        parts.append("<p class='muted'>No snapshots persisted yet.</p>")
    for lane in lanes:
        points = d.equity_curve(paths.db_path, lane.bot_id, lane.mode)
        start = points[0]["total_equity"] if points else None
        end = points[-1]["total_equity"] if points else None
        ret = (end - start) / start if start else None
        parts.append(
            f"<p><strong>{_esc(lane.bot_id)}</strong> "
            f"<span class='muted'>[{_esc(lane.mode)}] {lane.snapshots} snapshots, "
            f"return {_fmt_pct(ret)} <span class='fake-money'>(fake $)</span></span><br>"
            f"{equity_sparkline(points)}</p>"
        )

    parts.append("<h2>Slippage — realized vs assumed</h2>")
    slip = d.slippage_summary(paths.db_path, paths.assumed_slippage_pct)
    if slip:
        parts.append(
            "<table><tr>"
            f"{_th('bot', 'bot')}{_th('mode', 'mode')}{_th('fills', 'fills')}"
            f"{_th('mean realized', 'mean_realized')}{_th('max realized', 'max_realized')}"
            f"{_th('assumed', 'assumed')}{_th('realized / assumed', 'realized_vs_assumed')}"
            "</tr>"
        )
        for r in slip:
            ratio = "—" if r["realized_vs_assumed"] is None else f"{r['realized_vs_assumed']:.2f}×"
            parts.append(
                f"<tr><td>{_esc(r['bot_id'])}</td><td>{_esc(r['mode'])}</td>"
                f"<td>{r['fills']}</td><td>{_fmt_pct(r['mean_realized_slippage_pct'])}</td>"
                f"<td>{_fmt_pct(r['max_realized_slippage_pct'])}</td>"
                f"<td>{_fmt_pct(r['assumed_slippage_pct'])}</td><td>{ratio}</td></tr>"
            )
        parts.append("</table>")
        parts.append(
            "<p class='muted'>Phase 5 rule of thumb: if realized &gt; 2&times; assumed, "
            "shrink to more liquid names.</p>"
        )
    else:
        parts.append("<p class='muted'>No fills yet.</p>")

    # Shot 12: per-fill expected-vs-realized for LIVE fills. Signed slippage is
    # positive when adverse (BUY paid above / SELL received below the decision
    # mark); expected price is recovered from realized + signed slippage + side.
    live = d.live_slippage_detail(paths.db_path, mode="live")
    if live:
        parts.append("<h2>Live fills — expected vs realized</h2>")
        parts.append(
            "<table><tr>"
            f"{_th('filled at', 'filled_at')}{_th('bot', 'bot')}{_th('symbol', 'symbol')}"
            f"{_th('side', 'side')}{_th('qty', 'qty')}{_th('expected', 'expected')}"
            f"{_th('realized', 'realized')}{_th('slippage (signed)', 'slippage_signed')}"
            "</tr>"
        )
        for f in live:
            exp = "—" if f["expected_price"] is None else f"${f['expected_price']:,.4f}"
            slip_cls = "no" if f["slippage_pct"] > 0 else ("ok" if f["slippage_pct"] < 0 else "")
            parts.append(
                f"<tr><td>{_esc(f['filled_at'])}</td><td>{_esc(f['bot_id'])}</td>"
                f"<td>{_esc(f['symbol'])}</td><td>{_esc(f['side'])}</td>"
                f"<td>{f['quantity']:g}</td><td>{exp}</td>"
                f"<td>${f['realized_price']:,.4f}</td>"
                f"<td class='{slip_cls}'>{_fmt_pct(f['slippage_pct'])}</td></tr>"
            )
        parts.append("</table>")
        parts.append(
            "<p class='muted'>Signed slippage: positive = adverse (cost). Conflates the "
            "overnight gap (5 PM decision mark &rarr; next-open fill) with execution slippage.</p>"
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def _page_head(title: str, refresh_seconds: int | None) -> str:
    refresh = (
        f"<meta http-equiv='refresh' content='{refresh_seconds}'>" if refresh_seconds else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title>{refresh}<style>{_STYLE}</style></head><body>"
    )


def _make_paths(
    db_path: str | Path,
    assumed_slippage_pct: float | None,
    *,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
) -> DashboardPaths:
    return DashboardPaths(
        db_path=str(db_path),
        ops_db_path=str(ops_db_path or db_path),
        logs_dir=str(logs_dir),
        sidecar_path=str(sidecar_path),
        crumbs_json=str(crumbs_json),
        assumed_slippage_pct=assumed_slippage_pct,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )


def render_page(
    db_path: str | Path,
    assumed_slippage_pct: float | None,
    *,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    refresh_seconds: int | None = 60,
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
    today: date | None = None,
    now: datetime | None = None,
    **_: Any,
) -> str:
    """Home page — strategy scoreboard grouped by engine."""
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        sidecar_path=sidecar_path,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    body_parts = [
        _phase_journey(),
        "<p class='muted'>One card per strategy — click through for lanes, positions, race, and calibration.</p>",
    ]
    if not d.db_exists(paths.db_path):
        body_parts.append("<p class='muted'>No paper DB yet — scoreboard fills in after the first paper tick.</p>")
    else:
        sb = d.scoreboard(paths.db_path)
        cards_by_id = {c["strategy_id"]: c for c in sb.get("strategies") or []}
        for section in sb.get("engines") or []:
            engine_label = section.get("label") or section.get("engine_id")
            strats = section.get("strategies") or []
            body_parts.append(f"<details class='engine-section' open><summary>{_esc(engine_label)}</summary>")
            body_parts.append("<div class='sb-cards'>")
            for strat in strats:
                card = cards_by_id.get(strat["strategy_id"])
                if card:
                    body_parts.append(_scoreboard_card_html(card, paths.db_path))
            body_parts.append("</div></details>")
        body_parts.append(
            "<p class='muted'>Non-trading lens tools (Flow Lens, Crumbs) live on "
            "<a href='/lens'>/lens</a>. Shadow rehearsal on <a href='/ops'>/ops</a>.</p>"
        )
    body = "".join(body_parts)
    return _page_shell(
        "Stockbot — strategy scoreboard",
        body,
        paths,
        nav_path="/",
        refresh_seconds=refresh_seconds,
        today=today,
        now=now,
    )


def render_strategy_page(
    db_path: str | Path,
    strategy_id: str,
    *,
    assumed_slippage_pct: float | None = None,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
) -> str:
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        sidecar_path=sidecar_path,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    if not d.db_exists(paths.db_path):
        body = "<p class='muted'>No paper DB yet.</p>"
        return _page_shell(f"Strategy — {strategy_id}", body, paths, nav_path="/")

    try:
        sidecar = o.load_sidecar(paths.sidecar_path)
        detail = d.strategy_detail(
            paths.db_path, strategy_id, mode="paper", sidecar=sidecar
        )
    except KeyError:
        body = (
            f"<h2>Unknown strategy</h2>"
            f"<p class='muted'>No strategy registered with id <code>{_esc(strategy_id)}</code>.</p>"
            "<p><a href='/'>Back to scoreboard</a></p>"
        )
        return _page_shell("Unknown strategy", body, paths, nav_path="/")

    label = detail.get("label") or strategy_id
    bot_ids = [lane["bot_id"] for lane in detail.get("lanes") or []]
    tradeable_ids = [
        lane["bot_id"] for lane in detail.get("lanes") or [] if lane.get("tradeable")
    ]
    body_parts = [
        f"<p class='muted'><a href='/'>Scoreboard</a> &rarr; {_esc(label)}</p>",
        f"<p class='kv'><b>Standing</b> {_esc(_standing_line(detail.get('standing') or {}))}</p>",
        _panel_lanes_table(detail.get("lanes") or []),
    ]
    if tradeable_ids:
        body_parts.append(_panel_positions(paths, tradeable_ids))
    body_parts.append(_panel_closed_trades(detail.get("closed_trades") or []))
    body_parts.append(_panel_race_strategy(detail.get("race") or {}, strategy_id))
    body_parts.append(_panel_calibration(paths, strategy_id=strategy_id))
    body_parts.append(_panel_theses(paths, bot_ids=bot_ids))
    if strategy_id == "crash_fade":
        body_parts.append(_panel_fade_debates_index(paths.db_path))
        body_parts.append(_panel_model_disagreement(paths))
    return _page_shell(f"Strategy — {label}", "".join(body_parts), paths, nav_path="/")


def render_execution_page(
    db_path: str | Path,
    assumed_slippage_pct: float | None,
    *,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
) -> str:
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        sidecar_path=sidecar_path,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    trades = d.closed_trades(paths.db_path, strategy_id=None, mode="paper") if d.db_exists(paths.db_path) else []
    body = "".join([
        "<p class='muted'>All strategies — FIFO round trips with realized $ P/L, plus slippage for the $500 pilot.</p>",
        _panel_closed_trades(trades, title="Closed trades (all strategies)"),
        _panel_lanes_and_slippage(paths),
    ])
    return _page_shell("Execution", body, paths, nav_path="/execution")


def render_ops_page(
    db_path: str | Path,
    assumed_slippage_pct: float | None,
    *,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
    today: date | None = None,
) -> str:
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        sidecar_path=sidecar_path,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    body_parts = [
        "<p class='muted'>Scheduled jobs, run history, go-live reference, shadow rehearsal.</p>",
        _panel_ops_health(paths, today=today),
    ]
    if d.db_exists(paths.db_path):
        run = d.last_run_info(paths.db_path, mode="paper")
        if run:
            ts = run["finished_at"] or run["started_at"]
            body_parts.append(
                f"<h2>Last paper run</h2><p class='kv'>"
                f"<b>run_id</b> {_esc(run['run_id'])}<br>"
                f"<b>bot</b> {_esc(run['bot_id'])}<br>"
                f"<b>finished</b> {_fmt_ts(ts)} ({_esc(run['status'])})</p>"
            )
        shadow = next(
            (s for s in d.strategies(paths.db_path) if s["strategy_id"] == "shadow_rehearsal"),
            None,
        )
        if shadow:
            body_parts.append("<h2>Shadow rehearsal</h2>")
            body_parts.append(_panel_lanes_table(shadow.get("lanes") or []))
            shadow_id = shadow["lanes"][0]["bot_id"] if shadow.get("lanes") else None
            if shadow_id:
                pts = d.equity_curve(paths.db_path, shadow_id, "paper")
                if pts:
                    body_parts.append(
                        f"<p>{equity_sparkline(pts)}</p>"
                    )
                else:
                    body_parts.append("<p class='muted'>No shadow snapshots yet.</p>")
    body_parts.append(
        "<h2>Go-live reference</h2>"
        "<p class='muted'>Kill switch: create a <code>KILL_SWITCH</code> file at the repo root to "
        "refuse all new live orders. Robinhood auth: "
        "<code>python -m stockbot.tools.rh_auth login</code> before pilot. "
        "See <code>docs/go-live-runbook.md</code>.</p>"
    )
    return _page_shell("Ops", "".join(body_parts), paths, nav_path="/ops", today=today)


def render_lens_page(
    db_path: str | Path,
    assumed_slippage_pct: float | None,
    *,
    ops_db_path: str | Path | None = None,
    logs_dir: str | Path = "logs",
    sidecar_path: str | Path = "data/paper_strategy_state.json",
    crumbs_json: str | Path = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
) -> str:
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        sidecar_path=sidecar_path,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    body = _panel_lens(paths)
    return _page_shell("Lens", body, paths, nav_path="/lens")


def render_debates_page(
    db_path: str | Path,
    *,
    assumed_slippage_pct: float | None = None,
    ops_db_path: str | Path | None = None,
    logs_dir: str = "logs",
    crumbs_json: str = "data/crumbs_alerts.json",
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
    **_ignore: object,  # tolerate the shared `common` kwargs bag from do_GET
) -> str:
    """AI Debates index — recent fade decisions. Route: /debates"""
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        ops_db_path=ops_db_path,
        logs_dir=logs_dir,
        crumbs_json=crumbs_json,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    body_parts = [
        "<p class='muted'>Recent fade decisions, newest first. "
        "<a href='/strategy/crash_fade'>Crash fade strategy page</a>.</p>",
    ]
    if not d.db_exists(db_path):
        body_parts.append("<p class='muted'>No paper DB yet.</p>")
        return _page_shell("AI debates", "".join(body_parts), paths, nav_path="/debates")

    debates = d.fade_debates_index(db_path, limit=40)
    if not debates:
        body_parts.append("<p class='muted'>No fade decisions recorded yet.</p>")
        return _page_shell("AI debates", "".join(body_parts), paths, nav_path="/debates")

    body_parts.append(
        "<table><tr>"
        f"{_th('date', 'created')}{_th('symbol', 'symbol')}"
        f"{_th('action', 'action')}{_th('ensemble p', 'stated_p')}"
        f"{_th('flags', 'flags')}{_th('outcome', 'outcome')}"
        f"{_th('debate', 'thesis')}</tr>"
    )
    for deb in debates:
        if deb["entered"]:
            action_cell = "<td class='ok'>ENTERED</td>"
        elif deb["vetoed"]:
            action_cell = "<td class='no'>VETOED</td>"
        else:
            action_cell = f"<td class='warn'>{_esc(deb['action'])}</td>"
        outcome_val = deb["outcome"]
        if outcome_val is None:
            outcome_cell = "<td><span class='muted'>open</span></td>"
        elif int(outcome_val) == 1:
            outcome_cell = "<td class='ok'>win</td>"
        else:
            outcome_cell = "<td class='no'>loss</td>"
        key_flags = [
            f for f in (deb.get("flags") or [])
            if f in {
                "ensemble_disagreement", "ensemble_unavailable",
                "cross_model_failed", "counter_pass_veto",
            }
        ]
        tid = deb["thesis_id"] or ""
        link = f"<a href='/thesis/{_esc(tid)}'>view debate</a>" if tid else "—"
        body_parts.append(
            f"<tr><td>{_esc(_fmt_central(deb['as_of']))}</td>"
            f"<td>{_esc(deb['symbol'] or '—')}</td>"
            f"{action_cell}"
            f"<td>{_fmt(deb['ensemble_probability'], 2)}</td>"
            f"<td>{_fmt_flags(key_flags) or '—'}</td>"
            f"{outcome_cell}"
            f"<td>{link}</td></tr>"
        )
    body_parts.append("</table>")
    return _page_shell("AI debates", "".join(body_parts), paths, nav_path="/debates")


def render_glossary_page(
    *,
    db_path: str = "data/paper.db",
    assumed_slippage_pct: float | None = None,
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
    **_ignore: object,  # tolerate the shared `common` kwargs bag from do_GET
) -> str:
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    body_parts = [
        "<p class='muted'>Plain-English terms used on the dashboard.</p>",
        "<dl class='kv'>",
    ]
    for term, definition in g.GLOSSARY_TERMS.items():
        body_parts.append(
            f"<dt><b>{_esc(term)}</b></dt><dd style='margin:0 0 0.8rem 0'>{_esc(definition)}</dd>"
        )
    body_parts.append("</dl>")
    return _page_shell("Glossary", "".join(body_parts), paths, nav_path="/glossary")


def render_thesis_page(
    db_path: str | Path,
    thesis_id: str,
    *,
    assumed_slippage_pct: float | None = None,
    kill_switch_path: str = "KILL_SWITCH",
    rh_token_path: str = "secrets/robinhood_token.json",
    **_ignore: object,  # tolerate the shared `common` kwargs bag from do_GET
) -> str:
    """Drill-down: one thesis with its full decision/fill audit trail."""
    paths = _make_paths(
        db_path,
        assumed_slippage_pct,
        kill_switch_path=kill_switch_path,
        rh_token_path=rh_token_path,
    )
    detail = d.thesis_detail(db_path, thesis_id) if d.db_exists(db_path) else None
    if detail is None:
        body = f"<h2>Thesis not found</h2><p class='muted'>{_esc(thesis_id)}</p>"
        return _page_shell("Thesis not found", body, paths, nav_path="/")

    outcome = (
        "open" if detail["outcome"] is None else ("WIN" if detail["outcome"] else "LOSS")
    )
    body_parts = [
        f"<p class='muted'><a href='/'>Scoreboard</a></p>",
        f"<h2>{_esc(detail['symbol'])} — {_esc(detail['strategy_bucket'])} ({outcome})</h2>",
        "<p class='kv'>"
        f"<b>thesis</b> {_esc(detail['thesis_id'])}<br>"
        f"<b>lane / mode</b> {_esc(detail['bot_id'])} / {_esc(detail['mode'])}<br>"
        f"<b>direction</b> {_esc(detail['direction'])}<br>"
        f"<b>stated probability</b> {_fmt(detail['stated_probability'], 2)}<br>"
        f"<b>created</b> {_esc(_fmt_central(detail['created_at']))}<br>"
        f"<b>resolved</b> {_esc(_fmt_central(detail['resolved_at']) if detail['resolved_at'] else '—')}</p>",
        f"<h2>Rationale</h2><pre>{_esc(detail['rationale'] or '(none)')}</pre>",
    ]

    quality = detail["quality"]
    if quality:
        flags = list(quality.get("flags", []) or [])
        body_parts.append(
            "<h2>Quality assessment (2c)</h2>"
            "<p class='kv'>"
            f"<b>quality score</b> {_fmt(quality.get('quality_score'), 2)}<br>"
            f"<b>stated probability</b> {_fmt(quality.get('stated_probability'), 2)}<br>"
            f"<b>ensemble flags</b> {_fmt_flags(flags) or '—'}<br>"
            f"<b>triage</b> {_esc((quality.get('triage') or {}).get('reason', '—'))}</p>"
        )

    entry_dec = next(
        (dec for dec in detail["decisions"] if dec["decision_type"] == "entry"), None
    )
    entry_inputs = entry_dec["inputs"] if entry_dec else {}
    fg = entry_inputs.get("fade_gate") or {}
    if fg:
        body_parts.append(
            _debate_section(fg, prompt_version=entry_dec.get("prompt_version") if entry_dec else None)
        )
    elif (detail.get("strategy_bucket") or "").startswith("crash:"):
        body_parts.append(
            "<h2>AI Debate</h2>"
            "<p class='muted'>No AI gate data for this fade thesis — "
            "mechanical twin lane or row pre-dates the debate pipeline.</p>"
        )

    body_parts.append("<h2>Decisions</h2>")
    if detail["decisions"]:
        for dec in detail["decisions"]:
            meta = []
            if dec["model_id"]:
                meta.append(f"model {dec['model_id']}")
            if dec["prompt_version"]:
                meta.append(f"prompt {dec['prompt_version']}")
            meta_str = f" · {' · '.join(meta)}" if meta else ""
            body_parts.append(
                f"<h3>{_esc(_fmt_central(dec['as_of']))} — {_esc(dec['decision_type'])} / "
                f"{_esc(dec['action'])}<span class='muted'>{_esc(meta_str)}</span></h3>"
            )
            body_parts.append(f"<pre>{_esc(json.dumps(dec['inputs'], indent=2, default=str))}</pre>")
    else:
        body_parts.append("<p class='muted'>No decisions recorded for this thesis.</p>")

    body_parts.append("<h2>Fills</h2>")
    if detail["fills"]:
        body_parts.append(
            "<table><tr><th>filled at</th><th>side</th><th>qty</th><th>price</th><th>slippage</th></tr>"
        )
        for f in detail["fills"]:
            body_parts.append(
                f"<tr><td>{_esc(_fmt_central(f['filled_at']))}</td><td>{_esc(f['side'])}</td>"
                f"<td>{f['quantity']:g}</td><td>{_fmt(f['price'], 2)}</td>"
                f"<td>{_fmt_pct(f['slippage_pct'])}</td></tr>"
            )
        body_parts.append("</table>")
    else:
        body_parts.append("<p class='muted'>No fills matched to this thesis yet.</p>")

    return _page_shell(f"Thesis — {detail['symbol']}", "".join(body_parts), paths, nav_path="/")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


def make_handler(paths: DashboardPaths):
    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_html(self, body: str, status: int = 200) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            route = unquote(self.path.split("?", 1)[0])
            common = dict(
                assumed_slippage_pct=paths.assumed_slippage_pct,
                ops_db_path=paths.ops_db_path,
                logs_dir=paths.logs_dir,
                sidecar_path=paths.sidecar_path,
                crumbs_json=paths.crumbs_json,
                kill_switch_path=paths.kill_switch_path,
                rh_token_path=paths.rh_token_path,
            )
            if route == "/glossary":
                self._send_html(render_glossary_page(db_path=paths.db_path, **common))
                return
            if route == "/debates":
                self._send_html(render_debates_page(paths.db_path, **common))
                return
            if route == "/execution":
                self._send_html(render_execution_page(paths.db_path, **common))
                return
            if route == "/ops":
                self._send_html(render_ops_page(paths.db_path, **common))
                return
            if route == "/lens":
                self._send_html(render_lens_page(paths.db_path, **common))
                return
            if route.startswith("/strategy/"):
                strategy_id = route[len("/strategy/"):]
                self._send_html(render_strategy_page(paths.db_path, strategy_id, **common))
                return
            if route.startswith("/thesis/"):
                thesis_id = route[len("/thesis/"):]
                self._send_html(render_thesis_page(paths.db_path, thesis_id, **common))
                return
            if route.startswith("/fill/"):
                fill_id = route[len("/fill/"):]
                thesis_id = (
                    d.thesis_id_for_fill(paths.db_path, fill_id)
                    if d.db_exists(paths.db_path)
                    else None
                )
                if thesis_id:
                    self.send_response(302)
                    self.send_header("Location", f"/thesis/{thesis_id}")
                    self.end_headers()
                else:
                    self._send_html(
                        render_thesis_page(paths.db_path, "(no thesis for fill)", **common),
                        404,
                    )
                return
            self._send_html(
                render_page(
                    paths.db_path,
                    refresh_seconds=paths.refresh_seconds,
                    **common,
                )
            )

        def log_message(self, fmt: str, *args) -> None:  # quiet
            pass

    return DashboardHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stockbot dashboard v3")
    parser.add_argument("--db", default="data/paper.db", help="Paper state DB")
    parser.add_argument("--ops-db", default=None, help="Ops DB for Flow Lens (default: config state.db_path)")
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--sidecar", default="data/paper_strategy_state.json")
    parser.add_argument("--crumbs-json", default="data/crumbs_alerts.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--refresh", type=int, default=60, help="Auto-refresh seconds (0 = off)")
    args = parser.parse_args(argv)

    cfg = load_config()
    paths = DashboardPaths(
        db_path=args.db,
        ops_db_path=args.ops_db or cfg.state.db_path,
        logs_dir=args.logs_dir,
        sidecar_path=args.sidecar,
        crumbs_json=args.crumbs_json,
        assumed_slippage_pct=cfg.simulated_broker.slippage_pct,
        refresh_seconds=args.refresh or None,
        kill_switch_path=cfg.live_brokers.kill_switch_path,
        rh_token_path=str(Path("secrets") / "robinhood_token.json"),
    )
    server = HTTPServer(("127.0.0.1", args.port), make_handler(paths))
    print(f"Dashboard on http://127.0.0.1:{args.port} (paper db: {paths.db_path})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
