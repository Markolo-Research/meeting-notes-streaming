from datetime import datetime, timedelta
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


def cleanup_old_recordings(recordings_dir: Path, retention_days: int) -> None:
    """Delete WAV recordings older than the configured retention window."""
    if retention_days <= 0 or not recordings_dir.is_dir():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    for wav_file in recordings_dir.glob("*.wav"):
        try:
            if datetime.fromtimestamp(wav_file.stat().st_mtime) < cutoff:
                logger.info("Removing old recording: %s (older than %s days)", wav_file.name, retention_days)
                wav_file.unlink()
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", wav_file.name, exc)
