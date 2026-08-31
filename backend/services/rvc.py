"""Optional RVC (Retrieval-based Voice Conversion) post-processing.

Disabled by default. Enable by setting env var OMNIVOICE_RVC_ENABLED=1 and providing:
  OMNIVOICE_RVC_MODEL   — path to .pth voice model
  OMNIVOICE_RVC_INDEX   — path to .index file (optional)

Parameters (with defaults matching voice-pro conventions):
  OMNIVOICE_RVC_PITCH       — semitone shift, -24..+24 (default 0)
  OMNIVOICE_RVC_INDEX_RATE  — 0..1 (default 0.7)
  OMNIVOICE_RVC_FILTER_RAD  — 0..10 (default 3)
  OMNIVOICE_RVC_PROTECT     — 0..0.5 (default 0.33)
  OMNIVOICE_RVC_F0_METHOD   — rmvpe|crepe|pm|harvest (default rmvpe)

The actual RVC library (rvc-python / lib-rvc) is not a hard dependency.
Calls are no-ops unless RVC is enabled AND the library is importable.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger("omnivoice.rvc")


def rvc_config() -> Optional[dict]:
    """Return RVC config dict, or None if disabled/unconfigured."""
    if os.environ.get("OMNIVOICE_RVC_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        return None
    model = os.environ.get("OMNIVOICE_RVC_MODEL", "").strip()
    if not model or not os.path.exists(model):
        logger.warning("RVC enabled but OMNIVOICE_RVC_MODEL missing or invalid: %r", model)
        return None
    return {
        "model_path": model,
        "index_path": os.environ.get("OMNIVOICE_RVC_INDEX", "").strip() or None,
        "pitch": int(os.environ.get("OMNIVOICE_RVC_PITCH", "0")),
        "index_rate": float(os.environ.get("OMNIVOICE_RVC_INDEX_RATE", "0.7")),
        "filter_radius": int(os.environ.get("OMNIVOICE_RVC_FILTER_RAD", "3")),
        "protect": float(os.environ.get("OMNIVOICE_RVC_PROTECT", "0.33")),
        "f0_method": os.environ.get("OMNIVOICE_RVC_F0_METHOD", "rmvpe"),
    }


def is_enabled() -> bool:
    return rvc_config() is not None


_rvc_engine = None


def _get_engine():
    """Lazy-import the RVC library. Returns None if not installed."""
    global _rvc_engine
    if _rvc_engine is not None:
        return _rvc_engine
    try:
        # rvc-python is the most common drop-in. Adapt here if a different lib is used.
        from rvc_python.infer import RVCInference  # type: ignore
    except ImportError:
        logger.warning("RVC enabled but rvc-python not installed; skipping conversion.")
        return None
    cfg = rvc_config()
    if cfg is None:
        return None
    try:
        _rvc_engine = RVCInference(device="cpu")
        _rvc_engine.load_model(cfg["model_path"])
        if cfg["index_path"]:
            _rvc_engine.set_index_path(cfg["index_path"])
        _rvc_engine.set_params(
            f0_method=cfg["f0_method"],
            f0_up_key=cfg["pitch"],
            index_rate=cfg["index_rate"],
            filter_radius=cfg["filter_radius"],
            protect=cfg["protect"],
        )
        return _rvc_engine
    except Exception:
        logger.exception("Failed to initialise RVC engine")
        return None


def apply_rvc(wav_path: str) -> str:
    """Apply RVC to a segment wav in place. Returns the same path.

    No-op when RVC is disabled, unconfigured, or the library is missing.
    Errors during inference are logged and swallowed (returns original path).
    """
    if not is_enabled():
        return wav_path
    engine = _get_engine()
    if engine is None:
        return wav_path
    try:
        engine.infer_file(wav_path, wav_path)
        return wav_path
    except Exception:
        logger.exception("RVC inference failed for %s", wav_path)
        return wav_path
