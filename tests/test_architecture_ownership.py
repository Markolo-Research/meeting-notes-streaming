"""Executable ownership rules for the layered monolith."""

import ast
from pathlib import Path


PACKAGE = Path(__file__).parents[1] / "meeting_notes"
UI = {"app", "settings"}
DOMAIN = {"ai_models", "note_maker", "recording_retention", "recording_session", "settings_model", "summarizer_port"}
RUNTIME = {"runtime_services"}
ADAPTERS = {
    "ai_summarizer",
    "audio_devices",
    "desktop_integration",
    "ollama_utils",
    "recorder",
    "summarizer",
    "summarizer_factory",
    "transcriber",
}
SHARED = {"config", "logger"}
ENTRYPOINTS = {"migrate_split"}


def imports(path: Path) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                result.add(f"meeting_notes.{node.module}")
            else:
                result.add(node.module)
    return result


def test_every_module_has_one_canonical_owner():
    actual = {path.stem for path in PACKAGE.glob("*.py")} - {"__init__"}
    classified = UI | DOMAIN | RUNTIME | ADAPTERS | SHARED | ENTRYPOINTS
    assert actual == classified


def test_lower_layers_do_not_import_ui():
    forbidden = {"meeting_notes.app", "meeting_notes.settings"}
    for module in DOMAIN | ADAPTERS | SHARED:
        assert imports(PACKAGE / f"{module}.py").isdisjoint(forbidden), module


def test_textual_is_owned_only_by_ui():
    for module in DOMAIN | ADAPTERS | SHARED:
        imported = imports(PACKAGE / f"{module}.py")
        assert not any(name == "textual" or name.startswith("textual.") for name in imported), module


def test_relative_imports_are_canonicalized_for_boundary_checks(tmp_path):
    module = tmp_path / "example.py"
    module.write_text("from .settings import SettingsScreen\n")
    assert imports(module) == {"meeting_notes.settings"}


def test_note_workflow_does_not_select_concrete_summarizers():
    imported = imports(PACKAGE / "note_maker.py")
    assert imported.isdisjoint(
        {"meeting_notes.ai_summarizer", "meeting_notes.summarizer", "meeting_notes.summarizer_factory"}
    )


def test_domain_does_not_import_adapters_or_runtime():
    forbidden = {f"meeting_notes.{name}" for name in ADAPTERS | RUNTIME}
    for module in DOMAIN:
        assert imports(PACKAGE / f"{module}.py").isdisjoint(forbidden), module
