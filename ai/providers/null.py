from ai.provider import AnalysisProvider, AnalysisResult
from ai.settings import configured_model, public_provider_label


class NullProvider(AnalysisProvider):
    name = "none"

    def __init__(self):
        self.model = configured_model()

    def analyze(self, question, context):
        return AnalysisResult(
            ok=False,
            text="",
            provider=public_provider_label(),
            model=self.model,
            error_code="missing_config",
            error_message=(
                "No AI provider is configured. Set STOCK_COMP_AI_PROVIDER "
                "and STOCK_COMP_AI_API_KEY (or OPENAI_API_KEY), then click Analyze."
            ),
        )
