"""Configuration management for Meeting Notes."""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict

from .logger import get_logger
from .ai_models import PROVIDERS, configured_api_key

logger = get_logger(__name__)


@dataclass
class AppConfig:
    """Application configuration."""

    # AI Summarization
    ai_provider: str = "anthropic"  # "openai", "anthropic", "openrouter", "local", or "none"
    ai_model: str = "haiku"  # Model tier (varies by provider)

    # API Keys (or set environment variables)
    openai_api_key: str = ""  # OPENAI_API_KEY
    anthropic_api_key: str = ""  # ANTHROPIC_API_KEY
    openrouter_api_key: str = ""  # OPENROUTER_API_KEY

    # Legacy (kept for backwards compatibility)
    ollama_model: str = "llama3.2:3b"

    # Other settings
    whisper_model: str = "base"
    notes_dir: str = "notes"
    recordings_dir: str = "recordings"
    transcripts_dir: str = "transcripts"
    editor: str = "nvim"
    terminal_file_browser: str = ""  # Terminal file browser (ranger, vidir, nnn, lf, vifm, yazi, etc.)
    recording_mode: str = "combined"
    recording_retention_days: int = 30  # Auto-delete .wav files older than this on startup (0 to disable)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return asdict(self)

    def _redact_key(self, key: str) -> str:
        """Redact API key for logging."""
        if not key or len(key) < 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def to_safe_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary with redacted API keys (safe for logging)."""
        data = self.to_dict()
        # Redact sensitive keys
        if data.get("openai_api_key"):
            data["openai_api_key"] = self._redact_key(data["openai_api_key"])
        if data.get("anthropic_api_key"):
            data["anthropic_api_key"] = self._redact_key(data["anthropic_api_key"])
        if data.get("openrouter_api_key"):
            data["openrouter_api_key"] = self._redact_key(data["openrouter_api_key"])
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Create config from dictionary."""
        # Filter out any unknown keys
        valid_keys = {field for field in cls.__dataclass_fields__}
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered_data)


def get_config_path() -> Path:
    """Get the path to the config file."""
    # Use XDG_CONFIG_HOME if set, otherwise ~/.config
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        config_dir = Path(config_home) / "meeting-notes"
    else:
        config_dir = Path.home() / ".config" / "meeting-notes"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.yaml"


def load_config() -> AppConfig:
    """Load configuration from file, or create default if not exists."""
    config_path = get_config_path()
    logger.info(f"Loading config from: {config_path}")

    if not config_path.exists():
        # First run - create default config
        logger.info("Config file not found, creating default config")
        config = AppConfig()
        save_config(config)
        return config

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            # Empty file
            logger.warning("Config file is empty, using defaults")
            return AppConfig()

        config = AppConfig.from_dict(data)
        logger.info(f"Config loaded successfully (ai_provider: {config.ai_provider}, ai_model: {config.ai_model})")
        return config

    except (OSError, TypeError, yaml.YAMLError) as e:
        logger.error(f"Could not load config from {config_path}: {e}", exc_info=True)
        raise RuntimeError(f"Could not load config from {config_path}: {e}") from e


def save_config(config: AppConfig) -> None:
    """Save configuration to file."""
    config_path = get_config_path()
    logger.info(f"Saving config to: {config_path}")

    try:
        with open(config_path, "w") as f:
            yaml.safe_dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)

        # Set restrictive permissions (user read/write only) to protect API keys
        import os
        import stat

        os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

        logger.info("Config saved successfully with secure permissions (600)")
    except Exception as e:
        logger.error(f"Failed to save config: {e}", exc_info=True)
        raise RuntimeError(f"Failed to save config: {e}")


def validate_config(config: AppConfig) -> tuple[bool, Optional[str]]:
    """
    Validate configuration values.

    Returns:
        (is_valid, error_message)
    """
    # Validate AI provider
    valid_providers = list(PROVIDERS)
    if config.ai_provider not in valid_providers:
        return False, f"Invalid ai_provider: {config.ai_provider}. Must be one of {valid_providers}"

    # Check for API keys based on provider
    provider_spec = PROVIDERS[config.ai_provider]
    if provider_spec.env_var:
        api_key = configured_api_key(config, config.ai_provider)
        if not api_key:
            return False, (
                f"ai_provider is '{config.ai_provider}' but no API key found.\n"
                f"Set {provider_spec.env_var} environment variable or {provider_spec.api_key_field} in config"
            )
    if provider_spec.models:
        valid_models = list(provider_spec.models)
        if config.ai_model not in valid_models:
            return (
                False,
                f"Invalid ai_model for {provider_spec.label}: {config.ai_model}. Must be one of {valid_models}",
            )

    # Validate whisper model
    valid_whisper = ["tiny", "base", "small", "medium", "large"]
    if config.whisper_model not in valid_whisper:
        return False, f"Invalid whisper_model: {config.whisper_model}. Must be one of {valid_whisper}"

    # Validate recording mode
    valid_modes = ["mic", "system", "combined"]
    if config.recording_mode not in valid_modes:
        return False, f"Invalid recording_mode: {config.recording_mode}. Must be one of {valid_modes}"

    # Validate directories exist or can be created (allow defaults to be auto-created)
    notes_path = Path(config.notes_dir).expanduser().absolute()
    if config.notes_dir != "notes":
        # Non-default paths must already exist
        if not notes_path.exists():
            return (
                False,
                f"Notes directory does not exist: {notes_path}\nPlease create it first or use a relative path like 'notes'",
            )
        if not notes_path.is_dir():
            return False, f"Notes path is not a directory: {notes_path}"

    rec_path = Path(config.recordings_dir).expanduser().absolute()
    if config.recordings_dir != "recordings":
        # Non-default paths must already exist
        if not rec_path.exists():
            return (
                False,
                f"Recordings directory does not exist: {rec_path}\nPlease create it first or use a relative path like 'recordings'",
            )
        if not rec_path.is_dir():
            return False, f"Recordings path is not a directory: {rec_path}"

    transcripts_path = Path(config.transcripts_dir).expanduser().absolute()
    if config.transcripts_dir != "transcripts":
        # Non-default paths must already exist
        if not transcripts_path.exists():
            return (
                False,
                f"Transcripts directory does not exist: {transcripts_path}\nPlease create it first or use a relative path like 'transcripts'",
            )
        if not transcripts_path.is_dir():
            return False, f"Transcripts path is not a directory: {transcripts_path}"

    return True, None


if __name__ == "__main__":
    # Test config loading/saving
    print(f"Config path: {get_config_path()}")

    config = load_config()
    print(f"Loaded config: {config}")

    valid, error = validate_config(config)
    if valid:
        print("✓ Config is valid")
    else:
        print(f"✗ Config error: {error}")
