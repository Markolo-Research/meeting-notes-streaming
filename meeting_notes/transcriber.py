"""Transcription backends for recorded meeting audio."""

import json
import shlex
import shutil
import subprocess
import whisper
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class TranscriptSegment:
    """A segment of transcribed text with timing information."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """Complete transcription result."""

    text: str
    segments: list[TranscriptSegment]
    language: str
    duration: float


class WhisperTranscriber:
    """Transcribe audio files using Whisper."""

    def __init__(self, model_name: str = "base"):
        """Initialize the transcriber.

        Args:
            model_name: Whisper model to use (tiny, base, small, medium, large)
        """
        logger.info(f"Initializing WhisperTranscriber (model: {model_name})")
        self.model_name = model_name
        self.model: Optional[whisper.Whisper] = None

    def load_model(self):
        """Load the Whisper model (lazy loading)."""
        if self.model is None:
            logger.info(f"Loading Whisper {self.model_name} model...")
            self.model = whisper.load_model(self.model_name)
            logger.info("Whisper model loaded successfully")

    def transcribe(
        self, audio_path: str, progress_callback: Optional[Callable[[float], None]] = None
    ) -> TranscriptResult:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio file
            progress_callback: Optional callback function for progress updates (0.0 to 1.0)

        Returns:
            TranscriptResult with text and segments
        """
        logger.info(f"Starting transcription: {audio_path}")
        self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_file}")
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Transcribing {audio_file.name} ({file_size_mb:.1f} MB)...")

        # Transcribe with word-level timestamps
        if self.model is None:
            logger.error("Model not loaded")
            raise RuntimeError("Model not loaded")

        result = self.model.transcribe(
            str(audio_file),
            language=None,  # Auto-detect
            task="transcribe",
            verbose=False,
        )

        # Convert segments to our format
        segments = [
            TranscriptSegment(start=seg["start"], end=seg["end"], text=seg["text"].strip())
            for seg in result["segments"]
        ]

        # Calculate duration from last segment
        duration = segments[-1].end if segments else 0.0

        logger.info(
            f"Transcription complete: {len(segments)} segments, {duration:.1f}s duration, language: {result.get('language', 'unknown')}"
        )

        return TranscriptResult(
            text=result["text"].strip(),
            segments=segments,
            language=result.get("language", "unknown"),
            duration=duration,
        )

    def format_transcript_with_timestamps(self, result: TranscriptResult) -> str:
        """Format transcript with timestamps for each segment.

        Args:
            result: TranscriptResult to format

        Returns:
            Formatted transcript string
        """
        lines = []
        for seg in result.segments:
            timestamp = self._format_timestamp(seg.start)
            lines.append(f"**[{timestamp}]** {seg.text}")

        return "\n\n".join(lines)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"


class ParakeetCppTranscriber:
    """Transcribe audio files using mudler/parakeet.cpp's GGUF CLI."""

    def __init__(
        self,
        cli: str = "parakeet-cli",
        model_path: str = "~/.local/share/parakeet.cpp/models/tdt_ctc-110m-q8_0.gguf",
        threads: int = 8,
        extra_args: str = "",
    ):
        logger.info(f"Initializing ParakeetCppTranscriber (model: {model_path})")
        self.cli = cli
        self.model_path = str(Path(model_path).expanduser())
        self.threads = threads
        self.extra_args = extra_args

    def load_model(self):
        """Validate CLI/model availability. parakeet.cpp loads per transcription."""
        if shutil.which(self.cli) is None:
            raise FileNotFoundError(f"{self.cli} not found on PATH")
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"parakeet.cpp model not found: {self.model_path}")

    def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> TranscriptResult:
        del progress_callback
        self.load_model()
        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_file}")
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        cmd = [
            self.cli,
            "transcribe",
            "--model",
            self.model_path,
            "--input",
            str(audio_file),
            "--json",
        ]
        if self.threads > 0:
            cmd.extend(["--threads", str(self.threads)])
        if self.extra_args:
            cmd.extend(shlex.split(self.extra_args))

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Transcribing {audio_file.name} ({file_size_mb:.1f} MB) with parakeet.cpp...")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"parakeet.cpp transcription failed: {stderr}")

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"parakeet.cpp returned invalid JSON: {proc.stdout[:200]!r}") from exc

        text = str(payload.get("text", "")).strip()
        words = payload.get("words") or []
        segments = self._segments_from_words(words, text)
        duration = segments[-1].end if segments else 0.0
        logger.info(f"parakeet.cpp transcription complete: {len(segments)} segments, {duration:.1f}s duration")
        return TranscriptResult(
            text=text,
            segments=segments,
            language="unknown",
            duration=duration,
        )

    @staticmethod
    def _segments_from_words(words: list[dict], fallback_text: str) -> list[TranscriptSegment]:
        """Group word timestamps into readable transcript chunks."""
        if not words:
            return [TranscriptSegment(start=0.0, end=0.0, text=fallback_text)] if fallback_text else []

        segments: list[TranscriptSegment] = []
        current: list[str] = []
        start = float(words[0].get("start", 0.0) or 0.0)
        end = start

        for word in words:
            token = str(word.get("w", "")).strip()
            if not token:
                continue
            word_start = float(word.get("start", end) or end)
            word_end = float(word.get("end", word_start) or word_start)
            if current and (word_start - start >= 15.0 or current[-1].endswith((".", "?", "!"))):
                segments.append(TranscriptSegment(start=start, end=end, text=" ".join(current)))
                current = []
                start = word_start
            current.append(token)
            end = word_end

        if current:
            segments.append(TranscriptSegment(start=start, end=end, text=" ".join(current)))
        return segments


if __name__ == "__main__":
    # Simple test
    import sys

    if len(sys.argv) < 2:
        print("Usage: uv run -m meeting_notes.transcriber <audio_file>")
        sys.exit(1)

    transcriber = WhisperTranscriber()
    result = transcriber.transcribe(sys.argv[1])

    print(f"\nLanguage: {result.language}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"\nTranscript:\n{result.text}")
    print(f"\nWith timestamps:\n{transcriber.format_transcript_with_timestamps(result)}")
