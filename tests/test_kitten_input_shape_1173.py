"""KittenTTS input-shape hardening (#1173).

Field evidence (macOS, /v1/audio/speech): KittenTTS died inside
onnxruntime with "Expand node … invalid expand shape" on the first POST.
Root cause: the shipped ONNX graph's BERT front-end has a hard 512-token
positional cap, upstream chunks by *text characters* (400), and token
count is the length of the *phonemized* string — espeak verbalizes
digits explosively (110 chars of digits → ~1150 tokens). The
openai-compat route is the natural trigger because OpenAI callers send
no `language`, so app-level normalize_for_tts() skips numbers→words
there, letting raw digits reach espeak. A second crash class: empty /
punctuation-only input hit `np.concatenate([])` ("need at least one
array to concatenate") — also a 500.

Fix (adapter-level, benefits every route): KittenTTSBackend pre-measures
each chunk with the model's own tokenizer, splits oversized chunks at
word boundaries, runs the engine's own text cleaner, and raises the
typed TTSInputError (→ 400) when nothing speakable remains.

Mock-level tests always run; real-inference tests run only when the tiny
(~80 MB) kitten-tts-mini-0.8 model is already in the local HF cache.
"""
from __future__ import annotations

import numpy as np
import pytest


def _tts_mod():
    import importlib

    return importlib.import_module("services.tts_backend")


# ── Fake kitten internals (mimic kittentts 0.8.x + the 512-token cap) ──────


class _FakeOnnxInvalidArgument(RuntimeError):
    """Stands in for onnxruntime InvalidArgument 'invalid expand shape'."""


class _FakeOnnx:
    """Mimics kittentts.onnx_model.KittenTTS_1_Onnx: digits phonemize
    explosively (60 tokens/char), everything else 1 token/char, and any
    run over 512 tokens aborts like the real /bert/Expand node."""

    MAX = 512

    def __init__(self):
        self.max_tokens_seen = 0
        self.chunks: list[str] = []

    def _token_len(self, text: str) -> int:
        return sum(60 if c.isdigit() else 1 for c in text) + 3

    def _prepare_inputs(self, text: str, voice: str, speed: float) -> dict:
        return {
            "input_ids": np.zeros((1, self._token_len(text)), dtype=np.int64),
            "style": np.zeros((1, 256), dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),
        }

    def preprocessor(self, text: str) -> str:  # identity: keep digits raw
        return text

    def generate_single_chunk(self, text: str, voice: str, speed: float):
        n = self._prepare_inputs(text, voice, speed)["input_ids"].shape[1]
        self.max_tokens_seen = max(self.max_tokens_seen, n)
        if n > self.MAX:
            raise _FakeOnnxInvalidArgument(
                "Non-zero status code returned while running Expand node. "
                "Name:'/bert/Expand' Status Message: invalid expand shape"
            )
        self.chunks.append(text)
        return np.ones(240, dtype=np.float32)


class _FakeKitten:
    """Mimics the kittentts.KittenTTS wrapper (upstream char-only chunking,
    no token guard) — exactly what the adapter used to call."""

    def __init__(self):
        self.model = _FakeOnnx()

    def generate(self, text, voice="expr-voice-5-m", speed=1.0, clean_text=False):
        from kittentts.onnx_model import chunk_text

        outs = [
            self.model.generate_single_chunk(c, voice, speed)
            for c in chunk_text(text)
        ]
        return np.concatenate(outs, axis=-1)


def _kitten_backend_with_fake():
    backend = _tts_mod().KittenTTSBackend()
    backend._model = _FakeKitten()
    return backend


# ── Mock-level regression tests (always run) ───────────────────────────────


def test_digit_explosion_is_split_below_token_cap():
    """The #1173 repro shape: digits phonemize past the 512-token ONNX cap.
    Before the fix this raised the fake InvalidArgument (as the real graph
    did); after, the adapter splits at word boundaries and every chunk the
    session sees is within budget."""
    backend = _kitten_backend_with_fake()
    wav = backend.generate("9999999999 " * 10)

    fake = backend._model.model
    assert fake.max_tokens_seen <= fake.MAX
    assert len(fake.chunks) > 1  # actually split, not silently truncated
    assert wav.shape[0] == 1 and wav.shape[1] > 0


def test_single_monster_token_is_bisected():
    """A single unbroken 'word' over the cap (no whitespace to split on)
    falls back to character bisection instead of aborting."""
    backend = _kitten_backend_with_fake()
    wav = backend.generate("9" * 40)  # 40 digits → ~2400 fake tokens

    fake = backend._model.model
    assert fake.max_tokens_seen <= fake.MAX
    assert wav.shape[1] > 0


@pytest.mark.parametrize("text", ["", "   ", "...", "\n\n"])
def test_unspeakable_input_raises_typed_input_error(text):
    """Empty / punctuation-only input used to die in np.concatenate([])
    ('need at least one array to concatenate') — now a typed TTSInputError
    that routes map to 400."""
    backend = _kitten_backend_with_fake()
    with pytest.raises(_tts_mod().TTSInputError):
        backend.generate(text)


def test_normal_text_is_not_split():
    """Text within budget goes through unchanged as upstream would chunk
    it — the guard must not alter well-formed inputs."""
    backend = _kitten_backend_with_fake()
    wav = backend.generate("hello world")

    fake = backend._model.model
    assert len(fake.chunks) == 1
    assert "hello world" in fake.chunks[0]
    assert wav.shape[1] > 0


# ── Route-level mapping: TTSInputError → 400 on /v1/audio/speech ───────────


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_speech_route_maps_tts_input_error_to_400(client, monkeypatch):
    import torch

    tts = _tts_mod()

    class _RejectsInput(tts.TTSBackend):
        id = "rejects-input-engine"
        display_name = "Rejects Input (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["en"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            raise tts.TTSInputError(
                "KittenTTS: the input contains no speakable text"
            )

    monkeypatch.setitem(tts._REGISTRY, "rejects-input-engine", _RejectsInput)
    res = client.post("/v1/audio/speech", json={
        "model": "rejects-input-engine", "input": "...",
        "response_format": "wav",
    })
    assert res.status_code == 400, res.text
    assert "no speakable text" in res.json()["detail"]


# ── Real-inference regression (only when the tiny model is cached) ─────────


_KITTEN_REPO = "KittenML/kitten-tts-mini-0.8"


def _kitten_model_cached() -> bool:
    try:
        import json

        from huggingface_hub import try_to_load_from_cache

        cfg = try_to_load_from_cache(_KITTEN_REPO, "config.json")
        if not isinstance(cfg, str):
            return False
        with open(cfg) as f:
            conf = json.load(f)
        return all(
            isinstance(try_to_load_from_cache(_KITTEN_REPO, fn), str)
            for fn in (conf["model_file"], conf["voices"])
        )
    except Exception:
        return False


_needs_kitten_model = pytest.mark.skipif(
    not _kitten_model_cached(),
    reason=f"{_KITTEN_REPO} not in the local HF cache (~80 MB; not fetched in CI)",
)


@pytest.fixture(scope="module")
def real_kitten_backend():
    backend = _tts_mod().KittenTTSBackend()
    backend._ensure_loaded()
    return backend


@_needs_kitten_model
def test_real_model_digit_explosion_synthesizes(real_kitten_backend):
    """The exact #1173 trigger against the real ONNX graph: 110 chars of
    digits phonemize to ~1150 tokens. Before the fix: ONNXRuntimeError
    InvalidArgument '/bert/Expand invalid expand shape'. After: audio."""
    wav = real_kitten_backend.generate("9999999999 " * 10)
    assert wav.shape[0] == 1
    assert wav.shape[1] > 24000  # digits verbalized — well over a second


@_needs_kitten_model
def test_real_model_hello_world_still_works(real_kitten_backend):
    wav = real_kitten_backend.generate("hello world")
    assert wav.shape[0] == 1 and wav.shape[1] > 0


@_needs_kitten_model
def test_real_model_empty_input_typed_error(real_kitten_backend):
    with pytest.raises(_tts_mod().TTSInputError):
        real_kitten_backend.generate("")
