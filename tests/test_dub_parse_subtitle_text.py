"""Tests for POST /dub/parse-subtitle-text.

Stateless wrapper over `services.srt_parser.parse_srt` that backs the
"paste a translation from an external source" flow. It must stay
job-free (no mutation, no file I/O), reject oversized pastes, and give a
typed 400 when the paste has no timed cues at all.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("OMNIVOICE_MODEL", "test")


def _call(text):
    from api.routers import dub_core
    from schemas.requests import ParseSubtitleTextRequest

    return dub_core.dub_parse_subtitle_text(ParseSubtitleTextRequest(text=text))


SRT = """1
00:00:01,000 --> 00:00:04,500
Hola mundo.

2
00:00:05,250 --> 00:00:08,000
Segunda línea.
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.500
Hola mundo.

00:00:05.250 --> 00:00:08.000
Segunda línea.
"""


def test_parses_srt_into_cues():
    res = _call(SRT)
    assert res["skipped_cues"] == 0
    assert res["dropped_overlaps"] == 0
    assert res["segments"] == [
        {"start": 1.0, "end": 4.5, "text": "Hola mundo."},
        {"start": 5.25, "end": 8.0, "text": "Segunda línea."},
    ]


def test_parses_vtt_style_timestamps():
    # WebVTT uses `.` for the ms separator and has no cue indices; the
    # lenient parser handles both, and the `WEBVTT` header sits before the
    # first timing line so it is never mistaken for cue text.
    res = _call(VTT)
    assert [s["text"] for s in res["segments"]] == ["Hola mundo.", "Segunda línea."]
    assert res["segments"][0]["start"] == 1.0


def test_response_carries_no_job_shaped_fields():
    # The client keeps its own segments (ids, timings, text_original); this
    # endpoint must hand back cues only, never a segment record that could
    # tempt a caller into replacing rows wholesale.
    res = _call(SRT)
    assert set(res["segments"][0]) == {"start", "end", "text"}


def test_garbage_input_is_a_typed_400():
    with pytest.raises(HTTPException) as exc:
        _call("Just some translated prose with no timestamps at all.")
    assert exc.value.status_code == 400
    assert "no timed cues" in str(exc.value.detail).lower()


def test_empty_input_is_a_typed_400():
    with pytest.raises(HTTPException) as exc:
        _call("")
    assert exc.value.status_code == 400


def test_oversized_paste_is_rejected_before_parsing():
    from api.routers import dub_core

    with pytest.raises(HTTPException) as exc:
        _call("x" * (dub_core._MAX_SUBTITLE_PASTE_CHARS + 1))
    assert exc.value.status_code == 413
    assert "too large" in str(exc.value.detail).lower()


def test_at_the_size_limit_is_still_parsed():
    from api.routers import dub_core

    pad = "\n" * (dub_core._MAX_SUBTITLE_PASTE_CHARS - len(SRT))
    res = _call(SRT + pad)
    assert len(res["segments"]) == 2


def test_route_is_registered_without_a_job_id():
    from api.routers import dub_core

    paths = {r.path for r in dub_core.router.routes}
    assert "/dub/parse-subtitle-text" in paths
