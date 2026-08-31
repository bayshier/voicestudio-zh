"""Integration coverage for the GPU gateway's scheduler contract."""

from __future__ import annotations

import inspect

import pytest


@pytest.mark.asyncio
async def test_remote_run_uses_real_scheduler_contract(tmp_path):
    # Import inside the test: tests/backend/conftest.py deliberately reloads
    # services.* and worker dependencies between tests.
    from services import gpu_gateway
    from worker.lifecycle import TaskState
    from worker.pool import WorkerPool
    from worker.routing import Decision
    from worker.scheduler import Scheduler

    result_path = tmp_path / "result.bin"
    result_path.write_bytes(b"remote result")
    scheduler = Scheduler(WorkerPool(), persist=False)
    submitted = []

    # This listener stands in for the worker transport.  Submission, lookup,
    # waiting, and the Task object all remain the real scheduler implementation.
    def complete_from_transport(event, task):
        if event == "queued":
            submitted.append(task)
            task.state = TaskState.COMPLETED
            task.result_ref = str(result_path)

    scheduler.on_change(complete_from_transport)

    class Plane:
        running = True
        pool = None

        def __init__(self):
            self.scheduler = scheduler

    local_called = False

    def local():
        nonlocal local_called
        local_called = True
        return b"local result"

    decision = Decision(
        remote=True,
        worker_id="worker-1",
        label="Test worker",
        reason="chosen",
    )
    result = await gpu_gateway.run(
        "tts",
        local=gpu_gateway.LocalCall(local),
        remote=gpu_gateway.RemoteCall(
            engine="test-engine",
            model_id="test:model",
            deadline_seconds=1,
            decode=lambda remote_result: remote_result.read(),
        ),
        decision=decision,
        control_plane=Plane(),
    )

    assert result == b"remote result"
    assert local_called is False
    assert submitted[0].pinned_worker_id == decision.worker_id


def test_gateway_dependency_call_signatures_are_compatible():
    """Keep every gateway call into its orchestration dependencies bindable."""
    from services.model_manager import check_gpu_admission, run_on_gpu_pool_guarded
    from worker import routing
    from worker.pool import WorkerPool
    from worker.scheduler import Scheduler
    from worker.service import ControlPlane
    from worker.transport.server import WorkerServicer

    target = object()
    calls = [
        (routing.decide, (target,), {"op": "tts"}),
        (
            Scheduler.submit,
            (target,),
            {
                "operation": "tts",
                "engine": "test-engine",
                "model_id": "test:model",
                "params": {},
                "idempotency_key": "request-1",
                "deadline_seconds": 1,
                "pinned_worker_id": "worker-1",
            },
        ),
        (Scheduler.wait, (target, "task-1"), {"timeout": 1}),
        (Scheduler.get, (target, "task-1"), {}),
        (Scheduler.cancel, (target, "task-1"), {"reason": "cancelled"}),
        (ControlPlane.cancel, (target, "task-1"), {"reason": "cancelled"}),
        (WorkerPool.get, (target, "worker-1"), {}),
        (WorkerServicer.prewarm, (target, "worker-1"), {"engine": "test-engine"}),
        (
            WorkerServicer.prewarm,
            (target, "worker-1"),
            {"engine": "", "model_id": "repo/model", "download_if_missing": True},
        ),
        (check_gpu_admission, (), {"what": "GPU job", "executor": target}),
        (
            run_on_gpu_pool_guarded,
            (lambda: None,),
            {
                "what": "GPU job",
                "timeout": 1,
                "queue_timeout": 1,
                "min_vram_gb": 1,
                "executor": target,
            },
        ),
    ]

    for callee, args, kwargs in calls:
        inspect.signature(callee).bind(*args, **kwargs)
