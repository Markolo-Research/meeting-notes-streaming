# AGENTS.md

Follow `~/unified_docs/AGENTS.md` for code discipline, hard rules, and review
expectations. Your work may be reviewed by Codex or the `code-reviewer` agent.

## What this is

A local, keyboard-driven TUI for recording meetings, streaming transcription
via Parakeet, and generating AI summary notes. Built on Textual.

Upstream: `github.com/jamespember/meeting-notes-tui` — be mindful when pushing.

## Layout

- `meeting_notes/` — TUI (Textual). Light deps; install with `uv sync` at the repo root.
- `server/` — the streaming Parakeet ASR server. Heavy deps (torch + nemo-toolkit),
  separate `.venv`, separate `pyproject.toml`. See `server/CLAUDE.md`.
- The TUI talks to the server over `/tmp/parakeet-stream.sock` (newline JSON).
  Wire-protocol changes touch both halves — keep them in one commit.

## Tooling

- **Never use bare `python` or `pip`** — always `uv run` / `uv pip`.
- Use `just` for the standard repo surface; `just doctor` is the contract check.
- Install TUI: `uv sync` (root). Install server: `cd server && uv sync`.
- The TUI reads config from `~/.config/meeting-notes/config.yaml`. Don't commit secrets back.

## Modules

- `meeting_notes/app.py` — Textual UI, recording lifecycle, note rendering.
- `meeting_notes/parakeet_stream.py` — streaming client + recorder, stereo split for combined mode.
- `meeting_notes/ai_summarizer.py` — OpenAI / Anthropic / OpenRouter prompts and parsing.
- `meeting_notes/note_maker.py` — orchestration: transcript → AI summary → markdown note.
- `server/parakeet-stream-server.py` — Unix-socket ASR server (concurrent sessions).
- `tests/test_parakeet_stream.py` — covers ffmpeg cmd shape, stereo deinterleave, and a fake-server round trip.

## When changing the streaming pipeline

The pump/streamer/queue setup in `StreamingAudioRecorder` is order-sensitive
(see the existing test `test_combined_mode_splits_into_two_streams`).
Initialize queues and streamer threads **before** the pump thread captures
references. Run the test suite after any change here.
