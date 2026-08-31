"""Integration test for the real network-share listener lifecycle.

enable() starts a SECOND in-process uvicorn.Server bound to 0.0.0.0 on a
dedicated port serving the same app; disable() stops it. This test exercises
the real socket: it confirms a TCP connection succeeds on the reported
share_port while enabled, and is refused after disable().

Wrapped in asyncio.run inside a sync test so it does not depend on a
pytest-asyncio event-loop mode being configured.
"""
import asyncio
import socket

import pytest

from services import network_share as ns


def _can_connect(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if a TCP connection to host:port is accepted."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _wait_closed(port: int, host: str = "127.0.0.1", tries: int = 40) -> bool:
    """Poll until the port stops accepting connections (tolerates teardown lag)."""
    for _ in range(tries):
        if not _can_connect(port, host):
            return True
        # Synchronous sleep is fine here — runs outside the event loop, between
        # connect probes, after the server has been asked to exit.
        socket_wait = 0.05
        import time

        time.sleep(socket_wait)
    return False


async def _exercise_lifecycle():
    # A minimal FastAPI app is enough — enable() only needs an ASGI app object
    # and a place to stash app.state.network_share.
    from fastapi import FastAPI

    app = FastAPI()

    # Sanity: starts Local (nothing bound to 0.0.0.0).
    assert ns.get_state().enabled is False

    state = await ns.enable(app)
    try:
        assert state.enabled is True
        assert ns.get_state().enabled is True
        port = state.share_port
        assert isinstance(port, int) and port > 0
        # app.state is updated to the enabled state.
        assert app.state.network_share.enabled is True
        assert app.state.network_share.share_port == port
        # The listener is really up: a TCP connect to the reported port succeeds.
        # The server binds 0.0.0.0; connect via loopback, which 0.0.0.0 covers.
        assert _can_connect(port), f"expected a live listener on port {port}"
    finally:
        await ns.disable(app)

    # After disable(): state reset and the socket is closed.
    assert ns.get_state().enabled is False
    assert app.state.network_share.enabled is False
    assert _wait_closed(port), f"expected port {port} closed after disable()"
    return port


def test_share_listener_lifecycle():
    # Ensure a clean starting state regardless of test ordering.
    if ns.get_state().enabled:
        asyncio.run(ns.disable(__import__("fastapi").FastAPI()))

    try:
        # Probe whether binding 0.0.0.0 is permitted in this sandbox.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("0.0.0.0", 0))
            except OSError as e:
                pytest.skip(f"binding 0.0.0.0 not permitted in this sandbox: {e}")

        asyncio.run(_exercise_lifecycle())
    finally:
        # Defensive cleanup so a failure mid-test never leaves a stray listener
        # or a dirty module-level _state for the next test.
        if ns.get_state().enabled:
            asyncio.run(ns.disable(__import__("fastapi").FastAPI()))
