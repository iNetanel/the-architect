"""Tests for the model-based project intelligence pre-planning pass."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from the_architect.config import ArchitectConfig
from the_architect.core.intelligence import (
    _read_intelligence_prompt,
    _section_body,
    assess_architect_md_quality,
    build_intelligence_instruction,
    refresh_project_intelligence,
)
from the_architect.core.structure import Component, RepoType, StructureReport


def test_assessment_runs_for_empty_memory(tmp_path: Path) -> None:
    """Missing ARCHITECT.md content should trigger the deep pass."""
    assessment = assess_architect_md_quality(tmp_path, "")

    assert assessment.should_run is True
    assert "missing or empty" in assessment.reasons[0]


def test_assessment_runs_when_repo_evidence_is_missing(tmp_path: Path) -> None:
    """Repo evidence absent from memory should trigger model curation."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n", encoding="utf-8")
    (tmp_path / "documentation").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    assessment = assess_architect_md_quality(
        tmp_path,
        "# ARCHITECT.md\n\n## Project Overview\n\n- _No overview recorded yet._\n",
    )

    assert assessment.should_run is True
    assert "root project metadata missing" in assessment.reasons
    assert "documentation directory missing" in assessment.reasons
    assert "CI workflow memory missing" in assessment.reasons


def test_assessment_skips_when_memory_has_expected_facts(tmp_path: Path) -> None:
    """A memory file with key evidence and curated sections should skip the deep pass."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n", encoding="utf-8")
    (tmp_path / "documentation").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    content = """# ARCHITECT.md

## Project Overview

- Root Python project: `app`.

## Key Flows

- Planning flow: context is gathered, memory is refreshed, then tasks are generated.

## Shared Contracts

- Provider output is normalized before execution state is evaluated.

## Code Locations

- `documentation/` - docs.
- `.github/workflows/ci.yml` - CI.
- `tests/` - test suite.

## Known Constraints

- Keep generated task state under `tasks/` and durable memory in `ARCHITECT.md`.
"""

    assessment = assess_architect_md_quality(tmp_path, content)

    assert assessment.should_run is False


def test_build_instruction_limits_agent_to_architect_md(tmp_path: Path) -> None:
    """The deep intelligence instruction should forbid planning and code edits."""
    report = StructureReport(
        repo_type=RepoType.SINGLE_REPO,
        components=[Component(path="./", language="Python")],
    )

    instruction = build_intelligence_instruction(
        project_dir=tmp_path,
        structure_report=report,
        project_context="## File Tree\nFile tree:",
        architect_md_content="# ARCHITECT.md",
        reasons=["memory is shallow"],
    )

    assert "memory is shallow" in instruction
    assert "edit only `ARCHITECT.md`" in instruction
    assert "do not create task files" in instruction
    assert "Deterministic Structure Report" in instruction


# ---------------------------------------------------------------------------
# T02 — New branch coverage tests
# ---------------------------------------------------------------------------


def test_assessment_runs_when_many_placeholder_sections(tmp_path: Path) -> None:
    """Content with 3+ '_No ' occurrences should flag several placeholder sections."""
    content = (
        "# ARCHITECT.md\n\n"
        "## Key Flows\n\n_No key flows recorded yet.\n\n"
        "## Shared Contracts\n\n_No shared contracts recorded yet.\n\n"
        "## Known Constraints\n\n_No known constraints recorded yet.\n\n"
        "## Permanent Decisions\n\n_No permanent decisions recorded yet.\n"
    )
    assessment = assess_architect_md_quality(tmp_path, content)

    assert assessment.should_run is True
    assert any("placeholder" in r for r in assessment.reasons)


def test_assessment_runs_when_auto_detected_sections_are_thin(tmp_path: Path) -> None:
    """Sections with thin curated content above an auto-detected block should trigger the pass."""
    content = (
        "# ARCHITECT.md\n\n"
        "## Key Flows\n\n"
        "Ok.\n\n"
        "### Auto-Detected Project Intelligence\n\n"
        "- generated key flows\n\n"
        "## Shared Contracts\n\n"
        "Short.\n\n"
        "### Auto-Detected Project Intelligence\n\n"
        "- generated contracts\n\n"
        "## Known Constraints\n\n"
        "- Brief.\n"
    )
    assessment = assess_architect_md_quality(tmp_path, content)

    assert assessment.should_run is True
    assert any("little curated knowledge" in r for r in assessment.reasons)


def test_assessment_skips_when_auto_detected_sections_have_curated_content(tmp_path: Path) -> None:
    """Sections with ample curated content before an auto-detected block should not trigger."""
    curated = "- This is a well-documented section with enough content to pass the quality gate.\n"
    content = (
        "# ARCHITECT.md\n\n"
        "## Key Flows\n\n"
        f"{curated}"
        "### Auto-Detected Project Intelligence\n\n"
        "- generated\n\n"
        "## Shared Contracts\n\n"
        f"{curated}"
        "### Auto-Detected Project Intelligence\n\n"
        "- generated\n\n"
        "## Known Constraints\n\n"
        f"{curated}"
        "### Auto-Detected Project Intelligence\n\n"
        "- generated\n"
    )
    assessment = assess_architect_md_quality(tmp_path, content)

    assert assessment.should_run is False


def test_section_body_returns_empty_when_heading_missing() -> None:
    """A heading not present in the content should yield an empty string."""
    result = _section_body(
        "# Some content\n\n## Other Section\n\nBody.\n",
        "## Missing Section",
    )

    assert result == ""


def test_section_body_returns_body_up_to_next_section() -> None:
    """The body under a heading should stop at the next level-two heading."""
    content = "## First\n\nFirst body.\n\n## Second\n\nSecond body.\n"
    result = _section_body(content, "## First")

    assert "First body." in result
    assert "Second body." not in result


def test_section_body_returns_remainder_at_end_of_file() -> None:
    """The last section should return everything after its heading."""
    content = "## Only Section\n\nThis is the only content.\n"
    result = _section_body(content, "## Only Section")

    assert "This is the only content." in result


# ---------------------------------------------------------------------------
# T03 — refresh_project_intelligence async function and _read_intelligence_prompt
# ---------------------------------------------------------------------------

_RICH_ARCHITECT_MD = """# ARCHITECT.md

## Project Overview

This is a well-documented project with enough content to pass the quality gate.

## Key Flows

- Planning flow: context is gathered and tasks are generated autonomously.

## Shared Contracts

- Provider output follows the JSON:API specification for consistency.

## Known Constraints

- Keep all generated task state under `tasks/` and durable memory in `ARCHITECT.md`.
"""


def _make_structure_report() -> StructureReport:
    return StructureReport(
        repo_type=RepoType.SINGLE_REPO,
        components=[Component(path="./", language="Python")],
    )


async def test_refresh_skips_when_quality_gate_passes(tmp_path: Path) -> None:
    """A rich ARCHITECT.md that passes the quality gate should skip the provider call."""
    (tmp_path / "ARCHITECT.md").write_text(_RICH_ARCHITECT_MD, encoding="utf-8")
    config = ArchitectConfig().resolve(tmp_path)
    provider = MagicMock()

    with patch("the_architect.core.intelligence.stream_provider") as mock_stream:
        assessment = await refresh_project_intelligence(
            project_dir=tmp_path,
            config=config,
            provider=provider,
            structure_report=_make_structure_report(),
        )

    assert assessment.should_run is False
    mock_stream.assert_not_called()


async def test_refresh_runs_with_agent_override_when_provider_supports_agents(
    tmp_path: Path,
) -> None:
    """Provider agent support should use the intelligence agent override."""
    config = ArchitectConfig().resolve(tmp_path)
    provider = MagicMock()
    provider.supports_agents.return_value = True

    mock_result = MagicMock()
    mock_result.exit_code = 0

    with (
        patch(
            "the_architect.core.intelligence.stream_provider",
            new=AsyncMock(return_value=mock_result),
        ) as mock_stream,
        patch(
            "the_architect.core.planner.gather_project_context",
            return_value="## File Tree\nFile tree:",
        ),
    ):
        assessment = await refresh_project_intelligence(
            project_dir=tmp_path,
            config=config,
            provider=provider,
            structure_report=_make_structure_report(),
        )

    assert assessment.should_run is True
    mock_stream.assert_called_once()
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["agent_override"] == "intelligence"
    assert call_kwargs["config_override"] == tmp_path / ".architect" / "architect.json"


async def test_refresh_prepends_intelligence_prompt_when_provider_has_no_agents(
    tmp_path: Path,
) -> None:
    """When provider has no agent support, instruction is prepended with the intelligence prompt."""
    config = ArchitectConfig().resolve(tmp_path)
    provider = MagicMock()
    provider.supports_agents.return_value = False

    mock_result = MagicMock()
    mock_result.exit_code = 0

    with (
        patch(
            "the_architect.core.intelligence.stream_provider",
            new=AsyncMock(return_value=mock_result),
        ) as mock_stream,
        patch(
            "the_architect.core.planner.gather_project_context",
            return_value="## File Tree\nFile tree:",
        ),
    ):
        assessment = await refresh_project_intelligence(
            project_dir=tmp_path,
            config=config,
            provider=provider,
            structure_report=_make_structure_report(),
        )

    assert assessment.should_run is True
    mock_stream.assert_called_once()
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["agent_override"] is None
    instruction = call_kwargs["instruction"]
    assert "ARCHITECT.md" in instruction


async def test_refresh_logs_warning_on_nonzero_exit_but_does_not_raise(
    tmp_path: Path,
) -> None:
    """A non-zero provider exit code should log a warning but not raise an exception."""
    config = ArchitectConfig().resolve(tmp_path)
    provider = MagicMock()
    provider.supports_agents.return_value = True

    mock_result = MagicMock()
    mock_result.exit_code = 1

    with (
        patch(
            "the_architect.core.intelligence.stream_provider",
            new=AsyncMock(return_value=mock_result),
        ),
        patch(
            "the_architect.core.planner.gather_project_context",
            return_value="## File Tree\nFile tree:",
        ),
    ):
        assessment = await refresh_project_intelligence(
            project_dir=tmp_path,
            config=config,
            provider=provider,
            structure_report=_make_structure_report(),
        )

    assert assessment.should_run is True


def test_read_intelligence_prompt_returns_non_empty_string() -> None:
    """The packaged intelligence prompt must be a non-empty string containing 'ARCHITECT.md'."""
    result = _read_intelligence_prompt()

    assert len(result) > 100
    assert "ARCHITECT.md" in result
