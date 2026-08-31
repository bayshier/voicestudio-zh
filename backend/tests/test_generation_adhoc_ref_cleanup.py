from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.asyncio
async def test_abandoned_reader_keeps_adhoc_reference_until_worker_finishes(tmp_path):
    from api.routers.generation import (
        _TempReferenceLease,
        _run_with_reference_lease,
    )
    from services.model_manager import run_on_gpu_pool_guarded

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"voice")
    lease = _TempReferenceLease(str(reference))
    started = threading.Event()
    release_worker = threading.Event()
    worker_read = threading.Event()

    def read_reference():
        started.set()
        assert release_worker.wait(timeout=2)
        assert reference.read_bytes() == b"voice"
        worker_read.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = asyncio.create_task(
            _run_with_reference_lease(
                lease,
                lambda on_abandon: run_on_gpu_pool_guarded(
                    read_reference,
                    executor=executor,
                    timeout=1,
                    on_abandon=on_abandon,
                ),
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        cancelled = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)

        lease.finish_request()
        assert reference.exists()
        release_worker.set()
        assert await asyncio.to_thread(worker_read.wait, 1)

    for _ in range(100):
        if not reference.exists():
            break
        await asyncio.sleep(0.01)
    assert not reference.exists()


def test_normal_request_deletes_adhoc_reference_immediately(tmp_path):
    from api.routers.generation import _TempReferenceLease

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"voice")
    lease = _TempReferenceLease(str(reference))

    release = lease.acquire()
    release()
    lease.finish_request()

    assert not reference.exists()
