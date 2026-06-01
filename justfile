set shell := ["bash", "-euo", "pipefail", "-c"]

sync:
    UV_NO_EXCLUDE_NEWER=1 uv sync --frozen --extra cloud --group dev

sync-if-needed:
    UV_NO_EXCLUDE_NEWER=1 uv sync --frozen --extra cloud --group dev

fmt:
    uv run ruff format .

fix:
    uv run ruff check --fix .
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

typecheck:
    uv run ty check .

test:
    uv run -m pytest -q

test-fast:
    uv run -m pytest -q tests/test_config.py tests/test_paths_and_fallbacks.py tests/test_recording_retention.py tests/test_summarizers.py

secrets:
    gitleaks detect --source . --redact --no-banner

secrets-staged:
    gitleaks protect --staged --redact --no-banner

actions-hygiene:
    actionlint
    zizmor --pedantic --min-severity high --min-confidence high .github/workflows

deps-check:
    UV_NO_EXCLUDE_NEWER=1 uv lock --check

deps-check-fast:
    UV_NO_EXCLUDE_NEWER=1 uv lock --check

doctor:
    scripts/guards/repo-contract full

doctor-staged:
    scripts/guards/repo-contract staged

check-changed:
    just fix
    just doctor-staged
    just lint
    just typecheck
    just test-fast

check:
    just doctor
    just fmt-check
    just lint
    just actions-hygiene
    just typecheck
    just test
    just secrets
    just deps-check

ci: check

run *args:
    uv run meeting-notes {{args}}
