# Changelog

## Unreleased

- Added Markolo repo standards scaffolding and `uv`-based maintenance commands.

### Changed

- Upgraded the Torch CUDA runtime and Triton dependency to patched releases, clearing the previously reported dependency vulnerability ([PR #16](https://github.com/Markolo-Research/meeting-notes-streaming/pull/16)).
- The AI model roster for OpenAI and OpenRouter providers was upgraded; OpenAI now offers GPT-5.4 Mini (`gpt-5.4-mini`) and GPT-5.5 (previously GPT-4o Mini and GPT-4o), and OpenRouter now offers Gemini 3.1 Flash-Lite, GPT-5.4 Mini, and Claude Sonnet 4.6 (previously Gemini 1.5 Flash, Claude 3 Haiku, and Claude 3.5 Sonnet). The default model tier for OpenAI changed from `mini` to `standard` (GPT-5.5) and for Anthropic from `haiku` to `sonnet` (Claude Sonnet 4.6); configs that stored the old tier key by name continue to resolve to the new model IDs ([PR #1](https://github.com/Markolo-Research/meeting-notes-streaming/pull/1)).

### Fixed

- Combined recording no longer passes an empty `--target=` argument to `pw-record` when no default audio sink can be detected via `pactl`; previously an undiscoverable sink would silently pass an invalid target and could prevent system audio from being captured ([PR #3](https://github.com/Markolo-Research/meeting-notes-streaming/pull/3)).
- When startup configuration validation fails, the app now falls back to `ai_provider = "none"` rather than continuing to initialize with a corrupted config; previously invalid settings could propagate into recording or summarization startup ([commit 4fc762b](https://github.com/Markolo-Research/meeting-notes-streaming/commit/4fc762ba35ec4cd73c4a202eaca8428980b36c06)).
