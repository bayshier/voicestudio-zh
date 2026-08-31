"""Dictation — streaming-ASR WebSocket contract, verified against the real
backend (shared boot): endpoint registered + loopback handshake accepted."""

from __future__ import annotations

import os

from . import spec as probe_spec
from .judges import dictation as D

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "dictation.probe.yaml")


def test_dictation_ws_contract(probe_report, boot_capture):
    spec = probe_spec.load_spec(_SPEC)
    ctx = {
        "ws_routes": boot_capture.get("ws_routes", []),
        "ws_transcribe_connected": boot_capture.get("ws_transcribe_connected", False),
    }
    results = probe_spec.run_judges(spec, ctx)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    assert "/ws/transcribe" in boot_capture.get("ws_routes", [])


def test_dictation_judges_synthetic():
    assert D.ws_endpoint_registered(["/ws/transcribe"], "/ws/transcribe").passed is True
    assert D.ws_endpoint_registered([], "/ws/transcribe").passed is False
    assert D.ws_handshake_ok(True).passed is True
    assert D.ws_handshake_ok(False).passed is False
