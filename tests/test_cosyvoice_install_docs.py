from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "docs/engines/cosyvoice.md").read_text(encoding="utf-8")
SIDECAR_SOURCE = (ROOT / "backend/services/sidecar_install.py").read_text(encoding="utf-8")


def test_cosyvoice_docs_match_one_click_installer_registry():
    installer_exists = re.search(
        r'^[ \t]*["\']cosyvoice["\']\s*:\s*SidecarSpec\s*\(',
        SIDECAR_SOURCE,
        flags=re.MULTILINE,
    ) is not None
    unavailable_notice = re.compile(
        r"does not provide a one-click CosyVoice runtime\s+installer"
    )

    if installer_exists:
        assert unavailable_notice.search(DOCS) is None
    else:
        assert unavailable_notice.search(DOCS) is not None
        assert 'Click **Install** next to "CosyVoice"' not in DOCS
        assert "creates a dedicated venv" not in DOCS


def test_cosyvoice_docs_separate_model_cache_from_runtime_readiness():
    for contract in (
        "model weights",
        "cosyvoice.cli.cosyvoice.AutoModel",
        "same Python interpreter",
        "OMNIVOICE_COSYVOICE_MODEL",
    ):
        assert contract in DOCS
