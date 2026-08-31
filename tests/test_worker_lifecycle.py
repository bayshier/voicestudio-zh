"""Task/attempt lifecycle — the duplicate-execution rules.

These tests encode the §10-vs-§21 fix: a disconnect is an unknown outcome, not
a failure, and a result that arrives late still commits exactly once.
"""
from __future__ import annotations

import pytest

from worker.errors import ErrorClass, WorkerError
from worker.lifecycle import (
    AttemptState,
    LifecycleError,
    PriorityClass,
    Task,
    TaskState,
    reconcile,
)


def _task(**kw) -> Task:
    defaults = dict(
        task_id="t1",
        operation="tts",
        engine="indextts",
        model_id="IndexTTS-2",
    )
    defaults.update(kw)
    return Task(**defaults)


def _err(cls: ErrorClass, code: str = "BOOM") -> WorkerError:
    return WorkerError(error_class=cls, code=code, message="boom")


# ── Happy path ─────────────────────────────────────────────────────────────


def test_full_lifecycle_reaches_completed():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    assert task.state is TaskState.ASSIGNED

    task.accept(attempt.attempt_id)
    assert task.state is TaskState.ACCEPTED
    task.model_loading(attempt.attempt_id)
    assert task.state is TaskState.MODEL_LOADING
    task.start(attempt.attempt_id)
    assert task.state is TaskState.RUNNING
    task.uploading(attempt.attempt_id)
    assert task.state is TaskState.RESULT_UPLOADING

    committed, attempt = task.commit_result(attempt.attempt_id, result_ref="a1")
    assert committed is True
    assert task.state is TaskState.COMPLETED
    assert task.result_ref == "a1"
    assert attempt.state is AttemptState.COMMITTED


def test_model_loading_is_optional():
    """A warm worker goes straight from ACCEPTED to RUNNING."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    assert task.state is TaskState.RUNNING


def test_illegal_transition_raises():
    task = _task()
    with pytest.raises(LifecycleError):
        task.commit_result("nope")
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.cancel()
    with pytest.raises(LifecycleError):
        task.assign(worker_id="w2", session_epoch=1)
    assert task.get_attempt(attempt.attempt_id).state is AttemptState.CANCELLED


# ── The §10 / §21 contradiction ────────────────────────────────────────────


def test_disconnect_does_not_fail_the_attempt():
    """A dropped connection starts a grace window and nothing else.

    This is the whole point: if we failed here and reassigned, the worker that
    is still rendering would produce a second, duplicate execution.
    """
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)

    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1000.0)

    assert task.state is TaskState.RUNNING
    assert attempt.state is AttemptState.RUNNING
    assert attempt.grace_expires_at == 1045.0
    assert attempt.grace_expired(now=1044.0) is False
    assert attempt.grace_expired(now=1046.0) is True


def test_reconnect_inside_grace_window_commits_without_a_second_attempt():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1000.0)

    # Worker comes back carrying a finished result.
    committed, _ = task.commit_result(attempt.attempt_id, result_ref="a1", now=1020.0)

    assert committed is True
    assert task.state is TaskState.COMPLETED
    assert task.attempt_count == 1, "no duplicate attempt was ever created"


def test_grace_expiry_loses_the_attempt_and_requeues():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1000.0)

    task.lose_attempt(attempt.attempt_id, now=1046.0)

    assert attempt.state is AttemptState.LOST
    assert task.state is TaskState.QUEUED
    assert "w1" in task.excluded_workers, "a retry must be a different worker"


def test_late_result_from_a_lost_attempt_still_commits():
    """The work was really done. Throwing it away wastes a finished dub."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.lose_attempt(attempt.attempt_id, now=1046.0)
    assert task.state is TaskState.QUEUED

    committed, _ = task.commit_result(attempt.attempt_id, result_ref="a1", now=1050.0)

    assert committed is True
    assert task.state is TaskState.COMPLETED


def test_duplicate_commit_is_acked_but_not_applied():
    """At-least-once delivery, exactly-once commit."""
    task = _task()
    a1 = task.assign(worker_id="w1", session_epoch=1)
    task.accept(a1.attempt_id)
    task.start(a1.attempt_id)
    first, _ = task.commit_result(a1.attempt_id, result_ref="first")
    second, attempt = task.commit_result(a1.attempt_id, result_ref="second")

    assert first is True
    assert second is False, "a redelivered result must not commit twice"
    assert task.result_ref == "first"


def test_first_commit_wins_and_supersedes_its_sibling():
    """Both attempts finished. One result is authoritative; the other is ACKed
    and dropped so the worker stops redelivering it."""
    task = _task(max_attempts=3)
    a1 = task.assign(worker_id="w1", session_epoch=1)
    task.accept(a1.attempt_id)
    task.start(a1.attempt_id)
    task.lose_attempt(a1.attempt_id, now=1000.0)

    a2 = task.assign(worker_id="w2", session_epoch=1)
    task.accept(a2.attempt_id)
    task.start(a2.attempt_id)

    committed_2, _ = task.commit_result(a2.attempt_id, result_ref="w2-result", now=1100.0)
    # w1 resurfaces with its own finished result.
    committed_1, attempt_1 = task.commit_result(a1.attempt_id, result_ref="w1-result", now=1101.0)

    assert committed_2 is True
    assert committed_1 is False
    assert task.result_ref == "w2-result"
    assert attempt_1.state is AttemptState.SUPERSEDED


def test_running_sibling_is_superseded_on_commit():
    task = _task(max_attempts=3)
    a1 = task.assign(worker_id="w1", session_epoch=1)
    task.accept(a1.attempt_id)
    task.start(a1.attempt_id)
    task.lose_attempt(a1.attempt_id, now=1000.0)
    a2 = task.assign(worker_id="w2", session_epoch=1)
    task.accept(a2.attempt_id)
    task.start(a2.attempt_id)

    # w1's attempt was LOST, so re-open it to model "still actually running".
    a1.state = AttemptState.RUNNING
    task.commit_result(a2.attempt_id, result_ref="r", now=1100.0)

    assert a1.state is AttemptState.SUPERSEDED


# ── Fencing ────────────────────────────────────────────────────────────────


def test_stale_epoch_is_rejected():
    """A half-open previous stream must not be able to drive the task."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=7)
    with pytest.raises(LifecycleError, match="stale session epoch"):
        task.accept(attempt.attempt_id, session_epoch=6)
    task.accept(attempt.attempt_id, session_epoch=7)


# ── Retry policy ───────────────────────────────────────────────────────────


def test_capacity_rejection_does_not_exclude_the_worker():
    """A worker that was full will have room later — excluding it would
    shrink the fleet for being busy."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(attempt.attempt_id, _err(ErrorClass.CAPACITY, "WORKER_AT_CAPACITY"))

    assert attempt.state is AttemptState.REJECTED
    assert task.state is TaskState.QUEUED
    assert task.excluded_workers == set()


def test_terminal_error_fails_immediately_without_rotating_the_fleet():
    """The poison-task scenario: a bad input must not visit every worker."""
    task = _task(max_attempts=5)
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(attempt.attempt_id, _err(ErrorClass.TERMINAL, "INVALID_TASK_PARAMS"))

    assert task.state is TaskState.FAILED
    assert task.attempt_count == 1


def test_retries_exhaust_into_a_definitive_failure():
    task = _task(max_attempts=2)
    a1 = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(a1.attempt_id, _err(ErrorClass.TRANSIENT))
    assert task.state is TaskState.QUEUED

    a2 = task.assign(worker_id="w2", session_epoch=1)
    task.fail_attempt(a2.attempt_id, _err(ErrorClass.TRANSIENT))

    assert task.state is TaskState.FAILED
    assert task.attempts_remaining == 0
    assert task.error is not None


def test_excluded_worker_cannot_be_reassigned():
    task = _task(max_attempts=3)
    a1 = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(a1.attempt_id, _err(ErrorClass.TRANSIENT))
    with pytest.raises(LifecycleError, match="excluded"):
        task.assign(worker_id="w1", session_epoch=2)


def test_deadline_exceeded_beats_remaining_attempts():
    task = _task(max_attempts=5)
    task.deadline_at = 500.0
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(attempt.attempt_id, _err(ErrorClass.TRANSIENT), now=600.0)

    assert task.state is TaskState.TIMEOUT
    assert task.error.code == "TASK_DEADLINE_EXCEEDED"


def test_timeout_error_lands_in_timeout_state():
    task = _task(max_attempts=1)
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.fail_attempt(attempt.attempt_id, _err(ErrorClass.TIMEOUT, "EXECUTION_TIMEOUT"))
    assert task.state is TaskState.TIMEOUT


# ── Leases ─────────────────────────────────────────────────────────────────


def test_progress_renews_the_lease_and_clears_disconnect_state():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1000.0)

    attempt.renew_lease(120, now=1010.0)

    assert attempt.disconnected_at is None
    assert attempt.grace_expires_at is None
    assert attempt.lease_expired(now=1100.0) is False
    assert attempt.lease_expired(now=1200.0) is True


def test_a_slow_but_reporting_task_never_expires():
    """Liveness is progress, not wall-clock: a 40-minute dub is not hung."""
    task = _task(operation="dub")
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    now = 0.0
    for _ in range(40):
        now += 60.0
        attempt.renew_lease(120, now=now)
        assert attempt.lease_expired(now=now) is False


# ── Reconciliation ─────────────────────────────────────────────────────────


def test_reconcile_resumes_a_task_the_worker_still_holds():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1000.0)

    action = reconcile(
        task,
        worker_id="w1",
        worker_in_flight=[attempt.attempt_id],
        resume_lease_seconds=120,
        now=1030.0,
    )

    assert action == ("resume", attempt.attempt_id)
    assert attempt.disconnected_at is None
    assert task.state is TaskState.RUNNING


def test_reconcile_renews_the_lease_of_the_attempt_it_resumes():
    """A resume that only cleared the disconnect left the attempt carrying an
    expiry stamped before the outage — so the next sweep failed the task the
    reconnect had just recovered."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=1000.0)
    task.accept(attempt.attempt_id, now=1001.0)
    task.start(attempt.attempt_id, now=1002.0)
    attempt.renew_lease(120, now=1002.0)
    task.mark_disconnected(attempt.attempt_id, grace_seconds=45, now=1010.0)

    reconcile(
        task,
        worker_id="w1",
        worker_in_flight=[attempt.attempt_id],
        resume_lease_seconds=120,
        now=1200.0,
    )

    assert attempt.lease_expired(now=1201.0) is False
    assert attempt.lease_expires_at == 1320.0


def test_reconcile_loses_a_task_the_worker_no_longer_has():
    """The worker is the source of truth for what is executing on it."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)

    action = reconcile(
        task, worker_id="w1", worker_in_flight=[], resume_lease_seconds=120
    )

    assert action is None
    assert attempt.state is AttemptState.LOST
    assert task.state is TaskState.QUEUED


def test_reconcile_flags_a_zombie_the_server_wrote_off():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)
    task.lose_attempt(attempt.attempt_id, now=1046.0)

    action = reconcile(
        task,
        worker_id="w1",
        worker_in_flight=[attempt.attempt_id],
        resume_lease_seconds=120,
    )

    assert action == ("cancel_zombie", attempt.attempt_id)


# ── Lease ceilings and phase anchors ───────────────────────────────────────


def test_a_bounded_renewal_never_pushes_past_the_ceiling():
    """The keepalive's whole safety property: it may keep an attempt alive,
    but never past the budget of the phase it is keeping alive."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=1000.0)
    task.accept(attempt.attempt_id, now=1001.0)
    task.start(attempt.attempt_id, now=1002.0)

    attempt.renew_lease(120, not_after=1302.0, now=1250.0)

    assert attempt.lease_expires_at == 1302.0
    assert attempt.lease_expired(now=1303.0) is True


def test_an_unbounded_renewal_is_unchanged():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=1000.0)
    attempt.renew_lease(120, now=1250.0)
    assert attempt.lease_expires_at == 1370.0


def test_the_phase_anchor_survives_a_pinned_zero_clock():
    """`0.0` is a legitimate timestamp — an `or` chain would skip it and
    silently anchor the ceiling to the wrong phase (worker/clock.py)."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=0.0)
    task.accept(attempt.attempt_id, now=0.0)
    task.start(attempt.attempt_id, now=0.0)
    attempt.phase_started_at = 0.0

    assert attempt.phase_anchor == 0.0


def test_the_phase_anchor_moves_with_each_phase():
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=1000.0)
    task.accept(attempt.attempt_id, now=1001.0)
    assert attempt.phase_anchor == 1001.0
    task.model_loading(attempt.attempt_id, now=1002.0)
    assert attempt.phase_anchor == 1002.0
    task.start(attempt.attempt_id, now=1300.0)
    assert attempt.phase_anchor == 1300.0


def test_a_restored_attempt_still_has_an_anchor():
    """`phase_started_at` is not persisted, so recovery falls back to the
    phase timestamps that are."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1, now=1000.0)
    task.accept(attempt.attempt_id, now=1001.0)
    task.start(attempt.attempt_id, now=1002.0)
    attempt.phase_started_at = None

    assert attempt.phase_anchor == 1002.0


def test_model_loading_after_started_is_legal():
    """An out-of-order frame, or an engine loading a second model mid-run.
    Raising here ended the read loop and tore down a healthy session (B12)."""
    task = _task()
    attempt = task.assign(worker_id="w1", session_epoch=1)
    task.accept(attempt.attempt_id)
    task.start(attempt.attempt_id)

    task.model_loading(attempt.attempt_id)

    assert task.state is TaskState.MODEL_LOADING


def test_priority_classes_are_two_not_four():
    assert [p.value for p in PriorityClass] == [0, 1]
    assert PriorityClass.INTERACTIVE < PriorityClass.BATCH
