"""The committed protocol stubs must match the .proto they came from.

The stubs are committed so that neither the installer, the frozen build, nor
Docker needs ``protoc``. The cost of that convenience is drift: someone edits
``worker_v1.proto``, forgets to regenerate, and the mismatch surfaces later as
a baffling attribute error at runtime — or worse, as a field that silently
never arrives. Regenerating into a temporary directory and diffing turns that
into a red test with an obvious fix.
"""
from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEN_DIR = os.path.join(_REPO, "backend", "worker", "protocol", "gen")
_GENERATED_FILES = ("worker_v1_pb2.py", "worker_v1_pb2_grpc.py", "worker_v1_pb2.pyi")

pytest.importorskip(
    "grpc_tools",
    reason="grpcio-tools is a dev dependency; the committed stubs are what ship.",
)

sys.path.insert(0, os.path.join(_REPO, "scripts"))


def _normalise(text: str) -> list[str]:
    """Ignore trailing whitespace and blank-line churn between protoc builds."""
    return [line.rstrip() for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize("filename", _GENERATED_FILES)
def test_committed_stubs_match_the_proto(tmp_path, filename):
    import gen_worker_protocol

    assert gen_worker_protocol.generate(tmp_path) == 0, "protoc failed"

    fresh = (tmp_path / filename).read_text(encoding="utf-8")
    with open(os.path.join(_GEN_DIR, filename), encoding="utf-8") as fh:
        committed = fh.read()

    assert _normalise(committed) == _normalise(fresh), (
        f"{filename} is out of date with worker_v1.proto. "
        "Run: uv run python scripts/gen_worker_protocol.py"
    )


def test_generated_package_is_importable():
    """protoc emits a flat sibling import that only resolves if the output
    directory happens to be on sys.path; the generator rewrites it."""
    from worker.protocol.gen import worker_v1_pb2 as pb
    from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc

    assert hasattr(pb_grpc, "WorkerServiceStub")
    assert pb.TaskRef(task_id="t").task_id == "t"


def test_download_progress_is_additive_and_frame_14_stays_reserved():
    from worker.protocol.gen import worker_v1_pb2 as pb

    field = pb.WorkerMessage.DESCRIPTOR.fields_by_name["download_progress"]
    assert field.number == 13
    source = open(
        os.path.join(_REPO, "backend", "worker", "protocol", "worker_v1.proto"),
        encoding="utf-8",
    ).read()
    assert "reserved 14;" in source


def test_stub_import_is_relative():
    with open(os.path.join(_GEN_DIR, "worker_v1_pb2_grpc.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "from . import worker_v1_pb2" in source
    assert "\nimport worker_v1_pb2" not in source
