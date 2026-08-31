"""No unresolved git conflict markers in tracked text files.

A botched merge resolution can `git add` a file that still contains
`<<<<<<<` / `=======` / `>>>>>>>` markers — git treats it as resolved and
commits the markers as literal content. Neither the changelog-style linter
nor the format check catches that, so a marked-up file can ship (it did on a
branch during the #1190/#1191/#1177 fix batch, 2026-07-20). This scans every
tracked file git itself considers text and fails on any leftover marker.
"""
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# Conventional 7-char git markers, anchored at line start.
_MARKERS = ("<<<<<<<", ">>>>>>>", "=======")


def _tracked_text_files():
    # -z NUL-delimits; --numstat prints "-\t-\t<path>" for binary files, so a
    # leading digit/other means text. Simpler: ask git which files are text via
    # check-attr is overkill — list all tracked, skip unreadable/binary on read.
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def test_no_unresolved_conflict_markers():
    offenders = []
    for rel in _tracked_text_files():
        # This test file legitimately contains the marker literals above.
        if rel == "tests/test_no_conflict_markers.py":
            continue
        path = _ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — not a text conflict
        for i, line in enumerate(text.splitlines(), 1):
            # A real marker is the token at column 0 followed by space/newline
            # (`======` alone is common in docs rules, so require the 7-run and,
            # for `=`, that the whole line is exactly seven `=`).
            if line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "=======":
                offenders.append(f"{rel}:{i}: {line[:60]}")
    assert not offenders, "Unresolved conflict markers found:\n  " + "\n  ".join(offenders)
