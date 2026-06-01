"""Tests for the silence-reset gate in the streaming Parakeet server.

The gate decides when to reset the streaming decoder on speech onset after
sustained silence — the fix for "first 1-2 words clipped after a pause"
in `parakeet-realtime` sessions.

This is the only piece of new server logic that doesn't require torch/nemo,
so it's the only piece worth unit-testing here. The server file is loaded
via importlib because its filename has hyphens (matches the launcher name)
and we want to avoid duplicating the gate in a separate module.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest


def _load_server_module():
    path = Path(__file__).resolve().parent.parent / "server" / "parakeet-stream-server.py"
    spec = importlib.util.spec_from_file_location("parakeet_stream_server", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def server_mod():
    return _load_server_module()


def test_chunk_rms_zero_for_silence(server_mod):
    assert server_mod.chunk_rms(np.zeros(1600, dtype=np.float32)) == 0.0


def test_chunk_rms_matches_constant_amplitude(server_mod):
    arr = np.full(1600, 0.5, dtype=np.float32)
    assert abs(server_mod.chunk_rms(arr) - 0.5) < 1e-6


def test_chunk_rms_empty_returns_zero(server_mod):
    assert server_mod.chunk_rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_gate_fires_on_speech_after_silence_threshold(server_mod):
    gate = server_mod.SilenceResetGate(rms_threshold=0.01, silence_chunks_needed=9)
    for _ in range(9):
        assert not gate.should_reset(0.005, eligible=True)
        gate.commit(0.005)
    # 9 silent chunks in: first speech chunk now triggers a reset.
    assert gate.should_reset(0.05, eligible=True)
    gate.commit(0.05)
    # Commit zeroed the silence counter, so subsequent speech does not re-trigger.
    assert not gate.should_reset(0.05, eligible=True)


def test_gate_skips_when_not_eligible(server_mod):
    gate = server_mod.SilenceResetGate(rms_threshold=0.01, silence_chunks_needed=2)
    for _ in range(5):
        gate.commit(0.0)
    # `eligible=False` mirrors the server case where decoded_samples == 0
    # (state is already fresh, nothing to reset).
    assert not gate.should_reset(0.5, eligible=False)


def test_gate_does_not_fire_below_silence_threshold(server_mod):
    gate = server_mod.SilenceResetGate(rms_threshold=0.01, silence_chunks_needed=5)
    for _ in range(4):
        gate.commit(0.0)
    # Only 4 silent chunks accumulated; speech onset does not reset yet.
    assert not gate.should_reset(0.1, eligible=True)


def test_gate_speech_resets_silence_counter(server_mod):
    gate = server_mod.SilenceResetGate(rms_threshold=0.01, silence_chunks_needed=5)
    gate.commit(0.0)
    gate.commit(0.0)
    gate.commit(0.1)
    assert gate.silence_chunks == 0


def test_gate_continuous_silence_does_not_trigger(server_mod):
    gate = server_mod.SilenceResetGate(rms_threshold=0.01, silence_chunks_needed=3)
    for _ in range(100):
        assert not gate.should_reset(0.0, eligible=True)
        gate.commit(0.0)
