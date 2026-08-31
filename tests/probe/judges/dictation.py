"""Dictation judges — the streaming-ASR (real-time capture) WebSocket contract.

Model-free: verifies the endpoint is registered and accepts a loopback handshake.
Driving real partial/final transcription needs an ASR model and is left to an
enable-on-demand live test.
"""

from __future__ import annotations

from ..spec import JudgeResult


def ws_endpoint_registered(ws_routes: list, path: str) -> JudgeResult:
    routes = list(ws_routes or [])
    ok = path in routes
    return JudgeResult(
        name="ws_endpoint_registered",
        passed=ok,
        measured=path,
        detail=f"WebSocket {path!r} registered" if ok else f"{path!r} not in WS routes {routes}",
    )


def ws_handshake_ok(connected: bool) -> JudgeResult:
    ok = bool(connected)
    return JudgeResult(
        name="ws_handshake_ok",
        passed=ok,
        measured=ok,
        detail="dictation WS accepted a loopback handshake" if ok
        else "dictation WS refused the loopback handshake",
    )
