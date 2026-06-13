"""Dashboard v2 (4b-2) ops layer — health of the scheduled jobs, daily spend,
crumbs alert feed, and the paper strategy sidecar.

Everything here is read-only over artifacts the ops jobs already produce:
- ``logs/<prefix>-YYYY-MM-DD.log``   (the four daily scheduled tasks)
- ``data/crumbs_alerts.json``        (scanner output, via CRUMBS_OUT_JSON)
- ``data/paper_strategy_state.json`` (2d sidecar: open trades / exit state)

The daily logs are mixed-encoding (PowerShell sentinel lines are UTF-8, the
``*>>`` python output is UTF-16LE), so parsing strips NUL bytes first and only
matches ASCII patterns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# The four daily scheduled jobs (shot 2/3 OPS) in schedule order.
SCHEDULED_JOBS: tuple[tuple[str, str, str], ...] = (
    ("Daily ingestion", "ingestion", "08:00"),
    ("Crumbs scan", "crumbs", "08:30"),
    ("Flow Lens", "flow-lens", "09:00"),
    ("Paper tick", "paper-tick", "17:00"),
)

_EXIT_CODE_RE = re.compile(r"exit code:\s*(-?\d+)")
_FINISHED_RE = re.compile(r"=== .*? finished (\S+) ===")
_STARTED_RE = re.compile(r"=== .*? started (\S+) ===")
_PAPER_SUMMARY_RE = re.compile(r"paper tick complete: (\{.*\})")
_FLOW_SPEND_RE = re.compile(r"Est\. API spend:\s*\$([0-9][0-9.]*)")


def read_log_text(path: Path) -> str:
    """Tolerant read of a mixed UTF-8/UTF-16LE log: drop NULs, decode UTF-8."""
    raw = path.read_bytes()
    return raw.replace(b"\x00", b"").decode("utf-8", errors="replace")


@dataclass
class JobStatus:
    name: str
    log_prefix: str
    scheduled_time: str
    log_path: str | None = None
    ran_today: bool = False
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def state(self) -> str:
        """'ok' | 'failed' | 'running' | 'pending' for the green/red strip."""
        if not self.ran_today:
            return "pending"
        if self.exit_code is None:
            return "running"  # started today, no exit code line yet
        return "ok" if self.exit_code == 0 else "failed"


def job_statuses(logs_dir: str | Path, today: date | None = None) -> list[JobStatus]:
    """Did each scheduled job run today, and how did it exit?"""
    logs = Path(logs_dir)
    day = (today or date.today()).isoformat()
    out: list[JobStatus] = []
    for name, prefix, at in SCHEDULED_JOBS:
        status = JobStatus(name=name, log_prefix=prefix, scheduled_time=at)
        path = logs / f"{prefix}-{day}.log"
        if path.is_file():
            status.ran_today = True
            status.log_path = str(path)
            text = read_log_text(path)
            codes = _EXIT_CODE_RE.findall(text)
            status.exit_code = int(codes[-1]) if codes else None
            starts = _STARTED_RE.findall(text)
            finishes = _FINISHED_RE.findall(text)
            status.started_at = starts[-1] if starts else None
            status.finished_at = finishes[-1] if finishes else None
        out.append(status)
    return out


@dataclass
class DailySpend:
    paper_llm_usd: float | None = None
    paper_llm_cap_usd: float | None = None
    flow_lens_usd: float | None = None
    by_model: dict[str, float] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return (self.paper_llm_usd or 0.0) + (self.flow_lens_usd or 0.0)


def daily_spend(logs_dir: str | Path, today: date | None = None) -> DailySpend:
    """LLM/API spend logged by today's jobs (paper-tick summary + Flow Lens)."""
    logs = Path(logs_dir)
    day = (today or date.today()).isoformat()
    spend = DailySpend()

    paper_log = logs / f"paper-tick-{day}.log"
    if paper_log.is_file():
        matches = _PAPER_SUMMARY_RE.findall(read_log_text(paper_log))
        if matches:
            try:
                summary = json.loads(matches[-1])
                llm = summary.get("llm_spend") or {}
                spend.paper_llm_usd = float(llm.get("spent_usd", 0.0))
                spend.paper_llm_cap_usd = float(llm["cap_usd"]) if "cap_usd" in llm else None
                spend.by_model = {k: float(v) for k, v in (llm.get("by_model") or {}).items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    flow_log = logs / f"flow-lens-{day}.log"
    if flow_log.is_file():
        matches = _FLOW_SPEND_RE.findall(read_log_text(flow_log))
        if matches:
            spend.flow_lens_usd = float(matches[-1])
    return spend


def load_sidecar(path: str | Path) -> dict[str, Any]:
    """The 2d paper sidecar (open trades / pending orders per lane), or {}."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def open_trades_for(sidecar: dict[str, Any], bot_id: str) -> list[dict[str, Any]]:
    """Open-trade exit-rule state for one lane out of the sidecar."""
    lane = sidecar.get(bot_id) or {}
    strategy = lane.get("strategy") or {}
    trades = strategy.get("open") or []
    return [t for t in trades if isinstance(t, dict)]


def crumbs_alerts(path: str | Path, *, limit: int = 25) -> list[dict[str, Any]]:
    """Crumbs alert feed from the scanner's JSON artifact (CRUMBS_OUT_JSON)."""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        alerts = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(alerts, list):
        return []
    out = [a for a in alerts if isinstance(a, dict)]
    out.sort(key=lambda a: str(a.get("filed_at", "")), reverse=True)
    return out[:limit]


def crumbs_feed_status(
    path: str | Path,
    *,
    stale_hours: float = 36.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Distinguish Crumbs scan states for dashboard wording.

    - ``absent`` — no JSON artifact yet (scan has not produced output)
    - ``stale`` — file exists but mtime is older than ``stale_hours``
    - ``ran_empty`` — recent file with an empty alert list
    - ``ran_with_alerts`` — recent file with one or more alerts
    """
    p = Path(path)
    if not p.is_file():
        return {
            "state": "absent",
            "path": str(p),
            "last_scan_at": None,
            "alert_count": 0,
            "alerts": [],
        }

    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    age_hours = (clock - mtime).total_seconds() / 3600.0
    alerts = crumbs_alerts(p, limit=10_000)

    if age_hours > stale_hours:
        return {
            "state": "stale",
            "path": str(p),
            "last_scan_at": mtime.isoformat(),
            "alert_count": len(alerts),
            "alerts": alerts,
            "age_hours": round(age_hours, 2),
        }

    if not alerts:
        return {
            "state": "ran_empty",
            "path": str(p),
            "last_scan_at": mtime.isoformat(),
            "alert_count": 0,
            "alerts": [],
        }

    return {
        "state": "ran_with_alerts",
        "path": str(p),
        "last_scan_at": mtime.isoformat(),
        "alert_count": len(alerts),
        "alerts": alerts,
    }
