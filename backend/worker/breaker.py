"""Circuit breaking and failure attribution.

This replaces the goal doc's reliability-score / penalty-decay / quarantine /
probation machinery, which the council found actively harmful at the scale this
feature actually ships at (one user, one to three of their own machines):

  * There is no "test/low-risk workload" in a TTS product, so a quarantined
    worker had no defined path back — quarantine was effectively permanent.
  * "Connection failure → larger penalty" is backwards on the networks this is
    designed for. Home Wi-Fi drops. Penalising that quarantines every consumer
    worker within a day.
  * A demoted worker receives less work, so it produces fewer samples, so its
    score stays low. The recovery path starves itself.

A breaker has none of those failure modes because it does not accumulate an
opinion — it counts *consecutive* failures, and one success clears it. It is
also explainable in the UI, which a tuned score never is: "paused after 3
failures, retrying in 60s" versus "reliability 62%".

Two things make it safe:

**Attribution before penalty.** Most failures are not the worker's fault. A
worker declining work because it is full is doing its job. A 4 GB card refusing
a 6 GB engine is a capability mismatch. A network partition that takes out the
whole fleet is an infrastructure event. None of these open a breaker.

**Per (worker, model).** A model that OOMs on an M2 must not stop that machine
from serving the engines it handles fine.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Optional

from worker.clock import resolve
from worker.errors import ErrorClass, WorkerError

# Consecutive charged failures before the breaker opens.
_FAILURE_THRESHOLD = 3

# Cooldown before a probe is allowed, and the ceiling for repeated trips.
# Escalating cooldown means a genuinely broken worker stops being retried every
# minute, while a one-off blip costs a minute of availability.
_BASE_COOLDOWN_SECONDS = 60.0
_MAX_COOLDOWN_SECONDS = 30 * 60.0

# Successes required in HALF_OPEN before the breaker closes.
_PROBE_SUCCESSES = 1

# Fleet-wide failure detection: if this fraction of known workers fails inside
# the window, it is an infrastructure event, not a fleet of bad GPUs. Charging
# them all is how one network blip quarantines everything and the ensuing retry
# wave overloads whatever survived.
_MASS_FAILURE_FRACTION = 0.5
_MASS_FAILURE_WINDOW_SECONDS = 60.0
_MASS_FAILURE_MIN_WORKERS = 3


class Attribution(str, enum.Enum):
    """Who is responsible for a failure."""

    # Counts against the worker.
    WORKER = "worker"
    # Real failure, nobody's fault locally — do not charge.
    NEUTRAL = "neutral"
    # Fleet-wide event. Suppress penalties entirely.
    INFRA = "infra"


class BreakerState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def attribute(error: WorkerError, *, mass_failure: bool = False) -> Attribution:
    """Decide whether a failure is chargeable.

    Neutral by construction: capacity rejections, capability mismatches,
    protocol/auth problems, user cancellation, and anything happening during a
    detected fleet-wide event.
    """
    if mass_failure:
        return Attribution.INFRA
    if error.error_class in (ErrorClass.CAPACITY, ErrorClass.CAPABILITY):
        return Attribution.NEUTRAL
    if error.error_class is ErrorClass.PROTOCOL:
        return Attribution.NEUTRAL
    if error.code in _NEUTRAL_CODES:
        return Attribution.NEUTRAL
    if not error.charges_worker:
        return Attribution.NEUTRAL
    return Attribution.WORKER


# Codes that describe something other than a misbehaving worker even though
# their class would otherwise be chargeable.
_NEUTRAL_CODES = frozenset(
    {
        "CANCELLED",            # the user changed their mind
        "WORKER_DISCONNECTED",  # unknown outcome, not a failure
        "SERVER_RESTART",       # our fault
        "TASK_DEADLINE_EXCEEDED",  # the task waited too long, often in queue
        "WORKER_DRAINING",      # planned maintenance
    }
)


@dataclass
class Breaker:
    """Breaker for one (worker, model) pair."""

    worker_id: str
    model_key: str
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    trips: int = 0
    opened_at: Optional[float] = None
    retry_at: Optional[float] = None
    probe_successes: int = 0
    last_error: Optional[WorkerError] = None

    def allows(self, *, now: Optional[float] = None) -> bool:
        """May the scheduler send work through this breaker right now?"""
        stamp = resolve(now)
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if self.retry_at is not None and stamp >= self.retry_at:
                # Cooldown elapsed — allow exactly one probe through.
                self.state = BreakerState.HALF_OPEN
                self.probe_successes = 0
                return True
            return False
        # HALF_OPEN: one probe at a time.
        return True

    def record_success(self, *, now: Optional[float] = None) -> None:
        self.consecutive_failures = 0
        self.last_error = None
        if self.state is BreakerState.HALF_OPEN:
            self.probe_successes += 1
            if self.probe_successes >= _PROBE_SUCCESSES:
                self._close()
        elif self.state is BreakerState.OPEN:
            # A result arriving from an open breaker (a straggler committing
            # after the trip) is still proof the worker works.
            self._close()

    def record_failure(
        self, error: WorkerError, *, attribution: Attribution, now: Optional[float] = None
    ) -> bool:
        """Record a failure. Returns True if the breaker opened as a result."""
        if attribution is not Attribution.WORKER:
            return False
        stamp = resolve(now)
        self.last_error = error
        if self.state is BreakerState.HALF_OPEN:
            # The probe failed — straight back to open, with a longer cooldown.
            self._open(stamp)
            return True
        self.consecutive_failures += 1
        if self.consecutive_failures >= _FAILURE_THRESHOLD:
            self._open(stamp)
            return True
        return False

    def force_close(self) -> None:
        """Operator override — the user fixed the machine and knows it."""
        self._close()
        self.trips = 0

    def _open(self, now: float) -> None:
        self.state = BreakerState.OPEN
        self.trips += 1
        self.opened_at = now
        cooldown = min(_MAX_COOLDOWN_SECONDS, _BASE_COOLDOWN_SECONDS * (2 ** (self.trips - 1)))
        self.retry_at = now + cooldown
        self.consecutive_failures = 0
        self.probe_successes = 0

    def _close(self) -> None:
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self.probe_successes = 0
        self.opened_at = None
        self.retry_at = None

    def describe(self, *, now: Optional[float] = None) -> str:
        """One line for the UI. A breaker the user cannot understand is worse
        than no breaker at all."""
        if self.state is BreakerState.CLOSED:
            return "OK"
        if self.state is BreakerState.HALF_OPEN:
            return "Testing recovery with the next task"
        remaining = max(0, int((self.retry_at or 0) - resolve(now)))
        reason = self.last_error.message if self.last_error else "repeated failures"
        return f"Paused after {_FAILURE_THRESHOLD} failures ({reason}) — retrying in {remaining}s"

    def to_dict(self, *, now: Optional[float] = None) -> dict:
        return {
            "worker_id": self.worker_id,
            "model_key": self.model_key,
            "state": self.state.value,
            "trips": self.trips,
            "retry_at": self.retry_at,
            "summary": self.describe(now=now),
        }


class BreakerRegistry:
    """All breakers for all workers, plus fleet-wide event detection.

    Session-scoped by design: OSS control planes restart constantly (the app
    quits), and carrying a cooldown across a restart would mean a user who
    restarts to fix a problem still cannot use their GPU. The hosted control
    plane persists this instead.
    """

    def __init__(self) -> None:
        self._breakers: dict[tuple[str, str], Breaker] = {}
        self._recent_failures: list[tuple[float, str]] = []
        self._known_workers: set[str] = set()

    def note_worker(self, worker_id: str) -> None:
        self._known_workers.add(worker_id)

    def forget_worker(self, worker_id: str) -> None:
        self._known_workers.discard(worker_id)
        for key in [k for k in self._breakers if k[0] == worker_id]:
            self._breakers.pop(key, None)

    def get(self, worker_id: str, model_key: str) -> Breaker:
        key = (worker_id, model_key)
        breaker = self._breakers.get(key)
        if breaker is None:
            breaker = Breaker(worker_id=worker_id, model_key=model_key)
            self._breakers[key] = breaker
        return breaker

    def allows(self, worker_id: str, model_key: str, *, now: Optional[float] = None) -> bool:
        return self.get(worker_id, model_key).allows(now=now)

    def record_success(self, worker_id: str, model_key: str, *, now: Optional[float] = None) -> None:
        self.get(worker_id, model_key).record_success(now=now)

    def record_failure(
        self,
        worker_id: str,
        model_key: str,
        error: WorkerError,
        *,
        now: Optional[float] = None,
    ) -> tuple[Attribution, bool]:
        """Attribute and record one failure.

        Returns ``(attribution, opened)``.
        """
        stamp = resolve(now)
        self._record_recent(worker_id, stamp)
        mass = self._mass_failure(now=stamp)
        attribution = attribute(error, mass_failure=mass)
        opened = self.get(worker_id, model_key).record_failure(
            error, attribution=attribution, now=stamp
        )
        return attribution, opened

    def open_breakers(self, worker_id: str, *, now: Optional[float] = None) -> list[Breaker]:
        return [
            b
            for (wid, _), b in self._breakers.items()
            if wid == worker_id and not b.allows(now=now)
        ]

    def _record_recent(self, worker_id: str, now: float) -> None:
        cutoff = now - _MASS_FAILURE_WINDOW_SECONDS
        self._recent_failures = [(t, w) for t, w in self._recent_failures if t >= cutoff]
        self._recent_failures.append((now, worker_id))

    def _mass_failure(self, *, now: float) -> bool:
        """Are we watching an infrastructure event rather than bad workers?"""
        if len(self._known_workers) < _MASS_FAILURE_MIN_WORKERS:
            return False
        cutoff = now - _MASS_FAILURE_WINDOW_SECONDS
        failing = {w for t, w in self._recent_failures if t >= cutoff}
        return len(failing) / max(1, len(self._known_workers)) >= _MASS_FAILURE_FRACTION

    def snapshot(self, *, now: Optional[float] = None) -> list[dict]:
        return [b.to_dict(now=now) for b in self._breakers.values()]


__all__ = [
    "Attribution",
    "Breaker",
    "BreakerRegistry",
    "BreakerState",
    "attribute",
]
