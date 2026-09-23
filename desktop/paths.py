"""Stable, upgrade-safe locations for paper-trading data.

The packaged desktop app must not keep the SQLite ledger inside the
executable, PyInstaller extract folder, or other temp directories.
Existing project `paper_trading_data` folders are copied with a backup
and are never deleted or overwritten.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


APP_NAME = "StockComp"
DB_FILENAME = "paper_portfolio.sqlite3"
PAPER_DIR_NAME = "paper_trading_data"
ENV_PAPER_DIR = "STOCK_COMP_PAPER_DATA_DIR"
ENV_PAPER_DB = "STOCK_COMP_PAPER_DB"
ENV_CACHE_DIR = "STOCK_COMP_CACHE_DIR"
ENV_USER_ROOT = "STOCK_COMP_USER_DATA_ROOT"


def is_packaged():
    return bool(getattr(sys, "frozen", False))


def bundle_root():
    """Read-only app files: PyInstaller extract dir, or the repo root."""
    if is_packaged():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parents[1]


def executable_dir():
    if is_packaged():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def user_data_root():
    override = os.environ.get(ENV_USER_ROOT)
    if override:
        return Path(override).resolve()
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def default_paper_data_dir():
    return user_data_root() / PAPER_DIR_NAME


def default_cache_dir():
    return user_data_root() / "data_cache"


def default_logs_dir():
    return user_data_root() / "logs"


def default_backups_dir():
    return user_data_root() / "backups"


def _has_ledger(directory):
    return directory is not None and (Path(directory) / DB_FILENAME).is_file()


def discover_legacy_paper_data_dirs(extra_roots=None, include_default_roots=True):
    """Find existing ledgers that are not the packaged user-data destination."""
    dest = default_paper_data_dir().resolve()
    roots = []
    if include_default_roots:
        roots.append(executable_dir())
        roots.extend(executable_dir().parents)
        roots.append(Path.cwd())
        roots.append(Path.home() / "stock-comp")
        if not is_packaged():
            roots.append(Path(__file__).resolve().parents[1])
    if extra_roots:
        roots.extend(Path(item) for item in extra_roots)

    seen = set()
    found = []
    for root in roots:
        try:
            candidate = (Path(root) / PAPER_DIR_NAME).resolve()
        except OSError:
            continue
        if candidate in seen or candidate == dest:
            continue
        seen.add(candidate)
        if _has_ledger(candidate):
            found.append(candidate)
    return found


def _copy_ledger_dir(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copytree(source, destination)


def prepare_paper_data_dir(extra_legacy_roots=None, include_default_roots=True):
    """Return the writable paper-data directory, migrating a legacy copy if needed.

    Never deletes the source folder. If the destination already has a ledger,
    that ledger is kept and the legacy folder is left untouched.
    """
    destination = default_paper_data_dir()
    destination.mkdir(parents=True, exist_ok=True)
    report = {
        "destination": str(destination),
        "migrated_from": None,
        "backup": None,
        "skipped_overwrite": False,
        "legacy_candidates": [
            str(path)
            for path in discover_legacy_paper_data_dirs(
                extra_legacy_roots, include_default_roots=include_default_roots
            )
        ],
    }
    if _has_ledger(destination):
        report["skipped_overwrite"] = bool(report["legacy_candidates"])
        return destination, report

    legacy = next((Path(path) for path in report["legacy_candidates"] if _has_ledger(path)), None)
    if legacy is None:
        return destination, report

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = default_backups_dir() / f"{PAPER_DIR_NAME}_{stamp}"
    _copy_ledger_dir(legacy, backup)
    _copy_ledger_dir(legacy, destination)
    report["migrated_from"] = str(legacy)
    report["backup"] = str(backup)
    log_path = user_data_root() / "migration_log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        (
            f"Migrated paper-trading data at {stamp}\n"
            f"Source (left in place): {legacy}\n"
            f"Destination: {destination}\n"
            f"Backup: {backup}\n"
            "The original folder was not deleted or overwritten.\n"
        ),
        encoding="utf-8",
    )
    return destination, report


def apply_runtime_data_env(paper_dir=None, cache_dir=None):
    """Point the existing storage/cache helpers at the stable user folders."""
    paper_dir = Path(paper_dir or default_paper_data_dir())
    cache_dir = Path(cache_dir or default_cache_dir())
    paper_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_PAPER_DIR] = str(paper_dir)
    os.environ[ENV_PAPER_DB] = str(paper_dir / DB_FILENAME)
    os.environ[ENV_CACHE_DIR] = str(cache_dir)
    return paper_dir, cache_dir
