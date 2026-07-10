import pytest

from meeting_notes.recording_session import RecordingSession


def test_recording_session_lifecycle_and_elapsed_time():
    session = RecordingSession()
    assert not session.active
    session.start(100.0)
    assert session.active
    assert session.elapsed_seconds(112.9) == 12
    with pytest.raises(RuntimeError, match="already active"):
        session.start(113.0)
    session.stop()
    assert session.elapsed_seconds(200.0) == 0
