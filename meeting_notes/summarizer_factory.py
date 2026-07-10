"""Composition factory for concrete summarizer adapters."""

from dataclasses import dataclass
from typing import Protocol

from .ai_models import PROVIDERS
from .ai_summarizer import AnthropicSummarizer, OpenAISummarizer, OpenRouterSummarizer
from .summarizer import OllamaSummarizer
from .summarizer_port import MeetingSummary, Summarizer

CLOUD_SUMMARIZERS = {
    "openai": OpenAISummarizer,
    "anthropic": AnthropicSummarizer,
    "openrouter": OpenRouterSummarizer,
}


class ProviderSummarizer(Protocol):
    def summarize(self, transcript: str, user_notes: str = "") -> MeetingSummary: ...


@dataclass(frozen=True)
class ConfiguredSummarizer:
    """Expose the application port without leaking provider attributes."""

    delegate: ProviderSummarizer
    provider_id: str
    display_label: str

    def summarize(self, transcript: str, user_notes: str = "") -> MeetingSummary:
        return self.delegate.summarize(transcript, user_notes=user_notes)


def create_summarizer(provider: str, model: str, api_key: str | None) -> Summarizer | None:
    if provider == "none":
        return None
    if provider == "local":
        delegate = OllamaSummarizer(model=model or PROVIDERS["local"].default_model)
        return ConfiguredSummarizer(delegate, provider, f"Local Ollama: {delegate.model}")
    summarizer_cls = CLOUD_SUMMARIZERS.get(provider)
    if summarizer_cls is None:
        raise ValueError(f"Invalid ai_provider: {provider}. Must be one of {list(PROVIDERS)}")
    delegate = summarizer_cls(api_key=api_key, model=model)
    return ConfiguredSummarizer(delegate, provider, f"{PROVIDERS[provider].label}: {delegate.model_config['name']}")
