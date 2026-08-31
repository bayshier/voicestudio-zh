import asyncio
import io
import zipfile
from types import SimpleNamespace

import pytest
import torch


def test_worker_runs_dubbing_as_one_task_and_reports_each_segment(monkeypatch):
    from worker.executor import TaskExecutor

    class Backend:
        sample_rate = 24_000
        applies_own_mastering = True

        def generate(self, text, **_kwargs):
            return torch.full((1, len(text) * 10), 0.1)

    monkeypatch.setattr(TaskExecutor, "_load_backend", staticmethod(lambda _engine: Backend()))
    progress = []

    async def report(fraction, stage):
        progress.append((fraction, stage))

    assignment = SimpleNamespace(
        operation="dub_segments", engine="test", params_json=(
            '{"segments":[{"index":3,"text":"one","effect_preset":"raw",'
            '"watermark":false},{"index":8,"text":"two","effect_preset":"raw",'
            '"watermark":false}],"ref_audio":[null,null]}'
        ), inputs=[], deadlines=SimpleNamespace(model_load_seconds=30, execution_seconds=30),
    )
    result = asyncio.run(TaskExecutor().execute(assignment, on_progress=report))

    with zipfile.ZipFile(io.BytesIO(result["payload"])) as bundle:
        assert bundle.namelist() == ["segments/3.wav", "segments/8.wav"]
    assert progress == [(0.5, "segment 1 of 2"), (1.0, "segment 2 of 2")]


def test_remote_dub_decoder_rejects_non_segment_members(tmp_path, monkeypatch):
    from api.routers import dub_generate
    from services.gpu_gateway import RemoteResult

    artifact = tmp_path / "bad.zip"
    with zipfile.ZipFile(artifact, "w") as bundle:
        bundle.writestr("../escape.wav", b"bad")
    monkeypatch.setattr(dub_generate, "DUB_DIR", str(tmp_path / "dubs"))

    with pytest.raises(ValueError, match="unexpected dub artifact member"):
        dub_generate._decode_remote_dub(RemoteResult("task", "worker", "GPU", str(artifact)))
