"""CLI / scheduler entry for Flow Lens daily logging."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from stockbot.config import load_config
from stockbot.tools.flow_lens.f2_study import run_flow_validation_study
from stockbot.tools.flow_lens.logger import run_daily_flow_lens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flow Lens — synthetic retail-crowd logger")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run one daily sampling pass now")
    run_p.add_argument("--config", default="config.yaml")
    run_p.add_argument("--date", default=None, help="Logged date YYYY-MM-DD (default: today UTC)")
    run_p.add_argument("--offline", action="store_true", help="Use stub providers (no API spend)")

    sched_p = sub.add_parser("schedule", help="Run daily via APScheduler (blocking)")
    sched_p.add_argument("--config", default="config.yaml")
    sched_p.add_argument("--hour", type=int, default=14, help="UTC hour to run (default 14:00)")
    sched_p.add_argument("--minute", type=int, default=30)

    study_p = sub.add_parser("study", help="Run F2 flow validation study")
    study_p.add_argument("--config", default="config.yaml")
    study_p.add_argument("--finra-dir", required=True, help="Directory of FINRA CSV files")
    study_p.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    study_p.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    study_p.add_argument("--min-consensus-score", type=float, default=0.5)
    study_p.add_argument("--lookback-days", type=int, default=20)
    study_p.add_argument("--max-horizon-days", type=int, default=5)
    study_p.add_argument("--report-out", default=None, help="Optional markdown report output path")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.command == "run":
        logged = date.fromisoformat(args.date) if args.date else None
        result = run_daily_flow_lens(cfg, logged_date=logged, offline=args.offline)
        print(f"Logged date:     {result.logged_date}")
        print(f"Samples written: {result.samples_written}")
        print(f"Consensus rows:  {result.consensus_rows}")
        print(f"Providers ran:   {', '.join(result.models_run) or 'none'}")
        if result.models_skipped:
            print(f"Skipped (no key/client): {', '.join(result.models_skipped)}")
        if result.models_failed:
            print(f"FAILED mid-run:  {', '.join(result.models_failed)}")
        print(f"Est. API spend:  ${result.estimated_cost_usd:.4f}")
        if result.novel_tickers:
            print(f"Novel tickers:   {', '.join(result.novel_tickers)}")
        return 0

    if args.command == "schedule":
        scheduler = BlockingScheduler(timezone="UTC")

        def _job() -> None:
            run_daily_flow_lens(cfg, offline=False)

        scheduler.add_job(_job, "cron", hour=args.hour, minute=args.minute, id="flow_lens_daily")
        print(f"Flow Lens scheduled daily at {args.hour:02d}:{args.minute:02d} UTC (Ctrl+C to stop)")
        scheduler.start()
        return 0

    if args.command == "study":
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        result = run_flow_validation_study(
            finra_csv_dir=args.finra_dir,
            config=cfg,
            start_date=start,
            end_date=end,
            min_consensus_score=args.min_consensus_score,
            lookback_days=args.lookback_days,
            max_horizon_days=args.max_horizon_days,
        )
        print(result.report_text)
        if args.report_out:
            out = Path(args.report_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(result.report_text, encoding="utf-8")
            print(f"\nWrote report: {out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
