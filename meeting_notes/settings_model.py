"""UI-independent settings update and validation model."""

from collections.abc import Mapping
from dataclasses import dataclass

from .config import AppConfig, validate_config


@dataclass(frozen=True)
class SettingsUpdate:
    config: AppConfig | None
    error: str | None


def prepare_settings_update(current: Mapping[str, object], fields: Mapping[str, str]) -> SettingsUpdate:
    values = dict(current)
    values.update({key: value.strip() for key, value in fields.items()})
    config = AppConfig.from_dict(values)
    valid, error = validate_config(config)
    return SettingsUpdate(config if valid else None, None if valid else error)
