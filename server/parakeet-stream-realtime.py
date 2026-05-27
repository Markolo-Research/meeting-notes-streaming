#!/usr/bin/env python3
"""Realtime mic→socket→keyboard client for parakeet-stream-server.

Spawns pw-record for mic capture, streams PCM to the streaming server,
reads partial/final events, and live-injects text as it arrives using
ydotool (uinput-level, preferred) or wtype (Wayland virtual keyboard,
fallback). ydotool is required for Electron — wtype's synthetic events
get filtered there, dropping spaces unpredictably.

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

    Modes: ydotool > wtype. 'auto' (default) prefers ydotool when its
    socket is reachable, else falls back to wtype. Explicit modes fall
    back too rather than dying silently mid-session.
    """
    have_ydotool = shutil.which("ydotool") is not None and _ydotool_socket_ready()
    have_wtype = shutil.which("wtype") is not None
    candidates = {
        "auto": [("ydotool", have_ydotool), ("wtype", have_wtype)],
        "ydotool": [("ydotool", have_ydotool), ("wtype", have_wtype)],
        "wtype": [("wtype", have_wtype), ("ydotool", have_ydotool)],
    }
    for name, ok in candidates.get(requested, candidates["auto"]):
        if ok:
            return name
    return "none"


class Typer:
    """Diff-against-last-typed text injector.

    Backends:
    - ydotool: uinput-level events (looks like a real USB keyboard).
      Bypasses Wayland app filtering — works in Electron, password
      fields, everything. Requires ydotoold running and /dev/uinput
      access (user in 'input' group).
    - wtype: virtual_keyboard protocol. Fallback when ydotool isn't
      available. Electron silently drops synthetic events (spaces
      especially), so prefer ydotool there.
    """

    def __init__(self, enabled: bool, injector: str):
        self.mode = _resolve_injector(injector) if enabled else "none"
        self.enabled = self.mode != "none"
        self.visible = ""

    def set_target(self, target: str) -> None:
        if not self.enabled:
            self.visible = target
            return
        # Both backends resolve \n→Enter and \t→Tab via the keymap, which
        # would submit forms or jump focus mid-transcript. Flatten to spaces.
        target = target.replace("\n", " ").replace("\t", " ")
        prefix = common_prefix_len(self.visible, target)
        backspaces = len(self.visible) - prefix
        suffix = target[prefix:]
        if backspaces == 0 and not suffix:
            return
        try:
            if self.mode == "ydotool":
                self._apply_ydotool(backspaces, suffix)
            else:
                self._apply_wtype(backspaces, suffix)
        except FileNotFoundError as e:
            # Tool disappeared after the startup which() check (uninstalled,
            # PATH change). Stop dispatching so we don't blow up the receiver
            # thread on every subsequent partial.
            print(
                f"injector {self.mode} unavailable ({e.filename}); typing disabled",
                file=sys.stderr,
                flush=True,
            )
            self.enabled = False
            return
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
        # Single wtype invocation: per commit 0a9425f, the inter-process gap
        # between separate runs lets picky apps drop the leading space of the
        # next run ("hello world" → "helloworld").
        args = ["wtype"]
        for _ in range(backspaces):
            args.extend(["-k", "BackSpace"])
        if suffix:
            args.extend(["--", suffix])
        subprocess.run(args, check=False)

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
                   help="Disable live typing (ydotool/wtype); collect only")
    p.add_argument("--copy", action="store_true",
                   help="On exit, copy final transcript to clipboard via wl-copy")
    p.add_argument("--connect-timeout", type=float, default=45.0)
    p.add_argument("--injector",
                   default=os.environ.get("PARAKEET_RT_INJECTOR", "auto"),
                   choices=["auto", "ydotool", "wtype"],
                   help="Text injection backend. 'auto' (default) prefers "
                        "ydotool, falling back to wtype if ydotoold isn't "
                        "reachable.")
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
    typer = Typer(enabled=not args.no_live_type, injector=args.injector)
    if (
        args.injector != "auto"
        and typer.enabled
        and typer.mode != args.injector
    ):
        # Either the requested backend is unavailable, or the env-var default
        # is a value argparse choices= didn't validate (e.g. legacy 'paste').
        print(
            f"warning: requested injector={args.injector!r} unavailable, using {typer.mode}",
            file=sys.stderr,
            flush=True,
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
    if args.copy and transcript and shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=transcript.encode(), check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
