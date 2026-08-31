"""What the node owner can see, and what they can do about it.

Inbound mode has no per-job approval prompt — the enable toggle is the consent
surface, and a prompt per job would make a shared GPU unusable. That trade only
holds if "who is using my machine right now" is answerable at a glance and
actable in one click. Without this, a key that leaked is invisible until the
electricity bill.

Events are kept in memory with a hard cap. Persisting them would put a record
of other people's activity on disk by default, which is a bigger promise than
this feature needs to make.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

# Enough to cover a working day of joins and drops without becoming a log file
# nobody rotates.
_MAX_EVENTS = 200

# How long a kicked panel stays out. Long enough that the disconnect is
# visible and the person notices; short enough that it is plainly not a
# revocation, which is a separate and permanent action.
_KICK_COOLDOWN_SECONDS = 60.0


@dataclass
class Session:
    """One panel currently attached to this node."""

    session_id: str
    key_id: str
    label: str
    peer: str
    connected_at: float
    tasks_run: int = 0
    # Set when the owner kicks the session. The stream loop checks it, so a
    # disconnect that arrives mid-task ends cleanly rather than by exception.
    disconnect_requested: bool = False


@dataclass
class Event:
    at: float
    kind: str  # connected | disconnected | rejected | kicked
    label: str = ""
    peer: str = ""
    detail: str = ""


class ConnectionLog:
    def __init__(self, *, now: Optional[Callable[[], float]] = None) -> None:
        self._now = now or time.time
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._events: deque[Event] = deque(maxlen=_MAX_EVENTS)
        self._cooldowns: dict[str, float] = {}

    # ── Sessions ──────────────────────────────────────────────────────────

    def opened(self, *, session_id: str, key_id: str, label: str, peer: str) -> Session:
        session = Session(
            session_id=session_id,
            key_id=key_id,
            label=label,
            peer=peer,
            connected_at=self._now(),
        )
        with self._lock:
            self._sessions[session_id] = session
            self._events.append(
                Event(at=session.connected_at, kind="connected", label=label, peer=peer)
            )
        return session

    def closed(self, session_id: str, *, detail: str = "") -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            self._events.append(
                Event(
                    at=self._now(),
                    kind="kicked" if session.disconnect_requested else "disconnected",
                    label=session.label,
                    peer=session.peer,
                    detail=detail,
                )
            )

    def rejected(self, *, peer: str, detail: str) -> None:
        """A refused attempt is the event that matters most and the one a
        success-only log would omit entirely."""
        with self._lock:
            self._events.append(
                Event(at=self._now(), kind="rejected", peer=peer, detail=detail)
            )

    def task_started(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.tasks_run += 1

    def kick(self, session_id: str) -> bool:
        """Ask a session to end. Returns False if it already went away."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.disconnect_requested = True
            # A panel reconnects on its own, so without this the person is back
            # within two seconds and the button appears to do nothing —
            # verified on hardware, where the log read disconnected/connected
            # in the same breath. The cooldown makes the disconnect visible and
            # deliberately does NOT last: revoking the key is how you stop
            # somebody for good, and a kick that silently became permanent
            # would be a different promise than the button makes.
            self._cooldowns[session.key_id] = self._now() + _KICK_COOLDOWN_SECONDS
            return True

    def cooling_down(self, key_id: str) -> bool:
        with self._lock:
            return self._cooldowns.get(key_id, 0.0) > self._now()

    def disconnect_requested(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            return session is not None and session.disconnect_requested

    # ── Reporting ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "key_id": s.key_id,
                        "label": s.label,
                        "peer": s.peer,
                        "connected_at": s.connected_at,
                        "tasks_run": s.tasks_run,
                    }
                    for s in sorted(self._sessions.values(), key=lambda s: s.connected_at)
                ],
                "events": [
                    {
                        "at": e.at,
                        "kind": e.kind,
                        "label": e.label,
                        "peer": e.peer,
                        "detail": e.detail,
                    }
                    for e in reversed(self._events)
                ],
            }
