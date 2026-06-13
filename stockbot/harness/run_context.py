"""Run metadata, config hashing, and structured file logging (0.1e)."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from stockbot.config import AppConfig
from stockbot.core.state.store import StateStore


def hash_config(config: AppConfig) -> str:
    """Deterministic hash of the active config for run traceability."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_git_sha(project_root: Path | None = None) -> str | None:
    """Return the current git SHA, or None if unavailable."""
    root = project_root or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def model_versions_from_config(config: AppConfig) -> dict[str, str]:
    return {
        "cheap_tier": config.llm.cheap_tier,
        "strong_tier": config.llm.strong_tier,
        "cross_check": config.llm.cross_check,
    }


@dataclass
class RunContext:
    """Tracks a single harness run: DB row + structured logfile."""

    run_id: str
    bot_id: str
    mode: str
    config_hash: str
    git_sha: str | None
    model_versions: dict[str, str]
    log_path: Path
    store: StateStore
    logger: logging.Logger

    @classmethod
    def start(
        cls,
        config: AppConfig,
        store: StateStore,
        bot_id: str,
        logs_dir: str | Path | None = None,
        project_root: Path | None = None,
    ) -> RunContext:
        run_id = str(uuid.uuid4())
        db_parent = Path(config.state.db_path).parent
        log_dir = Path(logs_dir) if logs_dir is not None else db_parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{run_id}.log"

        config_hash = hash_config(config)
        git_sha = get_git_sha(project_root)
        model_versions = model_versions_from_config(config)

        store.start_run(
            run_id=run_id,
            bot_id=bot_id,
            mode=config.mode.value,
            config_hash=config_hash,
            model_versions=model_versions,
            git_sha=git_sha,
            log_path=str(log_path),
        )

        logger = logging.getLogger(f"stockbot.run.{run_id}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
        )
        logger.addHandler(handler)
        logger.propagate = False

        ctx = cls(
            run_id=run_id,
            bot_id=bot_id,
            mode=config.mode.value,
            config_hash=config_hash,
            git_sha=git_sha,
            model_versions=model_versions,
            log_path=log_path,
            store=store,
            logger=logger,
        )
        ctx.info(
            "run started",
            extra_fields={
                "run_id": run_id,
                "bot_id": bot_id,
                "mode": config.mode.value,
                "config_hash": config_hash,
                "git_sha": git_sha,
                "model_versions": model_versions,
            },
        )
        return ctx

    def info(self, message: str, extra_fields: dict | None = None) -> None:
        if extra_fields:
            self.logger.info("%s %s", message, json.dumps(extra_fields, sort_keys=True))
        else:
            self.logger.info(message)

    def finish(self, status: str = "completed") -> None:
        self.info("run finished", extra_fields={"status": status})
        self.store.finish_run(self.run_id, status=status)
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
