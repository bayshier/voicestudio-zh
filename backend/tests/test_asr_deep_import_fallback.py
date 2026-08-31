"""ASR deep-import env-rot degrades to the next engine instead of failing
wholesale (#1185).

``is_available()`` is a shallow probe: ``import whisperx`` succeeds even when
a transitive dep of ``whisperx.load_model()`` is missing, because pyannote /
pytorch_lightning only import at load time (whisperx → pyannote.audio →
pytorch_lightning → ``lightning_fabric``, which ships *inside* the
pytorch_lightning wheel). On a partially installed env (interrupted sync,
antivirus quarantine) auto-detect therefore picked whisperx and the dub
preflight died wholesale with "ASR backend initialization failed: No module
named 'lightning_fabric'" — even though faster-whisper (no lightning in its
chain) was sitting right there.

Fail-before/pass-after: on pre-#1185 code ``load_active_asr_backend`` does
not exist, ``_probe_available``/``list_backends`` trust the shallow probe,
and the pinned-engine error carries no repair hint — every test here fails.
"""
from __future__ import annotations

import pytest

# Fall-through mechanics assume ASR weights are present; the no-download
# fallback preflight (#1198 harvest) has its own dedicated tests.
pytestmark = pytest.mark.usefixtures("asr_model_installed")

from services import asr_backend as ab


def _broken(module_name: str):
    """An ``ensure_loaded`` that dies like a rotted deep import chain."""
    def _raise(self):
        raise ModuleNotFoundError(
            f"No module named '{module_name}'", name=module_name
        )
    return _raise


@pytest.fixture(autouse=True)
def _hermetic_selection(monkeypatch):
    """No env pin, no pref pin, no MPS — auto-detect goes whisperx-first —
    and shallow probes report ready regardless of the host's installs."""
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr("core.prefs.get", lambda key, default=None: None)
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(
        ab.WhisperXBackend, "is_available", classmethod(lambda cls: (True, "ready"))
    )
    monkeypatch.setattr(
        ab.FasterWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready"))
    )
    ab._DEEP_IMPORT_BROKEN.clear()
    ab._LAST_ERRORS.clear()
    yield
    ab._DEEP_IMPORT_BROKEN.clear()
    ab._LAST_ERRORS.clear()


def test_deep_import_failure_falls_through_to_next_backend(monkeypatch):
    loaded = []
    monkeypatch.setattr(ab.WhisperXBackend, "ensure_loaded", _broken("lightning_fabric"))
    monkeypatch.setattr(
        ab.FasterWhisperBackend, "ensure_loaded",
        lambda self: loaded.append("faster-whisper"),
    )

    backend = ab.load_active_asr_backend()

    # Fell through to the next engine — ASR init survives the rotted backend.
    assert isinstance(backend, ab.FasterWhisperBackend)
    assert loaded == ["faster-whisper"]
    # The broken backend is recorded with the missing module named …
    assert "lightning_fabric" in ab._DEEP_IMPORT_BROKEN["whisperx"]
    # … and probes/auto-detect now skip it (the shallow probe still says
    # ready, which is exactly the false positive #1185 is about).
    assert ab._probe_available(ab.WhisperXBackend) is False
    assert ab._auto_detect() == "faster-whisper"


def test_settings_reports_broken_backend_with_repair_hint(monkeypatch):
    monkeypatch.setattr(ab.WhisperXBackend, "ensure_loaded", _broken("lightning_fabric"))
    monkeypatch.setattr(ab.FasterWhisperBackend, "ensure_loaded", lambda self: None)
    ab.load_active_asr_backend()

    entry = {b["id"]: b for b in ab.list_backends()}["whisperx"]
    assert entry["available"] is False
    # Reason names the missing module and the concrete fix command.
    assert "lightning_fabric" in entry["reason"]
    assert "reinstall" in entry["reason"].lower()
    assert "uv sync" in entry["reason"]
    assert entry["install_hint"]  # the static per-engine hint still renders
    assert entry["last_error"] == entry["reason"]


def test_pinned_backend_raises_actionable_error_not_silent_switch(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "whisperx")
    monkeypatch.setattr(ab.WhisperXBackend, "ensure_loaded", _broken("lightning_fabric"))
    switched = []
    monkeypatch.setattr(
        ab.FasterWhisperBackend, "ensure_loaded",
        lambda self: switched.append("faster-whisper"),
    )

    with pytest.raises(RuntimeError) as ei:
        ab.load_active_asr_backend()

    msg = str(ei.value)
    assert "lightning_fabric" in msg          # names the missing package
    assert "reinstall" in msg.lower()          # names the fix
    assert switched == []                      # a pin is never silently swapped
    # Settings still learns why the pinned engine is down.
    assert "lightning_fabric" in ab._DEEP_IMPORT_BROKEN["whisperx"]


def test_every_candidate_broken_raises_instead_of_looping(monkeypatch):
    monkeypatch.setattr(ab.WhisperXBackend, "ensure_loaded", _broken("lightning_fabric"))
    monkeypatch.setattr(ab.FasterWhisperBackend, "ensure_loaded", _broken("ctranslate2"))
    monkeypatch.setattr(ab.PyTorchWhisperBackend, "ensure_loaded", _broken("transformers"))

    with pytest.raises(RuntimeError) as ei:
        ab.load_active_asr_backend()

    # The terminal error is the last resort's, with the repair hint attached.
    assert "transformers" in str(ei.value)
    assert set(ab._DEEP_IMPORT_BROKEN) == {
        "whisperx", "faster-whisper", "pytorch-whisper",
    }
