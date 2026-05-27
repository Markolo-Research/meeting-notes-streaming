#!/usr/bin/env python3
"""Realtime mic→socket→keyboard client for parakeet-stream-server.

Spawns pw-record for mic capture, streams PCM to the streaming server,
reads partial/final events, and live-injects text as it arrives using
one of three backends: ydotool (uinput-level, default when available),
wl-copy paste, or wtype synthetic keys.

On SIGINT/SIGTERM: stops capture, half-closes the socket so the server
flushes a final pass, injects any remaining diff, and writes the full
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


YDOTOOL_BACKSPACE = "14"  # Linux input code KEY_BACKSPACE


def _ydotool_socket_ready() -> bool:
    sock = os.environ.get(
        "YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket"
    )
    return Path(sock).is_socket()


def _resolve_injector(requested: str) -> str:
    """Pick a concrete injector mode given the user's preference.

    Modes: ydotool > paste > wtype. 'auto' (default) picks the first
    that's actually available. Explicit modes fall back if their tools
    are missing rather than dying silently mid-session.
    """
    have_ydotool = shutil.which("ydotool") is not None and _ydotool_socket_ready()
    have_paste = (
        shutil.which("wl-copy") is not None and shutil.which("wl-paste") is not None
    )
    have_wtype = shutil.which("wtype") is not None
    candidates = {
        "auto": [
            ("ydotool", have_ydotool),
            ("paste", have_paste),
            ("wtype", have_wtype),
        ],
        "ydotool": [("ydotool", have_ydotool), ("paste", have_paste), ("wtype", have_wtype)],
        "paste": [("paste", have_paste), ("wtype", have_wtype)],
        "wtype": [("wtype", have_wtype), ("paste", have_paste)],
    }
    for name, ok in candidates.get(requested, candidates["auto"]):
        if ok:
            return name
    return "none"


class Typer:
    """Diff-against-last-typed text injector.

    Injector backends, in preference order:
    - ydotool: uinput-level events (looks like a real USB keyboard).
      Bypasses Wayland app filtering — works in Electron, password
      fields, everything. Requires ydotoold running and /dev/uinput
      access (user in 'input' group).
    - paste: wl-copy + Ctrl+V. Atomic at the receiver, but clobbers
      clipboard mid-session and triggers per-app paste handlers.
    - wtype: virtual_keyboard protocol. Fastest but Electron drops
      synthetic events (spaces specifically) regardless of delays.
    """

    def __init__(
        self,
        enabled: bool,
        key_delay_ms: int,
        inter_op_ms: int,
        injector: str,
    ):
        self.mode = _resolve_injector(injector) if enabled else "none"
        self.enabled = self.mode != "none"
        self.visible = ""
        self.delay_args = ["-d", str(key_delay_ms)] if key_delay_ms > 0 else []
        self.inter_op = str(inter_op_ms) if inter_op_ms > 0 else None
        self._saved_clipboard: bytes | None = None

    def _gap(self, args: list[str]) -> None:
        if self.inter_op is not None:
            args.extend(["-s", self.inter_op])

    def set_target(self, target: str) -> None:
        if not self.enabled:
            self.visible = target
            return
        prefix = common_prefix_len(self.visible, target)
        backspaces = len(self.visible) - prefix
        suffix = target[prefix:]
        if backspaces == 0 and not suffix:
            return
        if self.mode == "ydotool":
            self._apply_ydotool(backspaces, suffix)
        elif self.mode == "paste":
            self._apply_paste(backspaces, suffix)
        else:
            self._apply_wtype(backspaces, suffix)
        self.visible = target

    def _apply_ydotool(self, backspaces: int, suffix: str) -> None:
        if backspaces > 0:
            key_args: list[str] = []
            for _ in range(backspaces):
                key_args.extend([f"{YDOTOOL_BACKSPACE}:1", f"{YDOTOOL_BACKSPACE}:0"])
            subprocess.run(["ydotool", "key", *key_args], check=False)
        if suffix:
            # --escape=0: treat input literally (no shell-style escape
            # interpretation), important for verbatim transcript text.
            subprocess.run(
                ["ydotool", "type", "--escape=0", "--", suffix], check=False
            )

    def _apply_wtype(self, backspaces: int, suffix: str) -> None:
        args = ["wtype", *self.delay_args]
        for _ in range(backspaces):
            args.extend(["-k", "BackSpace"])
        if suffix:
            chunks = suffix.split(" ")
            for i, chunk in enumerate(chunks):
                if i > 0:
                    self._gap(args)
                    args.extend(["-k", "space"])
                    self._gap(args)
                if chunk:
                    args.append(chunk)
        subprocess.run(args, check=False)

    def _apply_paste(self, backspaces: int, suffix: str) -> None:
        if self._saved_clipboard is None:
            self._saved_clipboard = self._snapshot_clipboard()
        if backspaces > 0:
            args = ["wtype"]
            for _ in range(backspaces):
                args.extend(["-k", "BackSpace"])
            subprocess.run(args, check=False)
        if suffix:
            subprocess.run(["wl-copy"], input=suffix.encode(), check=False)
            # wl-copy daemonises; give the compositor a moment to take
            # ownership before we trigger paste, or Ctrl+V grabs stale data.
            time.sleep(0.03)
            subprocess.run(
                ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=False
            )

    @staticmethod
    def _snapshot_clipboard() -> bytes:
        try:
            r = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True,
                timeout=1.0,
            )
            return r.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return b""

    def restore_clipboard(self) -> None:
        if self._saved_clipboard is None or not shutil.which("wl-copy"):
            return
        if self._saved_clipboard:
            subprocess.run(
                ["wl-copy"], input=self._saved_clipboard, check=False
            )
        else:
            subprocess.run(["wl-copy", "--clear"], check=False)

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
                   help="wtype -d delay between chars within a text arg (default 5)")
    p.add_argument("--inter-op-ms", type=int,
                   default=int(os.environ.get("PARAKEET_RT_INTER_OP_MS", "20")),
                   help="wtype -s gap around -k space presses (default 20)")
    p.add_argument("--injector",
                   default=os.environ.get("PARAKEET_RT_INJECTOR", "auto"),
                   choices=["auto", "ydotool", "paste", "wtype"],
                   help="Text injection backend. 'auto' (default) prefers "
                        "ydotool, then paste, then wtype. Each non-auto choice "
                        "falls back to the next available if its tool is missing.")
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
    typer = Typer(
        enabled=not args.no_live_type,
        key_delay_ms=args.key_delay_ms,
        inter_op_ms=args.inter_op_ms,
        injector=args.injector,
    )
    print(f"injector={typer.mode}", file=sys.stderr, flush=True)

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
    # In paste mode we clobbered the clipboard with intermediate suffixes;
    # restore the user's original. --copy below overwrites it again with
    # the final transcript when requested.
    typer.restore_clipboard()
    if args.copy and transcript and shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=transcript.encode(), check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
