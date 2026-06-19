set shell := ["bash", "-euo", "pipefail", "-c"]

fmt:
    scripts/dev/fmt

fix:
    scripts/dev/fix

fmt-check:
    scripts/dev/fmt-check

lint:
    scripts/dev/lint

typecheck:
    scripts/dev/typecheck

test:
    scripts/dev/test

test-fast:
    scripts/dev/test-fast

secrets:
    scripts/guards/secrets

secrets-staged:
    scripts/guards/secrets-staged

deps-check:
    scripts/guards/deps-check

deps-check-fast:
    scripts/guards/deps-check-fast

actions-hygiene:
    scripts/guards/_repo-standards actions-hygiene

doctor:
    scripts/guards/repo-contract

doctor-staged:
    scripts/guards/repo-contract --staged

sync:
    scripts/dev/sync

sync-if-needed base_ref="HEAD@{1}":
    scripts/guards/_repo-standards sync-if-needed --base-ref {{quote(base_ref)}}

check-changed:
    just fix
    just doctor-staged
    just lint
    just typecheck
    just test-fast

check-paths *paths:
    scripts/dev/check-paths {{paths}}

check: doctor fmt-check lint actions-hygiene typecheck test secrets deps-check slop-scan

ci: check

deps-update:
    UV_NO_EXCLUDE_NEWER=1 uv lock --upgrade

deps-diff:
    git diff -- pyproject.toml uv.lock

slop-scan:
    scripts/guards/python-slop-scan

slop-scan-duplication:
    PYTHON_SLOP_SCAN_DUPLICATION=1 scripts/guards/python-slop-scan

hooks-install:
    scripts/guards/_repo-standards hooks-install
