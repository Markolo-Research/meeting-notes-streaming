"""Provider-neutral application port for meeting summarization."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class MeetingSummary:
    overview: str
    key_points: list[str]
    action_items: list[str]
    decisions: list[str]
    participants: list[str]


class Summarizer(Protocol):
    """Capability required by the note workflow, independent of provider."""

    @property
    def display_label(self) -> str: ...

    @property
    def provider_id(self) -> str: ...

    def summarize(self, transcript: str, user_notes: str = "") -> MeetingSummary: ...
