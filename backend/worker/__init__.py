"""Distributed worker support — protocol v1 domain core.

Scope note: this package holds the *domain* of the worker protocol — the task
lifecycle, deadline policy, capacity derivation, failure attribution, circuit
breaking, worker identity, and worker persistence. It deliberately contains no
network I/O and imports no gRPC: every rule in here is unit-testable without a
socket, and the transport layer (added later) is a thin adapter over it.

The wire contract lives in ``protocol/worker_v1.proto`` and is the only artifact
shared with the future Go control plane. See ``docs/remote-workers.md``.
"""
