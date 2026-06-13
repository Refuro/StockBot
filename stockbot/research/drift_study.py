"""Ticket 2b — mechanical drift backtest on REAL data (the kill checkpoint).

Pre-registered PRIMARY test (fixed before results were seen — do not tune):
    long top-decile positive earnings surprises (expanding-window decile),
    10-calendar-day hold, next_bar execution, small_cap cost profile.

Secondary cuts for the report: surprise thresholds (>5%, >10%), hold horizons
(5/10/20d), and a by-year breakdown of the primary cell.

Output is a REPORT, not a verdict: reviewers decide whether the gate passes.

Reproduction:
    python -m stockbot.data.ingest_universe --start 2022-06-01 --end 2026-06-09
    python -m stockbot.research.drift_study --data-dir data/real --out reports/2b-drift-report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockbot.bots.proportionality.ride import BOT_ID, ProportionalityRideStrategy, RideConfig
from stockbot.config import AppConfig, SimulatedBrokerConfig, SymbolCostConfig
from stockbot.core.strategy import Strategy
from stockbot.harness.loop import drive_strategy
from stockbot.harness.metrics import compute_backtest_metrics
from stockbot.core.state.store import StateStore
from stockbot.types import ExecutionMode

MIN_DOLLAR_ADV = 500_000.0
# Risk knob for the study run only: ~100 names report earnings in clusters, so
# the default 20 orders/day cap would silently censor entries. RiskEngine code
# is untouched; this is configuration, applied identically to every cell.
STUDY_MAX_ORDERS_PER_DAY = 200


@dataclass(frozen=True)
class CellSpec:
    name: str
    label: str
    ride: RideConfig


@dataclass
class TradeRecord:
    symbol: str
    thesis_id: str
    entry_year: int | None
    net_return: float


@dataclass
class CellResult:
    spec: CellSpec
    entries: int
    metrics: dict[str, Any]
    trades: list[TradeRecord]

    @property
    def closed(self) -> int:
        return len(self.trades)

    @property
    def hit_rate(self) -> float | None:
        if not self.trades:
            return None
        return sum(1 for t in self.trades if t.net_return > 0) / len(self.trades)

    @property
    def mean_return(self) -> float | None:
        if not self.trades:
            return None
        return sum(t.net_return for t in self.trades) / len(self.trades)

    @property
    def median_return(self) -> float | None:
        if not self.trades:
            return None
        return statistics.median(t.net_return for t in self.trades)


def symbol_dollar_adv(prices_csv: str | Path) -> dict[str, float]:
    """Median daily dollar volume per symbol, computed from the dataset itself."""
    per_symbol: dict[str, list[float]] = {}
    with Path(prices_csv).open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                price = float(row["price"])
                volume = float(row["volume"]) if row.get("volume") else 0.0
            except (TypeError, ValueError):
                continue
            per_symbol.setdefault(row["symbol"], []).append(price * volume)
    return {sym: statistics.median(vals) for sym, vals in per_symbol.items() if vals}


def symbol_median_volume(prices_csv: str | Path) -> dict[str, float]:
    """Median daily share volume per symbol (feeds the broker's ADV impact model)."""
    volumes: dict[str, list[float]] = {}
    with Path(prices_csv).open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                volume = float(row["volume"]) if row.get("volume") else 0.0
            except (TypeError, ValueError):
                continue
            volumes.setdefault(row["symbol"], []).append(volume)
    return {sym: statistics.median(vals) for sym, vals in volumes.items() if vals}


def build_cell_config(
    *,
    data_dir: Path,
    db_path: Path,
    symbol_volume: dict[str, float],
) -> AppConfig:
    """Backtest config for one study cell: real CSVs, small_cap costs, next_bar."""
    raw: dict[str, Any] = {
        "mode": ExecutionMode.BACKTEST,
        "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
        "broker": {"backtest": "simulated", "paper": "simulated", "live": "simulated"},
        "historical_data": {
            "prices_path": str(data_dir / "prices.csv"),
            "events_path": str(data_dir / "events.csv"),
        },
        "state": {"db_path": str(db_path)},
        "risk": {"max_orders_per_day": STUDY_MAX_ORDERS_PER_DAY},
    }
    cfg = AppConfig.model_validate(raw)
    # small_cap profile defaults (spread 35bps, impact 28bps) with per-symbol
    # ADV taken from the actual data instead of the profile's generic 350k.
    cfg.simulated_broker = SimulatedBrokerConfig(
        initial_cash=100_000.0,
        execution_timing="next_bar",
        cost_profile="small_cap",
        symbol_costs={
            sym: SymbolCostConfig(adv_shares=max(vol, 1.0)) for sym, vol in symbol_volume.items()
        },
    )
    return cfg


def cell_specs(allowlist: frozenset[str]) -> list[CellSpec]:
    base = {"symbol_allowlist": allowlist}
    specs = [
        CellSpec(
            name="primary_top_decile_h10",
            label="PRIMARY: top-decile surprise, 10d hold",
            ride=RideConfig(entry_mode="top_decile", hold_days=10, **base),
        )
    ]
    for hold in (5, 20):
        specs.append(
            CellSpec(
                name=f"top_decile_h{hold}",
                label=f"top-decile surprise, {hold}d hold",
                ride=RideConfig(entry_mode="top_decile", hold_days=hold, **base),
            )
        )
    for threshold in (0.05, 0.10):
        for hold in (5, 10, 20):
            specs.append(
                CellSpec(
                    name=f"thr{int(threshold * 100)}_h{hold}",
                    label=f"surprise > {threshold:.0%}, {hold}d hold",
                    ride=RideConfig(
                        entry_mode="threshold",
                        surprise_threshold=threshold,
                        hold_days=hold,
                        **base,
                    ),
                )
            )
    return specs


def run_cell(spec: CellSpec, data_dir: Path, study_dir: Path, symbol_volume: dict[str, float]) -> CellResult:
    db_path = study_dir / f"{spec.name}.db"
    if db_path.exists():
        db_path.unlink()
    cfg = build_cell_config(data_dir=data_dir, db_path=db_path, symbol_volume=symbol_volume)

    def factory(_comp: Any) -> Strategy:
        return ProportionalityRideStrategy(spec.ride)

    drive_strategy(factory, cfg)

    store = StateStore(db_path)
    metrics = compute_backtest_metrics(store, BOT_ID, "backtest").to_dict()

    entries = [
        d
        for d in store.get_decisions(BOT_ID, "backtest", "entry")
        if d["action"] == "buy"
    ]
    entry_year_by_thesis: dict[str, int] = {}
    for d in entries:
        try:
            entry_year_by_thesis[d["thesis_id"]] = datetime.fromisoformat(d["as_of"]).year
        except (TypeError, ValueError):
            continue

    trades: list[TradeRecord] = []
    for d in store.get_decisions(BOT_ID, "backtest", "resolution"):
        trades.append(
            TradeRecord(
                symbol=d["inputs"].get("symbol", ""),
                thesis_id=d["thesis_id"] or "",
                entry_year=entry_year_by_thesis.get(d["thesis_id"]),
                net_return=float(d["inputs"]["net_return"]),
            )
        )
    return CellResult(spec=spec, entries=len(entries), metrics=metrics, trades=trades)


def _fmt(value: Any, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    if pct:
        return f"{value * 100:.{digits}f}%"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_report(
    *,
    results: list[CellResult],
    data_dir: Path,
    eligible: list[str],
    excluded: dict[str, float],
    metadata: dict[str, Any],
    coverage: dict[str, Any] | None,
    expanded_screen: bool = False,
) -> str:
    primary = results[0]
    lines: list[str] = []
    if expanded_screen:
        lines.append(
            "# Ticket U1 — Mechanical Drift Report, EXPANDED universe "
            "(declared secondary; the 2b gate stays decided on the original run)"
        )
    else:
        lines.append("# Ticket 2b — Mechanical Drift Report (Proportionality Engine, ride side)")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append(
        "**This is a report, not a verdict.** The pre-registered primary test was fixed "
        "before results were computed; reviewers decide whether the 2b gate passes."
    )
    lines.append("")
    if expanded_screen:
        lines.append(
            "**Declared secondary (U1):** same pre-registered cells as the original 2b "
            "study, re-run unchanged on the mechanically screened expanded universe. "
            "The original `reports/2b-drift-report.md` is untouched and remains the "
            "gate-deciding run."
        )
        lines.append("")

    lines.append("## Pre-registered primary test")
    lines.append("")
    lines.append(
        "Long top-decile positive earnings surprises (90th-percentile cutoff over the "
        "expanding window of all *prior* surprises in the eligible universe; no entries "
        "until 40 surprises of history), 10-calendar-day time-stop, `next_bar` execution, "
        "`small_cap` cost profile with per-symbol ADV from the data. Sizing: $4,000 target "
        "notional per position, 5% position cap, mechanical constant p=0.55 per thesis."
    )
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| entries submitted | {primary.entries} |")
    lines.append(f"| closed round trips | {primary.closed} |")
    lines.append(f"| hit rate | {_fmt(primary.hit_rate, pct=True)} |")
    lines.append(f"| mean net return / trade | {_fmt(primary.mean_return, pct=True)} |")
    lines.append(f"| median net return / trade | {_fmt(primary.median_return, pct=True)} |")
    lines.append(f"| portfolio total return | {_fmt(primary.metrics.get('total_return'), pct=True)} |")
    lines.append(f"| Sharpe (annualized) | {_fmt(primary.metrics.get('sharpe_ratio'))} |")
    lines.append(f"| max drawdown | {_fmt(primary.metrics.get('max_drawdown'), pct=True)} |")
    lines.append(f"| total slippage cost | ${_fmt(primary.metrics.get('total_slippage_cost'))} |")
    lines.append(f"| turnover | {_fmt(primary.metrics.get('turnover'))} |")
    lines.append("")

    lines.append("### Primary cell, by entry year")
    lines.append("")
    lines.append("| year | closed trades | hit rate | mean net return |")
    lines.append("|---|---|---|---|")
    by_year: dict[int, list[TradeRecord]] = {}
    for t in primary.trades:
        if t.entry_year is not None:
            by_year.setdefault(t.entry_year, []).append(t)
    for year in sorted(by_year):
        trades = by_year[year]
        hit = sum(1 for t in trades if t.net_return > 0) / len(trades)
        mean = sum(t.net_return for t in trades) / len(trades)
        lines.append(f"| {year} | {len(trades)} | {hit * 100:.1f}% | {mean * 100:.2f}% |")
    if not by_year:
        lines.append("| — | 0 | — | — |")
    lines.append("")

    lines.append("## Secondary cuts (NOT pre-registered; context only)")
    lines.append("")
    lines.append("| cell | entries | closed | hit rate | mean net ret | median net ret | total ret | Sharpe |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.spec.label} | {r.entries} | {r.closed} | {_fmt(r.hit_rate, pct=True)} "
            f"| {_fmt(r.mean_return, pct=True)} | {_fmt(r.median_return, pct=True)} "
            f"| {_fmt(r.metrics.get('total_return'), pct=True)} | {_fmt(r.metrics.get('sharpe_ratio'))} |"
        )
    lines.append("")

    lines.append("## Universe")
    lines.append("")
    if expanded_screen:
        lines.append(
            "Candidates: MECHANICALLY SCREENED (ticket U1, no hand-curation) — every active "
            "US-listed common stock on NYSE/NASDAQ with market cap $300M–$3B (Alpha Vantage "
            "OVERVIEW, measured at screen time) and median daily dollar volume ≥ $500k "
            "(Tiingo prices over the study window): `stockbot/data/universe_expanded.csv`; "
            "full screen parameters and stage-by-stage counts in "
            "`stockbot/data/universe_expanded.metadata.json`. Final traded universe = "
            "candidates with a fully-cached data path (Tiingo prices + SEC 8-K Item-2.02 "
            "filings + Alpha Vantage EPS estimates) AND median daily dollar volume ≥ "
            f"${MIN_DOLLAR_ADV:,.0f} re-checked from the dataset itself."
        )
    else:
        lines.append(
            "Candidates: hand-curated US small/mid caps targeting the $300M–$3B band "
            "(`stockbot/data/universe_shot1.csv`). Final traded universe = candidates with a "
            "fully-cached data path (Tiingo prices + SEC 8-K Item-2.02 filings + Alpha Vantage "
            f"EPS estimates) AND median daily dollar volume ≥ ${MIN_DOLLAR_ADV:,.0f}."
        )
    lines.append("")
    if coverage:
        lines.append(
            f"- Alpha Vantage cache coverage at run time: {len(coverage.get('ready', []))} ready "
            f"/ {coverage.get('universe_size', '?')} candidates "
            f"({len(coverage.get('pending', []))} pending — cache fills ~25/day; re-run ingestion "
            "then this study to expand)."
        )
    lines.append(f"- Symbols in dataset passing the ADV floor ({len(eligible)}): {', '.join(sorted(eligible))}")
    if excluded:
        excl = ", ".join(f"{s} (${v:,.0f})" for s, v in sorted(excluded.items()))
        lines.append(f"- Excluded by ADV floor: {excl}")
    else:
        lines.append("- Excluded by ADV floor: none")
    lines.append("")
    lines.append("### Survivorship and screening caveats (read before judging the numbers)")
    lines.append("")
    if expanded_screen:
        lines.append(
            "1. **Survivorship bias is real here.** The screen ran in June 2026 over names "
            "active at screen time; names delisted/acquired before then never entered the "
            "universe. Dataset metadata flags this (`survivorship_biased: "
            f"{metadata.get('survivorship_biased', 'unknown')}`). Drift measured on survivors "
            "is likely flattered."
        )
        lines.append(
            "2. **The cap band is measured at screen time, not point-in-time.** Names that "
            "were in-band historically but migrated out before the screen date are missed, "
            "and names that grew into the band late contribute their full history. The ADV "
            "floor IS mechanically enforced from the data."
        )
    else:
        lines.append(
            "1. **Survivorship bias is real here.** The candidate list was curated in June 2026 "
            "from names trading at the time; names delisted/acquired before curation never made "
            "the list. Dataset metadata flags this (`survivorship_biased: "
            f"{metadata.get('survivorship_biased', 'unknown')}`). Drift measured on survivors "
            "is likely flattered."
        )
        lines.append(
            "2. **The cap band is approximate.** No point-in-time market-cap screen is wired; "
            "band membership was judged at curation. The ADV floor IS mechanically enforced "
            "from the data."
        )
    lines.append(
        "3. **Consensus source.** Alpha Vantage `estimatedEPS` is the estimate as reported "
        "for the quarter; its point-in-time integrity is asserted by the provider, not "
        "independently verified against an estimates-revision history."
    )
    lines.append("")

    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- Data dir: `{data_dir}`")
    lines.append(f"- Window: {metadata.get('start')} → {metadata.get('end')}")
    lines.append(
        f"- Rows: {metadata.get('rows', {}).get('prices', '?')} prices, "
        f"{metadata.get('rows', {}).get('events', '?')} earnings events"
    )
    lines.append(f"- Providers: {metadata.get('price_provider')} + {metadata.get('event_provider')}")
    lines.append("")

    lines.append("## Reproduction")
    lines.append("")
    lines.append("```")
    if expanded_screen:
        lines.append("python -m stockbot.data.screen_universe --start 2022-06-01 --end 2026-06-09")
        lines.append(
            "python -m stockbot.data.ingest_universe --universe stockbot/data/universe_expanded.csv"
            " --start 2022-06-01 --end 2026-06-09 --out-dir data/real_expanded --av-budget 2000"
        )
        lines.append(
            "python -m stockbot.research.drift_study --data-dir data/real_expanded"
            " --study-dir data/study_expanded --out reports/2b-drift-report-expanded.md"
            " --expanded-screen"
        )
    else:
        lines.append("python -m stockbot.data.ingest_universe --start 2022-06-01 --end 2026-06-09")
        lines.append("python -m stockbot.research.drift_study --data-dir data/real --out reports/2b-drift-report.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "Each cell runs in its own SQLite DB under `data/study/`; metrics come from the "
        "persisted DB via `compute_backtest_metrics`, never from in-memory broker state."
    )
    lines.append("")
    return "\n".join(lines)


def run_drift_study(
    *,
    data_dir: str | Path = "data/real",
    study_dir: str | Path = "data/study",
    out_path: str | Path = "reports/2b-drift-report.md",
    min_dollar_adv: float = MIN_DOLLAR_ADV,
    expanded_screen: bool = False,
) -> Path:
    data_dir = Path(data_dir)
    study_dir = Path(study_dir)
    out_path = Path(out_path)
    study_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    prices_csv = data_dir / "prices.csv"
    if not prices_csv.exists():
        raise FileNotFoundError(
            f"{prices_csv} not found — run `python -m stockbot.data.ingest_universe` first"
        )
    metadata: dict[str, Any] = {}
    metadata_path = data_dir / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    coverage: dict[str, Any] | None = None
    coverage_path = data_dir / "coverage.json"
    if coverage_path.exists():
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))

    dollar_adv = symbol_dollar_adv(prices_csv)
    eligible = sorted(s for s, adv in dollar_adv.items() if adv >= min_dollar_adv)
    excluded = {s: adv for s, adv in dollar_adv.items() if adv < min_dollar_adv}
    if not eligible:
        raise RuntimeError("No symbols pass the ADV floor — dataset too thin to study")
    symbol_volume = symbol_median_volume(prices_csv)

    results: list[CellResult] = []
    for spec in cell_specs(frozenset(eligible)):
        print(f"Running cell: {spec.name} ...")
        result = run_cell(spec, data_dir, study_dir, symbol_volume)
        print(
            f"  entries={result.entries} closed={result.closed} "
            f"hit={_fmt(result.hit_rate, pct=True)} mean={_fmt(result.mean_return, pct=True)}"
        )
        results.append(result)

    report = render_report(
        results=results,
        data_dir=data_dir,
        eligible=eligible,
        excluded=excluded,
        metadata=metadata,
        coverage=coverage,
        expanded_screen=expanded_screen,
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written: {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ticket 2b mechanical drift study")
    parser.add_argument("--data-dir", default="data/real")
    parser.add_argument("--study-dir", default="data/study")
    parser.add_argument("--out", default="reports/2b-drift-report.md")
    parser.add_argument("--min-dollar-adv", type=float, default=MIN_DOLLAR_ADV)
    parser.add_argument(
        "--expanded-screen",
        action="store_true",
        help="Render the U1 expanded-universe (declared secondary) report framing",
    )
    args = parser.parse_args(argv)
    run_drift_study(
        data_dir=args.data_dir,
        study_dir=args.study_dir,
        out_path=args.out,
        min_dollar_adv=args.min_dollar_adv,
        expanded_screen=args.expanded_screen,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
