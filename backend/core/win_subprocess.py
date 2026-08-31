"""Windows: run every child process the backend spawns without a console window.

A desktop GUI app has no console of its own — the Tauri shell spawns the backend
with ``CREATE_NO_WINDOW`` (see ``frontend/src-tauri/src/backend.rs``). On Windows,
when a console-less process spawns a *console* subprocess (ffmpeg for dubbing, an
engine sidecar, ``yt-dlp``, ``demucs``, WhisperX's converter, …), the OS allocates
a brand-new console window for that child, which flashes on screen for its whole
lifetime. During real use that is a storm of black ``cmd`` windows popping up
behind the app — a UX bug, not a functional one, but a loud one.

There are 70+ ``subprocess`` spawn sites across the backend, and more inside
third-party libraries we don't control (``imageio-ffmpeg`` and ``yt-dlp`` both
shell out to ffmpeg themselves). Editing each call site is neither complete nor
future-proof. Instead we install the flag at the single choke point every spawn
funnels through — ``subprocess.Popen`` — so ``subprocess.run`` / ``call`` /
``check_output`` / ``check_call`` (all built on ``Popen``) *and* every library
that uses the stdlib are covered by one auditable change.

No-op on macOS/Linux: there is no per-process console to hide there, so cross-
platform behaviour is unchanged (default-parity rule — the *visible* default,
no stray windows, is now identical on all three). Composes with callers that
already pass ``creationflags`` (e.g. ``CREATE_NEW_PROCESS_GROUP`` in
``services/subprocess_backend.py``): the flags are OR-ed, never replaced. A
caller that *explicitly* asks for a visible console (``CREATE_NEW_CONSOLE``) is
honoured — we only suppress the *accidental* window.
"""

from __future__ import annotations

import subprocess
import sys

# winbase.h flag values (defined here so the pure helper stays importable and
# testable on every platform — `subprocess.CREATE_*` only exist on Windows).
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def add_no_window_flag(kwargs: dict) -> dict:
    """Return ``kwargs`` with ``CREATE_NO_WINDOW`` OR-ed into ``creationflags``.

    Left unchanged when the caller explicitly requested a visible console
    (``CREATE_NEW_CONSOLE``). Pure and platform-agnostic so it can be unit
    tested off Windows; the actual ``Popen`` patch is Windows-only.
    """
    flags = kwargs.get("creationflags", 0) or 0
    if flags & CREATE_NEW_CONSOLE:
        return kwargs
    kwargs["creationflags"] = flags | CREATE_NO_WINDOW
    return kwargs


def install() -> None:
    """Idempotently patch ``subprocess.Popen`` so every child runs windowless.

    Must run before anything spawns (imported at the very top of ``main.py``).
    No-op off Windows and on repeat calls.
    """
    if sys.platform != "win32":
        return
    if getattr(subprocess.Popen, "_omnivoice_no_window", False):
        return

    _orig_init = subprocess.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **add_no_window_flag(kwargs))

    subprocess.Popen.__init__ = _patched_init  # type: ignore[assignment]
    subprocess.Popen._omnivoice_no_window = True  # type: ignore[attr-defined]
