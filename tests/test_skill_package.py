from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _frontmatter_version(path: Path) -> str:
    match = re.search(r"^version:\s*([0-9.]+)\s*$", path.read_text(encoding="utf-8"), re.M)
    if not match:
        raise AssertionError(f"missing version in {path}")
    return match.group(1)


def test_canonical_skill_package_is_self_contained(repository_root: Path) -> None:
    skill_dir = repository_root / "skills" / "outdoor-tour-assistant"
    required = {
        "contracts.py",
        "event_engine.py",
        "live_tour_gate.py",
        "output_safety.py",
        "prepare_tour.py",
        "providers.py",
        "route_engine.py",
        "tour_runtime.py",
        "tour_state.py",
        "tourctl.py",
    }

    assert not (repository_root / "SKILL.md").exists()
    assert required == {path.name for path in (skill_dir / "scripts").glob("*.py")}
    assert (skill_dir / "references" / "cron-prompt.md").exists()


def test_release_versions_are_consistent(repository_root: Path) -> None:
    project = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    expected = project["project"]["version"]

    assert _frontmatter_version(
        repository_root / "skills" / "outdoor-tour-assistant" / "SKILL.md"
    ) == expected
    assert _frontmatter_version(
        repository_root / "skills" / "live-location-nearby" / "SKILL.md"
    ) == expected


def test_skill_script_references_exist(repository_root: Path) -> None:
    skill_dir = repository_root / "skills" / "outdoor-tour-assistant"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    references = re.findall(r"\$\{HERMES_SKILL_DIR\}/scripts/([A-Za-z0-9_.-]+)", skill_text)

    assert references
    assert all((skill_dir / "scripts" / name).is_file() for name in references)


def test_productive_prompt_and_gate_do_not_use_legacy_state_keys(
    repository_root: Path,
) -> None:
    skill_dir = repository_root / "skills" / "outdoor-tour-assistant"
    paths = [
        skill_dir / "SKILL.md",
        skill_dir / "references" / "cron-prompt.md",
        skill_dir / "scripts" / "live_tour_gate.py",
        skill_dir / "scripts" / "prepare_tour.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "reported_facts" not in combined
    assert "startup_notified" not in combined
    assert 'get("tour"' not in combined
    assert "[SILENT]" in combined
