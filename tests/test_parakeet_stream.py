"""Unit tests for the Parakeet streaming client + recorder.

These tests don't require a running ASR server: they stand up a tiny
in-process Unix-socket server that mimics the wire protocol (newline-
delimited JSON with `ready`, `partial`, `final`, `closed` messages).
"""

import io
import json
import socket
import threading
import time
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from meeting_notes.parakeet_stream import (
    ParakeetStreamClient,
    StreamingAudioRecorder,
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
    assert "amix=inputs=2" in filt


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
