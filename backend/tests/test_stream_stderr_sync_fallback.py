"""`run_proc_streaming_stderr` must not crash on the thread-fallback proc.

On event loops without async-subprocess support — notably the Windows
``SelectorEventLoop`` that uvicorn forces under ``--reload`` — ``spawn_subprocess``
returns a thread-backed wrapper (``_AsyncCompatProc``) whose ``.stderr`` is a
plain SYNC pipe, not an asyncio ``StreamReader``. The streaming reader used to do
``await asyncio.wait_for(p.stderr.read(256), …)`` unconditionally; on that wrapper
``p.stderr.read(256)`` returns *bytes*, so ``asyncio.wait_for`` raised
``TypeError: An asyncio.Future, a coroutine or an awaitable is required`` and
crashed the demucs vocal-separation step during dubbing (dev mode on Windows).

The fix keys off the wrapper's ``uses_sync_pipes`` flag: on that loop it runs to
completion via the wrapper's async ``communicate()`` and replays stderr as the
same ``('stderr', line)`` events. These tests pin (1) the fallback no longer
raises and emits the expected line + done events, (2) a nonzero rc still
surfaces, and (3) the native async-StreamReader path is unchanged.
"""
import asyncio
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import dub_pipeline  # noqa: E402


class _FakeSyncProc:
    """Mimics _AsyncCompatProc: async communicate()/wait(), SYNC stderr pipe."""

    uses_sync_pipes = True

    def __init__(self, stderr_bytes: bytes, returncode: int = 0):
        self._stderr_bytes = stderr_bytes
        self._rc = returncode
        self.returncode = None
        # A real sync pipe (io.BufferedReader): .read() returns bytes, not a
        # coroutine — the exact shape that used to break the streaming reader.
        self.stderr = io.BytesIO(stderr_bytes)
        self.stdout = io.BytesIO(b"")
        self.pid = 4321

    async def communicate(self, _input=None):
        self.returncode = self._rc
        return b"", self._stderr_bytes

    async def wait(self):
        self.returncode = self._rc
        return self._rc

    def kill(self):
        pass


class _FakeStreamReader:
    def __init__(self, data: bytes):
        self._data = data
        self._i = 0

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._i : self._i + n]
        self._i += n
        return chunk


class _FakeAsyncProc:
    """Mimics a native asyncio subprocess: .stderr is an async StreamReader."""

    def __init__(self, stderr_bytes: bytes, returncode: int = 0):
        self._rc = returncode
        self.returncode = None
        self.stderr = _FakeStreamReader(stderr_bytes)
        self.stdout = io.BytesIO(b"")
        self.pid = 1234

    async def wait(self):
        self.returncode = self._rc
        return self._rc

    def kill(self):
        pass


@pytest.fixture(autouse=True)
def _neutralize_plumbing(monkeypatch):
    """Isolate the streaming reader from the real semaphore / proc registry."""
    monkeypatch.setattr(dub_pipeline, "_get_semaphore", lambda: asyncio.Semaphore())
    monkeypatch.setattr(dub_pipeline, "register_proc", lambda *a, **k: None)
    monkeypatch.setattr(dub_pipeline, "unregister_proc", lambda *a, **k: None)


async def _collect(cmd):
    events = []
    async for evt in dub_pipeline.run_proc_streaming_stderr("job1", cmd):
        events.append(evt)
    return events


def test_sync_fallback_streams_lines_and_done_without_typeerror(monkeypatch):
    stderr = b"Separating track\r 10%|## |\r100%|####|\ndone\n"
    monkeypatch.setattr(
        dub_pipeline, "_spawn_with_retry",
        lambda *a, **k: _make_coro(_FakeSyncProc(stderr, returncode=0)),
    )
    events = asyncio.run(_collect(["demucs", "in.wav"]))

    lines = [e[1] for e in events if e[0] == "stderr"]
    assert lines == ["Separating track", " 10%|## |", "100%|####|", "done"]

    done = [e for e in events if e[0] == "done"]
    assert len(done) == 1
    assert done[-1] == events[-1]  # done is always last
    assert done[0][1] == 0  # returncode
    assert done[0][2] == stderr  # full stderr bytes preserved


def test_sync_fallback_surfaces_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(
        dub_pipeline, "_spawn_with_retry",
        lambda *a, **k: _make_coro(_FakeSyncProc(b"boom\n", returncode=2)),
    )
    events = asyncio.run(_collect(["demucs", "in.wav"]))
    assert events[-1][0] == "done"
    assert events[-1][1] == 2


def test_native_async_path_unchanged(monkeypatch):
    """A proc without uses_sync_pipes still streams via the async StreamReader."""
    stderr = b"line-a\rline-b\nline-c\n"
    monkeypatch.setattr(
        dub_pipeline, "_spawn_with_retry",
        lambda *a, **k: _make_coro(_FakeAsyncProc(stderr, returncode=0)),
    )
    events = asyncio.run(_collect(["ffmpeg", "-i", "in.mp4"]))
    lines = [e[1] for e in events if e[0] == "stderr"]
    assert lines == ["line-a", "line-b", "line-c"]
    assert events[-1] == ("done", 0, stderr)


async def _make_coro(value):
    return value
