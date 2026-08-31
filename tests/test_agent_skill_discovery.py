from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_installs_skills_from_the_canonical_repository() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "npx skills add debpalash/VoiceStudio" in readme
    assert "npx skills add debpalash/omnivoice-studio" not in readme


def test_public_skill_surfaces_use_current_identity_and_license() -> None:
    canonical = (ROOT / "skills/omnivoice/SKILL.md").read_text(encoding="utf-8")
    claude = (ROOT / ".claude/skills/omnivoice/SKILL.md").read_text(encoding="utf-8")
    launcher = (
        ROOT / ".claude/skills/omnivoice/scripts/start-backend.sh"
    ).read_text(encoding="utf-8")

    for skill in (canonical, claude):
        assert "github.com/debpalash/VoiceStudio" in skill
        assert "FSL-1.1" not in skill

    assert "${OMNIVOICE_HOME:-$HOME/VoiceStudio}" in launcher
    assert "${OMNIVOICE_HOME:-$HOME/OmniVoice-Studio}" not in launcher
