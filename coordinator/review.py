"""Parse reviewer output. Malformed output is incomplete, never approved."""

from __future__ import annotations

import json
import re


ALLOWED_STATUSES = {"approved", "changes_requested", "blocked", "incomplete"}
ALLOWED_SEVERITIES = {"blocker", "major", "minor"}


def extract_json_object(text):
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _priority_to_severity(priority):
    try:
        value = int(priority)
    except (TypeError, ValueError):
        return "major"
    if value <= 1:
        return "blocker"
    if value == 2:
        return "major"
    return "minor"


def _from_native_codex(payload):
    correctness = str(payload.get("overall_correctness") or "").strip().lower()
    raw_findings = payload.get("findings")
    if correctness not in {"patch is correct", "patch is incorrect"} or not isinstance(raw_findings, list):
        return None
    clean_findings = []
    for item in raw_findings:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or "").strip()
        action = str(item.get("action") or item.get("body") or "").strip()
        if not title or not action:
            return None
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in ALLOWED_SEVERITIES:
            severity = _priority_to_severity(item.get("priority"))
        clean_findings.append({"severity": severity, "title": title, "action": action})
    status = "approved" if correctness == "patch is correct" else "changes_requested"
    if status == "approved" and any(item["severity"] == "blocker" for item in clean_findings):
        status = "changes_requested"
    return {
        "status": status,
        "summary": str(payload.get("overall_explanation") or payload.get("summary") or "").strip(),
        "tests_reviewed": bool(payload.get("tests_reviewed")),
        "findings": clean_findings,
        "valid": True,
    }


def parse_review(text):
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        return {
            "status": "incomplete",
            "summary": "Reviewer output was missing or not valid JSON.",
            "tests_reviewed": False,
            "findings": [],
            "valid": False,
        }
    native = _from_native_codex(payload)
    if native:
        return native
    status = str(payload.get("status") or "").strip().lower()
    findings = payload.get("findings")
    if status not in ALLOWED_STATUSES or not isinstance(findings, list):
        return {
            "status": "incomplete",
            "summary": "Reviewer JSON was missing a valid status or findings list.",
            "tests_reviewed": False,
            "findings": [],
            "valid": False,
        }
    clean_findings = []
    for item in findings:
        if not isinstance(item, dict):
            return {
                "status": "incomplete",
                "summary": "Reviewer findings contained a non-object item.",
                "tests_reviewed": False,
                "findings": [],
                "valid": False,
            }
        severity = str(item.get("severity") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        action = str(item.get("action") or "").strip()
        if severity not in ALLOWED_SEVERITIES or not title or not action:
            return {
                "status": "incomplete",
                "summary": "A finding was missing severity, title, or action.",
                "tests_reviewed": False,
                "findings": [],
                "valid": False,
            }
        clean_findings.append(
            {"severity": severity, "title": title, "action": action}
        )
    if status == "approved" and any(item["severity"] == "blocker" for item in clean_findings):
        status = "changes_requested"
    return {
        "status": status,
        "summary": str(payload.get("summary") or "").strip(),
        "tests_reviewed": bool(payload.get("tests_reviewed")),
        "findings": clean_findings,
        "valid": True,
    }


def findings_prompt(review):
    if not review.get("findings"):
        return review.get("summary") or "No structured findings."
    lines = [review.get("summary") or "Review findings:"]
    for item in review["findings"]:
        lines.append(f"- [{item['severity']}] {item['title']}: {item['action']}")
    return "\n".join(lines)
