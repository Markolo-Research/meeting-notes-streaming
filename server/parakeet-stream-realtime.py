#!/usr/bin/env python3
"""Realtime mic→socket→wtype client for parakeet-stream-server.

Spawns pw-record for mic capture, streams PCM to the streaming server,
reads partial/final events, and live-types text via wtype as it arrives.
On SIGINT/SIGTERM: stops capture, half-closes the socket so the server
flushes a final pass, types any remaining diff, and writes the full
transcript to --save-transcript before exiting.

Used by shout(1) for SHOUT_ENGINE=parakeet-realtime.
"""

import argparse
import errno
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

DEFAULT_SOCKET = "/tmp/parakeet-stream.sock"
SAMPLE_RATE = 16000
CHUNK_MS = 160  # matches server's encoder chunk
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # s16 mono


def common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class Typer:
    """wtype frontend with diff-against-last-typed.

    key_delay_ms sets wtype's inter-keystroke delay. Some apps (Electron,
    Chromium, GTK4) drop events on fast bursts — spaces tend to be the
    visible casualty. Default 5ms is reliable without being noticeable.
    """

    def __init__(self, enabled: bool, key_delay_ms: int):
        self.enabled = enabled and shutil.which("wtype") is not None
        self.visible = ""
        self.delay_args = ["-d", str(key_delay_ms)] if key_delay_ms > 0 else []

    def set_target(self, target: str) -> None:
        if not self.enabled:
            self.visible = target
            return
        prefix = common_prefix_len(self.visible, target)
        backspaces = len(self.visible) - prefix
        if backspaces > 0:
            args = ["wtype", *self.delay_args]
            for _ in range(backspaces):
                args.extend(["-k", "BackSpace"])
            subprocess.run(args, check=False)
        suffix = target[prefix:]
        if suffix:
            subprocess.run(["wtype", *self.delay_args, suffix], check=False)
        self.visible = target

    def typed_chars(self) -> int:
        return len(self.visible)


def wait_for_socket(path: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if Path(path).is_socket():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
                s.close()
                return True
            except OSError:
                pass
        time.sleep(0.2)
    return False


def open_audio_source(record_path: str | None) -> tuple[subprocess.Popen, "wave.Wave_write | None"]:
    """Spawn pw-record reading 16kHz mono s16 to stdout; optionally tee to wav."""
    proc = subprocess.Popen(
        ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1",
         "--format", "s16", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    wav = None
    if record_path:
        Path(record_path).parent.mkdir(parents=True, exist_ok=True)
        wav = wave.open(record_path, "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
    return proc, wav


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--socket", default=DEFAULT_SOCKET)
    p.add_argument("--save-transcript",
                   help="On exit, write the concatenated final transcript here")
    p.add_argument("--record-path",
                   help="Tee captured audio to this wav (recovery backup)")
    p.add_argument("--no-live-type", action="store_true",
                   help="Don't type via wtype; collect only")
    p.add_argument("--copy", action="store_true",
                   help="On exit, copy final transcript to clipboard via wl-copy")
    p.add_argument("--connect-timeout", type=float, default=45.0)
    p.add_argument("--key-delay-ms", type=int,
                   default=int(os.environ.get("PARAKEET_RT_KEY_DELAY_MS", "5")),
                   help="wtype -d delay between keystrokes in ms (default 5)")
    args = p.parse_args()

    if not wait_for_socket(args.socket, args.connect_timeout):
        print(f"timed out waiting for {args.socket}", file=sys.stderr)
        return 2

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(args.socket)
    sock.settimeout(1.0)

    stop = threading.Event()
    state_lock = threading.Lock()
    finals: list[str] = []
    current_partial = ""
    typer = Typer(enabled=not args.no_live_type, key_delay_ms=args.key_delay_ms)

    def render_target() -> str:
        parts = finals + ([current_partial] if current_partial else [])
        return " ".join(s for s in parts if s).strip()

    def handle_signal(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def receiver():
        nonlocal current_partial
        rx = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                if stop.is_set() and not rx:
                    # idle reads while stopping; let half-close + server flush proceed
                    pass
                continue
            except OSError:
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
                    continue
                with state_lock:
                    if "partial" in msg:
                        current_partial = msg["partial"].strip()
                        typer.set_target(render_target())
                    elif "final" in msg:
                        text = msg["final"].strip()
                        if text:
                            finals.append(text)
                        current_partial = ""
                        typer.set_target(render_target())
                    elif "status" in msg and msg.get("status") == "closed":
                        return

    rx_thread = threading.Thread(target=receiver, daemon=True)
    rx_thread.start()

    pw, wav = open_audio_source(args.record_path)

    try:
        assert pw.stdout is not None
        while not stop.is_set():
            data = pw.stdout.read(CHUNK_BYTES)
            if not data:
                break
            if wav is not None:
                wav.writeframes(data)
            try:
                sock.sendall(data)
            except (BrokenPipeError, OSError) as e:
                if e.errno not in (errno.EPIPE, errno.ECONNRESET):
                    print(f"socket error: {e}", file=sys.stderr)
                break
    finally:
        # Stop capture first so no more audio queues up
        pw.terminate()
        try:
            pw.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pw.kill()
        if wav is not None:
            wav.close()
        # Half-close write side; server flushes pending finals
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        # Wait for receiver to drain
        rx_thread.join(timeout=10)
        try:
            sock.close()
        except OSError:
            pass

    transcript = " ".join(finals).strip()
    if args.save_transcript:
        Path(args.save_transcript).write_text(transcript)
    if args.copy and transcript and shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=transcript.encode(), check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
