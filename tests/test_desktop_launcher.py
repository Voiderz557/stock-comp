import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from desktop.launcher import (
    HOST,
    find_free_localhost_port,
    stop_process_tree,
    wait_until_ready,
)
from paper_trading import storage


REPO = Path(__file__).resolve().parents[1]


class WaitReadyTests(unittest.TestCase):
    def test_wait_until_ready_accepts_localhost_http(self):
        port = find_free_localhost_port()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format, *args):
                return

        server = HTTPServer((HOST, port), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(wait_until_ready(f"http://{HOST}:{port}", timeout_seconds=5))
        finally:
            server.shutdown()

    def test_free_port_is_bound_only_to_localhost(self):
        port = find_free_localhost_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((HOST, port))


class DesktopLaunchPersistenceTests(unittest.TestCase):
    def test_headless_launch_close_relaunch_keeps_portfolio(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user_root = tmp_path / "user"
            ready_file = tmp_path / "ready.txt"
            env = os.environ.copy()
            env["STOCK_COMP_USER_DATA_ROOT"] = str(user_root)
            env["STOCK_COMP_DESKTOP_HEADLESS"] = "1"
            env["STOCK_COMP_DESKTOP_HOLD_SECONDS"] = "3"
            env["STOCK_COMP_DESKTOP_READY_FILE"] = str(ready_file)
            env["PYTHONPATH"] = str(REPO)
            db_path = user_root / "paper_trading_data" / "paper_portfolio.sqlite3"

            first = subprocess.Popen(
                [sys.executable, "-m", "desktop.launcher", "--headless"],
                cwd=str(REPO),
                env=env,
            )
            try:
                url = _wait_for_ready_file(ready_file, process=first, timeout=90)
                self.assertTrue(url.startswith(f"http://{HOST}:"))
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=10) as response:
                    body = response.read().decode("utf-8", errors="replace")
                self.assertTrue(
                    "streamlit" in body.lower() or "Paper Trading" in body,
                    msg="Expected the Streamlit dashboard to answer on localhost.",
                )
                self.assertTrue(db_path.is_file())
                account = storage.get_account_state(db_path)
                self.assertAlmostEqual(account["Cash"], 100_000.0)
            finally:
                if first.poll() is None:
                    first.wait(timeout=30)
                if first.poll() is None:
                    stop_process_tree(first)

            self.assertIsNotNone(first.poll())
            self.assertTrue(db_path.is_file())
            ready_file.unlink(missing_ok=True)

            second = subprocess.Popen(
                [sys.executable, "-m", "desktop.launcher", "--headless"],
                cwd=str(REPO),
                env=env,
            )
            try:
                url = _wait_for_ready_file(ready_file, process=second, timeout=90)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=10) as response:
                    body = response.read().decode("utf-8", errors="replace")
                self.assertTrue(
                    "streamlit" in body.lower() or "Paper Trading" in body,
                    msg="Expected the Streamlit dashboard to answer on localhost.",
                )
                account = storage.get_account_state(db_path)
                self.assertAlmostEqual(account["Cash"], 100_000.0)
                self.assertAlmostEqual(account["Starting Capital"], 100_000.0)
            finally:
                if second.poll() is None:
                    second.wait(timeout=30)
                if second.poll() is None:
                    stop_process_tree(second)


def _wait_for_ready_file(path, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8").strip()
        if process.poll() is not None:
            raise RuntimeError(
                f"Desktop launcher exited before becoming ready (code {process.returncode})."
            )
        time.sleep(0.25)
    raise TimeoutError(f"Ready file was not written: {path}")
