"""Inspect Cursor Agent CLI and Codex CLI without printing secrets."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(command):
    binary = shutil.which(command[0])
    if binary is None:
        return {"installed": False, "command": command, "error": "not on PATH"}
    resolved = [binary, *command[1:]]
    try:
        completed = subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return {"installed": False, "command": command, "error": "not on PATH"}
    except subprocess.TimeoutExpired:
        return {"installed": True, "command": command, "error": "timed out"}
    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    lowered = text.lower()
    if any(token in lowered for token in ("sk-", "cursor_", "bearer ", "api_key")):
        text = "[redacted command output]"
    return {
        "installed": True,
        "command": command,
        "exit": completed.returncode,
        "output": text[:2000],
    }


def diagnose():
    cursor = shutil.which("cursor")
    agent = shutil.which("agent")
    codex = shutil.which("codex")
    report = {
        "cursor_ide_cli": {
            "path": cursor,
            "note": "This launches the Cursor editor. It is not the Agent CLI.",
            "version": _run(["cursor", "--version"]) if cursor else {"installed": False},
        },
        "cursor_agent_cli": {
            "path": agent,
            "version": _run(["agent", "--version"]) if agent else {"installed": False},
            "help": _run(["agent", "--help"]) if agent else {"installed": False},
            "status": _run(["agent", "status"]) if agent else {"installed": False},
        },
        "codex_cli": {
            "path": codex,
            "version": _run(["codex", "--version"]) if codex else {"installed": False},
            "help": _run(["codex", "--help"]) if codex else {"installed": False},
            "login_status": _run(["codex", "login", "status"]) if codex else {"installed": False},
        },
        "policy": {
            "use_cursor_subscription_login": True,
            "use_chatgpt_subscription_login": True,
            "configure_paid_api_fallback": False,
        },
        "local_homes": {
            "codex_home_present": (Path.home() / ".codex").exists(),
            "cursor_agent_home_present": (Path.home() / ".cursor" / "bin").exists(),
            "note": "Home-directory presence only. Credential files are not read.",
        },
    }
    return report
