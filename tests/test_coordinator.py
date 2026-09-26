import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from coordinator.adapters import FakeReviewer
from coordinator.loop import continue_after_approval, start_task
from coordinator.review import parse_review
from coordinator.status_store import request_stop
from coordinator.worktree import explain_worktree_contents, inspect_source, remove_worktree


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "git failed")
    return result


def _init_repo(root):
    repo = Path(root)
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "coord-test@example.com")
    _git(repo, "config", "user.name", "Coordinator Test")
    (repo / "README.md").write_text("sample\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


class ReviewParseTests(unittest.TestCase):
    def test_missing_output_is_incomplete_never_approved(self):
        review = parse_review("")
        self.assertFalse(review["valid"])
        self.assertEqual(review["status"], "incomplete")

    def test_malformed_json_is_incomplete(self):
        review = parse_review("looks good to me")
        self.assertFalse(review["valid"])
        self.assertEqual(review["status"], "incomplete")

    def test_approved_with_blocker_is_changes_requested(self):
        review = parse_review(
            json.dumps(
                {
                    "status": "approved",
                    "summary": "ok",
                    "tests_reviewed": True,
                    "findings": [
                        {
                            "severity": "blocker",
                            "title": "missing test",
                            "action": "add a test",
                        }
                    ],
                }
            )
        )
        self.assertTrue(review["valid"])
        self.assertEqual(review["status"], "changes_requested")

    def test_native_codex_correct_patch_maps_to_approved(self):
        review = parse_review(
            json.dumps(
                {
                    "overall_correctness": "patch is correct",
                    "overall_explanation": "Tiny isolated change is fine.",
                    "findings": [],
                }
            )
        )
        self.assertTrue(review["valid"])
        self.assertEqual(review["status"], "approved")


class WorktreeInspectionTests(unittest.TestCase):
    def test_dirty_source_is_explained_not_copied(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp) / "src")
            (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
            inspection = inspect_source(repo)
            note = explain_worktree_contents(inspection)
            self.assertEqual(inspection["dirty_count"], 1)
            self.assertIn("dirty.txt", note)
            self.assertIn("not copied", note)
            self.assertIn(inspection["head"], note)


class FakeOrchestrationTests(unittest.TestCase):
    def test_existing_agents_file_is_preserved_and_handoffs_are_external(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            agents = repo / "AGENTS.md"
            agents.write_text("project-only\n", encoding="utf-8")
            _git(repo, "add", "AGENTS.md")
            _git(repo, "commit", "-m", "instructions")
            data_root = root / "coord-data"
            status = start_task(
                source_repo=repo,
                task="Add a short handoff note.",
                acceptance_criteria=["Create handoff_note.txt"],
                data_root=data_root,
                implementer="fake",
                reviewer="fake",
                timeout_seconds=30,
            )
            try:
                worktree = Path(status["worktree"])
                self.assertEqual((worktree / "AGENTS.md").read_text(encoding="utf-8"), "project-only\n")
                self.assertFalse((worktree / "COORDINATOR_TASK.md").exists())
                task_root = data_root / "tasks" / status["task_id"]
                self.assertTrue((task_root / "shared_instructions.md").is_file())
                self.assertTrue((task_root / "task.md").is_file())
            finally:
                remove_worktree(repo, status["worktree"])

    def test_fake_agents_approve_and_record_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            (repo / "local-only.txt").write_text("dirty\n", encoding="utf-8")
            data_root = root / "coord-data"
            status = start_task(
                source_repo=repo,
                task="Add a short handoff note.",
                acceptance_criteria=["Create handoff_note.txt"],
                data_root=data_root,
                implementer="fake",
                reviewer="fake",
                timeout_seconds=30,
            )
            try:
                self.assertEqual(status["state"], "approved")
                self.assertTrue((Path(status["worktree"]) / "handoff_note.txt").is_file())
                self.assertFalse((Path(status["worktree"]) / "local-only.txt").exists())
                self.assertIn("uncommitted", status["worktree_note"])
                report = data_root / "tasks" / status["task_id"] / "report.md"
                self.assertTrue(report.is_file())
                self.assertIn("approved", report.read_text(encoding="utf-8"))
            finally:
                remove_worktree(repo, status["worktree"])

    def test_malformed_review_stops_incomplete_not_approved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            data_root = root / "coord-data"
            fake = FakeReviewer(payload={"not": "a review"})
            with patch("coordinator.adapters.resolve_reviewer", return_value=fake):
                status = start_task(
                    source_repo=repo,
                    task="Add a short handoff note.",
                    acceptance_criteria=["Create handoff_note.txt"],
                    data_root=data_root,
                    implementer="fake",
                    reviewer="fake",
                    timeout_seconds=30,
                )
            try:
                self.assertEqual(status["state"], "incomplete_review")
                self.assertNotEqual(status["state"], "approved")
            finally:
                remove_worktree(repo, status["worktree"])

    def test_feedback_round_then_approval(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            data_root = root / "coord-data"
            fake = FakeReviewer(
                payloads=[
                    {
                        "status": "changes_requested",
                        "summary": "Need a clearer note.",
                        "tests_reviewed": False,
                        "findings": [
                            {
                                "severity": "minor",
                                "title": "note",
                                "action": "Rewrite the first line.",
                            }
                        ],
                    },
                    {
                        "status": "approved",
                        "summary": "Looks good.",
                        "tests_reviewed": True,
                        "findings": [],
                    },
                ]
            )
            with patch("coordinator.adapters.resolve_reviewer", return_value=fake):
                status = start_task(
                    source_repo=repo,
                    task="Add a short handoff note.",
                    acceptance_criteria=["Create handoff_note.txt"],
                    data_root=data_root,
                    implementer="fake",
                    reviewer="fake",
                    timeout_seconds=30,
                )
            try:
                self.assertEqual(status["state"], "approved")
                self.assertEqual(status["round"], 2)
            finally:
                remove_worktree(repo, status["worktree"])

    def test_require_approval_pauses_and_resume_continues(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            data_root = root / "coord-data"
            status = start_task(
                source_repo=repo,
                task="Add a short handoff note.",
                acceptance_criteria=["Create handoff_note.txt"],
                data_root=data_root,
                implementer="fake",
                reviewer="fake",
                require_approval=True,
                timeout_seconds=30,
            )
            try:
                self.assertEqual(status["state"], "needs_approval")
                self.assertFalse((Path(status["worktree"]) / "handoff_note.txt").exists())
                resumed = continue_after_approval(data_root, status["task_id"])
                self.assertEqual(resumed["state"], "approved")
                self.assertTrue((Path(status["worktree"]) / "handoff_note.txt").is_file())
            finally:
                remove_worktree(repo, status["worktree"])

    def test_stop_file_halts_before_implement(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root / "src")
            data_root = root / "coord-data"
            from coordinator.loop import run_loop
            from coordinator.status_store import save_status
            from coordinator.worktree import create_worktree

            task_id = "stop-test"
            destination = data_root / "worktrees" / task_id
            inspection = inspect_source(repo)
            create_worktree(repo, destination, "coord/stop-test")
            save_status(
                data_root,
                {
                    "task_id": task_id,
                    "state": "ready",
                    "task": "should not run",
                    "acceptance_criteria": ["none"],
                    "source_repo": str(repo),
                    "worktree": str(destination),
                    "branch": "coord/stop-test",
                    "head": inspection["head"],
                    "dirty_source_paths": [],
                    "worktree_note": "clean",
                    "implementer": "fake",
                    "reviewer": "fake",
                    "test_command": None,
                    "timeout_seconds": 30,
                    "max_rounds": 3,
                    "round": 0,
                    "require_approval": False,
                    "events": [],
                    "last_review": None,
                },
            )
            request_stop(data_root, task_id)
            status = run_loop(data_root, task_id)
            try:
                self.assertEqual(status["state"], "stopped")
                self.assertFalse((destination / "handoff_note.txt").exists())
            finally:
                remove_worktree(repo, destination)


if __name__ == "__main__":
    unittest.main()
