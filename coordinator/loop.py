"""Sequential Cursor implement → Codex review loop."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from coordinator import adapters
from coordinator.paths import worktree_dir
from coordinator.review import findings_prompt
from coordinator.status_store import (
    load_status,
    save_status,
    stop_requested,
    utc_now,
    write_report,
    write_text,
)
from coordinator.worktree import create_worktree, explain_worktree_contents, inspect_source


PACKAGE_DIR = Path(__file__).resolve().parent
MAX_ROUNDS = 3


def _event(status, kind, detail):
    status.setdefault("events", []).append(
        {"at": utc_now(), "kind": kind, "detail": detail}
    )


def _record_round(status, name, text):
    root = Path(status["task_dir"])
    path = root / "rounds" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text or "")
    return str(path)


def _save_shared_instructions(status):
    """Save coordinator handoffs without modifying the project worktree."""
    task_root = Path(status["task_dir"])
    overlay = (PACKAGE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    write_text(task_root / "shared_instructions.md", overlay)
    write_text(
        task_root / "task.md",
        (
            f"# Task\n\n{status['task']}\n\n# Acceptance criteria\n"
            + "\n".join(f"- {item}" for item in status["acceptance_criteria"])
            + "\n\nDo not commit, push, merge, publish, or place trades.\n"
        ),
    )


def start_task(
    source_repo,
    task,
    acceptance_criteria,
    data_root,
    implementer="cursor",
    reviewer="codex",
    test_command=None,
    timeout_seconds=600,
    max_rounds=MAX_ROUNDS,
    require_approval=False,
):
    source_repo = Path(source_repo).resolve()
    inspection = inspect_source(source_repo)
    task_id = utc_now().replace(":", "").replace("-", "")[:15] + "-" + uuid.uuid4().hex[:8]
    destination = worktree_dir(data_root, task_id)
    branch = f"coord/{task_id}"
    create_worktree(source_repo, destination, branch)
    note = explain_worktree_contents(inspection)
    status = {
        "task_id": task_id,
        "state": "ready",
        "task": task,
        "acceptance_criteria": list(acceptance_criteria),
        "source_repo": str(source_repo),
        "worktree": str(destination),
        "branch": branch,
        "head": inspection["head"],
        "dirty_source_paths": inspection["dirty"],
        "worktree_note": note,
        "implementer": implementer,
        "reviewer": reviewer,
        "test_command": test_command,
        "timeout_seconds": timeout_seconds,
        "max_rounds": max_rounds,
        "round": 0,
        "require_approval": require_approval,
        "events": [],
        "last_review": None,
        "setup_needed": None,
    }
    saved = save_status(data_root, status)
    status["task_dir"] = str(saved)
    _save_shared_instructions(status)
    _event(status, "created", note)
    save_status(data_root, status)
    return run_loop(data_root, task_id)


def run_loop(data_root, task_id):
    status = load_status(data_root, task_id)
    status["task_dir"] = str(Path(data_root) / "tasks" / task_id)
    implementer = adapters.resolve_implementer(status["implementer"])
    reviewer = adapters.resolve_reviewer(status["reviewer"])
    try:
        while True:
            if stop_requested(data_root, task_id):
                status["state"] = "stopped"
                _event(status, "stopped", "STOP file present.")
                break
            if status["state"] in {
                "approved",
                "blocked",
                "needs_approval",
                "auth_failed",
                "stopped",
                "max_rounds",
                "incomplete_review",
            }:
                break
            if status["round"] >= status["max_rounds"] and status["state"] != "ready":
                status["state"] = "max_rounds"
                _event(status, "max_rounds", "Reached the 3-round limit.")
                break
            if status["state"] in {"ready", "changes_requested"}:
                _implement(status, implementer)
            elif status["state"] == "implemented":
                _test(status)
            elif status["state"] == "tested":
                _review(status, reviewer)
            else:
                break
            save_status(data_root, status)
    except adapters.AgentError as error:
        if error.code == "missing_binary":
            status["state"] = "blocked"
            status["setup_needed"] = error.message
        else:
            status["state"] = "blocked"
        _event(status, "blocker", error.message)
        save_status(data_root, status)
    write_report(data_root, status)
    return status


def continue_after_approval(data_root, task_id):
    status = load_status(data_root, task_id)
    if status.get("state") == "needs_approval":
        status["require_approval"] = False
        status["state"] = "changes_requested" if status.get("last_review") else "ready"
        _event(status, "approval_granted", "Resume treated as explicit approval to continue.")
        save_status(data_root, status)
    return run_loop(data_root, task_id)


def _implement(status, implementer):
    shared = (PACKAGE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    prompt = (
        f"Implement this task in the current workspace.\n\n{status['task']}\n\n"
        "Acceptance criteria:\n"
        + "\n".join(f"- {item}" for item in status["acceptance_criteria"])
        + "\n\nRead and preserve any existing project AGENTS.md. "
        "Do not commit, push, or place trades. "
        "Do not touch portfolio databases, secrets, or packaged executables."
        f"\n\nCoordinator instructions:\n{shared}"
    )
    if status.get("last_review"):
        prompt += "\n\nAddress these review findings:\n" + findings_prompt(status["last_review"])
    if status.get("require_approval"):
        status["state"] = "needs_approval"
        _event(status, "needs_approval", "Paused before implementation because approval is required.")
        pending_round = int(status.get("round") or 0) + 1
        _record_round(status, f"{pending_round:02d}-pending-implement.txt", prompt)
        return
    status["round"] = int(status.get("round") or 0) + 1
    result = implementer.run(prompt, status["worktree"], status["timeout_seconds"])
    _record_round(status, f"{status['round']:02d}-cursor-implement.txt", result.get("text", ""))
    if result.get("auth_or_limit"):
        status["state"] = "auth_failed"
        _event(status, "auth_failed", "Implementer reported authentication or usage-limit failure.")
        return
    if result.get("needs_approval") or result.get("timed_out") and "approval" in (result.get("text") or "").lower():
        status["state"] = "needs_approval"
        _event(status, "needs_approval", "Implementer needs approval. Progress saved.")
        return
    if not result.get("ok"):
        status["state"] = "blocked"
        _event(status, "blocker", result.get("text") or "Implementer failed.")
        return
    status["state"] = "implemented"
    _event(status, "implemented", f"Round {status['round']} implementation finished.")


def _test(status):
    command = status.get("test_command")
    if not command:
        status["state"] = "tested"
        _event(status, "tests", "No test command configured.")
        return
    completed = subprocess.run(
        command,
        cwd=status["worktree"],
        capture_output=True,
        text=True,
        shell=True,
        timeout=status["timeout_seconds"],
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    _record_round(status, f"{status['round']:02d}-tests.txt", output)
    status["last_test_exit"] = completed.returncode
    status["state"] = "tested"
    _event(status, "tests", f"Test command exited {completed.returncode}.")


def _review(status, reviewer):
    diff = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=status["worktree"],
        capture_output=True,
        text=True,
        check=False,
    )
    shared = (PACKAGE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    prompt = (
        "Review the uncommitted diff and any test evidence. "
        "Read and follow the existing project AGENTS.md without modifying it. "
        "Emit only the required JSON object. "
        f"Task: {status['task']}\nAcceptance criteria:\n"
        + "\n".join(f"- {item}" for item in status["acceptance_criteria"])
        + f"\nCoordinator instructions:\n{shared}\n"
        f"Diff stat:\n{diff.stdout}"
    )
    result = reviewer.run(prompt, status["worktree"], status["timeout_seconds"])
    review = result.get("review") or {"status": "incomplete", "valid": False, "findings": []}
    _record_round(
        status,
        f"{status['round']:02d}-codex-review.json",
        json.dumps({"raw": result.get("text"), "parsed": review}, indent=2),
    )
    status["last_review"] = review
    if result.get("auth_or_limit"):
        status["state"] = "auth_failed"
        _event(status, "auth_failed", "Reviewer reported authentication or usage-limit failure.")
        return
    if not review.get("valid"):
        status["state"] = "incomplete_review"
        _event(status, "incomplete_review", "Malformed or missing reviewer output is not approval.")
        return
    if review["status"] == "approved":
        status["state"] = "approved"
        _event(status, "approved", review.get("summary") or "Reviewer approved.")
        return
    if review["status"] == "blocked":
        status["state"] = "blocked"
        _event(status, "blocked", review.get("summary") or "Reviewer blocked.")
        return
    if status["round"] >= status["max_rounds"]:
        status["state"] = "max_rounds"
        _event(status, "max_rounds", "Review still requests changes after the last allowed round.")
        return
    status["state"] = "changes_requested"
    _event(status, "changes_requested", findings_prompt(review))
