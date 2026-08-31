"""Loopback security — system routes must reject non-loopback origins (P0).
Verified against the real backend via the shared boot."""

from __future__ import annotations

import os

from . import spec as probe_spec

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "security.probe.yaml")


def test_loopback_rejection(probe_report, boot_capture):
    spec = probe_spec.load_spec(_SPEC)
    ctx = {"loopback_reject_status": boot_capture["loopback_reject_status"]}
    results = probe_spec.run_judges(spec, ctx)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    assert boot_capture["loopback_reject_status"] == 403  # not 200 — origin was rejected
