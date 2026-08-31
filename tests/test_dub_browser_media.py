"""Browser-safe media normalization for local dubbing uploads (#1643/#1644)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import dub_pipeline as dp


def _run_local_ingest(tmp_path, monkeypatch, *, input_type="video"):
    source_path = tmp_path / ("source.wav" if input_type == "audio" else "source.mp4")
    source_path.write_bytes(b"source")
    normalized_path = tmp_path / "source.browser.mp4"
    normalized_path.write_bytes(b"normalized")
    normalized = []
    saved = []

    async def ensure(job_id, path):
        assert job_id == "browser_media"
        normalized.append(path)
        return str(normalized_path)

    def factory(_job_id):
        async def run_proc(cmd, **_kwargs):
            output = next((str(arg) for arg in cmd if str(arg).endswith(".wav")), None)
            if output:
                with open(output, "wb") as handle:
                    handle.write(b"RIFF")
            return SimpleNamespace(returncode=0), b"", b""

        return run_proc

    monkeypatch.setattr(dp, "_ensure_browser_playable_mp4_for_job", ensure)
    monkeypatch.setattr(dp, "run_proc_factory", factory)
    monkeypatch.setattr(dp.sf, "info", lambda _path: SimpleNamespace(frames=16000, samplerate=16000))
    monkeypatch.setattr(dp, "compute_file_hash", lambda _path: "content-hash")
    monkeypatch.setattr(
        dp,
        "find_cached_job",
        lambda *_args: {
            "job_id": "cached",
            "vocals_path": None,
            "no_vocals_path": None,
            "thumb_path": None,
            "scene_cuts": [],
        },
    )
    monkeypatch.setattr(
        dp,
        "put_and_save_job",
        lambda _job_id, job, **_kwargs: saved.append(job.copy()) or True,
    )

    async def drain():
        return [
            event
            async for event in dp.ingest_pipeline(
                "browser_media",
                str(tmp_path),
                {"kind": "file", "path": str(source_path), "input_type": input_type},
            )
        ]

    asyncio.run(drain())
    return source_path, normalized_path, normalized, saved


def test_local_video_is_normalized_before_job_is_persisted(tmp_path, monkeypatch):
    source, normalized_path, normalized, saved = _run_local_ingest(tmp_path, monkeypatch)

    assert normalized == [str(source)]
    assert saved[-1]["video_path"] == str(normalized_path)


def test_audio_only_ingest_does_not_attempt_video_transcode(tmp_path, monkeypatch):
    source, _normalized_path, normalized, saved = _run_local_ingest(
        tmp_path, monkeypatch, input_type="audio"
    )

    assert normalized == []
    assert saved[-1]["video_path"] == str(source)


def test_upload_normalization_propagates_cancellation_to_registered_process(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    started = asyncio.Event()
    cleaned = asyncio.Event()
    seen = {}

    monkeypatch.setattr(dp, "_probe_codecs", lambda _path: ("vp9", "opus"))
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")

    def factory(job_id):
        seen["job_id"] = job_id

        async def run_proc(_cmd, *, timeout):
            seen["timeout"] = timeout
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        return run_proc

    monkeypatch.setattr(dp, "run_proc_factory", factory)

    async def cancel_normalization():
        task = asyncio.create_task(
            dp._ensure_browser_playable_mp4_for_job("cancel-job", str(source))
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("normalization did not propagate cancellation")
        await asyncio.wait_for(cleaned.wait(), timeout=1)

    asyncio.run(cancel_normalization())
    assert seen == {"job_id": "cancel-job", "timeout": 1800.0}


@pytest.mark.parametrize("failure", [RuntimeError("spawn failed"), asyncio.TimeoutError()])
def test_upload_normalization_failure_keeps_the_original_media(tmp_path, monkeypatch, failure):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(dp, "_probe_codecs", lambda _path: ("vp9", "opus"))
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")

    def factory(_job_id):
        async def run_proc(_cmd, *, timeout):
            assert timeout == 1800.0
            raise failure

        return run_proc

    monkeypatch.setattr(dp, "run_proc_factory", factory)

    result = asyncio.run(dp._ensure_browser_playable_mp4_for_job("failed-job", str(source)))

    assert result == str(source)
    assert source.exists()


def test_successful_remux_with_unsupported_codecs_is_transcoded(tmp_path, monkeypatch):
    source = tmp_path / "source.webm"
    source.write_bytes(b"source")
    target = tmp_path / "source.mp4"
    commands = []

    monkeypatch.setattr(dp, "_probe_codecs", lambda _path: ("vp9", "opus"))
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")

    def factory(_job_id):
        async def run_proc(cmd, *, timeout):
            assert timeout == 1800.0
            commands.append(cmd)
            target.write_bytes(b"normalized")
            return SimpleNamespace(returncode=0), b"", b""

        return run_proc

    monkeypatch.setattr(dp, "run_proc_factory", factory)

    result = asyncio.run(dp._ensure_browser_playable_mp4_for_job("codec-job", str(source)))

    assert result == str(target)
    assert [cmd[cmd.index("-c:v") + 1] for cmd in commands] == ["copy", "libx264"]
    assert [cmd[cmd.index("-c:a") + 1] for cmd in commands] == ["copy", "aac"]
    assert not source.exists()
