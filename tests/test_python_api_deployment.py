"""Deployment boundaries: no portfolio files, credentials, or desktop runtime."""

from __future__ import annotations

import fnmatch
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment" / "python-api"
DOMAIN_PACKAGES = {"api", "data", "market", "paper_trading", "strategies"}


def _rules():
    return [
        line.strip()
        for line in (DEPLOYMENT / "Dockerfile.dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _files():
    return {rule[1:] for rule in _rules() if rule.startswith("!") and not rule.endswith("/")}


def _included(path):
    # Only the deliberately restricted rule syntax below is supported here.
    # Directory reopen rules are checked separately; files have exact allows.
    included = True
    for rule in _rules():
        allow = rule.startswith("!")
        pattern = rule[1:] if allow else rule
        if pattern.endswith("/"):
            continue
        if fnmatch.fnmatchcase(path, pattern):
            included = allow
    return included


def _launcher():
    spec = importlib.util.spec_from_file_location("deployment_serve", DEPLOYMENT / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_context_is_deny_by_default_with_only_exact_reviewed_source_files():
    rules = _rules()
    assert rules[0] == "**"
    allowed = _files()
    assert "config.py" in allowed
    assert "requirements-web-api.txt" in allowed
    assert {p.split("/")[0] for p in allowed} == DOMAIN_PACKAGES | {
        "deployment", "config.py", "requirements-web-api.txt",
    }
    for index, rule in enumerate(rules[1:], 1):
        if rule.startswith("!"):
            path = rule[1:]
            assert not any(token in path for token in ("*", "?", "[", "..", "\\"))
            if path.endswith("/"):
                assert rules[index + 1] == path + "**", "Reopened directories must deny their children."
            else:
                assert _included(path)
                source = ROOT / path
                assert source.is_file()
                assert not source.is_symlink()
                assert source.resolve().is_relative_to(ROOT.resolve())
                for parent in source.parents:
                    if parent == ROOT:
                        break
                    assert not parent.is_symlink()
                    relative = parent.relative_to(ROOT).as_posix() + "/"
                    assert "!" + relative in rules
        else:
            assert rule.endswith("/**")
    assert {p for p in allowed if p.startswith("deployment/")} == {
        "deployment/python-api/Dockerfile",
        "deployment/python-api/Dockerfile.dockerignore",
        "deployment/python-api/requirements.txt",
        "deployment/python-api/serve.py",
    }
    assert all(Path(p).suffix == ".py" for p in allowed if p.split("/")[0] in DOMAIN_PACKAGES)
    assert not {"api/local_config.py", "api/run_local.py", "paper_trading/storage.py",
                "paper_trading/ledger.py", "paper_trading/plan_store.py"} & allowed


@pytest.mark.parametrize("directory", [
    "", "api/", "data/", "market/", "paper_trading/", "strategies/",
    "deployment/python-api/", "paper_trading_data/", "data_cache/",
    "historical_data/", "build/", "dist/", ".git/", ".venv/", "sites/", "web/",
])
def test_sensitive_and_future_files_are_excluded_even_inside_source_packages(directory):
    for filename in (
        "paper_portfolio.sqlite3", "paper_portfolio.sqlite3-wal", "paper_portfolio.sqlite3-shm",
        "accounts.db", "accounts.sqlite", "plans.json", "account_history.csv",
        ".env", ".env.local", ".env.production", "secrets.pem", "credentials.txt",
        "prices.parquet", "prices.pkl", "app.exe", "config", "future_module.py",
        "nested/private.sqlite3", "__pycache__/public_app.pyc",
    ):
        assert not _included(directory + filename), directory + filename


def test_docker_copies_only_approved_packages_and_uses_non_root_health_launcher():
    dockerfile = (DEPLOYMENT / "Dockerfile").read_text()
    copies = [line.split() for line in dockerfile.splitlines() if line.startswith("COPY ")]
    expected = {
        "requirements-web-api.txt", "deployment/python-api/requirements.txt", "config.py",
        "deployment/python-api/serve.py", *(package + "/" for package in DOMAIN_PACKAGES),
    }
    assert {line[1] for line in copies} == expected
    assert all(len(line) == 3 for line in copies)
    assert not any(line.startswith("ADD ") for line in dockerfile.splitlines())
    assert "USER 10001:10001" in dockerfile
    assert "STOCK_COMP_CACHE_DIR=/tmp/stock-comp/market-data" in dockerfile
    assert 'CMD ["python", "deployment/python-api/serve.py"]' in dockerfile
    assert '"--check-health"' in dockerfile
    assert "gunicorn" not in dockerfile


def test_render_blueprint_is_free_source_only_and_prompts_for_existing_env_names():
    import yaml

    blueprint = yaml.safe_load((DEPLOYMENT / "render.yaml").read_text())
    assert set(blueprint) == {"services"}, "No additional billable resources or env groups."
    assert len(blueprint["services"]) == 1
    service = blueprint["services"][0]
    assert set(service) == {
        "type", "name", "runtime", "plan", "dockerContext", "dockerfilePath",
        "healthCheckPath", "autoDeployTrigger", "maxShutdownDelaySeconds", "envVars",
    }
    assert service["type"] == "web"
    assert service["runtime"] == "docker"
    assert service["plan"] == "free"
    assert service["dockerContext"] == "."
    assert service["dockerfilePath"] == "deployment/python-api/Dockerfile"
    assert service["healthCheckPath"] == "/v1/health"
    assert service["autoDeployTrigger"] == "off"
    assert service["maxShutdownDelaySeconds"] == 170
    assert len(service["envVars"]) == 6
    assert {entry["key"] for entry in service["envVars"]} == {
        "STOCK_COMP_SUPABASE_URL", "STOCK_COMP_SUPABASE_PUBLISHABLE_KEY",
        "STOCK_COMP_SUPABASE_SECRET_KEY", "STOCK_COMP_ALLOWED_ORIGINS",
        "STOCK_COMP_HTTPS_ONLY", "STOCK_COMP_API_DEMO_MODE",
    }
    for entry in service["envVars"]:
        assert set(entry) == {"key", "sync"}
        assert entry["sync"] is False
    assert not _included("deployment/python-api/render.yaml")


def test_deployment_dependencies_cover_the_actual_api_and_market_loader():
    lines = (DEPLOYMENT / "requirements.txt").read_text().splitlines()
    assert "-r ../../requirements-web-api.txt" in lines
    requirements = {
        Requirement(line).name.lower(): Requirement(line)
        for line in lines if line and not line.startswith(("#", "-r"))
    }
    assert set(requirements) == {"pandas", "yfinance", "pyarrow"}
    assert all(requirement.specifier for requirement in requirements.values())


@pytest.mark.parametrize("raw, expected", [(None, 8000), ("10000", 10000), ("65535", 65535)])
def test_launcher_honors_host_port(monkeypatch, raw, expected):
    monkeypatch.delenv("PORT", raising=False)
    if raw is not None:
        monkeypatch.setenv("PORT", raw)
    assert _launcher().configured_port() == expected


@pytest.mark.parametrize("raw", ["", "0", "-1", "65536", "8000;echo secret", "eight"])
def test_invalid_port_fails_before_starting_a_server(monkeypatch, raw):
    monkeypatch.setenv("PORT", raw)
    with pytest.raises(ValueError, match="PORT must be"):
        _launcher().configured_port()


def test_startup_uses_original_app_single_worker_and_no_local_env_loader(monkeypatch):
    captured = {}
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setattr(sys, "argv", ["serve.py"])
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(
        run=lambda app, **options: captured.update(app=app, **options),
    ))
    _launcher().main()
    assert captured == {
        "app": "api.public_app:app", "app_dir": str(ROOT), "host": "0.0.0.0",
        "port": 10000, "workers": 1, "proxy_headers": False,
        "timeout_graceful_shutdown": 170,
    }


@pytest.mark.parametrize("status, payload, healthy", [
    (200, {"status": "ok"}, True),
    (200, {"status": "unavailable"}, False),
    (503, {"status": "ok"}, False),
])
def test_container_probe_uses_host_port_and_validates_liveness(monkeypatch, status, payload, healthy):
    launcher = _launcher()
    captured = {}
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setattr(sys, "argv", ["serve.py", "--check-health"])

    class Response(io.BytesIO):
        pass

    def open_response(url, timeout):
        captured.update(url=url, timeout=timeout)
        response = Response(json.dumps(payload).encode())
        response.status = status
        return response

    def opener(handler):
        assert handler.proxies == {}
        return SimpleNamespace(open=open_response)

    monkeypatch.setattr(launcher, "build_opener", opener)
    if healthy:
        launcher.main()
    else:
        with pytest.raises(RuntimeError, match="liveness probe failed"):
            launcher.main()
    assert captured == {"url": "http://127.0.0.1:10000/v1/health", "timeout": 3}


def test_source_only_context_imports_lazy_provider_and_serves_health_without_auth(tmp_path):
    # Only copy allowlisted input files. The repo must not remain on sys.path,
    # so this detects missing imports that a normal in-repo test would mask.
    for relative in _files():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    safe_env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("STOCK_COMP_", "PYTHON"))
    }
    safe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    source = """
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from api.public_app import app
import data.market_data
from strategies.registry import available_strategy_names
from fastapi.testclient import TestClient
assert Path(data.market_data.__file__).resolve().is_relative_to(Path.cwd())
client = TestClient(app)
health = client.get('/v1/health')
assert health.status_code == 200 and health.json()['status'] == 'ok'
assert client.get('/v1/snapshot').status_code == 503
assert available_strategy_names()
assert not Path('paper_trading_data').exists()
assert not list(Path.cwd().rglob('*.sqlite*'))
print(json.dumps(health.json()))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source], cwd=tmp_path, env=safe_env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ok"
