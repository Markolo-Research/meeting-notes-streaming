"""Tests for the model registries in summarizer classes.

These tests don't make network calls — they just check that the model IDs
configured in MODELS dicts haven't drifted from what the providers actually
accept. If a provider deprecates a model, you'd update both the constant
here and the source.
"""

from meeting_notes.ai_summarizer import (
    AnthropicSummarizer,
    BaseSummarizer,
    OpenAISummarizer,
    OpenRouterSummarizer,
)
from meeting_notes.summarizer import OllamaSummarizer


def test_anthropic_haiku_is_current_4_5():
    """eduspano/jmalobicky bumped Haiku from 3.5 to 4.5."""
    haiku = AnthropicSummarizer.MODELS["haiku"]
    assert haiku["id"] == "claude-haiku-4-5-20251001"
    assert "4.5" in haiku["name"]


def test_anthropic_sonnet_is_current_4_6():
    """eduspano bumped Sonnet to 4.6."""
    sonnet = AnthropicSummarizer.MODELS["sonnet"]
    assert sonnet["id"] == "claude-sonnet-4-6"
    assert "4.6" in sonnet["name"]


def test_anthropic_opus_is_current_4_8():
    opus = AnthropicSummarizer.MODELS["opus"]
    assert opus["id"] == "claude-opus-4-8"
    assert "4.8" in opus["name"]


def test_anthropic_no_deprecated_3_5_ids_remain():
    """Guard against accidental revert to deprecated 3.5 model IDs."""
    for tier, info in AnthropicSummarizer.MODELS.items():
        assert "3-5" not in info["id"], f"{tier} still references deprecated 3.5 model"
        assert "3.5" not in info["name"], f"{tier} still labelled as 3.5"


def test_openai_no_deprecated_4o_ids_remain():
    for tier, info in OpenAISummarizer.MODELS.items():
        assert "gpt-4" not in info["id"], f"{tier} still references a deprecated GPT-4-class model"


def test_openrouter_no_deprecated_model_ids_remain():
    deprecated_fragments = ("gemini-flash-1.5", "claude-3-haiku", "claude-3.5")
    for tier, info in OpenRouterSummarizer.MODELS.items():
        assert not any(fragment in info["id"] for fragment in deprecated_fragments), (
            f"{tier} still references a deprecated OpenRouter model"
        )


def test_anthropic_models_have_required_fields():
    for tier, info in AnthropicSummarizer.MODELS.items():
        for field in ("id", "name", "cost_per_1k_input", "cost_per_1k_output"):
            assert field in info, f"Anthropic {tier} missing {field}"
        assert isinstance(info["cost_per_1k_input"], (int, float))


def test_openai_models_have_required_fields():
    for tier, info in OpenAISummarizer.MODELS.items():
        for field in ("id", "name", "cost_per_1k_input", "cost_per_1k_output"):
            assert field in info, f"OpenAI {tier} missing {field}"


def test_openrouter_models_have_required_fields():
    for tier, info in OpenRouterSummarizer.MODELS.items():
        for field in ("id", "name"):
            assert field in info, f"OpenRouter {tier} missing {field}"


def test_openrouter_legacy_tier_aliases_remain_supported():
    for tier in ("cheap", "balanced", "premium"):
        assert tier in OpenRouterSummarizer.MODELS


def test_anthropic_summarizer_requires_api_key(monkeypatch):
    """Without an API key (and no env var), construction should fail clearly."""
    import pytest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        AnthropicSummarizer(api_key=None, model="haiku")


def test_anthropic_summarizer_rejects_unknown_model():
    """Unknown model tier should not be silently accepted."""
    import pytest

    with pytest.raises((KeyError, ValueError)):
        AnthropicSummarizer(api_key="sk-ant-test", model="not-a-tier")


def test_ollama_uses_canonical_summary_parser():
    summarizer = OllamaSummarizer()

    assert isinstance(summarizer, BaseSummarizer)

    parsed = summarizer._parse_response(
        """OVERVIEW:
Planning session.

KEY POINTS:
- Agree on scope

ACTION ITEMS:
- Rolf to open a PR

DECISIONS:
- Use one parser

PARTICIPANTS:
Rolf, Alice
"""
    )

    assert parsed.overview == "Planning session."
    assert parsed.key_points == ["Agree on scope"]
    assert parsed.action_items == ["Rolf to open a PR"]
    assert parsed.decisions == ["Use one parser"]
    assert parsed.participants == ["Rolf", "Alice"]
