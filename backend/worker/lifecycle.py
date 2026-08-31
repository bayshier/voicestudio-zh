"""Task and attempt lifecycle.

The original goal doc had a contradiction the council flagged as its single
worst correctness bug: ``§10`` reassigned a task the moment a worker
disconnected, while ``§21`` described the case where that same worker had
already finished the work and lost the connection before the acknowledgement.
Following both rules at once guarantees duplicate execution — two GPUs burning
on the same dub, two results racing to commit.

The fix is to stop treating a disconnect as a failure. A disconnect is an
**unknown outcome**. The distinction is carried structurally here:

  * ``TaskState``    — what the *task* is doing. One per task.
  * ``AttemptState`` — what one *try* is doing. Many per task.

A disconnected attempt does not fail; it stops renewing its lease. Only when
the lease expires (grace window) does it become ``ATTEMPT_LOST`` and free the
task to be retried. If the worker reconnects inside the window carrying a
finished result, that result commits and no second attempt was ever made.

Commit semantics: **at-least-once execution, exactly-once result commit**. The
first attempt to durably commit wins; any later duplicate is acknowledged and
discarded so the worker stops redelivering, and its losing sibling is
cancelled. This is why ``commit_result`` is idempotent on ``task_id`` rather
than on ``attempt_id``.
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from worker.clock import resolve
from worker.errors import ErrorClass, WorkerError


class TaskState(str, enum.Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    MODEL_LOADING = "model_loading"
    RUNNING = "running"
    RESULT_UPLOADING = "result_uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATES

    @property
    def in_flight(self) -> bool:
        """Is a worker actively holding this task right now?"""
        return self in _IN_FLIGHT_TASK_STATES


_TERMINAL_TASK_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.TIMEOUT, TaskState.CANCELLED}
)
_IN_FLIGHT_TASK_STATES = frozenset(
    {
        TaskState.ASSIGNED,
        TaskState.ACCEPTED,
        TaskState.MODEL_LOADING,
        TaskState.RUNNING,
        TaskState.RESULT_UPLOADING,
    }
)

# Legal task transitions. Anything absent is a bug, not a warning: an
# unexpected transition means two code paths disagree about who owns a task.
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.ASSIGNED, TaskState.CANCELLED, TaskState.TIMEOUT, TaskState.FAILED}),
    # Back to QUEUED on retry — assignment timeout, rejection, or a lost attempt.
    TaskState.ASSIGNED: frozenset(
        {TaskState.ACCEPTED, TaskState.QUEUED, TaskState.CANCELLED, TaskState.TIMEOUT, TaskState.FAILED}
    ),
    TaskState.ACCEPTED: frozenset(
        {
            TaskState.MODEL_LOADING,
            TaskState.RUNNING,
            TaskState.QUEUED,
            TaskState.CANCELLED,
            TaskState.TIMEOUT,
            TaskState.FAILED,
        }
    ),
    TaskState.MODEL_LOADING: frozenset(
        {TaskState.RUNNING, TaskState.QUEUED, TaskState.CANCELLED, TaskState.TIMEOUT, TaskState.FAILED}
    ),
    TaskState.RUNNING: frozenset(
        {
            # Back to MODEL_LOADING: a running attempt that loads a second
            # model reports it, and a model_loading frame can simply arrive
            # after the started frame that overtook it. Neither is a
            # disagreement about who owns the task, and raising here killed the
            # whole session because the read loop had nothing to catch it.
            TaskState.MODEL_LOADING,
            TaskState.RESULT_UPLOADING,
            TaskState.COMPLETED,
            TaskState.QUEUED,
            TaskState.CANCELLED,
            TaskState.TIMEOUT,
            TaskState.FAILED,
        }
    ),
    TaskState.RESULT_UPLOADING: frozenset(
        {TaskState.COMPLETED, TaskState.QUEUED, TaskState.CANCELLED, TaskState.TIMEOUT, TaskState.FAILED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.TIMEOUT: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class AttemptState(str, enum.Enum):
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    MODEL_LOADING = "model_loading"
    RUNNING = "running"
    UPLOADING = "uploading"
    # Result received and durably committed. Only now may the worker drop it.
    COMMITTED = "committed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    # Worker vanished and the grace window expired. NOT a failure — we simply
    # never learned the outcome.
    LOST = "lost"
    # Finished, but another attempt committed first. Ack it and drop it.
    SUPERSEDED = "superseded"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_ATTEMPT_STATES

    @property
    def live(self) -> bool:
        return self in _LIVE_ATTEMPT_STATES


_TERMINAL_ATTEMPT_STATES = frozenset(
    {
        AttemptState.COMMITTED,
        AttemptState.REJECTED,
        AttemptState.FAILED,
        AttemptState.TIMED_OUT,
        AttemptState.CANCELLED,
        AttemptState.LOST,
        AttemptState.SUPERSEDED,
    }
)
_LIVE_ATTEMPT_STATES = frozenset(
    {
        AttemptState.ASSIGNED,
        AttemptState.ACCEPTED,
        AttemptState.MODEL_LOADING,
        AttemptState.RUNNING,
        AttemptState.UPLOADING,
    }
)

# Attempt state → the task state it implies while it is the active attempt.
_ATTEMPT_TO_TASK: dict[AttemptState, TaskState] = {
    AttemptState.ASSIGNED: TaskState.ASSIGNED,
    AttemptState.ACCEPTED: TaskState.ACCEPTED,
    AttemptState.MODEL_LOADING: TaskState.MODEL_LOADING,
    AttemptState.RUNNING: TaskState.RUNNING,
    AttemptState.UPLOADING: TaskState.RESULT_UPLOADING,
}


class LifecycleError(RuntimeError):
    """An illegal transition was attempted."""


class PriorityClass(int, enum.Enum):
    """Two classes, not four.

    A single-user desktop has no fairness problem to solve, and four levels
    plus aging is a tuning surface nobody can test. What actually differs is
    whether a human is waiting: dictation and previews are INTERACTIVE, dubs
    and audiobooks are BATCH.
    """

    INTERACTIVE = 0
    BATCH = 1


@dataclass
class Attempt:
    """One try of a task on one worker."""

    attempt_id: str
    task_id: str
    worker_id: str
    session_epoch: int
    attempt_number: int
    state: AttemptState = AttemptState.ASSIGNED
    created_at: float = field(default_factory=time.time)
    accepted_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # When this attempt entered its current phase. The anchor a keepalive is
    # measured against: a lease renewal that says only "still alive" may not
    # push past the phase's own budget, or the budget stops existing. Not
    # persisted — after a restart the phase timestamps above stand in for it,
    # and a phase we cannot date is one we should not be enforcing a ceiling on.
    phase_started_at: Optional[float] = None
    # Progress lease: renewed by every progress/model-loading message. Liveness
    # is "is it still moving", not "has the clock run out" — a 40-minute dub is
    # not a hung task (docs/remote-workers.md).
    lease_expires_at: Optional[float] = None
    # Set when the worker's stream drops. The attempt is NOT failed yet.
    disconnected_at: Optional[float] = None
    grace_expires_at: Optional[float] = None
    progress: float = 0.0
    stage: str = ""
    error: Optional[WorkerError] = None

    def matches(self, *, session_epoch: Optional[int] = None) -> bool:
        """Fence check: reject messages from a superseded session."""
        if session_epoch is None:
            return True
        return session_epoch == self.session_epoch

    def renew_lease(
        self, seconds: float, *, not_after: Optional[float] = None, now: Optional[float] = None
    ) -> None:
        """Extend the lease by ``seconds``, never past ``not_after``.

        The ceiling is what separates "still alive" from "still working": a
        keepalive renewal is capped at the phase's absolute budget, so a wedged
        worker whose timer keeps firing still runs out, while a frame carrying
        real progress renews without one.
        """
        expiry = resolve(now) + seconds
        if not_after is not None:
            expiry = min(expiry, not_after)
        self.lease_expires_at = expiry
        # Progress proves the worker is alive, which clears any pending
        # disconnect bookkeeping from a reconnect mid-task.
        self.disconnected_at = None
        self.grace_expires_at = None

    @property
    def phase_anchor(self) -> float:
        """When the current phase began, as well as we can date it.

        Explicit ``is not None`` at every step: these are wall-clock stamps and
        ``0.0`` is a legitimate one (``clock.resolve`` exists for the same
        reason), so an ``or`` chain would skip a pinned test clock.
        """
        for stamp in (self.phase_started_at, self.started_at, self.accepted_at):
            if stamp is not None:
                return stamp
        return self.created_at

    def lease_expired(self, *, now: Optional[float] = None) -> bool:
        if self.lease_expires_at is None:
            return False
        return resolve(now) > self.lease_expires_at

    def grace_expired(self, *, now: Optional[float] = None) -> bool:
        if self.grace_expires_at is None:
            return False
        return resolve(now) > self.grace_expires_at

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "attempt_number": self.attempt_number,
            "state": self.state.value,
            "progress": self.progress,
            "stage": self.stage,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass
class Task:
    """A unit of inference work, independent of which worker runs it."""

    task_id: str
    operation: str
    engine: str
    model_id: str
    params: dict = field(default_factory=dict)
    priority: PriorityClass = PriorityClass.INTERACTIVE
    # Supplied by the client so a client-side retry does not create a second
    # task. Deduplication has to happen at the API boundary, before the worker
    # protocol is involved at all.
    idempotency_key: Optional[str] = None
    state: TaskState = TaskState.QUEUED
    max_attempts: int = 3
    attempts: list[Attempt] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    deadline_at: Optional[float] = None
    error: Optional[WorkerError] = None
    result_ref: Optional[str] = None
    # An explicit routing choice is a hard affinity, not a ranking hint.
    pinned_worker_id: Optional[str] = None
    # Workers this task must not be sent to again: each failed attempt excludes
    # its worker so a retry is genuinely a different try, not the same one.
    excluded_workers: set[str] = field(default_factory=set)

    # ── Queries ───────────────────────────────────────────────────────────

    @property
    def active_attempt(self) -> Optional[Attempt]:
        for attempt in reversed(self.attempts):
            if attempt.state.live:
                return attempt
        return None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def attempts_remaining(self) -> int:
        # Capacity rejections and a stream disappearing before dispatch are
        # advisory races, not executions. Keep their audit rows, but do not
        # spend the retry budget on work that never started.
        charged = sum(
            1
            for attempt in self.attempts
            if not (
                attempt.error is not None
                and (
                    attempt.error.error_class is ErrorClass.CAPACITY
                    or attempt.error.code == "WORKER_UNREACHABLE"
                )
            )
        )
        return max(0, self.max_attempts - charged)

    def get_attempt(self, attempt_id: str) -> Optional[Attempt]:
        for attempt in self.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        return None

    def deadline_exceeded(self, *, now: Optional[float] = None) -> bool:
        if self.deadline_at is None:
            return False
        return resolve(now) > self.deadline_at

    # ── Transitions ───────────────────────────────────────────────────────

    def _set_state(self, new: TaskState, *, now: Optional[float] = None) -> None:
        if new is self.state:
            return
        allowed = _TASK_TRANSITIONS.get(self.state, frozenset())
        if new not in allowed:
            raise LifecycleError(f"illegal task transition {self.state.value} → {new.value}")
        self.state = new
        if new.terminal:
            self.finished_at = resolve(now)

    def assign(self, *, worker_id: str, session_epoch: int, now: Optional[float] = None) -> Attempt:
        """Create the next attempt on ``worker_id``."""
        if self.state is not TaskState.QUEUED:
            raise LifecycleError(f"cannot assign a task in state {self.state.value}")
        if self.attempts_remaining <= 0:
            raise LifecycleError("no attempts remaining")
        if worker_id in self.excluded_workers:
            raise LifecycleError(f"worker {worker_id} is excluded from this task")
        attempt = Attempt(
            attempt_id=uuid.uuid4().hex[:16],
            task_id=self.task_id,
            worker_id=worker_id,
            session_epoch=session_epoch,
            attempt_number=self.attempt_count + 1,
            created_at=resolve(now),
        )
        self.attempts.append(attempt)
        self._set_state(TaskState.ASSIGNED, now=now)
        return attempt

    def _advance_attempt(
        self,
        attempt_id: str,
        new: AttemptState,
        *,
        session_epoch: Optional[int] = None,
        now: Optional[float] = None,
    ) -> Attempt:
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise LifecycleError(f"unknown attempt {attempt_id}")
        if not attempt.matches(session_epoch=session_epoch):
            raise LifecycleError("stale session epoch")
        if attempt.state.terminal:
            raise LifecycleError(f"attempt {attempt_id} already terminal ({attempt.state.value})")
        if new is not attempt.state:
            attempt.phase_started_at = resolve(now)
        attempt.state = new
        implied = _ATTEMPT_TO_TASK.get(new)
        if implied is not None:
            self._set_state(implied, now=now)
        return attempt

    def accept(self, attempt_id: str, **kw) -> Attempt:
        attempt = self._advance_attempt(attempt_id, AttemptState.ACCEPTED, **kw)
        attempt.accepted_at = resolve(kw.get("now"))
        return attempt

    def model_loading(self, attempt_id: str, **kw) -> Attempt:
        return self._advance_attempt(attempt_id, AttemptState.MODEL_LOADING, **kw)

    def start(self, attempt_id: str, **kw) -> Attempt:
        attempt = self._advance_attempt(attempt_id, AttemptState.RUNNING, **kw)
        attempt.started_at = resolve(kw.get("now"))
        return attempt

    def uploading(self, attempt_id: str, **kw) -> Attempt:
        return self._advance_attempt(attempt_id, AttemptState.UPLOADING, **kw)

    def commit_result(
        self,
        attempt_id: str,
        *,
        result_ref: Optional[str] = None,
        session_epoch: Optional[int] = None,
        now: Optional[float] = None,
    ) -> tuple[bool, Attempt]:
        """Durably commit an attempt's result.

        Returns ``(committed, attempt)``. ``committed`` is False when another
        attempt already won — the caller must still acknowledge the message so
        the worker stops redelivering, but must not apply the result twice.

        Idempotent on the *task*: this is what makes at-least-once delivery
        safe without claiming exactly-once execution.
        """
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise LifecycleError(f"unknown attempt {attempt_id}")
        if not attempt.matches(session_epoch=session_epoch):
            raise LifecycleError("stale session epoch")

        if self.state is TaskState.CANCELLED:
            # Cancellation is authoritative. A worker may be unable to stop a
            # native GPU call, but its late result cannot resurrect the task.
            if not attempt.state.terminal:
                attempt.state = AttemptState.CANCELLED
                attempt.finished_at = resolve(now)
            return False, attempt

        if self.state is TaskState.COMPLETED:
            # A duplicate. Ack-and-discard; never a second commit.
            if attempt.state is not AttemptState.COMMITTED:
                attempt.state = AttemptState.SUPERSEDED
                attempt.finished_at = resolve(now)
            return False, attempt

        if attempt.state is AttemptState.COMMITTED:
            return False, attempt
        if attempt.state.terminal:
            # Late result from an attempt we already wrote off (typically LOST
            # after a grace expiry). It still wins if nothing else committed —
            # the work is real and discarding it would waste a finished dub.
            attempt.state = AttemptState.COMMITTED
        else:
            attempt.state = AttemptState.COMMITTED
        attempt.finished_at = resolve(now)
        attempt.progress = 1.0
        self.result_ref = result_ref
        # Jump straight to COMPLETED regardless of the intermediate state we
        # believed we were in: the result is proof of what actually happened.
        self.state = TaskState.COMPLETED
        self.finished_at = attempt.finished_at
        # Any sibling still running lost the race.
        for other in self.attempts:
            if other is attempt or other.state.terminal:
                continue
            other.state = AttemptState.SUPERSEDED
            other.finished_at = attempt.finished_at
        return True, attempt

    def fail_attempt(
        self,
        attempt_id: str,
        error: WorkerError,
        *,
        session_epoch: Optional[int] = None,
        now: Optional[float] = None,
    ) -> Attempt:
        """Record an attempt failure and requeue the task if retries remain."""
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise LifecycleError(f"unknown attempt {attempt_id}")
        if not attempt.matches(session_epoch=session_epoch):
            raise LifecycleError("stale session epoch")
        if attempt.state.terminal:
            return attempt

        stamp = resolve(now)
        attempt.error = error
        attempt.finished_at = stamp
        if error.error_class is ErrorClass.CAPACITY:
            attempt.state = AttemptState.REJECTED
        elif error.error_class is ErrorClass.TIMEOUT:
            attempt.state = AttemptState.TIMED_OUT
        else:
            attempt.state = AttemptState.FAILED

        # A worker that declined for capacity is not excluded — it was right,
        # and it will have room later. Everything else gets excluded so a
        # retry is a genuinely different try.
        if error.error_class is not ErrorClass.CAPACITY and not self.pinned_worker_id:
            self.excluded_workers.add(attempt.worker_id)

        self._settle_after_attempt(error, now=stamp)
        return attempt

    def lose_attempt(self, attempt_id: str, *, now: Optional[float] = None) -> Attempt:
        """The grace window expired without word from the worker.

        Unknown outcome, not failure: the worker is excluded (we cannot ask it
        again) but it is NOT charged a breaker failure, because a home network
        dropping for 60 seconds says nothing about the GPU.
        """
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise LifecycleError(f"unknown attempt {attempt_id}")
        if attempt.state.terminal:
            return attempt
        stamp = resolve(now)
        attempt.state = AttemptState.LOST
        attempt.finished_at = stamp
        if not self.pinned_worker_id:
            self.excluded_workers.add(attempt.worker_id)
        self._settle_after_attempt(
            WorkerError(
                error_class=ErrorClass.TRANSIENT,
                code="WORKER_DISCONNECTED",
                message="The worker stopped responding and did not reconnect in time.",
                hint="The task will be retried on another worker if one is available.",
            ),
            now=stamp,
        )
        return attempt

    def _settle_after_attempt(self, error: WorkerError, *, now: float) -> None:
        """Decide between retry and terminal failure after an attempt ends."""
        if self.deadline_exceeded(now=now):
            self.error = WorkerError(
                error_class=ErrorClass.TIMEOUT,
                code="TASK_DEADLINE_EXCEEDED",
                message="The task ran past its overall deadline.",
                hint=error.hint,
            )
            self._set_state(TaskState.TIMEOUT, now=now)
            return
        if not error.retryable or self.attempts_remaining <= 0:
            self.error = (
                WorkerError(
                    error_class=error.error_class,
                    code="PINNED_WORKER_EXHAUSTED",
                    message=f"The selected worker {self.pinned_worker_id} could not finish the task.",
                    hint="Wake or repair that worker, choose another GPU, or run locally.",
                )
                if self.pinned_worker_id and self.attempts_remaining <= 0
                else error
            )
            self._set_state(
                TaskState.TIMEOUT if error.error_class is ErrorClass.TIMEOUT else TaskState.FAILED,
                now=now,
            )
            return
        # Retryable and budget remains — back to the queue for a different worker.
        self._set_state(TaskState.QUEUED, now=now)

    def cancel(self, *, reason: str = "cancelled by user", now: Optional[float] = None) -> None:
        if self.state.terminal:
            return
        stamp = resolve(now)
        for attempt in self.attempts:
            if not attempt.state.terminal:
                attempt.state = AttemptState.CANCELLED
                attempt.finished_at = stamp
        self.error = WorkerError(
            error_class=ErrorClass.TERMINAL, code="CANCELLED", message=reason
        )
        self._set_state(TaskState.CANCELLED, now=stamp)

    def mark_disconnected(
        self, attempt_id: str, *, grace_seconds: float, now: Optional[float] = None
    ) -> Optional[Attempt]:
        """The worker's stream dropped. Start the grace window; fail nothing.

        This is the whole §10-vs-§21 fix in one method: we record that we have
        stopped hearing from the worker, and we wait. If it reconnects with a
        result, that result commits and no duplicate work ever happened.
        """
        attempt = self.get_attempt(attempt_id)
        if attempt is None or attempt.state.terminal:
            return None
        stamp = resolve(now)
        attempt.disconnected_at = stamp
        attempt.grace_expires_at = stamp + grace_seconds
        return attempt

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "operation": self.operation,
            "engine": self.engine,
            "model_id": self.model_id,
            "state": self.state.value,
            "priority": int(self.priority),
            "attempts": [a.to_dict() for a in self.attempts],
            "max_attempts": self.max_attempts,
            "error": self.error.to_dict() if self.error else None,
            "result_ref": self.result_ref,
        }


def reconcile(
    task: Task,
    *,
    worker_id: str,
    worker_in_flight: Iterable[str],
    resume_lease_seconds: float,
    now: Optional[float] = None,
) -> Optional[tuple[str, str]]:
    """Reconcile one task against what a reconnecting worker claims to hold.

    Returns an action for the caller: ``"resume"`` (worker is still validly
    running it), ``"cancel_zombie"`` (worker is running something we have
    already written off — tell it to stop), or ``None`` (nothing to do).

    Without this, a control-plane restart orphans every live task: the server
    forgets, the worker keeps burning GPU, and the user sees a spinner that
    never resolves.

    ``resume_lease_seconds`` is required rather than defaulted because deadline
    policy belongs to the caller — and because a resume that cleared the
    disconnect bookkeeping without renewing the lease left the attempt holding
    an expiry stamped before the outage, so the very next sweep failed the task
    it had just recovered.
    """
    claimed = set(worker_in_flight)
    attempt = task.active_attempt
    if attempt is not None and attempt.worker_id == worker_id:
        if attempt.attempt_id in claimed:
            # renew_lease clears disconnected_at/grace_expires_at itself.
            attempt.renew_lease(resume_lease_seconds, now=now)
            return ("resume", attempt.attempt_id)
        # We think it is running; the worker says otherwise. The worker is the
        # source of truth for what is executing on it.
        task.lose_attempt(attempt.attempt_id, now=now)
        return None
    for attempt_id in claimed:
        known = task.get_attempt(attempt_id)
        if known is not None and known.state.terminal:
            return ("cancel_zombie", attempt_id)
    return None


__all__ = [
    "Attempt",
    "AttemptState",
    "LifecycleError",
    "PriorityClass",
    "Task",
    "TaskState",
    "reconcile",
]
