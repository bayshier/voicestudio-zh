"""The GPU target: one user-chosen destination, resolved honestly.

The property that matters most: `decide()` is the single answer used by both
the generation path and the header badge. If they could disagree, the badge
would claim work goes somewhere it does not — worse than showing nothing.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from worker import registry, routing
from worker.errors import ErrorClass, WorkerError
from worker.identity import WorkerKeypair, issue_session
from worker.pool import WorkerPool


@pytest.fixture
def db(tmp_path, monkeypatch):
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


@pytest.fixture
def settings(monkeypatch):
    """In-memory settings, so a target choice does not touch the real store."""
    store: dict[str, str] = {}
    monkeypatch.setattr("services.settings_store.get_text", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr("services.settings_store.set_text", lambda k, v: store.__setitem__(k, v))
    return store


class _Plane:
    """A control plane stub with a real pool, so availability is real logic."""

    def __init__(self, running=True):
        self.running = running
        self.pool = WorkerPool()


def _enroll(name="desktop-4090", **kw):
    return registry.enroll_worker(
        name=name, public_key=WorkerKeypair.generate().public_bytes(), **kw
    )


def _connect(plane, record, *, now=None):
    # Real clock: ConnectedWorker.stale() compares against now(), so a pinned
    # 1970 timestamp would make every worker look dead.
    now = time.time() if now is None else now
    return plane.pool.connect(
        record,
        session=issue_session(worker_id=record.id, key_id=record.key_id, epoch=1, now=now),
        epoch=1,
        backend="cuda",
        now=now,
    )


# ── The choice ─────────────────────────────────────────────────────────────


def test_defaults_to_local(settings):
    assert routing.get_target_id() == routing.LOCAL


def test_choice_is_persisted(settings):
    """A target that resets on restart would quietly send work elsewhere."""
    routing.set_target_id("abc123")
    assert routing.get_target_id() == "abc123"
    assert settings["worker_target"] == "abc123"


def test_empty_choice_means_local(settings):
    assert routing.set_target_id("") == routing.LOCAL
    assert routing.get_target_id() == routing.LOCAL


def test_a_broken_settings_store_falls_back_to_local(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr("services.settings_store.get_text", _boom)
    assert routing.get_target_id() == routing.LOCAL


# ── The list ───────────────────────────────────────────────────────────────


def test_local_is_always_first_and_always_available(db, settings):
    targets = routing.list_targets(_Plane(running=False))
    assert targets[0].id == routing.LOCAL
    assert targets[0].available is True


def test_enrolled_workers_are_listed(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)

    targets = routing.list_targets(plane)

    assert [t.id for t in targets] == [routing.LOCAL, worker.id]
    assert targets[1].label == "desktop-4090"
    assert targets[1].available is True


@pytest.mark.parametrize(
    "setup,detail",
    [
        (lambda w: registry.set_enabled(w.id, False), "disabled"),
        (lambda w: registry.revoke(w.id), "removed"),
    ],
)
def test_unusable_workers_say_why(db, settings, setup, detail):
    """A greyed entry has to name something the user can act on."""
    plane = _Plane()
    worker = _enroll()
    _connect(plane, worker)
    setup(worker)

    listed = {t.id: t for t in routing.list_targets(plane)}
    entry = listed.get(worker.id)
    if detail == "removed":
        # Revoked workers drop out of the list entirely.
        assert entry is None
        return
    assert entry.available is False
    assert entry.detail == detail


def test_a_disconnected_worker_is_listed_but_offline(db, settings):
    plane = _Plane()
    worker = _enroll()

    entry = [t for t in routing.list_targets(plane) if t.id == worker.id][0]

    assert entry.connected is False
    assert entry.available is False
    assert entry.detail == "offline"


def test_an_unapproved_worker_is_not_available(db, settings):
    plane = _Plane()
    worker = _enroll(consent_granted=False)
    _connect(plane, worker)

    entry = [t for t in routing.list_targets(plane) if t.id == worker.id][0]
    assert entry.available is False
    assert entry.detail == "not approved"


def test_a_paused_worker_reports_its_breaker(db, settings):
    plane = _Plane()
    worker = _enroll()
    _connect(plane, worker)
    # Real clock again: a breaker opened at a 1970 timestamp would have its
    # cooldown long expired by now and would read as closed.
    for _ in range(3):
        plane.pool.breakers.record_failure(
            worker.id,
            "e:m",
            WorkerError(error_class=ErrorClass.TRANSIENT, code="X", message="x"),
            now=time.time(),
        )

    entry = [t for t in routing.list_targets(plane) if t.id == worker.id][0]
    assert entry.available is False
    assert "paused" in entry.detail


# ── The decision ───────────────────────────────────────────────────────────


def test_local_choice_runs_locally(db, settings):
    assert routing.decide(_Plane()).remote is False


def test_chosen_and_reachable_runs_remotely(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    decision = routing.decide(plane)

    assert decision.remote is True
    assert decision.worker_id == worker.id
    assert decision.label == "desktop-4090"


def test_only_the_chosen_worker_is_used(db, settings):
    """Others may be connected; standby means they receive nothing."""
    plane = _Plane()
    chosen = _enroll("chosen")
    standby = _enroll("standby")
    _connect(plane, chosen)
    _connect(plane, standby)
    routing.set_target_id(chosen.id)

    assert routing.decide(plane).worker_id == chosen.id


def test_an_offline_choice_falls_back_locally_and_says_so(db, settings):
    """A user whose remote GPU went to sleep should get their audio, not an
    error about infrastructure."""
    plane = _Plane()
    worker = _enroll("desktop-4090")
    routing.set_target_id(worker.id)

    decision = routing.decide(plane)

    assert decision.remote is False
    assert "desktop-4090" in decision.reason
    assert "offline" in decision.reason


def test_feature_off_falls_back_locally(db, settings):
    worker = _enroll()
    routing.set_target_id(worker.id)
    decision = routing.decide(_Plane(running=False))
    assert decision.remote is False
    assert "turned off" in decision.reason


def test_a_deleted_choice_falls_back_rather_than_stranding(db, settings):
    routing.set_target_id("nosuchworker")
    decision = routing.decide(_Plane())
    assert decision.remote is False
    assert "no longer exists" in decision.reason


def test_a_revoked_choice_falls_back(db, settings):
    plane = _Plane()
    worker = _enroll()
    _connect(plane, worker)
    routing.set_target_id(worker.id)
    registry.revoke(worker.id)

    assert routing.decide(plane).remote is False


# ── Per-operation coverage ─────────────────────────────────────────────────


def test_a_ported_operation_still_runs_remotely(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    assert routing.decide(plane, op="tts").remote is True


def test_dubbing_claims_the_remote_target_after_its_producer_lands(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    decision = routing.decide(plane, op="dub")

    assert decision.remote is True
    assert decision.worker_id == worker.id


def test_dubbing_reports_reachability_after_its_producer_lands(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    routing.set_target_id(worker.id)

    assert "offline" in routing.decide(plane, op="dub").reason


def test_omitting_the_operation_answers_for_the_target(db, settings):
    """The picker's own menu asks about the machine, not about one job."""
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    assert routing.decide(plane).remote is True
    assert routing.supports_operation(None) is True


def test_unknown_operations_are_not_remote(db, settings):
    assert routing.supports_operation("tts") is True
    assert routing.supports_operation("dictation") is False
    assert routing.supports_operation("something-new") is False


def test_status_answers_for_the_operation_it_was_asked_about(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    payload = routing.status(plane, op="dub")

    assert payload["op"] == "dub"
    assert payload["target"] == worker.id, "the user's choice is not lost"
    assert payload["active"]["remote"] is True
    # What the menu needs to say "gpu2 · TTS only".
    assert payload["remote_operations"] == ["audiobook", "dub", "dub_segments", "tts"]


# ── The badge cannot lie ───────────────────────────────────────────────────


def test_status_agrees_with_decide(db, settings):
    """One answer, used by both the router and the badge."""
    plane = _Plane()
    worker = _enroll("desktop-4090")
    _connect(plane, worker)
    routing.set_target_id(worker.id)

    payload = routing.status(plane)

    assert payload["target"] == worker.id
    assert payload["active"] == routing.decide(plane).to_dict()
    assert payload["active"]["remote"] is True


def test_status_shows_local_when_the_choice_is_unreachable(db, settings):
    plane = _Plane()
    worker = _enroll("desktop-4090")
    routing.set_target_id(worker.id)

    payload = routing.status(plane)

    # The user's CHOICE is still the worker — that is not lost — but the
    # ACTIVE answer is local, and the badge must show the active one.
    assert payload["target"] == worker.id
    assert payload["active"]["remote"] is False
