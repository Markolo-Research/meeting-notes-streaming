# Architecture ownership

The application is a layered monolith. Module names are currently flat, so
this map is the source of truth until packages are introduced.

| Layer | Modules | Owns |
| --- | --- | --- |
| UI | `app.py`, `settings.py` | Textual screens, navigation, visible state, and view composition |
| Runtime composition | `runtime_services.py` | Construction and wiring of application services and concrete adapters |
| Application/domain | `note_maker.py`, `recording_retention.py`, `recording_session.py`, `ai_models.py`, `summarizer_port.py` | Meeting workflow and session state, note and retention policy, and provider-neutral summarizer capability |
| Adapters | `recorder.py`, `transcriber.py`, `ai_summarizer.py`, `summarizer.py`, `summarizer_factory.py`, `audio_devices.py`, `desktop_integration.py`, `ollama_utils.py` | Audio, transcription, provider construction, OS, and subprocess behavior |
| Shared core | `config.py`, `logger.py` | Configuration shapes/persistence and logging infrastructure |
| Migration entrypoint | `migrate_split.py` | One-off legacy data migration only |

## Dependency direction

- UI composes application/domain and adapters; no lower layer imports UI.
- Application/domain may use shared core and adapter interfaces, but cannot
  import Textual or UI modules.
- Adapters own vendor and OS behavior, not cross-provider meeting policy.
- `app.py` is the runtime composition root. New reusable workflow behavior must
  not be added there.

`tests/test_architecture_ownership.py` enforces classification, UI isolation,
and the absence of Textual in non-UI modules.

## Ownership decisions

- **Runtime owner:** `app.py` and the concrete recorder/transcriber processes.
- **First fix owner:** the UI or adapter where the wrong behavior executes.
- **Canonical long-term owner:** meeting/note workflow belongs in
  `note_maker.py` or a new narrow application module; provider-neutral policy
  belongs outside individual AI adapters.
- **Competing owners that are wrong:** Textual screens, recorder/transcriber
  adapters, and individual provider clients must not duplicate workflow policy.
- **Cleanup direction:** extract recording-session coordination from `app.py`,
  split settings persistence from its screen, and converge the summary facade
  as those behaviors are next changed.
