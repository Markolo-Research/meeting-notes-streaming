# AGENTS.md

Follow `~/unified_docs/CLAUDE.md` for code discipline and hard rules.

## What this is

A streaming Parakeet ASR server (`parakeet-stream-server.py`) using
`nvidia/parakeet-unified-en-0.6b` with cache-aware streaming. Listens on a
Unix socket; accepts concurrent sessions; emits newline-delimited JSON
(`ready` / `partial` / `final` / `closed`). Backup wav per session under
`$XDG_CACHE_HOME/parakeet-stream/`.

The companion TUI lives one level up (`../meeting_notes/parakeet_stream.py`).
The wire protocol is shared — coordinate changes in one commit.

## Tooling

- **Never use bare `python` or `pip`** — always `uv run` / `uv pip`.
- Separate `.venv` from the TUI's: heavy deps (torch + nemo-toolkit) stay
  isolated here so the TUI install stays light.
- Run the server in the foreground for debugging: `uv run python3 parakeet-stream-server.py --foreground`.
- Launcher on PATH: `~/.local/bin/parakeet-stream-server` (bash wrapper that
  cds in and exec's `.venv/bin/python`).

## Concurrency model

Per-connection thread; `model_lock` serializes GPU work across sessions; per-
session `send_lock` serializes writes on the socket. The server times out
when idle for `IDLE_TIMEOUT_S` seconds with zero active sessions. Don't
remove that idle-exit — it's how the auto-start path stays clean.
