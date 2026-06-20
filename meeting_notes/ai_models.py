"""Canonical AI provider and model policy."""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meeting_notes.config import AppConfig


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    description: str
    api_key_field: str | None
    env_var: str | None
    default_model: str
    models: dict[str, dict[str, Any]]


OPENAI_MODELS = {
    "mini": {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "description": "~$0.001/meeting - Ultra cheap",
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
    },
    "standard": {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "description": "~$0.015/meeting - Best quality",
        "cost_per_1k_input": 0.0025,
        "cost_per_1k_output": 0.01,
    },
}

ANTHROPIC_MODELS = {
    "haiku": {
        "id": "claude-haiku-4-5-20251001",
        "name": "Claude Haiku 4.5",
        "description": "~$0.005/meeting - Fast & affordable",
        "cost_per_1k_input": 0.0008,
        "cost_per_1k_output": 0.004,
    },
    "sonnet": {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "description": "~$0.020/meeting - Best quality",
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
    },
}

OPENROUTER_MODELS = {
    "cheap": {
        "id": "google/gemini-flash-1.5",
        "name": "Gemini 1.5 Flash",
        "description": "~$0.001/meeting",
        "cost_per_1k_tokens": 0.000075,
    },
    "balanced": {
        "id": "anthropic/claude-3-haiku",
        "name": "Claude 3 Haiku",
        "description": "~$0.01/meeting",
        "cost_per_1k_tokens": 0.00025,
    },
    "premium": {
        "id": "anthropic/claude-3.5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "description": "~$0.03/meeting",
        "cost_per_1k_tokens": 0.003,
    },
}

PROVIDERS = {
    "openai": ProviderSpec(
        label="OpenAI (GPT-4o Mini/4o)",
        description="Fast, cheap, great quality",
        api_key_field="openai_api_key",
        env_var="OPENAI_API_KEY",
        default_model="mini",
        models=OPENAI_MODELS,
    ),
    "anthropic": ProviderSpec(
        label="Anthropic (Claude)",
        description="Excellent quality, best for action items",
        api_key_field="anthropic_api_key",
        env_var="ANTHROPIC_API_KEY",
        default_model="haiku",
        models=ANTHROPIC_MODELS,
    ),
    "openrouter": ProviderSpec(
        label="OpenRouter",
        description="Access to 300+ models",
        api_key_field="openrouter_api_key",
        env_var="OPENROUTER_API_KEY",
        default_model="balanced",
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

CLOUD_PROVIDER_IDS = ("openai", "anthropic", "openrouter")
PROVIDER_IDS = tuple(PROVIDERS)


def configured_api_key(config: "AppConfig", provider: str) -> str | None:
    spec = PROVIDERS[provider]
    if not spec.api_key_field or not spec.env_var:
        return None
    return getattr(config, spec.api_key_field) or os.getenv(spec.env_var)
