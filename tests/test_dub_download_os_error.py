"""#1225: `download: Unable to download video: [Errno 22] Invalid argument`.

A Windows user hit this on three consecutive URL ingests. Two things made it a
dead end:

* `classify()` matched the generic `"errno 22"` rule (#763, written for the
  ASR path) BEFORE any download rule, so the attached hint told the user to
  check their system TEMP folder — while the failing directory is the job
  folder under the OmniVoice data dir. The one actionable instruction pointed
  at the wrong place.
* The message named neither the target nor the reason, so nothing in it could
  distinguish a full drive from a read-only folder from an antivirus lock.

These tests pin the download-specific class, that the ASR path keeps its own,
and the destination facts now attached to the failure.
"""
from __future__ import annotations

import os

import pytest

from core.failure import _HINTS, build_failure, classify, describe_path_target
from services import dub_pipeline


class _ReachedYtDlp(Exception):
    """Raised in place of any real yt-dlp call — no test here may hit the
    network, and a test that silently does is a test that stopped testing."""


def _block_network(monkeypatch):
    import yt_dlp

    def _explode(*_a, **_k):
        raise _ReachedYtDlp("yt-dlp was invoked")

    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _explode)


# ── classification ───────────────────────────────────────────────────────


def test_download_errno22_is_not_given_the_transcribe_hint():
    reason = "Unable to download video: [Errno 22] Invalid argument"
    assert classify(reason) == "VIDEO_DOWNLOAD_OS_ERROR"

    fields = build_failure(OSError(reason), stage="download")
    assert "TEMP" not in fields["hint"]
    assert "data directory" in fields["hint"]


def test_transcribe_errno22_keeps_its_own_class():
    """#763's class must not be swallowed by the new download rule."""
    assert classify("[Errno 22] Invalid argument") == "OS_INVALID_ARGUMENT"
    assert "TEMP" in _HINTS["OS_INVALID_ARGUMENT"]


@pytest.mark.parametrize(
    "reason",
    [
        "ERROR: unable to open for writing: [Errno 13] Permission denied",
        "yt_dlp.utils.DownloadError: unable to rename file: [Errno 22] Invalid argument",
    ],
)
def test_other_download_write_failures_share_the_class(reason):
    assert classify(reason) == "VIDEO_DOWNLOAD_OS_ERROR"


def test_a_network_download_failure_is_still_network():
    """The new rule must not steal transient failures — those DO retry."""
    reason = "Unable to download video: Connection reset by peer"
    assert classify(reason) == "VIDEO_DOWNLOAD_NETWORK"
    assert dub_pipeline._is_transient_download_error(OSError(reason))


# ── destination diagnosis ────────────────────────────────────────────────


def test_describe_path_target_reports_free_space(tmp_path):
    facts = describe_path_target(str(tmp_path / "original.mp4"))
    assert "MB free" in facts
    assert "does not exist" not in facts


def test_describe_path_target_flags_a_missing_folder(tmp_path):
    facts = describe_path_target(str(tmp_path / "gone" / "original.mp4"))
    assert "does not exist" in facts


def test_describe_path_target_never_raises():
    assert isinstance(describe_path_target("\x00not-a-path"), str)


# ── the failure carries the destination ─────────────────────────────────


def test_os_error_gains_the_destination_facts(tmp_path):
    exc = OSError("Unable to download video: [Errno 22] Invalid argument")
    described = dub_pipeline._with_target_facts(exc, str(tmp_path))

    msg = str(described)
    assert str(tmp_path) in msg
    assert "MB free" in msg
    assert "retrying the same link won't help" in msg
    # Still classifies as the download class — the yt-dlp wording is preserved.
    assert classify(msg) == "VIDEO_DOWNLOAD_OS_ERROR"


def test_network_failures_are_left_alone(tmp_path):
    exc = OSError("Unable to download video: Connection reset by peer")
    assert dub_pipeline._with_target_facts(exc, str(tmp_path)) is exc


def test_exception_types_that_reject_a_message_still_get_the_text(tmp_path):
    import soundfile as sf

    described = dub_pipeline._with_target_facts(
        sf.LibsndfileError(1), str(tmp_path)
    )
    # LibsndfileError takes an int code — the helper must not build one whose
    # str() raises. Either it declined to enrich, or it fell back to a type
    # that works; both are fine, an unprintable exception is not.
    assert isinstance(str(described), str)


def test_enoent_download_failure_also_gets_the_destination_facts(tmp_path):
    """Review finding (#1225): classify() covered ENOENT but the enrichment
    gate did not, so a job folder that vanished after preflight produced a
    disk-classified error that never named the folder — the one fact that
    makes it actionable. Both now read the same signature list."""
    exc = OSError("Unable to download video: [Errno 2] No such file or directory")
    described = dub_pipeline._with_target_facts(exc, str(tmp_path))
    assert str(tmp_path) in str(described)
    assert classify(str(described)) == "VIDEO_DOWNLOAD_OS_ERROR"


def test_the_two_consumers_share_one_signature_list():
    """A drift between "is this a disk problem?" (classify) and "should we name
    the folder?" (_with_target_facts) is what produced the finding above."""
    from core.failure import is_os_write_refusal

    for reason in (
        "Unable to download video: [Errno 2] No such file or directory",
        "Unable to download video: [Errno 22] Invalid argument",
        "ERROR: unable to open for writing: [Errno 13] Permission denied",
        "Unable to download video: [Errno 28] No space left on device",
    ):
        assert is_os_write_refusal(reason), reason
        assert classify(reason) == "VIDEO_DOWNLOAD_OS_ERROR", reason
    assert not is_os_write_refusal("Unable to download video: Connection reset by peer")


def test_unwritable_destination_fails_before_yt_dlp_runs(tmp_path, monkeypatch):
    """The preflight: don't start a download into a folder we can already see
    won't take the file."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    # Patch the module object dub_pipeline actually holds — a string-path
    # patch of "core.failure…" misses it if anything in the suite reloaded
    # the module, and the test then falls through to a REAL network call.
    monkeypatch.setattr(
        dub_pipeline.failure, "describe_path_target",
        lambda _p: "the folder is not writable",
    )
    _block_network(monkeypatch)

    with pytest.raises(OSError) as excinfo:
        dub_pipeline.yt_download_sync("https://example.com/v", str(job_dir))

    msg = str(excinfo.value)
    assert str(job_dir) in msg
    assert "not writable" in msg
    # Review finding (#1225): the preflight message classified as NOTHING, so
    # the user got no hint at all — the very failure mode this PR fixes. It
    # must carry both an OS-refusal signature and download context.
    assert classify(msg) == "VIDEO_DOWNLOAD_OS_ERROR"
    assert "data directory" in build_failure(excinfo.value, stage="download")["hint"]


def test_preflight_lets_a_healthy_folder_through(tmp_path, monkeypatch):
    """A writable folder must not be blocked — the preflight only rejects what
    it can positively see is broken."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _block_network(monkeypatch)

    # Reaching yt-dlp IS the pass condition: the preflight let it through.
    with pytest.raises(_ReachedYtDlp):
        dub_pipeline.yt_download_sync("https://example.com/v", str(job_dir))
    assert os.path.isdir(job_dir)
