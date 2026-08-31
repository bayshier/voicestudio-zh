"""Safely stop Linux AppImage processes owned by this checkout."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import select
import signal
import time
from typing import Callable


def _appimage_from_environ(raw: bytes) -> str | None:
    for item in raw.split(b"\0"):
        if item.startswith(b"APPIMAGE="):
            return os.fsdecode(item.removeprefix(b"APPIMAGE="))
    return None


def appimage_belongs_to_build(raw: bytes, build_root: Path) -> bool:
    """Require an exact debug AppImage directory and VoiceStudio filename."""
    value = _appimage_from_environ(raw)
    if not value:
        return False
    image = Path(os.path.normpath(value))
    expected_dir = build_root / "bundle" / "appimage"
    return (
        image.parent == expected_dir
        and image.name.startswith("VoiceStudio_")
        and image.name.endswith(".AppImage")
    )


def _process_start_time(process_dir: Path) -> str:
    """Return Linux /proc stat field 22 without splitting a spaced comm field."""
    fields_after_comm = (process_dir / "stat").read_text().rsplit(") ", 1)[1].split()
    return fields_after_comm[19]


def open_owned_processes(
    build_root: Path,
    proc_root: Path = Path("/proc"),
    *,
    pidfd_open: Callable[[int, int], int] | None = None,
    read_start_time: Callable[[Path], str] = _process_start_time,
) -> list[tuple[int, int]]:
    """Open identity-bound handles before inspecting each candidate process."""
    if pidfd_open is None:
        pidfd_open = getattr(os, "pidfd_open", None)
        if pidfd_open is None:
            raise RuntimeError("Linux pidfd support is required")
    owned: list[tuple[int, int]] = []
    for process_dir in sorted(proc_root.iterdir(), key=lambda path: path.name):
        if not process_dir.name.isdecimal():
            continue
        pid = int(process_dir.name)
        try:
            start_before = read_start_time(process_dir)
            pidfd = pidfd_open(pid, 0)
        except (OSError, ProcessLookupError):
            continue
        try:
            raw = (process_dir / "environ").read_bytes()
            start_after = read_start_time(process_dir)
        except (OSError, PermissionError):
            os.close(pidfd)
            continue
        if start_before == start_after and appimage_belongs_to_build(raw, build_root):
            owned.append((pid, pidfd))
            continue
        os.close(pidfd)
    return owned


def _signal_process(pidfd: int, sig: signal.Signals) -> None:
    try:
        signal.pidfd_send_signal(pidfd, sig)
    except ProcessLookupError:
        return


def _poll_exited(poller: select.poll, pending: set[int], deadline: float) -> set[int]:
    exited: set[int] = set()
    while time.monotonic() < deadline and exited != pending:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        exited.update(fd for fd, _ in poller.poll(min(remaining_ms, 100)))
    return exited


def stop_owned_processes(build_root: Path, timeout_s: float = 5.0) -> list[int]:
    """Stop only processes held by pidfds, preventing PID-reuse termination."""
    owned = open_owned_processes(build_root)
    if not owned:
        return []

    poller = select.poll()
    for _, pidfd in owned:
        poller.register(pidfd, select.POLLIN)
        _signal_process(pidfd, signal.SIGTERM)

    pending = {pidfd for _, pidfd in owned}
    exited = _poll_exited(poller, pending, time.monotonic() + timeout_s)
    killed = pending - exited
    for pidfd in killed:
        _signal_process(pidfd, signal.SIGKILL)

    if killed:
        exited.update(_poll_exited(poller, killed, time.monotonic() + timeout_s))

    for _, pidfd in owned:
        os.close(pidfd)
    if exited != pending:
        raise RuntimeError("VoiceStudio processes did not exit; refusing to reset app data")
    return [pid for pid, _ in owned]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_root", type=Path)
    args = parser.parse_args()
    for pid in stop_owned_processes(args.build_root):
        print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
