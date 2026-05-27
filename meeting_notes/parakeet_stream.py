"""Client + recorder for the streaming Parakeet ASR server.

The server (parakeet-stream-server) listens on a Unix socket, accepts raw
PCM s16le 16kHz mono, and emits newline-delimited JSON messages:

    {"status": "ready", "sample_rate": 16000, "chunk_ms": 160, "latency_ms": 560}
    {"partial": "text so far"}      # one per decoded chunk
    {"final": "...", "duration_s": 12.34}
    {"status": "closed", "backup": "/path/to/<ts>.wav"}

The recorder runs `pw-record`/`parec` at 16kHz mono, tees every chunk to
both the streaming client and a local wav backup, and exposes the
final transcript plus a list of partials with timestamps.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)

DEFAULT_SOCKET = "/tmp/parakeet-stream.sock"
SAMPLE_RATE = 16000
PUMP_CHUNK_BYTES = 3200  # 100 ms @ 16 kHz mono s16le


@dataclass
class Partial:
    elapsed_s: float
    text: str


@dataclass
class StreamResult:
    audio_path: str
    final_text: Optional[str]
    partials: list[Partial] = field(default_factory=list)
    duration_s: float = 0.0


class ParakeetStreamClient:
    """Minimal client for the Parakeet streaming server (Unix socket)."""

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET,
        on_partial: Optional[Callable[[str], None]] = None,
    ):
        self.socket_path = socket_path
        self.on_partial = on_partial
        self.sock: Optional[socket.socket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._closed = False
        self._final_event = threading.Event()
        self.partials: list[Partial] = []
        self.final_text: Optional[str] = None
        self.duration_s: float = 0.0
        self._start_time: float = 0.0

    def connect(self, timeout: float = 5.0) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(self.socket_path)
        sock.settimeout(120)
        self.sock = sock
        self._start_time = time.time()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def _recv_loop(self) -> None:
        assert self.sock is not None
        rx = b""
        try:
            while True:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError as exc:
                    # EBADF after close() during a normal shutdown — benign
                    if self._closed:
                        break
                    logger.warning(f"parakeet recv loop ended: {exc}")
                    break
                if not chunk:
                    break
                rx += chunk
                while b"\n" in rx:
                    line, rx = rx.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(f"parakeet: bad json: {line!r}")
                        continue
                    self._handle_message(msg)
        finally:
            self._final_event.set()

    def _handle_message(self, msg: dict) -> None:
        if "partial" in msg:
            text = msg["partial"]
            elapsed = time.time() - self._start_time
            self.partials.append(Partial(elapsed_s=elapsed, text=text))
            if self.on_partial is not None:
                try:
                    self.on_partial(text)
                except Exception as exc:
                    logger.warning(f"on_partial callback raised: {exc}")
        elif "final" in msg:
            self.final_text = msg["final"]
            self.duration_s = float(msg.get("duration_s", 0.0))
            self._final_event.set()
        elif "error" in msg:
            logger.error(f"parakeet server error: {msg['error']}")

    def send_pcm(self, data: bytes) -> bool:
        if self.sock is None or self._closed:
            return False
        try:
            self.sock.sendall(data)
            return True
        except OSError as exc:
            logger.error(f"parakeet send failed: {exc}")
            self._closed = True
            return False

    def finish(self, timeout: float = 60.0) -> Optional[str]:
        """Half-close write side and wait for the FINAL message."""
        self._closed = True
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        if self._final_event.wait(timeout):
            return self.final_text
        logger.warning("parakeet finish: timed out waiting for final transcript")
        return None

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def ensure_server_running(
    socket_path: str = DEFAULT_SOCKET,
    launcher: str = "parakeet-stream-server",
    boot_timeout: float = 45.0,
) -> Optional[subprocess.Popen]:
    """Make sure the streaming server is reachable.

    If the socket already accepts connections, return None (we don't own the
    server). Otherwise fork `launcher` and wait for the socket to appear.
    Returns the spawned Popen, or None if nothing was spawned.
    """
    if _socket_accepts(socket_path):
        return None
    if not shutil.which(launcher):
        raise FileNotFoundError(
            f"{launcher} not on PATH; install or set parakeet_socket"
        )
    logger.info(f"Starting {launcher} (cold start, ~5s model load)")
    proc = subprocess.Popen(
        [launcher, "--foreground"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + boot_timeout
    while time.time() < deadline:
        if _socket_accepts(socket_path):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                f"{launcher} exited with status {proc.returncode} before socket appeared"
            )
        time.sleep(0.5)
    proc.terminate()
    raise TimeoutError(f"{launcher} did not open {socket_path} within {boot_timeout}s")


def _socket_accepts(path: str) -> bool:
    if not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


class StreamingAudioRecorder:
    """Tee-to-wav-and-socket recorder for the Parakeet streaming server.

    Uses a single ffmpeg subprocess to capture audio (mic, system, or both
    mixed live) and emit s16le 16 kHz mono PCM on stdout. The pump thread
    forwards every chunk to both a local wav backup and the streaming
    server, so a server crash mid-meeting still leaves a recoverable wav.
    """

    def __init__(
        self,
        output_dir: str = "recordings",
        socket_path: str = DEFAULT_SOCKET,
        mode: str = "mic",
        dev_mode: bool = False,
    ):
        if mode not in ("mic", "system", "combined"):
            raise ValueError(f"Invalid mode: {mode!r}")
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.socket_path = socket_path
        self.dev_mode = dev_mode
        self.mode = mode

        self.current_file: Optional[Path] = None
        self.process: Optional[subprocess.Popen] = None
        self.client: Optional[ParakeetStreamClient] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._wav: Optional[wave.Wave_write] = None
        self._stop_pump = threading.Event()
        self._on_partial: Optional[Callable[[str], None]] = None

    def set_on_partial(self, cb: Optional[Callable[[str], None]]) -> None:
        self._on_partial = cb

    def start_recording(self, filename: Optional[str] = None) -> str:
        if self.is_recording():
            raise RuntimeError("Already recording")

        if filename is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            filename = f"{timestamp}.wav"
        self.current_file = self.output_dir / filename
        logger.info(f"Streaming recording to: {self.current_file}")

        self._wav = wave.open(str(self.current_file), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

        self.client = ParakeetStreamClient(self.socket_path, on_partial=self._on_partial)
        self.client.connect()

        cmd = self._build_capture_cmd()
        logger.debug(f"Capture cmd: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._stop_pump.clear()
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()
        return str(self.current_file)

    def _build_capture_cmd(self) -> list[str]:
        """Single ffmpeg invocation that captures + mixes + downsamples to
        s16le 16 kHz mono on stdout. Mode controls which inputs are wired."""
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found on PATH")

        common_out = [
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-f", "s16le",
            "-loglevel", "warning",
            "pipe:1",
        ]

        if self.mode == "mic":
            return [
                "ffmpeg", "-hide_banner", "-nostdin",
                "-f", "pulse", "-i", "default",
                *common_out,
            ]

        monitor = self._default_sink_monitor()
        if self.mode == "system":
            return [
                "ffmpeg", "-hide_banner", "-nostdin",
                "-f", "pulse", "-i", monitor,
                *common_out,
            ]

        # combined: mix mic + system live with the same filter graph the
        # whisper-mode post-mix uses (volume=2.0 each, amix, no normalize).
        return [
            "ffmpeg", "-hide_banner", "-nostdin",
            "-f", "pulse", "-i", "default",
            "-f", "pulse", "-i", monitor,
            "-filter_complex",
            "[0:a]volume=2.0[a0];[1:a]volume=2.0[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0[out]",
            "-map", "[out]",
            *common_out,
        ]

    @staticmethod
    def _default_sink_monitor() -> str:
        """Return the monitor source name for the default PulseAudio sink.

        Falls back to '@DEFAULT_MONITOR@' which PulseAudio resolves at
        connect time if pactl isn't available for some reason.
        """
        try:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=2,
            )
            sink = result.stdout.strip()
            if result.returncode == 0 and sink:
                return f"{sink}.monitor"
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"pactl get-default-sink failed: {exc}")
        return "@DEFAULT_MONITOR@"

    def _pump(self) -> None:
        assert self.process is not None and self._wav is not None and self.client is not None
        stdout = self.process.stdout
        while not self._stop_pump.is_set():
            try:
                data = stdout.read(PUMP_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not data:
                break
            self._wav.writeframesraw(data)
            self.client.send_pcm(data)

    def stop_recording(self) -> StreamResult:
        if not self.is_recording():
            raise RuntimeError("Not currently recording")
        assert self.process is not None and self._wav is not None and self.client is not None

        try:
            self.process.send_signal(signal.SIGINT)
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Capture process did not exit on SIGINT; terminating")
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

        self._stop_pump.set()
        if self._pump_thread:
            self._pump_thread.join(timeout=3)

        try:
            tail = self.process.stdout.read()
        except (OSError, ValueError):
            tail = b""
        if tail:
            self._wav.writeframesraw(tail)
            self.client.send_pcm(tail)

        self._wav.close()
        audio_path = str(self.current_file)

        final_text = self.client.finish(timeout=60)
        partials = list(self.client.partials)
        duration_s = self.client.duration_s
        self.client.close()

        self.process = None
        self.client = None
        self._pump_thread = None
        self._wav = None
        self.current_file = None

        return StreamResult(
            audio_path=audio_path,
            final_text=final_text,
            partials=partials,
            duration_s=duration_s,
        )

    def is_recording(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def get_recording_path(self) -> Optional[str]:
        return str(self.current_file) if self.current_file else None

    def get_audio_device_info(self) -> dict[str, str]:
        info: dict[str, str] = {"mode": self.mode, "backend": "parakeet-stream"}
        try:
            if self.mode in ("mic", "combined"):
                result = subprocess.run(
                    ["pactl", "get-default-source"],
                    capture_output=True, text=True, timeout=2,
                )
                info["mic_device"] = (
                    result.stdout.strip() if result.returncode == 0 else "System default"
                ) or "System default"
            if self.mode in ("system", "combined"):
                info["system_device"] = self._default_sink_monitor()
        except (OSError, subprocess.SubprocessError):
            if "mic_device" not in info and self.mode in ("mic", "combined"):
                info["mic_device"] = "System default"
            if "system_device" not in info and self.mode in ("system", "combined"):
                info["system_device"] = "@DEFAULT_MONITOR@"
        return info
