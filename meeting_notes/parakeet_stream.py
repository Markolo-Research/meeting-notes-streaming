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

import array
import json
import os
import queue
import shutil
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
# ~30s worth of 100ms chunks. The streamer drops new chunks when full so the
# wav backup is never blocked by a slow/stalled socket.
STREAM_QUEUE_MAX = 300
FINISH_TIMEOUT_S = 15.0


def _deinterleave_stereo_s16le(data: bytes) -> tuple[bytes, bytes]:
    """Split interleaved s16le stereo PCM into (left_mono, right_mono).

    Each frame is 4 bytes: [L_lo L_hi R_lo R_hi]. Uses stdlib array for
    a C-level stride, so this stays cheap even at full sample rate.
    """
    if len(data) % 4:
        # Trim trailing partial frame; ffmpeg may flush an odd number on close.
        data = data[: len(data) - (len(data) % 4)]
    arr = array.array("h")
    arr.frombytes(data)
    left = array.array("h", arr[0::2]).tobytes()
    right = array.array("h", arr[1::2]).tobytes()
    return left, right


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
    # Combined mode only: separate ASR stream for the system-audio side.
    # `final_text`/`partials` cover the mic ("you"); these cover system ("them").
    secondary_final: Optional[str] = None
    secondary_partials: list[Partial] = field(default_factory=list)


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
        raise FileNotFoundError(f"{launcher} not on PATH; install or set parakeet_socket")
    log_dir = Path.home() / ".cache" / "parakeet-stream"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"
    logger.info(f"Starting {launcher} (cold start, ~5s model load); log → {log_path}")
    log_fp = log_path.open("ab")
    proc = subprocess.Popen(
        [launcher, "--foreground"],
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + boot_timeout
    while time.time() < deadline:
        if _socket_accepts(socket_path):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"{launcher} exited with status {proc.returncode} before socket appeared")
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
        # Primary client is the mic ("you") channel in combined mode; the
        # only channel in mic/system modes.
        self.client: Optional[ParakeetStreamClient] = None
        # Secondary client = system audio ("them"), only used in combined mode.
        self.secondary_client: Optional[ParakeetStreamClient] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._streamer_thread: Optional[threading.Thread] = None
        self._streamer_thread_secondary: Optional[threading.Thread] = None
        self._stream_queue: Optional[queue.Queue] = None
        self._stream_queue_secondary: Optional[queue.Queue] = None
        self._dropped_chunks = 0
        self._dropped_chunks_secondary = 0
        self._wav: Optional[wave.Wave_write] = None
        self._stop_pump = threading.Event()
        self._on_partial: Optional[Callable[[str], None]] = None
        self._on_partial_secondary: Optional[Callable[[str], None]] = None

    def set_on_partial(self, cb: Optional[Callable[[str], None]]) -> None:
        self._on_partial = cb

    def set_on_partial_secondary(self, cb: Optional[Callable[[str], None]]) -> None:
        """Callback for system-audio partials in combined mode ("them")."""
        self._on_partial_secondary = cb

    def start_recording(self, filename: Optional[str] = None) -> str:
        if self.is_recording():
            raise RuntimeError("Already recording")

        if filename is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            filename = f"{timestamp}.wav"
        self.current_file = self.output_dir / filename
        logger.info(f"Streaming recording to: {self.current_file}")

        stereo = self.mode == "combined"
        self._wav = wave.open(str(self.current_file), "wb")
        self._wav.setnchannels(2 if stereo else 1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

        self.client = ParakeetStreamClient(self.socket_path, on_partial=self._on_partial)
        self.client.connect()
        if stereo:
            self.secondary_client = ParakeetStreamClient(self.socket_path, on_partial=self._on_partial_secondary)
            self.secondary_client.connect()

        cmd = self._build_capture_cmd()
        logger.debug(f"Capture cmd: {' '.join(cmd)}")
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._stop_pump.clear()
        # Allocate every queue and streamer first; only then start the pump
        # so it never reads a None secondary queue before we've set it up.
        self._stream_queue = queue.Queue(maxsize=STREAM_QUEUE_MAX)
        self._dropped_chunks = 0
        self._streamer_thread = threading.Thread(
            target=self._streamer,
            args=(self._stream_queue, self.client, "mic"),
            daemon=True,
        )
        if stereo:
            self._stream_queue_secondary = queue.Queue(maxsize=STREAM_QUEUE_MAX)
            self._dropped_chunks_secondary = 0
            self._streamer_thread_secondary = threading.Thread(
                target=self._streamer,
                args=(self._stream_queue_secondary, self.secondary_client, "sys"),
                daemon=True,
            )
        self._pump_thread = threading.Thread(target=self._pump_stereo if stereo else self._pump, daemon=True)

        self._streamer_thread.start()
        if stereo:
            assert self._streamer_thread_secondary is not None
            self._streamer_thread_secondary.start()
        self._pump_thread.start()
        return str(self.current_file)

    def _build_capture_cmd(self) -> list[str]:
        """Single ffmpeg invocation that captures + downsamples to s16le
        16 kHz on stdout.

        - mic / system modes: mono.
        - combined mode: stereo with mic on left, system on right, so the
          pump can split them and route each to its own ASR session.
        """
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found on PATH")

        mono_out = [
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-loglevel",
            "warning",
            "pipe:1",
        ]

        if self.mode == "mic":
            return [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-f",
                "pulse",
                "-i",
                "default",
                *mono_out,
            ]

        monitor = self._default_sink_monitor()
        if self.mode == "system":
            return [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-f",
                "pulse",
                "-i",
                monitor,
                *mono_out,
            ]

        # combined: keep mic and system as independent channels so the
        # recorder can split them and feed two ASR sessions in parallel.
        # Output is interleaved 16-bit stereo @ 16 kHz: L=mic, R=system.
        return [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-f",
            "pulse",
            "-i",
            "default",
            "-f",
            "pulse",
            "-i",
            monitor,
            "-filter_complex",
            "[0:a]aresample=16000,aformat=channel_layouts=mono,volume=2.0[mic];"
            "[1:a]aresample=16000,aformat=channel_layouts=mono,volume=2.0[sys];"
            "[mic][sys]join=inputs=2:channel_layout=stereo[out]",
            "-map",
            "[out]",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "2",
            "-f",
            "s16le",
            "-loglevel",
            "warning",
            "pipe:1",
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
                capture_output=True,
                text=True,
                timeout=2,
            )
            sink = result.stdout.strip()
            if result.returncode == 0 and sink:
                return f"{sink}.monitor"
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"pactl get-default-sink failed: {exc}")
        return "@DEFAULT_MONITOR@"

    def _pump(self) -> None:
        """Mono pump: read PCM from ffmpeg, write to wav, hand off to streamer.

        Wav write is local + fast and must never block. We push each chunk
        to a bounded queue for the streamer to send to the socket. If the
        queue is full (server slow/stalled), the chunk is dropped on the
        stream side so the wav backup keeps up.
        """
        assert self.process is not None and self._wav is not None
        stdout = self.process.stdout
        assert stdout is not None
        q = self._stream_queue
        while not self._stop_pump.is_set():
            try:
                data = stdout.read(PUMP_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not data:
                break
            self._wav.writeframesraw(data)
            self._enqueue_or_drop(q, data, channel="mic")

    def _pump_stereo(self) -> None:
        """Stereo pump (combined mode): write stereo backup, split L/R into
        two mono streams, route to two ASR sessions."""
        assert self.process is not None and self._wav is not None
        stdout = self.process.stdout
        assert stdout is not None
        mic_q = self._stream_queue
        sys_q = self._stream_queue_secondary
        # Read in stereo-aligned chunks (4 bytes/frame): mic L, sys R.
        read_bytes = PUMP_CHUNK_BYTES * 2
        while not self._stop_pump.is_set():
            try:
                data = stdout.read(read_bytes)
            except (OSError, ValueError):
                break
            if not data:
                break
            self._wav.writeframesraw(data)
            mic_pcm, sys_pcm = _deinterleave_stereo_s16le(data)
            self._enqueue_or_drop(mic_q, mic_pcm, channel="mic")
            self._enqueue_or_drop(sys_q, sys_pcm, channel="sys")

    def _enqueue_or_drop(self, q: Optional[queue.Queue], data: bytes, channel: str) -> None:
        if q is None or not data:
            return
        try:
            q.put_nowait(data)
        except queue.Full:
            if channel == "mic":
                self._dropped_chunks += 1
                count = self._dropped_chunks
            else:
                self._dropped_chunks_secondary += 1
                count = self._dropped_chunks_secondary
            if count == 1 or count % 50 == 0:
                logger.warning(
                    f"{channel} stream queue full; dropped {count} chunks (server slow — wav backup unaffected)"
                )

    def _streamer(self, q: queue.Queue, client: ParakeetStreamClient, channel: str) -> None:
        """Drain a queue → socket for one channel. Decoupled from the pump
        so a slow/stalled server cannot block ffmpeg or the wav backup."""
        while not self._stop_pump.is_set():
            try:
                data = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None:
                break
            if not client.send_pcm(data):
                logger.warning(f"{channel} streamer: socket send failed; abandoning streaming")
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
                return
        # Final drain on graceful stop
        while True:
            try:
                data = q.get_nowait()
            except queue.Empty:
                break
            if data is None:
                continue
            client.send_pcm(data)

    def stop_recording(self) -> StreamResult:
        if not self.is_recording():
            raise RuntimeError("Not currently recording")
        assert self.process is not None and self._wav is not None and self.client is not None

        # SIGTERM first (SIGINT is unreliable with ffmpeg -nostdin); SIGKILL as fallback.
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logger.warning("Capture process did not exit on SIGTERM; killing")
            self.process.kill()
            self.process.wait()

        try:
            stdout = self.process.stdout
            tail = stdout.read() if stdout is not None else b""
        except (OSError, ValueError):
            tail = b""
        if tail:
            self._wav.writeframesraw(tail)
            if self.mode == "combined":
                mic_tail, sys_tail = _deinterleave_stereo_s16le(tail)
                self._enqueue_or_drop(self._stream_queue, mic_tail, "mic")
                self._enqueue_or_drop(self._stream_queue_secondary, sys_tail, "sys")
            else:
                self._enqueue_or_drop(self._stream_queue, tail, "mic")

        self._stop_pump.set()
        # Sentinels so streamers wake from queue.get immediately.
        for q in (self._stream_queue, self._stream_queue_secondary):
            if q is not None:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass

        if self._pump_thread:
            self._pump_thread.join(timeout=2)
        if self._streamer_thread:
            self._streamer_thread.join(timeout=3)
        if self._streamer_thread_secondary:
            self._streamer_thread_secondary.join(timeout=3)

        self._wav.close()
        audio_path = str(self.current_file)
        if self._dropped_chunks:
            logger.warning(
                f"mic streaming dropped {self._dropped_chunks} chunks; "
                f"wav backup at {audio_path} is complete and usable"
            )
        if self._dropped_chunks_secondary:
            logger.warning(
                f"sys streaming dropped {self._dropped_chunks_secondary} chunks; "
                f"wav backup at {audio_path} is complete and usable"
            )

        # Finalize both ASR sessions in parallel — they're independent and
        # waiting for finals serially would double the worst-case latency.
        secondary_final: Optional[str] = None
        secondary_partials: list[Partial] = []
        if self.secondary_client is not None:
            secondary_client = self.secondary_client
            sec_holder: dict = {}

            def _finish_secondary() -> None:
                sec_holder["final"] = secondary_client.finish(timeout=FINISH_TIMEOUT_S)

            t = threading.Thread(target=_finish_secondary, daemon=True)
            t.start()
            final_text = self.client.finish(timeout=FINISH_TIMEOUT_S)
            t.join(timeout=FINISH_TIMEOUT_S + 2)
            secondary_final = sec_holder.get("final")
            secondary_partials = list(secondary_client.partials)
            secondary_client.close()
        else:
            final_text = self.client.finish(timeout=FINISH_TIMEOUT_S)

        partials = list(self.client.partials)
        duration_s = self.client.duration_s
        self.client.close()

        self.process = None
        self.client = None
        self.secondary_client = None
        self._pump_thread = None
        self._streamer_thread = None
        self._streamer_thread_secondary = None
        self._stream_queue = None
        self._stream_queue_secondary = None
        self._wav = None
        self.current_file = None

        return StreamResult(
            audio_path=audio_path,
            final_text=final_text,
            partials=partials,
            duration_s=duration_s,
            secondary_final=secondary_final,
            secondary_partials=secondary_partials,
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
                    capture_output=True,
                    text=True,
                    timeout=2,
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
