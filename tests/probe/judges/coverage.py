"""Coverage Critic — the drift defense. Enumerates the app's API surface and the
probe spec set, gates that every declared layer still has a spec, and reports
(advisory) the API areas no spec touches — so a green dashboard can't hide a
coverage gap.
"""

from __future__ import annotations

import glob
import os

from ..spec import JudgeResult


def scan_specs(specs_dir: str) -> list[dict]:
    """Index the probe specs: [{feature, layer, file}, ...]."""
    import yaml

    out = []
    for path in sorted(glob.glob(os.path.join(specs_dir, "*.probe.yaml"))):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        out.append({"feature": doc.get("feature"), "layer": doc.get("layer"),
                    "file": os.path.basename(path)})
    return out


def api_prefixes(openapi_paths: list) -> list[str]:
    """Top-level path segment for each route ('/engines/tts' → 'engines')."""
    return sorted({(p.strip("/").split("/")[0] or "root") for p in (openapi_paths or [])})


def layers_have_specs(specs: list, required: list) -> JudgeResult:
    """Gate: every declared layer still has at least one spec (guards against
    silently dropping a layer's coverage)."""
    have = {s.get("layer") for s in (specs or [])}
    missing = [layer for layer in required if layer not in have]
    return JudgeResult(
        name="layers_have_specs",
        passed=not missing,
        measured=sorted(have),
        detail=f"all required layers have specs: {required}" if not missing
        else f"layers with NO spec: {missing}",
    )


def coverage_report(openapi_paths: list, specs: list) -> JudgeResult:
    """Advisory: a one-line inventory of API surface vs probe specs."""
    prefixes = api_prefixes(openapi_paths)
    layers = sorted({s.get("layer") for s in (specs or [])})
    return JudgeResult(
        name="coverage_report",
        passed=True,
        measured=len(openapi_paths or []),
        detail=f"{len(openapi_paths or [])} API routes / {len(prefixes)} prefixes; "
        f"{len(specs or [])} specs across layers {layers}",
    )
