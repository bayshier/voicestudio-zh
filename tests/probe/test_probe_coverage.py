"""Coverage Critic — enumerate the OpenAPI surface + probe specs, gate that every
declared layer still has a spec, and report (advisory) the API inventory."""

from __future__ import annotations

import os

from . import spec as probe_spec
from .judges import coverage as C

_SPECS_DIR = os.path.join(os.path.dirname(__file__), "specs")
_SPEC = os.path.join(_SPECS_DIR, "coverage_critic.probe.yaml")


def test_coverage_critic(probe_report, boot_capture):
    specs = C.scan_specs(_SPECS_DIR)
    spec = probe_spec.load_spec(_SPEC)
    ctx = {"openapi_paths": boot_capture["openapi_paths"], "specs": specs}
    results = probe_spec.run_judges(spec, ctx)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    # The critic actually enumerated a real surface.
    assert len(boot_capture["openapi_paths"]) > 100
    assert len(specs) >= 10


def test_layers_have_specs_detects_gap():
    specs = [{"layer": "media"}, {"layer": "env"}]
    ok = C.layers_have_specs(specs, ["media", "env"])
    assert ok.passed is True
    gap = C.layers_have_specs(specs, ["media", "env", "desktop"])
    assert gap.passed is False and "desktop" in gap.detail
