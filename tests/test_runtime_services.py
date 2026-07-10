from meeting_notes.config import AppConfig
from meeting_notes.runtime_services import build_runtime_services


def test_runtime_services_compose_disabled_ai_without_ui(tmp_path):
    config = AppConfig(ai_provider="none", whisper_model="tiny")
    services = build_runtime_services(config, tmp_path / "notes", tmp_path / "transcripts")

    assert services.transcriber.model_name == "tiny"
    assert services.note_maker.ai_provider == "none"
    assert services.note_maker.summarizer is None
