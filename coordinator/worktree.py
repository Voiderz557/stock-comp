"""Isolated git worktrees. Never stash, reset, or omit dirty-state warnings."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(repo, args):
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def inspect_source(repo):
    repo = Path(repo).resolve()
    if not (repo / ".git").exists() and run_git(repo, ["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        raise RuntimeError(f"{repo} is not a git repository.")
    head = run_git(repo, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        raise RuntimeError(head.stderr.strip() or "Could not read HEAD.")
    status = run_git(repo, ["status", "--porcelain"])
    dirty = [line for line in (status.stdout or "").splitlines() if line.strip()]
    return {
        "repo": str(repo),
        "head": head.stdout.strip(),
        "dirty": dirty,
        "dirty_count": len(dirty),
    }


def explain_worktree_contents(inspection):
    if inspection["dirty_count"] == 0:
        return (
            f"Worktree will contain commit {inspection['head']} only. "
            "The source working tree is clean."
        )
    listed = "; ".join(inspection["dirty"][:12])
    extra = ""
    if inspection["dirty_count"] > 12:
        extra = f" and {inspection['dirty_count'] - 12} more"
    return (
        f"Source repo has {inspection['dirty_count']} uncommitted path(s): {listed}{extra}. "
        f"The isolated worktree will contain commit {inspection['head']} only. "
        "Uncommitted files are not copied, stashed, reset, or discarded."
    )


def create_worktree(repo, destination, branch_name):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = run_git(
        repo,
        ["worktree", "add", "-b", branch_name, str(destination), "HEAD"],
    )
    if created.returncode != 0:
        raise RuntimeError(created.stderr.strip() or "git worktree add failed.")
    return destination


def remove_worktree(repo, destination):
    run_git(repo, ["worktree", "remove", "--force", str(destination)])
