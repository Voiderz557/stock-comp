"""Task status, handoff files, and interruption recovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coordinator.paths import task_dir


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ensure_task_dir(data_root, task_id):
    path = task_dir(data_root, task_id)
    (path / "rounds").mkdir(parents=True, exist_ok=True)
    return path


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_status(data_root, task_id):
    path = task_dir(data_root, task_id) / "status.json"
    if not path.is_file():
        raise FileNotFoundError(f"No saved task {task_id}.")
    return read_json(path)


def save_status(data_root, payload):
    root = ensure_task_dir(data_root, payload["task_id"])
    payload["updated_at"] = utc_now()
    write_json(root / "status.json", payload)
    return root


def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")


def request_stop(data_root, task_id):
    root = ensure_task_dir(data_root, task_id)
    write_text(root / "STOP", f"stop requested at {utc_now()}\n")
    return root / "STOP"


def stop_requested(data_root, task_id):
    return (task_dir(data_root, task_id) / "STOP").is_file()


def write_report(data_root, status):
    root = task_dir(data_root, status["task_id"])
    lines = [
        f"# Coordinator report: {status['task_id']}",
        "",
        f"- State: `{status.get('state')}`",
        f"- Rounds: {status.get('round')}/{status.get('max_rounds')}",
        f"- Source: `{status.get('source_repo')}`",
        f"- Worktree: `{status.get('worktree')}`",
        f"- HEAD: `{status.get('head')}`",
        f"- Worktree note: {status.get('worktree_note')}",
        "",
        "## Task",
        status.get("task", ""),
        "",
        "## Acceptance criteria",
    ]
    for item in status.get("acceptance_criteria") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Events"])
    for event in status.get("events") or []:
        lines.append(f"- {event.get('at')}: {event.get('kind')} - {event.get('detail')}")
    if status.get("last_review"):
        lines.extend(["", "## Last review", json.dumps(status["last_review"], indent=2)])
    if status.get("setup_needed"):
        lines.extend(["", "## Remaining setup", status["setup_needed"]])
    write_text(root / "report.md", "\n".join(lines) + "\n")
    return root / "report.md"
