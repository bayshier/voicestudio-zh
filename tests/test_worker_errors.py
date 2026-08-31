"""Error taxonomy: would retrying somewhere else help, and who pays for it.

The scheduler needs two independent answers per failure, and conflating them is
what produces the two worst behaviours: a poison task that rotates through
every worker in the fleet, and a healthy fleet that quarantines itself for
being busy.
"""
from __future__ import annotations

import pytest

from worker import errors
from worker.errors import ErrorClass, WorkerError, from_exception, from_reason


@pytest.mark.parametrize(
    "cls,retryable",
    [
        (ErrorClass.TRANSIENT, True),
        (ErrorClass.CAPABILITY, True),
        (ErrorClass.CAPACITY, True),
        (ErrorClass.TIMEOUT, True),
        (ErrorClass.TERMINAL, False),
        (ErrorClass.PROTOCOL, False),
    ],
)
def test_retryability_matrix(cls, retryable):
    assert cls.retryable is retryable


@pytest.mark.parametrize(
    "cls,charges",
    [
        (ErrorClass.TRANSIENT, True),
        (ErrorClass.TIMEOUT, True),
        # Declining work you cannot or should not take is not misbehaviour.
        (ErrorClass.CAPACITY, False),
        (ErrorClass.CAPABILITY, False),
        (ErrorClass.TERMINAL, False),
        (ErrorClass.PROTOCOL, False),
    ],
)
def test_chargeability_matrix(cls, charges):
    assert cls.charges_worker is charges


def test_retryable_and_chargeable_are_independent():
    """Capability failures retry elsewhere yet never charge the worker — the
    two questions must not collapse into one flag."""
    assert ErrorClass.CAPABILITY.retryable is True
    assert ErrorClass.CAPABILITY.charges_worker is False


def test_protocol_codes_classify():
    assert errors.classify_code("WORKER_AT_CAPACITY") is ErrorClass.CAPACITY
    assert errors.classify_code("INSUFFICIENT_MEMORY") is ErrorClass.CAPABILITY
    assert errors.classify_code("EXECUTION_TIMEOUT") is ErrorClass.TIMEOUT
    assert errors.classify_code("STALE_EPOCH") is ErrorClass.PROTOCOL
    assert errors.classify_code("INVALID_TASK_PARAMS") is ErrorClass.TERMINAL


def test_docs_taxonomy_keys_are_reused_not_reinvented():
    """A failure that already has a user-facing hint keeps it on the wire."""
    assert errors.classify_code("HF_AUTH_FAILED") is ErrorClass.TERMINAL
    assert errors.classify_code("BROKEN_VENV") is ErrorClass.CAPABILITY
    assert errors.classify_code("VIDEO_DOWNLOAD_NETWORK") is ErrorClass.TRANSIENT


def test_unknown_codes_default_to_retryable():
    """One wasted retry beats permanently failing work that would have run."""
    assert errors.classify_code("SOMETHING_NEW") is ErrorClass.TRANSIENT


def test_secrets_never_reach_the_wire():
    """The worker is a remote machine whose logs the user may never read, so
    scrubbing has to happen before the error is sent, not at display time."""
    err = from_reason("failed with token hf_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert "hf_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in err.message


def test_hint_is_carried_when_the_taxonomy_has_one():
    err = from_reason("boom", code="HF_AUTH_FAILED")
    assert err.error_class is ErrorClass.TERMINAL
    assert err.hint


def test_every_protocol_code_has_an_actionable_hint():
    from core.failure import _HINTS

    assert errors._PROTOCOL_CODES.keys() <= _HINTS.keys()
    assert all(_HINTS[code].strip() for code in errors._PROTOCOL_CODES)


def test_from_exception_never_produces_an_empty_message():
    """Empty str(e) was the root of the 'unknown error' reports (#122/#63)."""

    class Silent(Exception):
        pass

    assert from_exception(Silent()).message


def test_wire_shape_is_complete():
    payload = WorkerError(
        error_class=ErrorClass.TIMEOUT, code="EXECUTION_TIMEOUT", message="m", hint="h"
    ).to_dict()
    assert payload == {
        "error_class": "timeout",
        "code": "EXECUTION_TIMEOUT",
        "message": "m",
        "hint": "h",
        "retryable": True,
    }
