"""OpenAI-compatible chat completions adapter. Stdlib HTTP only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ai.provider import AnalysisProvider, AnalysisResult, ProviderError
from ai.settings import (
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    api_key,
    configured_base_url,
    configured_model,
)


class OpenAICompatibleProvider(AnalysisProvider):
    name = "openai_compatible"

    def __init__(self, key=None, model=None, base_url=None, timeout_seconds=None):
        self._key = key if key is not None else api_key()
        self.model = model or configured_model()
        self.base_url = (base_url or configured_base_url()).rstrip("/")
        self.timeout_seconds = timeout_seconds or REQUEST_TIMEOUT_SECONDS

    def analyze(self, question, context):
        if not self._key:
            return AnalysisResult(
                ok=False,
                text="",
                provider=self.name,
                model=self.model,
                error_code="missing_config",
                error_message=(
                    "No API key is configured. Set STOCK_COMP_AI_API_KEY "
                    "or OPENAI_API_KEY."
                ),
            )
        try:
            text = self._complete(question, context)
        except ProviderError as error:
            return AnalysisResult(
                ok=False,
                text="",
                provider=self.name,
                model=self.model,
                error_code=error.code,
                error_message=error.message,
            )
        truncated = len(text) > MAX_RESPONSE_CHARS
        return AnalysisResult(
            ok=True,
            text=text[:MAX_RESPONSE_CHARS],
            provider=self.name,
            model=self.model,
            truncated=truncated,
        )

    def _complete(self, question, context):
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": [
                    {"role": "system", "content": question["system"]},
                    {"role": "user", "content": question["user"]},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as error:
            raise ProviderError("timeout", "The AI provider timed out.") from error
        except urllib.error.HTTPError as error:
            raise ProviderError(*_http_error(error)) from error
        except urllib.error.URLError as error:
            raise ProviderError("network", "Could not reach the AI provider.") from error
        except json.JSONDecodeError as error:
            raise ProviderError("provider", "The AI provider returned invalid JSON.") from error
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("provider", "The AI provider response was incomplete.") from error


def _http_error(error):
    if error.code in (401, 403):
        return "auth", "The AI provider rejected the configured credentials."
    if error.code == 429:
        return "rate_limit", "The AI provider rate-limited the request. Try again later."
    return "provider", f"The AI provider returned HTTP {error.code}."
