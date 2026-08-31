"""Worker TTS must preserve the same rendering contract as local `/generate`.

The control plane already sends the profile reference, pinned seed and every
quality control to a remote worker.  This test protects the other half of that
contract: the worker must call the canonical render helpers rather than a bare
``backend.generate()`` call that silently discards the controls.
"""
from __future__ import annotations

from contextlib import nullcontext


def _gallery_params():
    return {
        "ref_audio": "/worker-inputs/whisper-gallery.wav",
        "ref_text": "The gallery sample transcript.",
        "instruct": "female, whispering, warm",
        "language": "English",
        "duration": 3.5,
        "speed": 0.9,
        "num_step": 32,
        "guidance_scale": 2.0,
        "denoise": True,
        "postprocess_output": True,
        "t_shift": 0.4,
        "layer_penalty_factor": 1.1,
        "position_temperature": 0.7,
        "class_temperature": 0.8,
        "seed": 42,
        "max_chunk_chars": 180,
        "crossfade_ms": 55,
        "effect_preset": "broadcast",
    }


def test_worker_omnivoice_preserves_gallery_identity_contract(monkeypatch):
    """A selected Whisper archetype must reach native render unchanged."""
    from services import tts_backend
    from worker.executor import TaskExecutor
    import api.routers.generation as generation

    model = object()
    backend = tts_backend.OmniVoiceBackend(model=model)
    captured = {}

    monkeypatch.setattr(tts_backend, "engine_in_use", lambda _backend: nullcontext())
    monkeypatch.setattr(
        generation,
        "_run_inference",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or "audio",
    )

    assert TaskExecutor._synthesize(backend, "Whispered test line.", _gallery_params()) == "audio"

    args = captured["args"]
    assert args[0] is model
    assert args[1] == "Whispered test line."
    assert args[3] == "/worker-inputs/whisper-gallery.wav"
    assert args[4] == "The gallery sample transcript."
    assert args[5] == "female, whispering, warm"
    assert args[7:11] == (32, 2.0, 0.9, 0.4)
    assert args[13:17] == (1.1, 0.7, 0.8, 42)
    assert args[17:20] == ("broadcast", 180, 55)


def test_worker_generic_engine_preserves_seeded_render_controls(monkeypatch):
    """Non-native engines use the generic canonical helper with the same knobs."""
    from services import tts_backend
    from worker.executor import TaskExecutor
    import api.routers.generation as generation

    class Backend:
        applies_own_mastering = False

    backend = Backend()
    captured = {}
    monkeypatch.setattr(tts_backend, "engine_in_use", lambda _backend: nullcontext())
    monkeypatch.setattr(
        generation,
        "_run_backend_inference",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or "audio",
    )

    assert TaskExecutor._synthesize(backend, "Whispered test line.", _gallery_params()) == "audio"

    args = captured["args"]
    assert args[0] is backend
    assert args[1] == "Whispered test line."
    assert args[3] == "/worker-inputs/whisper-gallery.wav"
    assert args[4] == "The gallery sample transcript."
    assert args[5] == "female, whispering, warm"
    assert args[7:10] == (32, 2.0, 0.9)
    assert args[12:16] == (42, "broadcast", 180, 55)
