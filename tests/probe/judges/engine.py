"""Engine-matrix judges — verify the TTS/ASR backend registry (the engine
compatibility hard constraint). Operate on a ``/engines/{family}`` payload
(``{active, backends:[{id, available, reason}, ...]}``) captured from the app.
"""

from __future__ import annotations

from typing import Any

from ..spec import JudgeResult


def _backends(payload: Any) -> list:
    return list((payload or {}).get("backends") or [])


def engines_present(payload: dict, min_count: int = 1) -> JudgeResult:
    n = len(_backends(payload))
    return JudgeResult(
        name="engines_present",
        passed=n >= int(min_count),
        measured=n,
        detail=f"{n} backend(s) registered (min {min_count})",
    )


def active_engine_available(payload: dict) -> JudgeResult:
    """The default/active engine must actually be available — otherwise the app
    boots pointing at an engine that can't synthesize."""
    active = (payload or {}).get("active")
    match = next((b for b in _backends(payload) if b.get("id") == active), None)
    ok = bool(match) and bool(match.get("available"))
    return JudgeResult(
        name="active_engine_available",
        passed=ok,
        measured=active,
        detail=f"active engine {active!r} available" if ok
        else f"active engine {active!r} is NOT available/registered",
    )


def engine_available(payload: dict, engine_id: str) -> JudgeResult:
    b = next((b for b in _backends(payload) if b.get("id") == engine_id), None)
    ok = bool(b) and bool(b.get("available"))
    return JudgeResult(
        name="engine_available",
        passed=ok,
        measured=engine_id,
        detail=f"{engine_id!r} available" if ok else f"{engine_id!r} unavailable/missing",
    )


def unavailable_engines_explained(payload: dict) -> JudgeResult:
    """Every unavailable engine must carry a non-empty ``reason`` — that's how the
    user learns what to install. A silent unavailable engine is a UX bug."""
    silent = [
        b.get("id") for b in _backends(payload)
        if not b.get("available") and not (b.get("reason") or "").strip()
    ]
    return JudgeResult(
        name="unavailable_engines_explained",
        passed=not silent,
        measured=len(silent),
        detail="every unavailable engine explains why" if not silent
        else f"unavailable engines with no reason: {silent}",
    )
