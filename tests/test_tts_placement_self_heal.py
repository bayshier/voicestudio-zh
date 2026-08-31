"""The TTS model must never stay stranded on CPU after an ASR offload (#1191).

Reported as "generation speed varies greatly depending on the time of day" —
which is a red herring. The bug is fully deterministic:

``offload_tts_for_asr()`` moves the TTS model to CPU to make VRAM room for
WhisperX, and its partner ``restore_tts_after_asr()`` was only reachable on the
dub-transcribe **success** path. Any abort, terminal error, or client
disconnect skipped it — and because ``get_model()`` never re-checked placement,
EVERY subsequent /generate ran on CPU (10-50x slower, CPU pegged) until the
~15-minute idle unload happened to fire. Whether a user hit it came down to
whether they had aborted a dub earlier, which correlates with nothing but
feels like "time of day".

Two independent guarantees are tested here:

1. **Balanced pair** — gen()'s ``finally`` pays the restore debt on every exit
   path, not just success.
2. **Self-heal (the class fix)** — ``get_model()`` verifies placement and moves
   the model back itself, so a *future* unbalanced offload path cannot strand
   it either.

Fail-before: (1) restore was never called on abort/error; (2) ``get_model()``
returned the CPU-resident model untouched.
"""
from __future__ import annotations

import asyncio
import os
import struct
import threading
import wave

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest


# ── Fakes: no real weights, no real torch ─────────────────────────────────
class _FakeDev:
    def __init__(self, type_: str):
        self.type = type_

    def __repr__(self):  # pragma: no cover — debugging aid
        return f"device({self.type})"


class _FakeParam:
    def __init__(self, device: str):
        self.device = _FakeDev(device)


class _FakeTTS:
    """Stand-in for the TTS runtime: records ``.to()`` moves, owns a parameter."""

    def __init__(self, device: str = "cuda"):
        self._param = _FakeParam(device)
        self.moves: list[str] = []

    def parameters(self):
        yield self._param

    def to(self, device):
        device = device if isinstance(device, str) else getattr(device, "type", str(device))
        self.moves.append(device)
        self._param = _FakeParam(device)
        return self

    @property
    def device_type(self) -> str:
        return self._param.device.type


@pytest.fixture
def mm():
    """Resolve the app module at run time — a module-level import goes stale
    under the sys.modules pollution other test modules introduce."""
    import services.model_manager as _mm
    return _mm


@pytest.fixture
def gpu_host(mm, monkeypatch):
    """A dedicated-VRAM host (CUDA) with a loaded TTS model."""
    monkeypatch.setattr(mm, "_has_dedicated_vram", lambda: True)
    monkeypatch.setattr(mm, "get_best_device", lambda: "cuda")
    monkeypatch.setattr(mm, "free_vram", lambda: None)

    def _install(device: str = "cuda") -> _FakeTTS:
        fake = _FakeTTS(device)
        monkeypatch.setattr(mm, "model", fake, raising=False)
        return fake

    return _install


# ── 1. The placement probe ────────────────────────────────────────────────
def test_model_on_the_accelerator_is_not_stranded(mm, gpu_host):
    """The hot path must be a no-op: a healthy model costs one parameter probe."""
    gpu_host("cuda")
    assert mm._stranded_tts_target() is None


def test_model_left_on_cpu_is_reported_stranded(mm, gpu_host):
    """CPU-resident weights on a CUDA box is the #1191 state."""
    gpu_host("cpu")
    assert mm._stranded_tts_target() == "cuda"


def test_unified_memory_is_never_treated_as_stranded(mm, gpu_host, monkeypatch):
    """On Apple Silicon / CPU-only the offload RELEASES the model rather than
    moving it, and CPU is the legitimate home — healing there would be wrong."""
    gpu_host("cpu")
    monkeypatch.setattr(mm, "_has_dedicated_vram", lambda: False)
    assert mm._stranded_tts_target() is None


def test_probe_tolerates_a_model_without_parameters(mm, gpu_host, monkeypatch):
    """An engine wrapper we can't introspect must not break generation."""
    monkeypatch.setattr(mm, "model", object(), raising=False)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: (_ for _ in ()).throw(RuntimeError("no torch")))
    assert mm._stranded_tts_target() is None


# ── 2. The self-heal ──────────────────────────────────────────────────────
def test_ensure_tts_on_device_moves_a_stranded_model_back(mm, gpu_host):
    fake = gpu_host("cpu")
    assert mm.ensure_tts_on_device() is True
    assert fake.moves == ["cuda"]
    assert fake.device_type == "cuda"


def test_ensure_tts_on_device_is_a_noop_when_already_placed(mm, gpu_host):
    fake = gpu_host("cuda")
    assert mm.ensure_tts_on_device() is False
    assert fake.moves == []


def test_self_heal_failure_degrades_to_cpu_never_raises(mm, gpu_host, monkeypatch):
    """An OOM while moving back must leave the pre-fix behaviour (slow), not a
    failed generation."""
    fake = gpu_host("cpu")

    def _boom(_device):
        raise RuntimeError("CUDA out of memory: simulated")

    monkeypatch.setattr(fake, "to", _boom)
    assert mm.ensure_tts_on_device() is False  # no exception escapes


def test_get_model_heals_placement_before_returning(mm, gpu_host):
    """THE CLASS FIX. Fail-before: get_model() returned the CPU-resident model
    untouched, so every generation after a stranded offload ran on CPU."""
    fake = gpu_host("cpu")

    got = asyncio.run(mm.get_model())

    assert got is fake
    assert fake.moves == ["cuda"], "get_model() must move a stranded model back"
    assert fake.device_type == "cuda"


def test_self_heal_from_a_gpu_pool_thread_does_not_deadlock(mm, gpu_host):
    """The move is normally dispatched to the GPU pool so it serializes against
    in-flight inference. But ``OmniVoiceBackend._ensure_loaded()`` reaches
    ``get_model()`` through ``asyncio.run()`` from inside ``generate()`` — i.e.
    already on a GPU-pool worker. Dispatching back into that (1-worker) pool,
    or blocking on the model lock held by the loop that is waiting on us, would
    deadlock. That case must heal inline instead."""
    fake = gpu_host("cpu")
    result = {}

    def _worker():
        result["model"] = asyncio.run(mm.get_model())

    t = threading.Thread(target=_worker, name="gpu-pool_0")
    t.start()
    t.join(timeout=15)

    assert not t.is_alive(), "get_model() deadlocked on a GPU-pool thread"
    assert result["model"] is fake
    assert fake.moves == ["cuda"]


def test_get_model_does_not_move_a_healthy_model(mm, gpu_host):
    """The self-heal must be free on the hot path — no spurious device moves."""
    fake = gpu_host("cuda")
    assert asyncio.run(mm.get_model()) is fake
    assert fake.moves == []


# ── 3. The balanced pair, end to end through the SSE endpoint ─────────────
def _make_wav(path, seconds=1.0, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % int(rate * seconds), *([0] * int(rate * seconds))))


def _run_transcribe_stream(job_id, restored=None):
    from api.routers import dub_core as dc

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        # gen()'s finally hands the restore to the CPU pool fire-and-forget (it
        # runs under GeneratorExit, where awaiting is illegal). If we just
        # `asyncio.run()` and return, the loop can close before the pool thread
        # lands the restore — asyncio.run doesn't shut down our custom _cpu_pool,
        # but the timing race made the assertion flaky under CI load. Block
        # WITHIN this loop until the restore Event is set (on the default
        # executor, while _cpu_pool runs the restore) so the caller sees a
        # settled state — deterministic, no fire-and-forget race.
        if restored is not None:
            await asyncio.get_event_loop().run_in_executor(None, lambda: restored.wait(10))
        else:
            await asyncio.sleep(0)
        return "".join(parts)

    return asyncio.run(_collect())


@pytest.fixture
def transcribe_job(tmp_path, monkeypatch):
    """A dub job whose transcribe stream offloads the TTS model, with the
    offload/restore pair instrumented."""
    from api.routers import dub_core as dc

    job_id = "t_1191"
    audio = tmp_path / "a.wav"
    _make_wav(audio)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    calls: dict = {"offload": 0, "restore": 0, "restored": threading.Event()}

    def _offload():
        calls["offload"] += 1

    def _restore():
        calls["restore"] += 1
        calls["restored"].set()

    monkeypatch.setattr(dc, "offload_tts_for_asr", _offload)
    monkeypatch.setattr(dc, "restore_tts_after_asr", _restore)

    fake_model = type("_FakeModel", (), {"_asr_pipe": None})()

    async def _ok_model():
        return fake_model

    monkeypatch.setattr(dc, "get_model", _ok_model)

    class _FakeASR:
        id = "fake"

        def ensure_loaded(self):
            pass

        def transcribe(self, path, *, word_timestamps=True):
            return {
                "chunks": [{"text": "hi", "timestamp": (0.0, 0.5)}],
                "segments": [],
                "language": "en",
            }

        def unload(self):
            pass

    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda *a, **k: _FakeASR()
    )
    monkeypatch.setattr(
        "services.asr_backend.asr_model_missing_error", lambda *a, **k: None
    )

    try:
        yield job_id, dc._dub_jobs[job_id], calls
    finally:
        dc._dub_jobs.pop(job_id, None)


def test_aborted_transcribe_still_restores_the_tts_model(transcribe_job):
    """THE REPORTED BUG. Fail-before: the restore lived only on the success
    path, so aborting a dub left the TTS model on CPU for the rest of the
    process — and every later generation crawled."""
    job_id, job, calls = transcribe_job
    job["aborted"] = True

    body = _run_transcribe_stream(job_id, calls["restored"])

    assert "event: aborted" in body, body
    assert calls["offload"] == 1, "precondition: the stream must have offloaded"
    # The restore is dispatched fire-and-forget to a thread pool from the stream's
    # `finally` (dub_core: run_in_executor(_cpu_pool, restore_tts_after_asr)), so we
    # wait on the Event rather than assume it's done on return. The wait returns the
    # instant restore fires; the generous timeout only matters on a genuine
    # never-restored regression, and keeps a slow/loaded CI runner from flaking.
    assert calls["restored"].wait(timeout=60), (
        "an aborted transcribe left the TTS model stranded on CPU (#1191)"
    )
    assert calls["restore"] == 1


def test_crashed_transcribe_still_restores_the_tts_model(transcribe_job, monkeypatch):
    """Same debt, different exit path: an unanticipated mid-stream exception."""
    from api.routers import dub_core as dc

    job_id, _job, calls = transcribe_job

    def _boom(*a, **k):
        raise RuntimeError("segmentation exploded: simulated")

    monkeypatch.setattr(dc, "segment_transcript", _boom)

    body = _run_transcribe_stream(job_id, calls["restored"])

    assert "event: error" in body, body
    assert calls["offload"] == 1
    # Same fire-and-forget restore dispatch as the aborted case — wait generously
    # so a loaded CI runner can't flake, while a real never-restored bug still fails.
    assert calls["restored"].wait(timeout=60), (
        "a crashed transcribe left the TTS model stranded on CPU (#1191)"
    )


def test_successful_transcribe_restores_exactly_once(transcribe_job):
    """The finally must not double-restore what the success path already paid."""
    job_id, _job, calls = transcribe_job

    body = _run_transcribe_stream(job_id, calls["restored"])

    assert "event: final" in body, body  # the real success path, not an error exit
    assert calls["offload"] == 1
    assert calls["restore"] == 1, "restore ran twice — the success path already paid it"
