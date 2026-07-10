from meeting_notes.config import AppConfig
from meeting_notes.settings_model import prepare_settings_update


def test_settings_update_trims_fields_without_mutating_current():
    current = AppConfig(ai_provider="none").to_dict()
    before = dict(current)
    update = prepare_settings_update(current, {"editor": "  nvim  "})
    assert update.error is None
    assert update.config is not None and update.config.editor == "nvim"
    assert current == before


def test_settings_update_returns_validation_error():
    current = AppConfig(ai_provider="none").to_dict()
    update = prepare_settings_update(current, {"ai_provider": "invalid"})
    assert update.config is None
    assert "Invalid ai_provider" in (update.error or "")
