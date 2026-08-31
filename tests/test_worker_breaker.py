"""Circuit breaking and failure attribution.

These tests encode why the reputation system was replaced. Each one is a
failure mode the score-based design had: quarantining a worker for being busy,
for being the wrong size, or for having home Wi-Fi; and never letting it back.
"""
from __future__ import annotations

from worker.breaker import (
    Attribution,
    Breaker,
    BreakerRegistry,
    BreakerState,
    attribute,
)
from worker.errors import ErrorClass, WorkerError


def _err(cls: ErrorClass, code: str = "BOOM") -> WorkerError:
    return WorkerError(error_class=cls, code=code, message="boom")


# ── Attribution ────────────────────────────────────────────────────────────


def test_capacity_rejection_is_never_charged():
    """A worker declining because it is full is doing its job. Charging it is
    how a healthy busy fleet self-quarantines."""
    assert attribute(_err(ErrorClass.CAPACITY, "WORKER_AT_CAPACITY")) is Attribution.NEUTRAL


def test_capability_mismatch_is_never_charged():
    """A 4 GB card refusing a 6 GB engine is not flakiness (#1226)."""
    assert attribute(_err(ErrorClass.CAPABILITY, "INSUFFICIENT_MEMORY")) is Attribution.NEUTRAL


def test_disconnect_is_never_charged():
    """'Connection failure → larger penalty' is backwards for home networks —
    it quarantines every consumer worker within a day."""
    assert attribute(_err(ErrorClass.TRANSIENT, "WORKER_DISCONNECTED")) is Attribution.NEUTRAL


def test_user_cancellation_is_never_charged():
    assert attribute(_err(ErrorClass.TERMINAL, "CANCELLED")) is Attribution.NEUTRAL


def test_server_restart_is_never_charged():
    assert attribute(_err(ErrorClass.TRANSIENT, "SERVER_RESTART")) is Attribution.NEUTRAL


def test_real_worker_failures_are_charged():
    assert attribute(_err(ErrorClass.TRANSIENT, "ENGINE_CRASHED")) is Attribution.WORKER
    assert attribute(_err(ErrorClass.TIMEOUT, "EXECUTION_TIMEOUT")) is Attribution.WORKER


def test_mass_failure_suppresses_all_penalties():
    assert attribute(_err(ErrorClass.TRANSIENT), mass_failure=True) is Attribution.INFRA


# ── Breaker mechanics ──────────────────────────────────────────────────────


def _breaker() -> Breaker:
    return Breaker(worker_id="w1", model_key="indextts:IndexTTS-2")


def test_breaker_opens_after_consecutive_failures():
    b = _breaker()
    assert b.allows(now=0) is True
    for _ in range(2):
        assert b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0) is False
    assert b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0) is True
    assert b.state is BreakerState.OPEN
    assert b.allows(now=0) is False


def test_one_success_clears_the_count():
    """Consecutive, not cumulative — a breaker holds no grudge, which is the
    whole reason it cannot decay into permanent quarantine."""
    b = _breaker()
    b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    b.record_success(now=0)
    assert b.consecutive_failures == 0
    b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    assert b.state is BreakerState.CLOSED


def test_neutral_failures_never_open_the_breaker():
    b = _breaker()
    for _ in range(10):
        b.record_failure(_err(ErrorClass.CAPACITY), attribution=Attribution.NEUTRAL, now=0)
    assert b.state is BreakerState.CLOSED


def test_cooldown_elapses_into_a_half_open_probe():
    """The recovery path the score design never had: work flows again by
    itself, with no special 'test workload' that does not exist in a TTS
    product."""
    b = _breaker()
    for _ in range(3):
        b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    assert b.allows(now=30) is False
    assert b.allows(now=61) is True
    assert b.state is BreakerState.HALF_OPEN


def test_successful_probe_closes_the_breaker():
    b = _breaker()
    for _ in range(3):
        b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    b.allows(now=61)
    b.record_success(now=61)
    assert b.state is BreakerState.CLOSED


def test_failed_probe_reopens_with_a_longer_cooldown():
    b = _breaker()
    for _ in range(3):
        b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    first_retry = b.retry_at
    b.allows(now=61)
    b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=61)
    assert b.state is BreakerState.OPEN
    assert b.retry_at - 61 > first_retry - 0, "repeated trips must back off further"


def test_cooldown_is_capped():
    b = _breaker()
    for trip in range(20):
        for _ in range(3):
            b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
        b.state = BreakerState.CLOSED
    assert b.retry_at - 0 <= 30 * 60


def test_operator_can_force_close():
    """The user fixed the machine and knows it — a breaker with no manual
    clear is the quarantine trap again."""
    b = _breaker()
    for _ in range(3):
        b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    b.force_close()
    assert b.allows(now=0) is True


def test_summary_is_explainable_to_a_user():
    b = _breaker()
    assert b.describe(now=0) == "OK"
    for _ in range(3):
        b.record_failure(_err(ErrorClass.TRANSIENT), attribution=Attribution.WORKER, now=0)
    text = b.describe(now=10)
    assert "Paused after" in text and "retrying in" in text


# ── Registry ───────────────────────────────────────────────────────────────


def test_breakers_are_scoped_per_model():
    """A model that OOMs on an M2 must not stop it serving engines it handles."""
    reg = BreakerRegistry()
    reg.note_worker("m2")
    for _ in range(3):
        reg.record_failure("m2", "big:Model", _err(ErrorClass.TRANSIENT), now=0)

    assert reg.allows("m2", "big:Model", now=0) is False
    assert reg.allows("m2", "small:Model", now=0) is True


def test_fleet_wide_failures_are_read_as_infrastructure():
    """One network blip must not quarantine every worker and then overload
    whatever survived with the retry wave."""
    reg = BreakerRegistry()
    for wid in ("w1", "w2", "w3", "w4"):
        reg.note_worker(wid)

    attributions = [
        reg.record_failure(wid, "e:m", _err(ErrorClass.TRANSIENT), now=0)[0]
        for wid in ("w1", "w2", "w3")
    ]

    assert attributions[-1] is Attribution.INFRA
    assert all(reg.allows(w, "e:m", now=0) for w in ("w1", "w2", "w3"))


def test_isolated_failure_is_still_charged_in_a_healthy_fleet():
    reg = BreakerRegistry()
    for wid in ("w1", "w2", "w3", "w4"):
        reg.note_worker(wid)
    attribution, _ = reg.record_failure("w1", "e:m", _err(ErrorClass.TRANSIENT), now=0)
    assert attribution is Attribution.WORKER


def test_forgetting_a_worker_drops_its_breakers():
    reg = BreakerRegistry()
    reg.note_worker("w1")
    for _ in range(3):
        reg.record_failure("w1", "e:m", _err(ErrorClass.TRANSIENT), now=0)
    reg.forget_worker("w1")
    assert reg.allows("w1", "e:m", now=0) is True
