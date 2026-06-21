"""Canonical AI provider and model policy."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    description: str
    api_key_field: str | None
    env_var: str | None
    default_model: str
    models: dict[str, dict[str, Any]]


OPENAI_MODELS = {
    tier: {
        "id": model_id,
        "name": name,
        "description": description,
        "cost_per_1k_input": input_cost,
        "cost_per_1k_output": output_cost,
    }
    for tier, model_id, name, description, input_cost, output_cost in (
        ("mini", "gpt-5.4-mini", "GPT-5.4 Mini", "Lower-latency GPT-5.4 option", 0.00075, 0.0045),
        ("standard", "gpt-5.5", "GPT-5.5", "Frontier OpenAI option", 0.005, 0.03),
    )
}

ANTHROPIC_MODELS = {
    tier: {
        "id": model_id,
        "name": name,
        "description": description,
        "cost_per_1k_input": input_cost,
        "cost_per_1k_output": output_cost,
    }
    for tier, model_id, name, description, input_cost, output_cost in (
        ("haiku", "claude-haiku-4-5-20251001", "Claude Haiku 4.5", "Fast Claude option", 0.0008, 0.004),
        ("sonnet", "claude-sonnet-4-6", "Claude Sonnet 4.6", "Balanced Claude option", 0.003, 0.015),
        ("opus", "claude-opus-4-8", "Claude Opus 4.8", "Most capable Claude option", 0.005, 0.025),
    )
}

OPENROUTER_MODELS = {
    tier: {"id": model_id, "name": name, "description": description, "cost_per_1k_tokens": token_cost}
    for tier, model_id, name, description, token_cost in (
        ("gemini-lite", "google/gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "Low-latency Gemini option", 0.0002),
        ("openai-mini", "openai/gpt-5.4-mini", "GPT-5.4 Mini", "Efficient OpenAI option", 0.0045),
        ("claude-sonnet", "anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6", "Strong Claude option", 0.015),
    )
}
OPENROUTER_MODELS.update(
    cheap=OPENROUTER_MODELS["gemini-lite"],
    balanced=OPENROUTER_MODELS["claude-sonnet"],
    premium=OPENROUTER_MODELS["claude-sonnet"],
)

PROVIDERS = {
    "openai": ProviderSpec(
        label="OpenAI (GPT-5.4 Mini/GPT-5.5)",
        description="Current OpenAI frontier and efficient GPT-5.4-class models",
        api_key_field="openai_api_key",
        env_var="OPENAI_API_KEY",
        default_model="standard",
        models=OPENAI_MODELS,
    ),
    "anthropic": ProviderSpec(
        label="Anthropic (Claude 4.5/4.6/4.8)",
        description="Current Claude models for high-quality meeting synthesis",
        api_key_field="anthropic_api_key",
        env_var="ANTHROPIC_API_KEY",
        default_model="sonnet",
        models=ANTHROPIC_MODELS,
    ),
    "openrouter": ProviderSpec(
        label="OpenRouter",
        description="Access to 300+ models",
        api_key_field="openrouter_api_key",
        env_var="OPENROUTER_API_KEY",
        default_model="claude-sonnet",
        models=OPENROUTER_MODELS,
    ),
    "local": ProviderSpec(
        label="Local (Ollama)",
        description="Private, offline, slow",
        api_key_field=None,
        env_var=None,
        default_model="llama3.2:3b",
        models={},
    ),
    "none": ProviderSpec(
        label="No AI",
        description="Just transcripts, no summary",
        api_key_field=None,
        env_var=None,
        default_model="",
        models={},
    ),
}
