from __future__ import annotations

import asyncio
import io
import threading

import pytest
from fastapi import UploadFile


@pytest.mark.asyncio
async def test_preview_ffmpeg_does_not_block_event_loop(monkeypatch, tmp_path):
    from api.routers import dub_core

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def slow_ffmpeg(*_args, **_kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=2)

    monkeypatch.setattr(dub_core, "PREVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(dub_core, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dub_core.subprocess, "run", slow_ffmpeg)
    upload = UploadFile(filename="preview.mp4", file=io.BytesIO(b"video"))

    before = loop.time()
    task = asyncio.create_task(dub_core.preview_upload(upload))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert loop.time() - before < 0.5
    finally:
        release.set()

    result = await task
    assert result["audioUrl"].endswith(".wav")
