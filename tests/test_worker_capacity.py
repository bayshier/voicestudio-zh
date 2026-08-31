"""Capacity derivation and zombie-slot accounting.

Every rule here exists because the repo already paid for it: #315
(torch.compile thread affinity → silent audio corruption), #567 (concurrent
clone jobs → sticky CUDA abort), and the un-killable GPU thread that made
``_ResilientGpuPool.reset()`` reclaim nothing.
"""
from __future__ import annotations

import pytest

from worker.capacity import ModelSlot, WorkerCapacity, derive_concurrency

GB = 1024**3


def test_compiled_models_are_always_serial():
    """Thread-local cudagraph state (#315): a second concurrent job produces
    corrupted audio with no exception to catch."""
    assert derive_concurrency(backend="cuda", free_memory_bytes=48 * GB, compiled=True) == 1


def test_apple_unified_memory_is_always_serial():
    """MPS/MLX share memory with everything else the user is running, so a
    number that was safe at config time is not safe at execution time."""
    for backend in ("mps", "mlx", "cpu"):
        assert derive_concurrency(backend=backend, free_memory_bytes=64 * GB) == 1


def test_cuda_concurrency_derives_from_free_memory():
    assert derive_concurrency(backend="cuda", free_memory_bytes=4 * GB) == 1
    assert derive_concurrency(backend="cuda", free_memory_bytes=11 * GB) == 2
    assert derive_concurrency(backend="cuda", free_memory_bytes=24 * GB) == 4


def test_concurrency_is_capped_regardless_of_card_size():
    assert derive_concurrency(backend="cuda", free_memory_bytes=200 * GB) == 4


def test_model_that_does_not_fit_returns_zero():
    """A 4 GB card refusing a 6 GB engine is correct behaviour (#1226), and the
    scheduler must read it as 'send it elsewhere', never as a worker fault."""
    assert derive_concurrency(backend="cuda", free_memory_bytes=4 * GB, min_model_bytes=6 * GB) == 0
    assert derive_concurrency(backend="mps", free_memory_bytes=4 * GB, min_model_bytes=6 * GB) == 0


def test_large_model_reduces_derived_concurrency():
    """A model bigger than the per-job budget takes the space of more than one."""
    small = derive_concurrency(backend="cuda", free_memory_bytes=24 * GB, min_model_bytes=2 * GB)
    large = derive_concurrency(backend="cuda", free_memory_bytes=24 * GB, min_model_bytes=20 * GB)
    assert large < small


# ── Slot accounting ────────────────────────────────────────────────────────


def _cap(**kw) -> WorkerCapacity:
    defaults = dict(worker_id="w1", max_concurrent_tasks=2, backend="cuda")
    defaults.update(kw)
    return WorkerCapacity(**defaults)


def test_reserve_and_release_round_trip():
    cap = _cap()
    assert cap.available_slots == 2
    cap.reserve("indextts", "IndexTTS-2")
    assert cap.available_slots == 1
    cap.release("indextts", "IndexTTS-2")
    assert cap.available_slots == 2


def test_engine_only_capability_and_named_task_share_one_slot():
    """An old worker's model_id="" is a wildcard, not extra capacity."""
    cap = _cap(max_concurrent_tasks=2)
    cap.slots["indextts:"] = ModelSlot(
        engine="indextts", model_id="", derived_concurrency=1
    )

    cap.reserve("indextts", "indextts:default")

    assert set(cap.slots) == {"indextts:"}
    assert cap.can_accept("indextts", "indextts:default") is False
    assert cap.release("indextts", "indextts:default") is True


def test_engine_only_task_matching_remains_tolerant():
    """Restored tasks may still match a capability by engine alone."""
    cap = _cap()
    cap.slots["indextts:"] = ModelSlot(engine="indextts", model_id="")

    cap.reserve("indextts", "")

    assert cap.slot_for("indextts", "") is cap.slots["indextts:"]
    assert cap.release("indextts", "") is True


def test_timeout_parks_a_zombie_slot_instead_of_returning_it():
    """A timed-out GPU job cannot be killed — the thread keeps the device.
    Returning the slot early is how a worker gets overcommitted into an OOM."""
    cap = _cap()
    cap.reserve("indextts", "IndexTTS-2")
    cap.release("indextts", "IndexTTS-2", zombie=True)

    assert cap.zombie_tasks == 1
    assert cap.available_slots == 1, "the stuck thread still holds its slot"

    cap.reap_zombie("indextts", "IndexTTS-2")
    assert cap.available_slots == 2


# ── Getting a parked slot back (B2) ────────────────────────────────────────


def test_a_parked_slot_is_reclaimed_when_its_ttl_runs_out():
    """B2: `reap_zombie` had no caller and the protocol has no message that
    would trigger one, so a single lease expiry at max_concurrent_tasks=1 made
    a worker unschedulable forever — while it heartbeated active=0, free=1
    every twenty seconds."""
    cap = _cap(max_concurrent_tasks=1)
    cap.reserve("indextts", "IndexTTS-2")
    cap.release("indextts", "IndexTTS-2", zombie=True, zombie_ttl_seconds=300, now=1000.0)
    assert cap.available_slots == 0

    assert cap.expire_zombies(now=1299.0) == 0
    assert cap.available_slots == 0, "the thread's own budget has not run out yet"

    assert cap.expire_zombies(now=1301.0) == 1
    assert cap.available_slots == 1


def test_a_busy_heartbeat_does_not_reclaim_a_park():
    """The inverse of the tempting heuristic, and the reason it is wrong.

    Reconciling parks against the worker's reported load reads as sensible —
    "it is already at its ceiling, the headroom is spent" — but at a ceiling of
    one the ONLY task such a worker can report is the wedged one itself. So
    "busy" would drop the park, and the very next idle heartbeat would hand the
    slot out with the GPU thread still alive: the overcommit-into-OOM of
    #730/#1190. Parks come back on a timer or on a restart, never on a report.
    """
    cap = _cap(max_concurrent_tasks=1)
    cap.reserve("indextts", "IndexTTS-2")
    cap.release("indextts", "IndexTTS-2", zombie=True, zombie_ttl_seconds=300, now=1000.0)

    cap.apply_snapshot(active_tasks=1, available_slots=0, now=1001.0)
    assert cap.zombie_tasks == 1

    # …and the follow-up heartbeat that used to collect the freed slot.
    cap.apply_snapshot(active_tasks=0, available_slots=1, now=1002.0)
    assert cap.zombie_tasks == 1, "a park survived one report only to die on the next"

    # The TTL remains the way out, so a timeout still cannot cost the worker
    # permanently.
    cap.apply_snapshot(active_tasks=0, available_slots=1, now=1400.0)
    assert cap.zombie_tasks == 0


def test_an_idle_worker_keeps_its_park():
    """The worker counts asyncio tasks, not GPU threads — its "I am free" is
    exactly the claim the park exists to disbelieve (#730/#1190)."""
    cap = _cap(max_concurrent_tasks=1)
    cap.reserve("indextts", "IndexTTS-2")
    cap.release("indextts", "IndexTTS-2", zombie=True, zombie_ttl_seconds=300, now=1000.0)

    cap.apply_snapshot(active_tasks=0, available_slots=1, now=1010.0)

    assert cap.zombie_tasks == 1
    assert cap.available_slots == 0


def test_a_ttl_is_clamped_to_something_survivable():
    cap = _cap()
    cap.reserve("a", "m")
    cap.release("a", "m", zombie=True, zombie_ttl_seconds=1, now=1000.0)
    assert cap.expire_zombies(now=1030.0) == 0, "a park that short means nothing"

    cap.expire_zombies(now=1_000_000.0)
    cap.reserve("a", "m")
    cap.release("a", "m", zombie=True, zombie_ttl_seconds=10**9, now=1000.0)
    assert cap.expire_zombies(now=1000.0 + 3601) == 1, "one timeout cannot cost a session"


def test_a_double_release_cannot_invent_capacity():
    """`release` guarded its per-model slot but decremented the worker-wide
    count regardless, so two paths ending one attempt overcommitted the
    machine by a slot."""
    cap = _cap()
    cap.reserve("indextts", "IndexTTS-2")

    assert cap.release("indextts", "IndexTTS-2") is True
    assert cap.release("indextts", "IndexTTS-2") is False
    assert cap.active_tasks == 0
    assert cap.available_slots == 2


def test_a_double_release_cannot_invent_a_zombie():
    """A worker-wide zombie counter kept beside the per-slot ones could be
    incremented for a slot that owned nothing — leaving a park no reap could
    ever find."""
    cap = _cap()
    cap.reserve("indextts", "IndexTTS-2")
    cap.release("indextts", "IndexTTS-2", zombie=True, now=1000.0)
    cap.release("indextts", "IndexTTS-2", zombie=True, now=1000.0)

    assert cap.zombie_tasks == 1


def test_the_ceiling_follows_the_worker_down_as_well_as_up():
    """The worker computes active+available as its own max_concurrent_tasks.
    A ceiling we refuse to lower is one we keep dispatching against after the
    worker has said it can no longer honour it."""
    cap = _cap(max_concurrent_tasks=1)
    cap.apply_snapshot(active_tasks=0, available_slots=4, now=1000.0)
    assert cap.max_concurrent_tasks == 4

    cap.apply_snapshot(active_tasks=0, available_slots=1, now=1010.0)
    assert cap.max_concurrent_tasks == 1


def test_worker_wide_cap_binds_before_per_model_cap():
    """Per-model concurrencies are not independent — they share one VRAM pool."""
    cap = _cap(max_concurrent_tasks=1)
    cap.slots["indextts:IndexTTS-2"] = ModelSlot(
        engine="indextts", model_id="IndexTTS-2", derived_concurrency=4
    )
    cap.reserve("indextts", "IndexTTS-2")
    assert cap.can_accept("indextts", "IndexTTS-2") is False


def test_unknown_model_defers_to_the_worker():
    """The worker's accept/reject is authoritative; the scheduler's view is
    advisory and may be stale."""
    cap = _cap()
    assert cap.can_accept("brand-new-engine", "whatever") is True


def test_snapshot_is_absolute_not_a_delta():
    """Out-of-order deltas corrupt the count permanently after a reconnect."""
    cap = _cap()
    cap.reserve("indextts", "IndexTTS-2")
    cap.reserve("indextts", "IndexTTS-2")
    cap.apply_snapshot(active_tasks=0, available_slots=2, resident_models={"indextts:IndexTTS-2"})

    assert cap.active_tasks == 0
    assert cap.available_slots == 2


def test_residency_is_visible_to_the_scheduler():
    """Warm vs cold is the dominant latency term — 8s versus minutes."""
    cap = _cap(resident_models={"indextts:IndexTTS-2"})
    assert cap.is_resident("indextts", "IndexTTS-2") is True
    assert cap.is_resident("cosyvoice", "CosyVoice2") is False


def test_release_never_underflows():
    cap = _cap()
    cap.release("indextts", "IndexTTS-2")
    cap.reap_zombie("indextts", "IndexTTS-2")
    assert cap.active_tasks == 0
    assert cap.zombie_tasks == 0


# ── Latency measurement ────────────────────────────────────────────────────


def _pool_with_worker():
    import time

    from worker.identity import WorkerKeypair, issue_session
    from worker.pool import WorkerPool
    from worker.registry import RemoteWorker

    record = RemoteWorker(
        id="w1", name="w1", key_id="k1", public_key=b"\x00" * 32, created_at=time.time()
    )
    pool = WorkerPool()
    pool.connect(
        record,
        session=issue_session(worker_id="w1", key_id="k1", epoch=1, now=time.time()),
        epoch=1,
        now=time.time(),
    )
    return pool


def test_a_single_sample_is_not_published():
    """The first round trip after connect lands while the worker is still
    importing torch — publishing it shows a wildly wrong number."""
    pool = _pool_with_worker()
    pool.record_latency("w1", 139.4)
    assert pool.get("w1").latency_ms == 0.0


def test_a_startup_outlier_does_not_dominate():
    pool = _pool_with_worker()
    for sample in (139.4, 4.0, 3.8, 4.2, 3.9):
        pool.record_latency("w1", sample)
    # A running average would still be carrying the 139; a median ignores it.
    assert pool.get("w1").latency_ms < 10


def test_the_window_is_bounded():
    pool = _pool_with_worker()
    for sample in range(50):
        pool.record_latency("w1", float(sample))
    assert len(pool.get("w1").latency_samples) <= 5


def test_latency_tracks_a_link_that_degrades():
    pool = _pool_with_worker()
    for _ in range(5):
        pool.record_latency("w1", 4.0)
    assert pool.get("w1").latency_ms == pytest.approx(4.0)

    for _ in range(5):
        pool.record_latency("w1", 120.0)
    assert pool.get("w1").latency_ms == pytest.approx(120.0)


def test_latency_for_an_unknown_worker_is_ignored():
    _pool_with_worker().record_latency("nosuch", 5.0)
