"""Injectable clock.

Every deadline, lease, grace window, and cooldown in this package takes an
optional ``now``. The obvious spelling — ``now or time.time()`` — is a trap:
``0.0`` is falsy, so a caller that pins time at the epoch silently gets the
wall clock instead. That makes tests lie (they pass while measuring real time)
and would make any future replay or simulation harness quietly wrong.

One helper, used everywhere, so the mistake cannot recur.
"""
from __future__ import annotations

import time
from typing import Optional


def resolve(now: Optional[float] = None) -> float:
    """Return ``now`` when supplied — including ``0.0`` — else the wall clock."""
    return time.time() if now is None else float(now)


__all__ = ["resolve"]
