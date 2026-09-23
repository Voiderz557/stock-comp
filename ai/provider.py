"""Provider protocol. Implementations must not receive mutation tools."""

from __future__ import annotations

from dataclasses import dataclass


class ProviderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AnalysisResult:
    ok: bool
    text: str
    provider: str
    model: str
    error_code: str | None = None
    error_message: str | None = None
    truncated: bool = False

    def as_dict(self):
        return {
            "ok": self.ok,
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "truncated": self.truncated,
            "layer": "ai_interpretation",
        }


class AnalysisProvider:
    name = "base"
    model = ""

    def analyze(self, question, context):
        raise NotImplementedError
