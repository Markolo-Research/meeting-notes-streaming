"""Application bootstrap for recording/transcription/note services."""

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, configured_api_key
from .note_maker import NoteMaker
from .summarizer_factory import create_summarizer
from .transcriber import WhisperTranscriber


@dataclass(frozen=True)
class RuntimeServices:
    """Long-lived workflow services composed outside the Textual UI."""

    transcriber: WhisperTranscriber
    note_maker: NoteMaker


def build_runtime_services(config: AppConfig, notes_dir: Path, transcripts_dir: Path) -> RuntimeServices:
    summarizer = create_summarizer(
        config.ai_provider,
        config.ai_model,
        configured_api_key(config, config.ai_provider),
    )
    return RuntimeServices(
        transcriber=WhisperTranscriber(config.whisper_model),
        note_maker=NoteMaker(
            output_dir=notes_dir,
            transcripts_dir=transcripts_dir,
            ai_provider=config.ai_provider,
            summarizer=summarizer,
        ),
    )
