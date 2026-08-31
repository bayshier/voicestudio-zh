"""Real ASR round-trip — enable-on-demand end-to-end.

The true round-trip (TTS speech → Whisper transcript → WER) needs a real TTS
model producing intelligible speech, so it can't run offline. Gated on
PROBE_E2E=1 plus a real speech sample at PROBE_ASR_SAMPLE (and optional expected
text at PROBE_ASR_TEXT). Without those it skips. The non-gated test confirms the
default ASR backend wires up (no model load).
"""

from __future__ import annotations

import os

import pytest

from .judges import transcription as T


def test_faster_whisper_backend_wires_up():
    """Constructs the default real transcriber without loading a model — proves
    the round-trip ASR path is importable and conforms to the Transcriber API."""
    tr = T.FasterWhisperTranscriber(model_size="tiny")
    assert hasattr(tr, "transcribe") and callable(tr.transcribe)
    assert tr._model is None  # lazy — no model loaded yet


@pytest.mark.skipif(os.environ.get("PROBE_E2E") != "1", reason="real ASR is enable-on-demand (PROBE_E2E=1)")
def test_real_round_trip_asr():
    sample = os.environ.get("PROBE_ASR_SAMPLE")
    if not sample or not os.path.isfile(sample):
        pytest.skip("set PROBE_ASR_SAMPLE=/path/to/speech.wav to run the real round-trip")
    tr = T.FasterWhisperTranscriber(model_size=os.environ.get("PROBE_ASR_MODEL", "tiny"))
    heard = tr.transcribe(sample)
    assert heard.strip(), "ASR returned empty transcript on real speech"
    expected = os.environ.get("PROBE_ASR_TEXT")
    if expected:
        wer = T.word_error_rate(expected, heard)
        assert wer <= float(os.environ.get("PROBE_ASR_MAX_WER", "0.3")), f"WER={wer:.3f}; heard {heard!r}"
