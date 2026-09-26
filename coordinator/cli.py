"""Command-line entry for the coordinator."""

from __future__ import annotations

import argparse
import json

from coordinator.diagnose import diagnose
from coordinator.loop import continue_after_approval, start_task
from pathlib import Path

from coordinator.paths import default_data_root
from coordinator.status_store import load_status, request_stop, write_report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Cursor → Codex → Cursor coordinator (no trades, no auto-git publish)."
    )
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--data-root", default=None)
    parser.add_argument("--data-root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diagnose", parents=[shared], help="Check CLI install and login status without printing secrets.")

    run = sub.add_parser("run", parents=[shared], help="Start a task in an isolated git worktree.")
    run.add_argument("--task", required=True)
    run.add_argument("--criterion", action="append", dest="criteria", required=True)
    run.add_argument("--source-repo", default=".")
    run.add_argument("--implementer", choices=("cursor", "fake"), default="cursor")
    run.add_argument("--reviewer", choices=("codex", "fake"), default="codex")
    run.add_argument("--test-command", default=None)
    run.add_argument("--timeout-seconds", type=int, default=600)
    run.add_argument("--max-rounds", type=int, default=3)
    run.add_argument("--require-approval", action="store_true")

    resume = sub.add_parser("resume", parents=[shared], help="Continue a saved task after interruption.")
    resume.add_argument("task_id")

    stop = sub.add_parser("stop", parents=[shared], help="Request a stop; the loop exits at the next checkpoint.")
    stop.add_argument("task_id")

    status = sub.add_parser("status", parents=[shared], help="Print saved task status.")
    status.add_argument("task_id")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    data_root = Path(args.data_root).resolve() if args.data_root else default_data_root()
    if args.command == "diagnose":
        report = diagnose()
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "last_diagnose.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "run":
        status = start_task(
            source_repo=args.source_repo,
            task=args.task,
            acceptance_criteria=args.criteria,
            data_root=data_root,
            implementer=args.implementer,
            reviewer=args.reviewer,
            test_command=args.test_command,
            timeout_seconds=args.timeout_seconds,
            max_rounds=args.max_rounds,
            require_approval=args.require_approval,
        )
        print(json.dumps({"task_id": status["task_id"], "state": status["state"], "report": str(data_root / "tasks" / status["task_id"] / "report.md")}, indent=2))
        return 0 if status["state"] == "approved" else 2
    if args.command == "resume":
        status = continue_after_approval(data_root, args.task_id)
        print(json.dumps({"task_id": status["task_id"], "state": status["state"]}, indent=2))
        return 0 if status["state"] == "approved" else 2
    if args.command == "stop":
        path = request_stop(data_root, args.task_id)
        print(f"Stop requested: {path}")
        return 0
    if args.command == "status":
        status = load_status(data_root, args.task_id)
        write_report(data_root, status)
        print(json.dumps(status, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
