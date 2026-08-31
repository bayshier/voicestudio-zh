"""#1229: one optional transformers symbol killed the entire backend at import.

`backend/api/routers/profiles.py` imports two pure-stdlib helpers from
`omnivoice.utils.voice_design`. That import used to drag in `omnivoice/__init__`
→ `omnivoice.models.omnivoice` → torch + torchaudio + transformers + a
top-level `from transformers import HiggsAudioV2TokenizerModel`. transformers
exposes that class lazily and gates it on the torchaudio backend, so on a host
where torchaudio is missing/ABI-mismatched/metadata-less (Colab's system
Python) the *attribute access* raised `ModuleNotFoundError: Could not import
module 'HiggsAudioV2TokenizerModel'` — during `backend/main.py`'s module import,
before FastAPI existed. Every feature died and the user got a uvicorn traceback
plus "Backend did not become healthy within 5 minutes".

These tests pin: the package's heavy exports stay lazy, the backend's utils
imports never pull the model stack, and the deferred failure is actionable and
classified.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from core.failure import _HINTS, classify


def _in_subprocess(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a clean interpreter — import side effects don't survive
    into it, so `sys.modules` assertions actually mean something."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )


def test_utils_import_does_not_load_the_model_stack():
    """The import that #1229 died on. `profiles.py` needs two regex helpers;
    it must not pay for torch/transformers — nor die with them."""
    proc = _in_subprocess(
        "import sys\n"
        "from omnivoice.utils.voice_design import heal_design_instruct, sanitize_instruct\n"
        "heavy = [m for m in ('torch', 'transformers', 'torchaudio') if m in sys.modules]\n"
        "assert not heavy, f'eagerly imported: {heavy}'\n"
        "assert 'omnivoice.models.omnivoice' not in sys.modules\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_importing_the_package_alone_stays_light():
    proc = _in_subprocess(
        "import sys, omnivoice\n"
        "assert 'torch' not in sys.modules, 'omnivoice/__init__ still imports torch'\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_lazy_exports_still_resolve():
    """`from omnivoice import OmniVoice` must behave exactly as before — only
    the timing of the heavy import changed."""
    proc = _in_subprocess(
        "from omnivoice import OmniVoice, OmniVoiceConfig, OmniVoiceGenerationConfig\n"
        "import omnivoice\n"
        "assert omnivoice.OmniVoice is OmniVoice\n"
        "assert 'OmniVoice' in dir(omnivoice)\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_unknown_attribute_still_raises_attribute_error():
    import omnivoice

    with pytest.raises(AttributeError):
        omnivoice.NoSuchThing


def test_audio_tokenizer_import_failure_is_actionable_and_classified(monkeypatch):
    """The deferred failure must name the real remedy and land in the class
    that carries a repair hint — instead of dying unclassified at startup."""
    import types

    from omnivoice.models import omnivoice as m

    # Stand in a transformers whose lazy resolution of the symbol raises, the
    # way it does when the torchaudio backend gate fails (the #1229 host).
    class _Broken(types.ModuleType):
        def __getattr__(self, name):
            raise ModuleNotFoundError(
                f"Could not import module '{name}'. "
                "Are this object's requirements defined correctly?"
            )

    monkeypatch.setitem(sys.modules, "transformers", _Broken("transformers"))

    with pytest.raises(ImportError) as excinfo:
        m._audio_tokenizer_cls()

    msg = str(excinfo.value)
    assert "torchaudio" in msg
    assert "--reinstall" in msg
    assert classify(msg) == "TRANSFORMERS_IMPORT"
    assert "torchaudio" in _HINTS["TRANSFORMERS_IMPORT"]


def test_audio_tokenizer_returns_the_class_when_importable():
    from omnivoice.models import omnivoice as m

    transformers = pytest.importorskip("transformers")
    assert m._audio_tokenizer_cls() is transformers.HiggsAudioV2TokenizerModel
