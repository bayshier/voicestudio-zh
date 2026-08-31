from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.asyncio
async def test_abandon_callback_waits_for_running_worker_to_finish():
    from services.model_manager import run_on_gpu_pool_guarded

    started = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()

    def job():
        started.set()
        assert release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = asyncio.create_task(
            run_on_gpu_pool_guarded(
                job,
                executor=executor,
                timeout=1,
                on_abandon=cleaned.set,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        cancelled = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)

        assert not cleaned.is_set()
        release.set()
        assert await asyncio.to_thread(cleaned.wait, 1)


@pytest.mark.asyncio
async def test_queued_cancellation_releases_without_running_job():
    from services.model_manager import GpuPoolBusyError, run_on_gpu_pool_guarded

    hog_started = threading.Event()
    release_hog = threading.Event()
    cleaned = threading.Event()
    queued_job_ran = threading.Event()

    def hog():
        hog_started.set()
        assert release_hog.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        hog_future = executor.submit(hog)
        assert hog_started.wait(timeout=1)
        try:
            with pytest.raises(GpuPoolBusyError):
                await run_on_gpu_pool_guarded(
                    queued_job_ran.set,
                    executor=executor,
                    timeout=1,
                    queue_timeout=0.05,
                    on_abandon=cleaned.set,
                )
            assert cleaned.is_set()
            assert not queued_job_ran.is_set()
        finally:
            release_hog.set()
            hog_future.result(timeout=1)
