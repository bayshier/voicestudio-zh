"""The single copy-pasteable string that joins a panel to a node.

Host, port and key are three things to transcribe and three ways to fail with
an unhelpful error, and the failures are hard to tell apart from the outside: a
typo'd port and a wrong key both surface as "cannot connect". Collapsing them
into one artifact with one copy button removes the whole class.

    ovnode://ovnode_<secret>@192.168.0.110:7444?fingerprint=<sha256>

Deliberately URL-shaped so it survives being pasted into a chat window, and
deliberately not an http(s) URL so no browser or link scanner treats it as
something to fetch — a credential in a URL that something prefetches is a
credential in somebody's access log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from worker.inbound.keys import KEY_PREFIX

SCHEME = "ovnode"

# Deliberately permissive about the host (IPv4, IPv6, DNS name, .local) and
# strict about the key, because a malformed key is the recoverable mistake and
# an unusual-looking host usually is not a mistake at all.
_KEY_RE = re.compile(rf"^{re.escape(KEY_PREFIX)}[A-Za-z0-9_-]{{16,128}}$")
_FINGERPRINT_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class InvalidConnectionString(ValueError):
    """Raised with a message written for the person who pasted it."""


@dataclass(frozen=True)
class Connection:
    host: str
    port: int
    secret: str
    fingerprint: str

    @property
    def endpoint(self) -> str:
        """host:port, bracketing IPv6 the way gRPC's resolver expects."""
        if ":" in self.host and not self.host.startswith("["):
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"

    def redacted(self) -> str:
        """Safe to log. Keeps enough of the key to tell two panels apart."""
        return f"{SCHEME}://{self.secret[: len(KEY_PREFIX) + 4]}…@{self.endpoint}"


def format_connection(*, host: str, port: int, secret: str, fingerprint: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return (
        f"{SCHEME}://{secret}@{host}:{port}?{urlencode({'fingerprint': fingerprint})}"
    )


def parse_connection(text: str) -> Connection:
    """Parse a pasted connection string, or explain what is wrong with it."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise InvalidConnectionString(
            "Paste the connection string from the GPU machine."
        )

    # A bare host:port is the most likely near-miss — someone copies the
    # address out of the UI and leaves the key behind. Naming that is far more
    # use than "invalid connection string".
    if "://" not in cleaned:
        raise InvalidConnectionString(
            "That looks like an address without a key. Copy the whole "
            f"{SCHEME}://… string from the GPU machine's Settings → Remote workers."
        )

    parts = urlsplit(cleaned)
    if parts.scheme != SCHEME:
        raise InvalidConnectionString(
            f"Expected a {SCHEME}:// connection string, but got {parts.scheme}://."
        )

    # urlsplit puts the credential in `username` only when an `@` is present;
    # without one the key would be silently read as the hostname and the error
    # would come out as a DNS failure.
    if parts.username is None:
        raise InvalidConnectionString(
            "That connection string has no key in it. Copy the whole string, "
            "including the part before the @."
        )

    secret = unquote(parts.username)
    if not _KEY_RE.match(secret):
        hint = (
            " Enrollment tokens (ovw_…) are for the other direction, where the "
            "GPU machine connects to you."
            if secret.startswith("ovw_")
            else ""
        )
        raise InvalidConnectionString(f"That key is not in the expected format.{hint}")

    try:
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        # urlsplit raises rather than returning None for a non-numeric or
        # out-of-range port.
        raise InvalidConnectionString("That port number is not valid.") from exc
    if not host:
        raise InvalidConnectionString("That connection string has no address in it.")
    if not port:
        raise InvalidConnectionString(
            "That connection string has no port in it. It should end in :port."
        )

    fingerprint_values = parse_qs(parts.query, keep_blank_values=True).get(
        "fingerprint", []
    )
    fingerprint = (
        fingerprint_values[0].strip().lower() if len(fingerprint_values) == 1 else ""
    )
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise InvalidConnectionString(
            "That connection string has no valid certificate fingerprint. "
            "Create a new connection string on the GPU machine and copy it in full."
        )

    return Connection(host=host, port=port, secret=secret, fingerprint=fingerprint)


def try_parse(text: str) -> Optional[Connection]:
    try:
        return parse_connection(text)
    except InvalidConnectionString:
        return None
