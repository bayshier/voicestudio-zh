"""#1224: a truncated model download aborted the install instead of retrying.

The reporter's log tail, captured just before the backend was SIGKILLed:

    httpx.RemoteProtocolError: peer closed connection without sending complete
    message body (received 4084175097 bytes, expected 4580080592)

That is a 4.6 GB model dying at 4.0 GB — the single most retry-worthy failure
in the whole download path, and it was retried nowhere:

* the installer's retry loop caught ``(HfHubHTTPError, LocalEntryNotFoundError,
  OSError)``. ``httpx.RemoteProtocolError`` inherits ``Exception``, NOT
  ``OSError``, so it escaped all five attempts;
* ``is_hf_connectivity_error`` — the single source of truth for "transient
  download failure" — had no truncation signature, so even a widened catch
  would have classified it as permanent;
* the engine load path (``VoxCPM.from_pretrained``) had no retry at all.

The HF cache is resumable (correctly-sized blobs are skipped by hash), so a
retry continues rather than restarting — which is what makes retrying correct
here and not merely hopeful.
"""
from __future__ import annotations

import pytest

from core.failure import is_hf_connectivity_error
from services import tts_backend


# ── classification ───────────────────────────────────────────────────────


def test_the_reporters_error_is_recognised_as_transient():
    assert is_hf_connectivity_error(
        "peer closed connection without sending complete message body "
        "(received 4084175097 bytes, expected 4580080592)"
    )


@pytest.mark.parametrize(
    "reason",
    [
        # urllib3 / http.client wording for the same truncation.
        "IncompleteRead(4084175097 bytes read, 495905495 more expected)",
        "ProtocolError('Connection broken: IncompleteRead(…)')",
        "http.client.IncompleteRead: incomplete read",
        "Response ended prematurely",
    ],
)
def test_other_truncation_wordings_are_recognised(reason):
    assert is_hf_connectivity_error(reason)


def test_a_real_failure_is_still_permanent():
    """Widening the net must not make genuine errors retry forever."""
    assert not is_hf_connectivity_error("401 Unauthorized: invalid token")
    assert not is_hf_connectivity_error("No such file or directory: config.json")
    assert not is_hf_connectivity_error("CUDA out of memory")


# ── the engine load path retries ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_BACKOFF_S", "0")


def test_truncated_download_is_retried_and_succeeds(monkeypatch):
    calls = []

    def loader():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError(
                "peer closed connection without sending complete message body"
            )
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2") == "model"
    assert len(calls) == 3


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_RETRIES", "2")
    calls = []

    def loader():
        calls.append(1)
        raise RuntimeError("peer closed connection without sending complete message body")

    with pytest.raises(RuntimeError):
        tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2")
    assert len(calls) == 2


def test_a_non_transient_failure_is_not_retried():
    calls = []

    def loader():
        calls.append(1)
        raise ValueError("checkpoint has no config.json")

    with pytest.raises(ValueError):
        tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2")
    assert len(calls) == 1, "a permanent failure must fail fast, not retry"


def test_the_closed_client_path_still_resets_the_session(monkeypatch):
    """#880's behaviour must survive the widening."""
    reset = []
    import huggingface_hub.utils as hub_utils

    monkeypatch.setattr(hub_utils, "close_session", lambda: reset.append(1), raising=False)

    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2") == "model"
    assert reset, "the HF session must be reset before retrying a closed client"


def test_the_closed_client_path_stays_single_shot(monkeypatch):
    """The two failure shapes get deliberately different budgets. A closed
    client is a client-state bug, not a network condition: if a FRESH session
    hits it again, repeating won't help, and #880 chose to surface it. Only
    the transient-download path gets the multi-attempt budget."""
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_RETRIES", "5")
    calls = []

    def loader():
        calls.append(1)
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    with pytest.raises(RuntimeError):
        tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2")
    assert len(calls) == 2, "closed-client must stay single-shot regardless of the budget"


def test_voxcpm2_load_actually_retries_a_truncated_download(monkeypatch):
    """The #1224 call site itself, exercised — not grepped.

    An earlier version of this test asserted the wrapper's NAME appeared in the
    method source, which would have passed even if the wrapper were called with
    the wrong argument or its result discarded (#1224 review)."""
    import sys
    import types

    calls = []

    class _FakeVoxCPM:
        @staticmethod
        def from_pretrained(checkpoint, **kw):
            calls.append(checkpoint)
            if len(calls) < 3:
                raise RuntimeError(
                    "peer closed connection without sending complete message body"
                )
            return f"model:{checkpoint}"

    monkeypatch.setitem(
        sys.modules, "voxcpm", types.SimpleNamespace(VoxCPM=_FakeVoxCPM)
    )
    monkeypatch.setenv("OMNIVOICE_VOXCPM_MODEL", "openbmb/VoxCPM2")
    monkeypatch.setattr(
        tts_backend.VoxCPM2Backend, "is_available", classmethod(lambda cls: (True, ""))
    )
    monkeypatch.setattr(tts_backend, "_voxcpm_upgrade_hint", lambda: None)

    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded()

    assert backend._model == "model:openbmb/VoxCPM2"
    assert len(calls) == 3, "the load must retry, not fail on the first truncation"


def test_voxcpm2_load_still_fails_fast_on_a_real_error(monkeypatch):
    import sys
    import types

    calls = []

    class _FakeVoxCPM:
        @staticmethod
        def from_pretrained(checkpoint, **kw):
            calls.append(1)
            raise ValueError("checkpoint has no config.json")

    monkeypatch.setitem(
        sys.modules, "voxcpm", types.SimpleNamespace(VoxCPM=_FakeVoxCPM)
    )
    monkeypatch.setattr(
        tts_backend.VoxCPM2Backend, "is_available", classmethod(lambda cls: (True, ""))
    )
    monkeypatch.setattr(tts_backend, "_voxcpm_upgrade_hint", lambda: None)

    with pytest.raises(ValueError):
        tts_backend.VoxCPM2Backend()._ensure_loaded()
    assert len(calls) == 1


# ── the installer's retry loop catches it ────────────────────────────────


def test_installer_retries_a_real_remoteprotocolerror():
    """The #1224 root cause, against a REAL httpx exception instance.

    An earlier version asserted `"is_hf_connectivity_error" in getsource(...)`,
    which passed merely because the module imports it — it would not have
    noticed the loop ignoring the classifier entirely (#1224 review)."""
    import httpx

    from api.routers.setup.download import _is_retryable_download_error

    truncated = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(received 4084175097 bytes, expected 4580080592)"
    )
    assert not isinstance(truncated, OSError), (
        "if this ever becomes an OSError the original bug is gone, but the "
        "classification path must still hold"
    )
    assert _is_retryable_download_error(truncated)


def test_installer_does_not_retry_a_cancel_or_a_real_error():
    from api.routers.setup.download import (
        _InstallCancelled,
        _is_retryable_download_error,
    )

    assert not _is_retryable_download_error(_InstallCancelled())
    assert not _is_retryable_download_error(ValueError("no such repo"))
    assert not _is_retryable_download_error(RuntimeError("401 Unauthorized"))


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_installer_does_not_retry_a_settled_hub_verdict(status):
    """Review finding (#1224): every HfHubHTTPError was retried, so a wrong
    token or a gated repo burned all five attempts with backoff before showing
    the user the same message. The verdict is settled — fail fast."""
    import httpx
    from huggingface_hub.utils import HfHubHTTPError

    from api.routers.setup.download import _is_retryable_download_error

    response = httpx.Response(status, request=httpx.Request("GET", "https://hf.co/x"))
    assert not _is_retryable_download_error(
        HfHubHTTPError(f"{status} Client Error", response=response)
    )


def test_installer_still_retries_a_server_side_hub_error():
    """A 5xx (or a rate-limit) is transient and must keep retrying."""
    import httpx
    from huggingface_hub.utils import HfHubHTTPError

    from api.routers.setup.download import _is_retryable_download_error

    for status in (429, 500, 503):
        response = httpx.Response(
            status, request=httpx.Request("GET", "https://hf.co/x")
        )
        assert _is_retryable_download_error(
            HfHubHTTPError(f"{status} Error", response=response)
        ), status


def test_installer_still_retries_the_original_type_based_cases():
    """Widening to classification must not drop what the old tuple caught."""
    from huggingface_hub.utils import LocalEntryNotFoundError

    from api.routers.setup.download import _is_retryable_download_error

    assert _is_retryable_download_error(OSError("connection reset by peer"))
    assert _is_retryable_download_error(LocalEntryNotFoundError("offline"))


# ── the streaming path leaves an OOM breadcrumb ──────────────────────────


def test_stream_path_checks_memory_before_loading():
    """The reporter was SIGKILLed on a 16 GB Mac. /generate has logged a
    low-memory advisory since the earlier reports of that class, but the
    STREAMING path — which the desktop UI tries first — did not, so the load
    most likely to tip the machine over left no trail in the captured stderr
    tail a SIGKILL report has to go on.

    Structural rather than behavioural: reaching the call needs a live
    WebSocket session. Kept honest by also resolving the symbol it names, so a
    rename or removal on either side fails here (#1224 review).
    """
    import inspect

    from api.routers import tts_stream
    from services.memory_budget import log_if_low

    assert callable(log_if_low)
    src = inspect.getsource(tts_stream)
    assert "from services.memory_budget import log_if_low" in src
    assert "log_if_low(f\"TTS stream load" in src


def test_the_two_budgets_do_not_share_a_counter(monkeypatch):
    """Review finding (#1224): the closed-client reset incremented the same
    counter as the download retries, so a session reset followed by transient
    failures left a resumable multi-GB download one attempt short of its
    configured budget."""
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_RETRIES", "3")
    import huggingface_hub.utils as hub_utils

    monkeypatch.setattr(hub_utils, "close_session", lambda: None, raising=False)

    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        raise RuntimeError("peer closed connection without sending complete message body")

    with pytest.raises(RuntimeError):
        tts_backend._retry_once_with_fresh_hf_client(loader, "VoxCPM2")
    # 1 closed-client + a full budget of 3 download attempts.
    assert len(calls) == 4


@pytest.mark.parametrize("bad", ["inf", "-inf", "nan"])
def test_a_non_finite_backoff_falls_back_to_the_default(monkeypatch, bad):
    """Review finding (#1224): `float("inf")` parses fine and then makes
    `sleep(inf)` raise OverflowError, replacing a retryable download failure
    with an unrelated crash that hides the original error."""
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_BACKOFF_S", bad)
    assert tts_backend._float_env("OMNIVOICE_MODEL_LOAD_BACKOFF_S", 2.0) == 2.0
