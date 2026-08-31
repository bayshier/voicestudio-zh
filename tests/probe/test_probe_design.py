"""Voice design — the design output runs the same audio-correctness ladder as
TTS (design *quality* stays human-judgment-only). Offline: synthetic audio +
FakeTranscriber; a live run plugs in the real /generate design output."""

from __future__ import annotations

import os

import numpy as np
import pytest
import soundfile as sf

from . import spec as probe_spec
from .judges.transcription import FakeTranscriber

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "voice_design.probe.yaml")
_TEXT = "Designing a brand new voice."


@pytest.fixture
def designed_audio(tmp_path):
    t = np.linspace(0, 1.8, int(24000 * 1.8), endpoint=False)
    path = str(tmp_path / "design.wav")
    sf.write(path, (0.35 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), 24000, subtype="FLOAT")
    return path


def test_voice_design_verdict(probe_report, designed_audio):
    spec = probe_spec.load_spec(_SPEC)
    results = probe_spec.run_judges(
        spec,
        context={"audio": designed_audio},
        backends={"transcriber": FakeTranscriber(fixed=_TEXT)},
    )
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
