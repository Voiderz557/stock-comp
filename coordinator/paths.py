"""Portable data-root and isolation paths."""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "StockCompCoordinator"
BLOCKED_WRITE_NAMES = (
    "paper_trading_data",
    "dist",
    ".env",
    "auth.json",
    "*.sqlite3",
)


def default_data_root():
    override = os.environ.get("STOCK_COMP_COORDINATOR_ROOT")
    if override:
        return Path(override).resolve()
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def task_dir(data_root, task_id):
    return Path(data_root) / "tasks" / task_id


def worktree_dir(data_root, task_id):
    return Path(data_root) / "worktrees" / task_id
