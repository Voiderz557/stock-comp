"""Check source/document boundaries without touching application runtime data."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optional_cursor_coordinator_is_not_an_application_dependency():
    for package in ("api", "app", "desktop", "paper_trading", "strategies", "ml"):
        for source in (ROOT / package).rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert not re.search(r"^\s*(from\s+coordinator(?:\.|\s)|import\s+coordinator\b)", text, re.MULTILINE), source


def test_shared_instructions_survive_tool_cleanup():
    assert (ROOT / "AGENTS.md").is_file()
    assert (ROOT / "web" / "AGENTS.md").is_file()
    assert ".cursor/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_readme_local_document_links_resolve():
    for readme in (ROOT / "README.md", ROOT / "docs" / "README.md"):
        text = readme.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            assert (readme.parent / link.split("#", 1)[0]).is_file(), (readme, link)


def test_roadmap_document_links_resolve_after_relocation():
    document = ROOT / "docs" / "PROJECT_MASTER_ROADMAP.md"
    for link in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
        if "://" not in link and not link.startswith("#"):
            assert (document.parent / link.split("#", 1)[0]).is_file(), link


def test_existing_entrypoints_remain_in_place():
    for path in ("app/competition_dashboard.py", "api/public_app.py", "web/package.json",
                 "desktop/__main__.py", "launch_competition_dashboard.bat", "StockCompDashboard.spec"):
        assert (ROOT / path).is_file(), path
