# AGENTS.md

Your work will be reviewed by Codex.

## Python Tooling
- Never use bare `python` or `pip` for repo workflows; use `uv run` and `uv sync`.
- During edits: `just fix` then `just check-changed`
- Before handoff: `just check`
- CI parity: `just ci`
- Lint: `just lint` | Types: `just typecheck` | Tests: `just test`
- Hooks: `just hooks-install`

## Repo Notes
- This repo is a Markolo-maintained fork of `meeting-notes-tui`.
- Keep standards scaffolding aligned with `docs/_Infrastructure/ProjectStandards.md`.
- Avoid broad app rewrites while tightening repo-contract compliance.

## Git
- Default branch: `main`
- No emojis. No em dashes.

## Code Discipline
- State assumptions explicitly before writing code.
- Verify changes with repo commands after editing.
- Prefer small, maintainable fixes over speculative redesigns.
