"""/v1/audio/speech vs broken engine binaries (#1172).

Field evidence (macOS Apple Silicon, source checkout): the repo ships
zero-byte placeholders in bin/ (real binaries come from CI / the
installer, and bin/checksums.sha256 is not committed), and the GGUF
engine's is_available() blessed one as ready — is_file() passes for an
empty file, the absent manifest silently skipped the SHA check, and the
#437 exec-bit self-heal even chmod +x'd it. The request then died at
spawn with a bare 500: "[Errno 8] Exec format error".

Fix (the whole class, not the instance): services.binary_preflight
validates every managed executable (non-empty + real Mach-O/ELF/PE/
shebang magic) before exec; the GGUF engine validates in is_available()
BEFORE the chmod self-heal and again pre-exec; OS-level exec refusals
are converted to the typed InvalidBinaryError; routes map it to an
actionable 503 (and the engine-picker path to a 400 naming the fix).
Engine-layer coverage lives in tests/backend/engines/test_omnivoice_gguf.py
and tests/backend/services/test_binary_preflight.py — this file covers
the HTTP mapping.
"""
from __future__ import annotations

import pytest


def _tts_mod():
    import importlib

    return importlib.import_module("services.tts_backend")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_speech_maps_invalid_binary_to_503_with_repair_hint(client, monkeypatch):
    """A generate() that dies on a broken managed binary must surface as a
    503 carrying the repair hint — before the fix this was a 500 whose
    entire detail was "[Errno 8] Exec format error: '…'"."""
    import torch

    from services.binary_preflight import InvalidBinaryError

    tts = _tts_mod()

    class _BrokenBinary(tts.TTSBackend):
        id = "broken-binary-engine"
        display_name = "Broken Binary (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"  # the pre-fix lie — exec still refuses

        def generate(self, text, **kw) -> torch.Tensor:
            raise InvalidBinaryError(
                "bin/omnivoice-tts-darwin-arm64",
                "file is empty (0 bytes) — a placeholder, not a real binary",
                "run `scripts/build-omnivoice-tts.sh --platform darwin-arm64` "
                "or reinstall OmniVoice Studio",
            )

    monkeypatch.setitem(tts._REGISTRY, "broken-binary-engine", _BrokenBinary)
    res = client.post("/v1/audio/speech", json={
        "model": "broken-binary-engine", "input": "hello",
        "response_format": "wav",
    })
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert "build-omnivoice-tts.sh" in detail
    assert "placeholder" in detail
    assert "Exec format error" not in detail


def test_speech_gguf_placeholder_rejected_at_engine_selection(client, monkeypatch):
    """Selecting the GGUF engine explicitly while bin/ holds a 0-byte
    placeholder must 400 at _resolve_engine (is_available now tells the
    truth) with the actionable reason — never reach the exec path."""
    from engines.omnivoice_gguf import backend as gguf_backend

    placeholder_dir = None
    import tempfile
    from pathlib import Path

    placeholder_dir = Path(tempfile.mkdtemp(prefix="gguf-placeholder-"))
    placeholder = placeholder_dir / "omnivoice-tts-darwin-arm64"
    placeholder.write_bytes(b"")
    placeholder.chmod(0o755)

    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: placeholder)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    res = client.post("/v1/audio/speech", json={
        "model": "omnivoice-gguf", "input": "hello",
        "response_format": "wav",
    })
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "not available" in detail
    assert "build-omnivoice-tts.sh" in detail
