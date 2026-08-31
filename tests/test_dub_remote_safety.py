"""Safety invariants for the unfinished coarse remote-dubbing port."""

import pytest

from api.routers import dub_generate


def test_local_oom_flushes_the_cuda_cache_before_retry(monkeypatch):
    flushed = []
    monkeypatch.setattr(dub_generate.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(dub_generate.torch.cuda, "empty_cache", lambda: flushed.append(True))

    assert dub_generate._prepare_oom_retry(
        RuntimeError("CUDA out of memory"), execution_target="local"
    )
    assert flushed == [True]


def test_remote_oom_never_flushes_the_control_plane_gpu(monkeypatch):
    flushed = []
    monkeypatch.setattr(dub_generate.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(dub_generate.torch.cuda, "empty_cache", lambda: flushed.append(True))
    error = RuntimeError("CUDA out of memory on worker gpu2")

    with pytest.raises(RuntimeError) as caught:
        dub_generate._prepare_oom_retry(error, execution_target="remote")

    assert caught.value is error
    assert flushed == []


def test_non_oom_does_not_flush_or_retry(monkeypatch):
    flushed = []
    monkeypatch.setattr(dub_generate.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(dub_generate.torch.cuda, "empty_cache", lambda: flushed.append(True))

    assert not dub_generate._prepare_oom_retry(
        RuntimeError("bad reference audio"), execution_target="local"
    )
    assert flushed == []
