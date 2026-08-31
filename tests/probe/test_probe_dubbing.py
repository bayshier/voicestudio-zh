"""L4 dubbing — offline tests of timing + export-format judges, and the dub
spec wired against synthetic pipeline outputs."""

from __future__ import annotations

import os

from . import spec as probe_spec
from .judges import dubbing as D

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "dub_export.probe.yaml")

_SRT = "1\n00:00:00,000 --> 00:00:01,000\nHello world\n\n2\n00:00:01,000 --> 00:00:02,000\nGoodbye\n"
_VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n"


def test_segments_duration_ratio():
    ok = [{"source": 1.0, "dubbed": 1.1}, {"start": 0.0, "end": 2.0, "dubbed": 2.3}]
    assert D.segments_duration_ratio(ok, max_ratio=1.6).passed is True
    bad = [{"source": 1.0, "dubbed": 3.0}]  # 3.0× → way over
    assert D.segments_duration_ratio(bad, max_ratio=1.6).passed is False


def test_srt_vtt_well_formed():
    assert D.srt_well_formed(_SRT).passed is True
    assert D.srt_well_formed("not subtitles").passed is False
    assert D.vtt_well_formed(_VTT).passed is True
    assert D.vtt_well_formed(_SRT).passed is False  # SRT comma timing != VTT


def test_archive_has():
    names = ["001_vocals.wav", "002_no_vocals_background.wav"]
    assert D.archive_has(names, ["vocals", "background"]).passed is True
    assert D.archive_has(names, ["music"]).passed is False


def test_output_language_skips_without_detector():
    assert D.output_language_is("x.wav", "en").skipped is True


def test_output_language_with_detector():
    class FakeDet:
        def detect(self, path):
            return "en"

    assert D.output_language_is("x.wav", "en", detector=FakeDet()).passed is True
    assert D.output_language_is("x.wav", "fr", detector=FakeDet()).passed is False


def test_dub_spec_verdict(probe_report):
    spec = probe_spec.load_spec(_SPEC)
    ctx = {
        "segments": [{"source": 1.0, "dubbed": 1.05}, {"source": 2.0, "dubbed": 2.4}],
        "srt": _SRT,
        "vtt": _VTT,
        "zip_names": ["001_vocals.wav", "002_no_vocals_background.wav"],
        "dub_audio": "dubbed_en.wav",  # advisory langid skips (no detector)
    }
    results = probe_spec.run_judges(spec, ctx)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
