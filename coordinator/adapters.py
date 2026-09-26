"""Agent adapters. Only the Cursor implementer may edit project files."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from coordinator.review import parse_review


AUTH_HINTS = (
    "not authenticated",
    "please log in",
    "login required",
    "unauthorized",
    "401",
    "usage limit",
    "rate limit",
    "quota",
)


def looks_like_auth_or_limit(text):
    lowered = (text or "").lower()
    return any(hint in lowered for hint in AUTH_HINTS)


class AgentError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def which(name):
    return shutil.which(name)


class CommandResult:
    def __init__(self, returncode, stdout, stderr, timed_out=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def combined(self):
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


def run_command(command, cwd, timeout_seconds, extra_env=None):
    env = os.environ.copy()
    env.pop("CURSOR_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode("utf-8", errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        return CommandResult(-1, stdout, stderr + "\nTIMEOUT", timed_out=True)
    except FileNotFoundError as error:
        raise AgentError("missing_binary", str(error)) from error
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


class FakeImplementer:
    name = "fake-cursor"

    def run(self, prompt, cwd, timeout_seconds):
        target = Path(cwd) / "handoff_note.txt"
        target.write_text(f"implemented:{prompt[:80]}\n", encoding="utf-8")
        return {
            "ok": True,
            "text": f"Wrote {target.name}",
            "needs_approval": False,
            "auth_or_limit": False,
        }


class FakeReviewer:
    name = "fake-codex"

    def __init__(self, payload=None, payloads=None):
        default = {
            "status": "approved",
            "summary": "Fake reviewer accepted the isolated change.",
            "tests_reviewed": True,
            "findings": [],
        }
        if payloads is not None:
            self.payloads = list(payloads)
        else:
            self.payloads = [payload or default]
        self.calls = 0

    def run(self, prompt, cwd, timeout_seconds):
        import json

        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        text = json.dumps(payload)
        review = parse_review(text)
        return {
            "ok": review["valid"],
            "text": text,
            "review": review,
            "needs_approval": False,
            "auth_or_limit": False,
        }


class CursorCliImplementer:
    name = "cursor-cli"

    def run(self, prompt, cwd, timeout_seconds):
        binary = which("agent")
        if not binary:
            raise AgentError(
                "missing_binary",
                "Cursor Agent CLI (`agent`) is not installed.",
            )
        command = [
            binary,
            "-p",
            "--output-format",
            "json",
            "--trust",
            "--sandbox",
            "enabled",
            "--workspace",
            str(cwd),
            prompt,
        ]
        result = run_command(command, cwd, timeout_seconds)
        text = result.combined
        if result.timed_out:
            return {
                "ok": False,
                "text": text,
                "needs_approval": "approval" in text.lower() or "y/n" in text.lower(),
                "auth_or_limit": looks_like_auth_or_limit(text),
                "timed_out": True,
            }
        if looks_like_auth_or_limit(text) or result.returncode != 0 and looks_like_auth_or_limit(text):
            return {
                "ok": False,
                "text": text,
                "needs_approval": False,
                "auth_or_limit": True,
            }
        if result.returncode != 0:
            raise AgentError("implement_failed", text or "Cursor CLI exited non-zero.")
        return {
            "ok": True,
            "text": text,
            "needs_approval": False,
            "auth_or_limit": False,
        }


class CodexCliReviewer:
    name = "codex-cli"

    def run(self, prompt, cwd, timeout_seconds):
        binary = which("codex")
        if not binary:
            raise AgentError("missing_binary", "Codex CLI (`codex`) is not installed.")
        command = [
            binary,
            "review",
            "--uncommitted",
            prompt or "Review uncommitted changes against the project instructions.",
        ]
        result = run_command(command, cwd, timeout_seconds)
        text = result.combined
        if result.timed_out:
            return {
                "ok": False,
                "text": text,
                "review": parse_review(""),
                "needs_approval": False,
                "auth_or_limit": looks_like_auth_or_limit(text),
                "timed_out": True,
            }
        if looks_like_auth_or_limit(text):
            return {
                "ok": False,
                "text": text,
                "review": parse_review(""),
                "needs_approval": False,
                "auth_or_limit": True,
            }
        review = parse_review(text)
        return {
            "ok": review["valid"],
            "text": text,
            "review": review,
            "needs_approval": False,
            "auth_or_limit": False,
        }


def resolve_implementer(name):
    if name == "fake":
        return FakeImplementer()
    if name == "cursor":
        return CursorCliImplementer()
    raise ValueError(f"Unknown implementer: {name}")


def resolve_reviewer(name, fake_payload=None):
    if name == "fake":
        return FakeReviewer(fake_payload)
    if name == "codex":
        return CodexCliReviewer()
    raise ValueError(f"Unknown reviewer: {name}")
