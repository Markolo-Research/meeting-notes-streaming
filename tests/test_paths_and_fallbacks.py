"""Tests for path handling (expanduser, parents=True) and ollama_model fallback.

These cover behaviour added across mathstuf #7 (expanduser), jmalobicky's
parents=True, and eduspano's empty-ollama-model fallback.
"""

from pathlib import Path

import pytest

from meeting_notes.note_maker import NoteMaker
from meeting_notes.summarizer_factory import create_summarizer
from meeting_notes.recorder import AudioRecorder


def test_note_maker_expands_user(tmp_path, monkeypatch):
    """NoteMaker should expand ~ in directory paths (mathstuf #7)."""
    # Point HOME at a temp dir so ~ resolves somewhere safe
    monkeypatch.setenv("HOME", str(tmp_path))
    nm = NoteMaker(
        output_dir="~/notes",
        transcripts_dir="~/transcripts",
        ai_provider="none",
    )
    assert nm.output_dir == tmp_path / "notes"
    assert nm.transcripts_dir == tmp_path / "transcripts"
    assert nm.output_dir.is_dir()
    assert nm.transcripts_dir.is_dir()


def test_note_maker_creates_nested_dirs(tmp_path):
    """parents=True (jmalobicky) lets users configure deep nested paths."""
    deep_out = tmp_path / "a" / "b" / "c" / "out"
    deep_tx = tmp_path / "a" / "b" / "c" / "tx"
    nm = NoteMaker(
        output_dir=str(deep_out),
        transcripts_dir=str(deep_tx),
        ai_provider="none",
    )
    assert nm.output_dir.is_dir()
    assert nm.transcripts_dir.is_dir()


def test_recorder_creates_nested_dirs(tmp_path):
    """AudioRecorder should also handle nested output dirs."""
    deep = tmp_path / "x" / "y" / "z" / "recordings"
    AudioRecorder(output_dir=str(deep))
    assert deep.is_dir()


def test_recorder_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = AudioRecorder(output_dir="~/recordings")
    assert r.output_dir == tmp_path / "recordings"
    assert r.output_dir.is_dir()


def test_empty_ollama_model_falls_back():
    """eduspano's fix: empty string must not pass through dict.get default.

    This is the regression test for the actual bug — `dict.get(key, default)`
    returns the empty string when the key is present-but-empty, not the default.
    The fix uses `or` to also catch falsy values.
    """
    cfg = {"ollama_model": ""}

    # The old, buggy pattern:
    buggy = cfg.get("ollama_model", "llama3.2:3b")
    assert buggy == "", "this empty string is exactly what caused 'model is required'"

    # The fixed pattern:
    fixed = cfg.get("ollama_model") or "llama3.2:3b"
    assert fixed == "llama3.2:3b"


def test_missing_ollama_model_falls_back():
    """When the key is genuinely missing, both patterns work — sanity check."""
    cfg = {}
    assert (cfg.get("ollama_model") or "llama3.2:3b") == "llama3.2:3b"
    assert cfg.get("ollama_model", "llama3.2:3b") == "llama3.2:3b"


def test_set_ollama_model_passes_through():
    """A real model name should not be replaced by the fallback."""
    cfg = {"ollama_model": "qwen2.5:7b"}
    assert (cfg.get("ollama_model") or "llama3.2:3b") == "qwen2.5:7b"


def test_note_maker_empty_local_model_uses_canonical_default(tmp_path):
    summarizer = create_summarizer("local", "", None)
    assert summarizer is not None
    assert summarizer.display_label == "Local Ollama: llama3.2:3b"


def test_note_maker_rejects_unknown_ai_provider(tmp_path):
    with pytest.raises(ValueError, match="Invalid ai_provider"):
        create_summarizer("not-real", "", None)


def test_note_maker_does_not_silently_disable_configured_cloud_ai(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OpenAI .*API key required"):
        create_summarizer("openai", "standard", None)


def test_note_maker_rejects_provider_summarizer_mismatches(tmp_path):
    local = create_summarizer("local", "", None)
    with pytest.raises(ValueError, match="AI-disabled"):
        NoteMaker(tmp_path / "notes", tmp_path / "transcripts", ai_provider="none", summarizer=local)
    with pytest.raises(ValueError, match="does not match"):
        NoteMaker(tmp_path / "notes", tmp_path / "transcripts", ai_provider="openai", summarizer=local)
