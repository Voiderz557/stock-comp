import hashlib
import json
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai.analyze import SYSTEM_PROMPT, analyze_question, build_provider_payload, resolve_provider
from ai.provider import AnalysisProvider, AnalysisResult
from ai.providers.null import NullProvider
from ai.providers.openai_compatible import OpenAICompatibleProvider
from paper_trading import storage
from paper_trading.analysis_context import build_analysis_context
from paper_trading.plan_store import save_plan
from paper_trading.prices import SOURCE_LABEL


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeProvider(AnalysisProvider):
    name = "fake"

    def __init__(self):
        self.model = "fake-model"
        self.calls = []

    def analyze(self, question, context):
        self.calls.append({"question": question, "context": context})
        return AnalysisResult(
            ok=True,
            text="Fake interpretation of the current snapshot.",
            provider=self.name,
            model=self.model,
        )


class AnalyzeLayerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "paper_portfolio.sqlite3"
        storage.initialize_storage(self.db_path)
        save_plan(
            {
                "mode": "Baseline",
                "strategy_name": "Baseline",
                "regime_name": "SIDEWAYS",
                "strategy_reason": "fixture",
                "price_session_start": "2026-09-21",
                "price_session_end": "2026-09-22",
                "price_source": SOURCE_LABEL,
                "cash_after": 100_000.0,
                "remaining_capacity_before": 100_000.0,
                "remaining_capacity_after": 100_000.0,
                "no_purchase_explanation": None,
                "data_warnings": [],
                "buys": [
                    {
                        "Ticker": "AAPL",
                        "Signal": "BUY",
                        "Score": 1.0,
                        "Reason": "fixture buy",
                        "Price": 100.0,
                        "Price As Of": "2026-09-22",
                        "Quantity": 10.0,
                        "Allocation": 1_000.0,
                    }
                ],
                "holdings": [],
                "skipped_buys": [],
                "unavailable_universe": [],
            },
            storage.get_account_state(self.db_path),
            [],
            db_path=self.db_path,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_fake_provider_does_not_write_and_has_no_tools(self):
        before = _digest(self.db_path)
        context = build_analysis_context(db_path=self.db_path, current_prices={})
        fake = FakeProvider()
        result = analyze_question("What changed since the previous plan?", context, provider=fake)
        self.assertTrue(result.ok)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(context["mutation_tools"], [])
        self.assertNotIn("execute_recommendation", SYSTEM_PROMPT.lower())
        self.assertIn("no tools", SYSTEM_PROMPT.lower())
        self.assertEqual(_digest(self.db_path), before)
        self.assertEqual(storage.get_open_positions(self.db_path), [])

    def test_payload_treats_notes_as_untrusted_and_bounds_question(self):
        context = build_analysis_context(db_path=self.db_path, current_prices={})
        payload = build_provider_payload("x" * 10_000, context)
        self.assertIn("untrusted", payload["system"].lower())
        self.assertLessEqual(len(json.loads(payload["user"])["question"]), 2_000)
        self.assertIn("application_state", json.loads(payload["user"]))

    def test_missing_config_does_not_call_network(self):
        with patch.dict("os.environ", {"STOCK_COMP_AI_PROVIDER": "none"}, clear=False):
            provider = resolve_provider()
            self.assertIsInstance(provider, NullProvider)
            with patch("urllib.request.urlopen") as urlopen:
                result = analyze_question("Summarize", {}, provider=provider)
            urlopen.assert_not_called()
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "missing_config")

    def test_provider_http_errors(self):
        cases = {
            401: "auth",
            429: "rate_limit",
        }
        for status, code in cases.items():
            error = urllib.error.HTTPError(
                "https://api.openai.com/v1/chat/completions",
                status,
                "no",
                hdrs=None,
                fp=BytesIO(b"{}"),
            )
            provider = OpenAICompatibleProvider(key="sk-test", model="gpt-test")
            with patch("urllib.request.urlopen", side_effect=error):
                result = provider.analyze(
                    {"system": "s", "user": "u"},
                    {},
                )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, code)
            self.assertNotIn("sk-test", result.error_message or "")

    def test_provider_timeout_and_network(self):
        provider = OpenAICompatibleProvider(key="sk-test", model="gpt-test")
        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            result = provider.analyze({"system": "s", "user": "u"}, {})
        self.assertEqual(result.error_code, "timeout")
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            result = provider.analyze({"system": "s", "user": "u"}, {})
        self.assertEqual(result.error_code, "network")

    def test_successful_adapter_truncates_response(self):
        payload = {
            "choices": [
                {"message": {"content": "ok " * 10_000}}
            ]
        }
        class _Resp:
            def read(self):
                return json.dumps(payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        provider = OpenAICompatibleProvider(key="sk-test", model="gpt-test")
        with patch("urllib.request.urlopen", return_value=_Resp()):
            result = provider.analyze({"system": "s", "user": "u"}, {})
        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 8_000)


if __name__ == "__main__":
    unittest.main()
