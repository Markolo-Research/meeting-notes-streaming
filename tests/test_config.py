"""Tests for AppConfig: defaults, serialisation, validation."""

import pytest

from meeting_notes.ai_models import PROVIDERS
from meeting_notes.config import AppConfig, configured_api_key, validate_config


def test_default_recording_retention_days():
    """The retention field should default to 30 days (jmalobicky fork)."""
    cfg = AppConfig()
    assert cfg.recording_retention_days == 30


def test_recording_retention_days_can_be_disabled():
    """Setting retention to 0 disables cleanup."""
    cfg = AppConfig.from_dict({"recording_retention_days": 0})
    assert cfg.recording_retention_days == 0


def test_recording_retention_roundtrips_through_dict():
    """to_dict / from_dict should preserve the new field."""
    cfg = AppConfig.from_dict({"recording_retention_days": 7})
    assert cfg.recording_retention_days == 7
    d = cfg.to_dict()
    assert d["recording_retention_days"] == 7
    cfg2 = AppConfig.from_dict(d)
    assert cfg2.recording_retention_days == 7


def test_terminal_file_browser_defaults_to_empty():
    """The terminal_file_browser field (mathstuf #8) defaults to empty string."""
    cfg = AppConfig()
    assert cfg.terminal_file_browser == ""


def test_unknown_keys_in_from_dict_are_ignored():
    """Old/unknown config keys should not crash from_dict."""
    cfg = AppConfig.from_dict(
        {
            "ai_provider": "anthropic",
            "future_field_we_dont_know_about": "value",
        }
    )
    assert cfg.ai_provider == "anthropic"


def test_load_config_fails_loudly_on_invalid_yaml(tmp_path, monkeypatch):
    from meeting_notes.config import load_config

    config_dir = tmp_path / "meeting-notes"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("ai_provider: [", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="Could not load config"):
        load_config()


def test_load_config_fails_loudly_on_non_mapping_yaml(tmp_path, monkeypatch):
    from meeting_notes.config import load_config

    config_dir = tmp_path / "meeting-notes"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("- ai_provider\n- anthropic\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="Config data must be a mapping"):
        load_config()


def test_default_provider_is_anthropic():
    """Sanity check: the default cloud provider hasn't drifted."""
    cfg = AppConfig()
    assert cfg.ai_provider == "anthropic"
    assert cfg.ai_model == "sonnet"


def test_validate_rejects_unknown_provider():
    cfg = AppConfig(ai_provider="not-a-real-provider")
    ok, err = validate_config(cfg)
    assert not ok
    assert "ai_provider" in err.lower()


def test_validate_rejects_invalid_anthropic_model():
    cfg = AppConfig(ai_provider="anthropic", ai_model="not-a-real-model", anthropic_api_key="sk-ant-test")
    ok, err = validate_config(cfg)
    assert not ok
    assert "ai_model" in err.lower()


def test_validate_accepts_valid_anthropic_config():
    cfg = AppConfig(ai_provider="anthropic", ai_model="sonnet", anthropic_api_key="sk-ant-test")
    ok, err = validate_config(cfg)
    assert ok, f"expected valid, got error: {err}"


def test_config_validation_uses_canonical_provider_catalog():
    assert set(PROVIDERS) == {"openai", "anthropic", "openrouter", "local", "none"}

    cfg = AppConfig(ai_provider="openai", ai_model="standard", openai_api_key="sk-test")
    ok, err = validate_config(cfg)

    assert ok, err


def test_configured_api_key_prefers_config_over_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    cfg = AppConfig(ai_provider="openai", openai_api_key="config-key")

    assert configured_api_key(cfg, "openai") == "config-key"


def test_validate_none_provider_is_always_valid():
    """ai_provider='none' should validate without any keys."""
    cfg = AppConfig(ai_provider="none")
    ok, err = validate_config(cfg)
    assert ok, err


def test_validate_accepts_legacy_openrouter_aliases():
    for legacy_model in ("cheap", "balanced", "premium"):
        cfg = AppConfig(ai_provider="openrouter", ai_model=legacy_model, openrouter_api_key="sk-or-test")
        ok, err = validate_config(cfg)
        assert ok, err


def test_to_safe_dict_redacts_keys():
    """API keys must be redacted in the safe dict (used for logging)."""
    cfg = AppConfig(
        anthropic_api_key="sk-ant-supersecretkey12345",
        openai_api_key="sk-openaikey9876543210",
        openrouter_api_key="orkey1234567890",
    )
    safe = cfg.to_safe_dict()
    assert "supersecretkey" not in safe["anthropic_api_key"]
    assert "openaikey" not in safe["openai_api_key"]
    assert "1234567890" not in safe["openrouter_api_key"]
