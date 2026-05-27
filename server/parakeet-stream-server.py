#!/usr/bin/env python3
"""Streaming Parakeet ASR server (parakeet-unified-en-0.6b, cache-aware streaming).

Single-session Unix socket server. Client opens, streams raw PCM s16le 16kHz mono,
half-closes write side to signal end. Server emits newline-delimited JSON:
  {"status": "ready", "sample_rate": 16000, "chunk_ms": 160, "latency_ms": 560}
  {"partial": "text so far"}   (one per decoded chunk)
  {"final": "text", "duration_s": 12.34}
  (connection closed)

Audio is also written to a backup wav at $XDG_CACHE_HOME/parakeet-stream/<ts>.wav
so a server crash mid-meeting still leaves a recoverable recording.
"""

import argparse
import copy
import json
import os
import socket
import struct
import sys
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("NEMO_SUPPRESS_WARNINGS", "1")

import warnings

warnings.filterwarnings("ignore")

SOCKET_PATH = "/tmp/parakeet-stream.sock"
MODEL_NAME = "nvidia/parakeet-unified-en-0.6b"
SAMPLE_RATE = 16000
IDLE_TIMEOUT_S = 600

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "parakeet-stream"
BACKUP_KEEP = 20

# Latency target: chunk 160 ms + right 400 ms = 560 ms (model card's mid-low setting)
CHUNK_SECS = 0.16
LEFT_CONTEXT_SECS = 5.6
RIGHT_CONTEXT_SECS = 0.40


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def prune_backups() -> None:
    wavs = sorted(CACHE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for old in wavs[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def load_model():
    import torch
    from omegaconf import DictConfig, OmegaConf, open_dict

    from nemo.collections.asr.models import ASRModel
    from nemo.collections.asr.parts.submodules.rnnt_decoding import RNNTDecodingConfig
    from nemo.collections.asr.parts.utils.streaming_utils import ContextSize

    log(f"Loading {MODEL_NAME} on {'cuda' if torch.cuda.is_available() else 'cpu'}...")
    model = ASRModel.from_pretrained(MODEL_NAME)

    with open_dict(model.cfg):
        if model.cfg.get("validation_ds") is None:
            model.cfg.validation_ds = DictConfig({"use_start_end_token": False, "sample_rate": SAMPLE_RATE})

    OmegaConf.set_struct(model.cfg.preprocessor, False)
    model.cfg.preprocessor.dither = 0.0
    model.cfg.preprocessor.pad_to = 0
    OmegaConf.set_struct(model.cfg.preprocessor, True)

    decoding_cfg = OmegaConf.structured(RNNTDecodingConfig())
    with open_dict(decoding_cfg):
        decoding_cfg.strategy = "greedy_batch"
        decoding_cfg.greedy.loop_labels = True
        decoding_cfg.greedy.preserve_alignments = False
        decoding_cfg.fused_batch_size = -1
    model.change_decoding_strategy(decoding_cfg)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    model.freeze()
    model.eval()
    model.preprocessor.featurizer.dither = 0.0
    model.preprocessor.featurizer.pad_to = 0

    feature_stride = model.cfg.preprocessor["window_stride"]
    subsampling = model.encoder.subsampling_factor
    features_per_sec = 1.0 / feature_stride
    feat_frame_samples = (int(SAMPLE_RATE * feature_stride) // subsampling) * subsampling
    encoder_frame_samples = feat_frame_samples * subsampling

    ctx_enc = ContextSize(
        left=int(LEFT_CONTEXT_SECS * features_per_sec / subsampling),
        chunk=int(CHUNK_SECS * features_per_sec / subsampling),
        right=int(RIGHT_CONTEXT_SECS * features_per_sec / subsampling),
    )
    ctx_samples = ContextSize(
        left=ctx_enc.left * subsampling * feat_frame_samples,
        chunk=ctx_enc.chunk * subsampling * feat_frame_samples,
        right=ctx_enc.right * subsampling * feat_frame_samples,
    )

    if model.cfg.encoder.att_context_style == "chunked_limited_with_rc":
        model.encoder.set_default_att_context_size(
            att_context_size=[ctx_enc.left, ctx_enc.chunk, ctx_enc.right]
        )

    log(
        f"Ready. Chunk={ctx_samples.chunk/SAMPLE_RATE*1000:.0f}ms "
        f"Right={ctx_samples.right/SAMPLE_RATE*1000:.0f}ms "
        f"Latency={int((ctx_samples.chunk+ctx_samples.right)/SAMPLE_RATE*1000)}ms"
    )
    return model, ctx_samples, ctx_enc, encoder_frame_samples


def handle_session(
    conn: socket.socket,
    model,
    ctx_samples,
    ctx_enc,
    encoder_frame_samples,
    model_lock: threading.Lock,
) -> None:
    import torch

    from nemo.collections.asr.parts.utils.rnnt_utils import batched_hyps_to_hypotheses
    from nemo.collections.asr.parts.utils.streaming_utils import StreamingBatchedAudioBuffer

    device = next(model.parameters()).device
    decoding_computer = model.decoding.decoding.decoding_computer
    send_lock = threading.Lock()  # serialize send() across this session's threads

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = CACHE_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}.wav"
    backup = wave.open(str(backup_path), "wb")
    backup.setnchannels(1)
    backup.setsampwidth(2)
    backup.setframerate(SAMPLE_RATE)
    log(f"Session started. Backup: {backup_path}")

    def send(payload: dict) -> None:
        with send_lock:
            conn.sendall((json.dumps(payload) + "\n").encode())

    send({"status": "ready", "sample_rate": SAMPLE_RATE,
          "chunk_ms": int(ctx_samples.chunk / SAMPLE_RATE * 1000),
          "latency_ms": int((ctx_samples.chunk + ctx_samples.right) / SAMPLE_RATE * 1000)})

    buffer = StreamingBatchedAudioBuffer(
        batch_size=1, context_samples=ctx_samples, dtype=torch.float32, device=device
    )
    state = None
    current_hyps = None
    pending = bytearray()
    decoded_samples = 0
    total_received_samples = 0
    chunk_samples = ctx_samples.chunk
    right_samples = ctx_samples.right
    first_window_samples = chunk_samples + right_samples

    def decode_step(samples_np, is_last: bool) -> str:
        nonlocal state, current_hyps
        audio_t = torch.from_numpy(samples_np).to(device=device, dtype=torch.float32).unsqueeze(0)
        length_t = torch.tensor([audio_t.shape[1]], device=device, dtype=torch.long)
        buffer.add_audio_batch_(
            audio_t,
            audio_lengths=length_t,
            is_last_chunk=is_last,
            is_last_chunk_batch=torch.tensor([is_last], device=device),
        )
        # The model + decoding_computer are shared across sessions; lock for
        # the duration of the GPU work. Per-session buffer/state stays local.
        with model_lock:
            encoder_out, encoder_len = model(input_signal=buffer.samples,
                                              input_signal_length=buffer.context_size_batch.total())
            encoder_out = encoder_out.transpose(1, 2)
            enc_ctx = buffer.context_size.subsample(factor=encoder_frame_samples)
            enc_ctx_batch = buffer.context_size_batch.subsample(factor=encoder_frame_samples)
            encoder_out = encoder_out[:, enc_ctx.left:]
            out_len = encoder_len - enc_ctx_batch.left if is_last else enc_ctx_batch.chunk
            chunk_hyps, _, state = decoding_computer(
                x=encoder_out, out_len=out_len, prev_batched_state=state, multi_biasing_ids=None
            )
            if current_hyps is None:
                current_hyps = chunk_hyps
            else:
                current_hyps.merge_(chunk_hyps)
            hyp = batched_hyps_to_hypotheses(current_hyps, None, batch_size=1)[0]
            return model.tokenizer.ids_to_text(hyp.y_sequence.tolist())

    try:
        with torch.inference_mode():
            import numpy as np

            while True:
                data = conn.recv(65536)
                if not data:
                    break
                total_received_samples += len(data) // 2
                pending.extend(data)
                backup.writeframesraw(data)

                # First decode needs chunk+right samples; subsequent need chunk samples
                needed = first_window_samples if decoded_samples == 0 else chunk_samples
                needed_bytes = needed * 2

                while len(pending) >= needed_bytes:
                    samples_np = (np.frombuffer(bytes(pending[:needed_bytes]), dtype=np.int16)
                                  .astype(np.float32) / 32768.0)
                    del pending[:needed_bytes]
                    partial = decode_step(samples_np, is_last=False)
                    decoded_samples += needed
                    needed = chunk_samples
                    needed_bytes = needed * 2
                    send({"partial": partial})

            # Client done sending: flush remaining samples as last chunk
            if pending or decoded_samples == 0:
                import numpy as np
                tail = (np.frombuffer(bytes(pending), dtype=np.int16).astype(np.float32) / 32768.0
                        if pending else np.zeros(1, dtype=np.float32))
                final = decode_step(tail, is_last=True)
            else:
                # Nothing left; finalize with empty last marker
                final = batched_hyps_to_hypotheses(current_hyps, None, batch_size=1)[0]
                final = model.tokenizer.ids_to_text(final.y_sequence.tolist())

            send({"final": final, "duration_s": total_received_samples / SAMPLE_RATE})
            log(f"Session done. {total_received_samples/SAMPLE_RATE:.1f}s audio → {len(final)} chars")
    finally:
        backup.close()
        try:
            send({"status": "closed", "backup": str(backup_path)})
        except OSError:
            pass
        prune_backups()


def main() -> int:
    parser = argparse.ArgumentParser(description="Streaming Parakeet ASR server")
    parser.add_argument("--socket", default=SOCKET_PATH)
    parser.add_argument("--foreground", "-f", action="store_true")
    args = parser.parse_args()

    if os.path.exists(args.socket):
        os.remove(args.socket)

    model, ctx_samples, ctx_enc, encoder_frame_samples = load_model()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(args.socket)
    sock.listen(8)
    sock.settimeout(60)
    os.chmod(args.socket, 0o600)
    log(f"Listening on {args.socket} (idle timeout {IDLE_TIMEOUT_S}s, concurrent sessions)")

    model_lock = threading.Lock()
    active_sessions = [0]  # boxed counter for nonlocal mutation from threads
    sessions_lock = threading.Lock()

    def _session_thread(conn: socket.socket) -> None:
        try:
            handle_session(conn, model, ctx_samples, ctx_enc, encoder_frame_samples, model_lock)
        except Exception as exc:
            log(f"Session error: {exc!r}")
            try:
                conn.sendall((json.dumps({"error": str(exc)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()
            with sessions_lock:
                active_sessions[0] -= 1

    last_used = time.time()
    while True:
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            with sessions_lock:
                active = active_sessions[0]
            if active == 0 and time.time() - last_used > IDLE_TIMEOUT_S:
                log("Idle timeout, exiting")
                break
            continue
        last_used = time.time()
        with sessions_lock:
            active_sessions[0] += 1
        threading.Thread(target=_session_thread, args=(conn,), daemon=True).start()

    sock.close()
    if os.path.exists(args.socket):
        os.remove(args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
