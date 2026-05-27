#!/usr/bin/env python3
"""Smoke-test client for parakeet-stream-server.

Reads a wav file (or raw s16le 16kHz mono PCM from stdin), streams it to the
server, and prints partial / final transcripts as they arrive.
"""

import argparse
import json
import socket
import sys
import time
import wave


def stream_from_wav(path: str, sock: socket.socket, realtime: bool, chunk_ms: int) -> None:
    with wave.open(path, "rb") as wf:
        assert wf.getnchannels() == 1, "mono only"
        assert wf.getsampwidth() == 2, "16-bit only"
        assert wf.getframerate() == 16000, "16kHz only"
        chunk_frames = int(16000 * chunk_ms / 1000)
        while True:
            frames = wf.readframes(chunk_frames)
            if not frames:
                break
            sock.sendall(frames)
            if realtime:
                time.sleep(chunk_ms / 1000)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="16kHz mono s16le wav file")
    p.add_argument("--socket", default="/tmp/parakeet-stream.sock")
    p.add_argument("--realtime", action="store_true",
                   help="Pace audio at real-time speed (simulate mic)")
    p.add_argument("--chunk-ms", type=int, default=160,
                   help="Audio chunk size sent per write (matches server chunk by default)")
    args = p.parse_args()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(args.socket)
    sock.settimeout(120)

    rx_buf = b""

    def drain_partial(blocking_final: bool = False) -> None:
        nonlocal rx_buf
        old_timeout = sock.gettimeout()
        sock.settimeout(0.05 if not blocking_final else 30)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                rx_buf += chunk
                while b"\n" in rx_buf:
                    line, rx_buf = rx_buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if "partial" in msg:
                        print(f"  ~ {msg['partial']}", flush=True)
                    elif "final" in msg:
                        print(f"\nFINAL ({msg.get('duration_s', 0):.1f}s): {msg['final']}")
                    elif "status" in msg:
                        print(f"[{msg['status']}] {json.dumps({k:v for k,v in msg.items() if k!='status'})}")
                    elif "error" in msg:
                        print(f"ERROR: {msg['error']}", file=sys.stderr)
        except socket.timeout:
            return
        finally:
            sock.settimeout(old_timeout)

    drain_partial()
    stream_from_wav(args.wav, sock, args.realtime, args.chunk_ms)
    sock.shutdown(socket.SHUT_WR)
    drain_partial(blocking_final=True)
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
