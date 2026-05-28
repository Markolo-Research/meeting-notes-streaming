"""Unit tests for the Parakeet streaming client + recorder.

These tests don't require a running ASR server: they stand up a tiny
in-process Unix-socket server that mimics the wire protocol (newline-
delimited JSON with `ready`, `partial`, `final`, `closed` messages).
"""

import array
import io
import json
import socket
import threading
import time
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from meeting_notes.app import _tokens_with_times
from meeting_notes.parakeet_stream import (
    ParakeetStreamClient,
    Partial,
    StreamingAudioRecorder,
    _deinterleave_stereo_s16le,
)


def test_capture_cmd_modes(tmp_path, monkeypatch):
    """Each mode yields a single ffmpeg invocation with the right inputs."""
    monkeypatch.setattr(
        "meeting_notes.parakeet_stream.StreamingAudioRecorder._default_sink_monitor",
        staticmethod(lambda: "fake-sink.monitor"),
    )

    rec_mic = StreamingAudioRecorder(output_dir=str(tmp_path), mode="mic")
    cmd = rec_mic._build_capture_cmd()
    assert cmd[0] == "ffmpeg"
    assert "default" in cmd and "fake-sink.monitor" not in cmd
    assert "-filter_complex" not in cmd

    rec_sys = StreamingAudioRecorder(output_dir=str(tmp_path), mode="system")
    cmd = rec_sys._build_capture_cmd()
    assert "fake-sink.monitor" in cmd and "default" not in cmd[:cmd.index("fake-sink.monitor")]
    assert "-filter_complex" not in cmd

    rec_comb = StreamingAudioRecorder(output_dir=str(tmp_path), mode="combined")
    cmd = rec_comb._build_capture_cmd()
    assert "default" in cmd and "fake-sink.monitor" in cmd
    assert "-filter_complex" in cmd
    filt = cmd[cmd.index("-filter_complex") + 1]
    # Combined mode now emits stereo with mic on L, system on R so each
    # channel can be transcribed independently. The filter uses join, not amix.
    assert "join=inputs=2:channel_layout=stereo" in filt
    # And the output is stereo s16le.
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "2"


def test_deinterleave_stereo_s16le():
    """L/R samples come out in the right channels."""
    # Two frames: L=0x1234, R=0x5678; then L=0x0001, R=0xfffe (treated as signed below)
    stereo = bytes([0x34, 0x12, 0x78, 0x56, 0x01, 0x00, 0xfe, 0xff])
    left, right = _deinterleave_stereo_s16le(stereo)
    assert left == bytes([0x34, 0x12, 0x01, 0x00])
    assert right == bytes([0x78, 0x56, 0xfe, 0xff])
    # An odd trailing partial frame is dropped, not raised.
    left2, right2 = _deinterleave_stereo_s16le(stereo + b"\xaa")
    assert (left2, right2) == (left, right)


def test_tokens_with_times_handles_long_growing_partial_timeline():
    """Long meetings emit many cumulative partials; timestamping must stay linear."""
    tokens = [f"word{i}" for i in range(2500)]
    partials = [
        Partial(elapsed_s=float(i), text=" ".join(tokens[: i + 1]))
        for i in range(len(tokens))
    ]

    mapped_tokens, times = _tokens_with_times(partials, " ".join(tokens))

    assert mapped_tokens == tokens
    assert times[:3] == [0.0, 1.0, 2.0]
    assert times[-1] == float(len(tokens) - 1)


def test_tokens_with_times_tolerates_partial_whitespace_differences():
    partials = [
        Partial(elapsed_s=1.0, text=" hello"),
        Partial(elapsed_s=2.0, text=" hello  world"),
        Partial(elapsed_s=3.0, text=" hello  world again"),
    ]

    mapped_tokens, times = _tokens_with_times(partials, "hello world again")

    assert mapped_tokens == ["hello", "world", "again"]
    assert times == [1.0, 2.0, 3.0]


def test_tokens_with_times_handles_revised_partial_prefix():
    partials = [
        Partial(elapsed_s=1.0, text="hello word"),
        Partial(elapsed_s=2.0, text="hello world"),
        Partial(elapsed_s=3.0, text="hello world again"),
    ]

    mapped_tokens, times = _tokens_with_times(partials, "hello world again")

    assert mapped_tokens == ["hello", "world", "again"]
    assert times == [1.0, 2.0, 3.0]


def test_tokens_with_times_recovers_after_non_prefix_correction():
    partials = [
        Partial(elapsed_s=1.0, text="hello"),
        Partial(elapsed_s=2.0, text="noise"),
        Partial(elapsed_s=3.0, text="hello world"),
        Partial(elapsed_s=4.0, text="hello world again"),
    ]

    mapped_tokens, times = _tokens_with_times(partials, "hello world again")

    assert mapped_tokens == ["hello", "world", "again"]
    assert times == [1.0, 3.0, 4.0]


def test_tokens_with_times_recovers_after_shorter_partial_correction():
    partials = [
        Partial(elapsed_s=3.0, text="a b c"),
        Partial(elapsed_s=4.0, text="a b"),
        Partial(elapsed_s=5.0, text="a b c d"),
        Partial(elapsed_s=6.0, text="a b c d e"),
    ]

    mapped_tokens, times = _tokens_with_times(partials, "a b c d e")

    assert mapped_tokens == ["a", "b", "c", "d", "e"]
    assert times == [3.0, 3.0, 3.0, 5.0, 6.0]


@pytest.fixture
def fake_server(tmp_path):
    """Spin up a fake Parakeet server on a temp Unix socket.

    The server reads all incoming PCM, emits two partials, then emits a
    final + closed status when the client half-closes write.
    """
    socket_path = str(tmp_path / "fake.sock")
    received = bytearray()
    ready = threading.Event()

    def serve():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(socket_path)
        srv.listen(1)
        srv.settimeout(5)
        ready.set()
        conn, _ = srv.accept()
        try:
            conn.sendall(
                (json.dumps({"status": "ready", "sample_rate": 16000,
                             "chunk_ms": 160, "latency_ms": 560}) + "\n").encode()
            )
            # Emit two partials as bytes arrive
            partial_count = 0
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
                partial_count += 1
                if partial_count <= 2:
                    conn.sendall(
                        (json.dumps({"partial": f"partial-{partial_count}"}) + "\n").encode()
                    )
            conn.sendall(
                (json.dumps({"final": "the final text", "duration_s": 1.23}) + "\n").encode()
            )
            conn.sendall(
                (json.dumps({"status": "closed", "backup": "/tmp/x.wav"}) + "\n").encode()
            )
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ready.wait(timeout=2)
    yield socket_path, received
    t.join(timeout=2)


def test_client_round_trip(fake_server):
    socket_path, received = fake_server
    seen: list[str] = []
    client = ParakeetStreamClient(socket_path=socket_path, on_partial=seen.append)
    client.connect()
    client.send_pcm(b"\x00\x00" * 1600)  # 100 ms silence @ 16 kHz s16le
    client.send_pcm(b"\x00\x01" * 1600)
    final = client.finish(timeout=5)
    client.close()

    assert final == "the final text"
    assert client.duration_s == pytest.approx(1.23)
    assert seen == ["partial-1", "partial-2"]
    assert len(received) == 6400  # two 100-ms chunks


def test_streaming_recorder_tees_to_wav_and_socket(fake_server, tmp_path):
    """The recorder should write the captured PCM to its wav backup and
    forward the same bytes to the streaming server."""
    socket_path, received = fake_server

    # Synthesize 0.5s of dummy PCM and stand in for pw-record
    pcm = (b"\x00\x10" * 8000)  # 0.5s @ 16 kHz s16le

    class FakeProc:
        def __init__(self, data: bytes):
            self.stdout = io.BytesIO(data)
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def send_signal(self, sig):
            self._alive = False

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            self._alive = False

        def kill(self):
            self._alive = False

    recorder = StreamingAudioRecorder(
        output_dir=str(tmp_path / "rec"),
        socket_path=socket_path,
    )
    with patch("meeting_notes.parakeet_stream.subprocess.Popen", return_value=FakeProc(pcm)):
        recorder.start_recording("test.wav")
        # Pump thread drains FakeProc.stdout quickly
        time.sleep(0.3)
        result = recorder.stop_recording()

    assert result.final_text == "the final text"
    assert Path(result.audio_path).exists()
    # Backup wav contains exactly the PCM we fed in (16-bit mono 16kHz)
    with wave.open(result.audio_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.readframes(wf.getnframes()) == pcm
    # Server received exactly the same bytes
    assert bytes(received) == pcm


@pytest.fixture
def dual_fake_server(tmp_path):
    """Spin up a fake Parakeet server that accepts two concurrent sessions.

    Each session gets its own thread and emits {ready, final, closed}.
    Bytes are appended to per-session buffers as they arrive so tests can
    observe them as soon as the recorder finishes — no need to wait for
    the server threads to fully unwind.
    """
    socket_path = str(tmp_path / "fake_dual.sock")
    sessions: list[bytearray] = [bytearray(), bytearray()]
    session_done = [threading.Event(), threading.Event()]
    next_idx = [0]
    next_idx_lock = threading.Lock()
    ready = threading.Event()

    def session_loop(conn):
        with next_idx_lock:
            idx = next_idx[0]
            next_idx[0] += 1
        buf = sessions[idx]
        try:
            conn.sendall(
                (json.dumps({"status": "ready", "sample_rate": 16000,
                             "chunk_ms": 160, "latency_ms": 560}) + "\n").encode()
            )
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            conn.sendall(
                (json.dumps({"final": f"session-{idx}-final", "duration_s": 0.5}) + "\n").encode()
            )
            conn.sendall(
                (json.dumps({"status": "closed", "backup": "/tmp/x.wav"}) + "\n").encode()
            )
        finally:
            conn.close()
            session_done[idx].set()

    def serve():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(socket_path)
        srv.listen(4)
        srv.settimeout(5)
        ready.set()
        try:
            for _ in range(2):
                conn, _ = srv.accept()
                threading.Thread(target=session_loop, args=(conn,), daemon=True).start()
        finally:
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ready.wait(timeout=2)
    yield socket_path, sessions[0], sessions[1], session_done
    t.join(timeout=4)


def test_combined_mode_splits_into_two_streams(dual_fake_server, tmp_path):
    """Combined mode: ffmpeg emits stereo, recorder routes L→mic-session,
    R→system-session, and writes a stereo wav backup."""
    socket_path, mic_received, sys_received, session_done = dual_fake_server

    # Build interleaved stereo PCM with clearly distinct L/R streams.
    n_frames = 4000  # 0.25s @ 16 kHz
    arr = array.array("h")
    for i in range(n_frames):
        arr.append(i % 1000 + 1)        # left / mic: positive
        arr.append(-(i % 1000) - 1)     # right / sys: negative
    stereo_pcm = arr.tobytes()  # 4 bytes/frame

    class FakeProc:
        def __init__(self, data: bytes):
            self.stdout = io.BytesIO(data)
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def send_signal(self, sig):
            self._alive = False

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            self._alive = False

        def kill(self):
            self._alive = False

    recorder = StreamingAudioRecorder(
        output_dir=str(tmp_path / "rec_dual"),
        socket_path=socket_path,
        mode="combined",
    )
    # Skip the pactl probe — we don't actually use the sink name in this test.
    recorder._default_sink_monitor = staticmethod(lambda: "fake.monitor")
    with patch("meeting_notes.parakeet_stream.subprocess.Popen", return_value=FakeProc(stereo_pcm)):
        recorder.start_recording("dual.wav")
        time.sleep(0.4)
        result = recorder.stop_recording()

    # Wait for both server-side sessions to drain (recv'd all bytes + sent final).
    assert session_done[0].wait(timeout=5)
    assert session_done[1].wait(timeout=5)

    # Both ASR sessions return a final. Order isn't guaranteed (which connection
    # arrives first is a race), so we just check both are present.
    finals = {result.final_text, result.secondary_final}
    assert finals == {"session-0-final", "session-1-final"}

    # Wav backup is stereo and contains the bytes we fed in.
    with wave.open(result.audio_path, "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.readframes(wf.getnframes()) == stereo_pcm

    # The two sessions together received exactly the mono mic stream and the
    # mono system stream that the deinterleaver would produce.
    expected_left = array.array("h", arr[0::2]).tobytes()
    expected_right = array.array("h", arr[1::2]).tobytes()
    received = {bytes(mic_received), bytes(sys_received)}
    assert received == {expected_left, expected_right}
