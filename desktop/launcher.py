"""Start the local Streamlit dashboard inside a desktop window.

The trading engine and Streamlit page are unchanged. This module only
starts a localhost server, waits until it answers, shows that page, and
stops the server when the window closes.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from desktop.paths import (
    apply_runtime_data_env,
    bundle_root,
    default_cache_dir,
    default_logs_dir,
    is_packaged,
    prepare_paper_data_dir,
)


HOST = "127.0.0.1"
CHILD_ENV = "STOCK_COMP_DESKTOP_CHILD"
HEADLESS_ENV = "STOCK_COMP_DESKTOP_HEADLESS"
READY_FILE_ENV = "STOCK_COMP_DESKTOP_READY_FILE"
DASHBOARD_SCRIPT = Path("app") / "competition_dashboard.py"


def dashboard_script_path():
    return bundle_root() / DASHBOARD_SCRIPT


def find_free_localhost_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_until_ready(url, timeout_seconds=90, sleep_seconds=0.25, process=None):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    opener = _local_opener()
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"Dashboard server exited before it was ready (code {process.returncode}): {last_error}"
            )
        try:
            with opener.open(url, timeout=2) as response:
                if 200 <= response.status < 400:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
        time.sleep(sleep_seconds)
    raise RuntimeError(f"Dashboard server was not ready at {url}: {last_error}")


def stop_process_tree(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except Exception:
        process.kill()


def run_streamlit_child(script_path, port):
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(script_path),
        "--server.address",
        HOST,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
        "--global.developmentMode",
        "false",
    ]
    streamlit_cli.main()


def child_command(script_path, port):
    if is_packaged():
        return [
            sys.executable,
            "--desktop-child",
            "--port",
            str(port),
            "--script",
            str(script_path),
        ]
    return [
        sys.executable,
        "-m",
        "desktop.launcher",
        "--desktop-child",
        "--port",
        str(port),
        "--script",
        str(script_path),
    ]


def start_dashboard_server(script_path, port, env, log_path=None):
    child_env = env.copy()
    child_env[CHILD_ENV] = "streamlit"
    child_env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    stdout = subprocess.DEVNULL
    log_handle = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "a", encoding="utf-8")
        stdout = log_handle
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        child_command(script_path, port),
        env=child_env,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        cwd=str(bundle_root()),
        creationflags=creationflags,
    )
    process._stock_comp_log_handle = log_handle
    return process


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stock Comp paper-trading desktop app")
    parser.add_argument("--desktop-child", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--script", default=None)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args(argv)


def _open_desktop_window(url):
    try:
        import webview
    except ImportError as error:
        raise RuntimeError(
            "pywebview is required for the desktop window. "
            "Install requirements-desktop.txt and rebuild."
        ) from error
    webview.create_window(
        "Paper Trading Assistant",
        url,
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    webview.start()


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.desktop_child or os.environ.get(CHILD_ENV) == "streamlit":
        script = Path(args.script or os.environ.get("STOCK_COMP_DASHBOARD_SCRIPT") or dashboard_script_path())
        port = args.port or int(os.environ.get("STOCK_COMP_DESKTOP_PORT") or "0")
        if not port:
            raise SystemExit("Desktop child is missing --port.")
        run_streamlit_child(script, port)
        return 0

    paper_dir, migration = prepare_paper_data_dir()
    cache_dir = default_cache_dir()
    apply_runtime_data_env(paper_dir, cache_dir)
    log_path = default_logs_dir() / "desktop.log"
    default_logs_dir().mkdir(parents=True, exist_ok=True)

    script = Path(args.script or dashboard_script_path())
    if not script.is_file():
        raise FileNotFoundError(f"Dashboard script not found: {script}")
    port = args.port or find_free_localhost_port()
    url = f"http://{HOST}:{port}"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"paper_data={paper_dir}\n")
        log.write(f"migration={migration}\n")
        log.write(f"url={url}\n")
    process = start_dashboard_server(script, port, os.environ.copy(), log_path=log_path)
    ready_file = os.environ.get(READY_FILE_ENV)
    try:
        wait_until_ready(url, process=process)
        if ready_file:
            Path(ready_file).write_text(url, encoding="utf-8")
        headless = args.headless or os.environ.get(HEADLESS_ENV) == "1"
        if headless:
            hold_seconds = float(os.environ.get("STOCK_COMP_DESKTOP_HOLD_SECONDS") or "2")
            time.sleep(max(0.0, hold_seconds))
        else:
            _open_desktop_window(url)
    finally:
        stop_process_tree(process)
        log_handle = getattr(process, "_stock_comp_log_handle", None)
        if log_handle:
            log_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
