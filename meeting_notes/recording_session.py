"""Provider- and UI-independent recording session state."""

from dataclasses import dataclass


@dataclass
class RecordingSession:
    started_at: float | None = None

    @property
    def active(self) -> bool:
        return self.started_at is not None

    def start(self, now: float) -> None:
        if self.active:
            raise RuntimeError("recording session is already active")
        self.started_at = now

    def stop(self) -> None:
        self.started_at = None

    def elapsed_seconds(self, now: float) -> int:
        return max(0, int(now - self.started_at)) if self.started_at is not None else 0
