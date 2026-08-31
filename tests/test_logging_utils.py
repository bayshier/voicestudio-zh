"""Untrusted values cannot forge records at the shared logging seam."""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

import pytest

from core.logging_utils import DEFAULT_LOG_VALUE_LIMIT, log_safe


@pytest.mark.parametrize(
    "value, marker",
    [
        ("x\nFORGED", r"\nFORGED"),
        ("x\rFORGED", r"\rFORGED"),
        ("x\x1b[31mFORGED", r"\x1b[31mFORGED"),
        ("x\x00FORGED", r"\x00FORGED"),
        ("x\u2028FORGED", r"\u2028FORGED"),
        ("x\u2029FORGED", r"\u2029FORGED"),
    ],
)
def test_log_safe_renders_controls_without_losing_forensic_text(value, marker):
    rendered = log_safe(value)
    assert marker in rendered
    assert all(not unicodedata.category(char).startswith("C") for char in rendered)


def test_log_safe_bounds_oversized_values():
    rendered = log_safe("x" * 10_000)
    assert len(rendered) <= DEFAULT_LOG_VALUE_LIMIT
    assert rendered.endswith("…")


def test_log_safe_preserves_unicode_and_never_raises():
    class Broken:
        def __str__(self):
            raise RuntimeError("nope")

    assert log_safe("声 🎙️") == "声 🎙️"
    assert log_safe(Broken()) == "<Broken>"
    assert log_safe(RuntimeError("x\nFORGED")) == r"RuntimeError: x\nFORGED"


def test_formatted_record_stays_on_one_bounded_line(caplog):
    logger = logging.getLogger("test.log-safety")
    payload = "voice.wav\r\nERROR forged\x1b[2J" + ("z" * 10_000)
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("uploaded filename=%s", log_safe(payload))
    message = caplog.records[-1].getMessage()
    assert "\r" not in message and "\n" not in message and "\x1b" not in message
    assert len(message) <= len("uploaded filename=") + DEFAULT_LOG_VALUE_LIMIT


def test_sensitive_logging_sites_emit_metadata_not_paths_keys_or_tracebacks():
    repository_root = Path(__file__).resolve().parents[1]
    sources = {
        path: (repository_root / path).read_text(encoding="utf-8")
        for path in (
            "backend/api/routers/dub_export.py",
            "backend/api/routers/batch.py",
            "backend/api/routers/marketplace.py",
            "backend/api/routers/system.py",
            "backend/services/dub_pipeline.py",
            "backend/services/settings_store.py",
            "backend/services/sonitranslate.py",
        )
    }
    combined = "\n".join(sources.values())
    for unsafe_shape in (
        "Native save wrote %s",
        "Dub mux wrote %s",
        "Published voice %s to marketplace: %s",
        "Set environment variable: %s",
        "Cleared environment variable: %s",
        'logger.exception("Download failed',
        'logger.exception("Extract failed',
        'logger.exception("Ingest pipeline failed',
        'logger.exception("settings_store.get_secret',
        "Submitting dub job to SoniTranslate: %s",
        "SoniTranslate dub complete: %s",
    ):
        assert unsafe_shape not in combined
    assert 'logger.error("settings_store.get_secret: SQLite read failed")' in combined
