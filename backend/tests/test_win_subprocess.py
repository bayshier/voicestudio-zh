"""Guard: every backend subprocess spawns without a console window on Windows.

Covers the flag logic (pure, runs on every platform) and the idempotent Popen
patch. The visible symptom this prevents — a storm of cmd windows during
dubbing/generation on Windows — can't be asserted in CI, so we pin the
mechanism instead. See core/win_subprocess.py. (#1178)
"""

import subprocess
import sys

from core.win_subprocess import (
    CREATE_NEW_CONSOLE,
    CREATE_NO_WINDOW,
    add_no_window_flag,
    install,
)


def test_adds_flag_to_empty_kwargs():
    assert add_no_window_flag({})["creationflags"] == CREATE_NO_WINDOW


def test_ors_with_existing_flags_never_replaces():
    # subprocess_backend.py passes CREATE_NEW_PROCESS_GROUP (0x200); the window
    # flag must be added on top, not clobbered.
    group = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    out = add_no_window_flag({"creationflags": group})["creationflags"]
    assert out & group, "existing creationflags must be preserved"
    assert out & CREATE_NO_WINDOW, "CREATE_NO_WINDOW must be added"


def test_honours_explicit_new_console():
    # A caller that deliberately wants a visible console is left alone.
    out = add_no_window_flag({"creationflags": CREATE_NEW_CONSOLE})
    assert out["creationflags"] == CREATE_NEW_CONSOLE
    assert not (out["creationflags"] & CREATE_NO_WINDOW)


def test_install_is_idempotent_and_safe():
    # Safe to call repeatedly; a real spawn still works afterwards.
    install()
    install()
    if sys.platform == "win32":
        assert getattr(subprocess.Popen, "_omnivoice_no_window", False)
    # A trivial spawn must still succeed with the patch installed.
    out = subprocess.run(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "ok"
