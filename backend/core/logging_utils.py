"""Safe rendering for untrusted values at the logging seam."""

from __future__ import annotations

import unicodedata

DEFAULT_LOG_VALUE_LIMIT = 512


def log_safe(value: object, *, limit: int = DEFAULT_LOG_VALUE_LIMIT) -> str:
    """Return one bounded log-line value with controls rendered visibly.

    User filenames, identifiers, subprocess diagnostics, and exception text
    can contain CR/LF or terminal controls. Rendering instead of deleting them
    preserves forensic meaning without allowing forged records or ANSI output.
    """
    try:
        raw = (
            f"{type(value).__name__}: {value}"
            if isinstance(value, BaseException)
            else str(value)
        )
    except Exception:  # pragma: no cover - pathological __str__, safe fallback
        raw = f"<{type(value).__name__}>"
    # Keep the canonical CR/LF transformation explicit: besides documenting
    # the primary invariant, static analyzers recognize this as the sanitizer
    # before the broader Unicode-control rendering below.
    raw = raw.replace("\r", r"\r").replace("\n", r"\n")
    limit = max(8, int(limit))
    parts: list[str] = []
    used = 0
    truncated = False
    for char in raw:
        code = ord(char)
        if char == "\t":
            rendered = r"\t"
        elif unicodedata.category(char).startswith("C") or char in {"\u2028", "\u2029"}:
            rendered = f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"
        else:
            rendered = char
        if used + len(rendered) > limit - 1:
            truncated = True
            break
        parts.append(rendered)
        used += len(rendered)
    if truncated:
        parts.append("…")
    return "".join(parts)
