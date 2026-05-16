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
from the_architect.core.project_intelligence import (
    _all_components,
    _component_name,
    _component_to_node,
    _dependency_to_edge,
    _detect_commands,
    _detect_conventions,
    _detect_domains,
    _detect_project_name,
    _detect_project_type,
    _flows,
    _is_valid_project_intelligence,
    _known_gaps,
    _node_id,
    _prefix_command,
    _read_json,
    _read_text,
    _read_toml,
    _tech_stack,
    format_project_intelligence_for_prompt,
    read_project_intelligence,
    write_project_intelligence,
)
from the_architect.core.structure import (
    Component,
    Dependency,
    RepoType,
    StructureReport,
)


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
        components=[
            Component(
                path="./",
                language="Python",
                role="CLI tool",
                description="Autonomous planning CLI",
                test_command="pytest tests/ -v --tb=short",
                lint_command="ruff check .",
            )
        ],
    )


def test_write_project_intelligence_creates_valid_cache(tmp_path: Path) -> None:
    """Structured intelligence should be generated from deterministic structure signals."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo-cli'\n[project.scripts]\ndemo = 'demo:main'\n",
        encoding="utf-8",
    )

    path = write_project_intelligence(tmp_path, _make_structure_report())
    data = read_project_intelligence(tmp_path)

    assert path == tmp_path / ".architect" / "intelligence.json"
    assert data is not None
    assert data["project"]["name"] == "demo-cli"
    assert data["project"]["type"] == "CLI tool"
    assert data["commands"]["test"] == "pytest tests/ -v --tb=short"
    assert data["nodes"][0]["id"] == "root"


def test_read_project_intelligence_ignores_invalid_cache(tmp_path: Path) -> None:
    """Invalid structured intelligence should be ignored instead of injected."""
    cache = tmp_path / ".architect" / "intelligence.json"
    cache.parent.mkdir(parents=True)
    cache.write_text('{"version": "0.1"}', encoding="utf-8")

    assert read_project_intelligence(tmp_path) is None


def test_format_project_intelligence_for_prompt_is_compact(tmp_path: Path) -> None:
    """Validated structured intelligence should format as planner context."""
    write_project_intelligence(tmp_path, _make_structure_report())
    summary = format_project_intelligence_for_prompt(read_project_intelligence(tmp_path))

    assert "Project:" in summary
    assert "Key components:" in summary
    assert "pytest tests/ -v --tb=short" in summary


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


async def test_refresh_uses_standalone_mode_as_model_override(
    tmp_path: Path,
) -> None:
    """config.standalone_mode should be forwarded as model_override to stream_provider."""
    config = ArchitectConfig(standalone_mode="gpt-4o").resolve(tmp_path)
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
    assert call_kwargs["model_override"] == "gpt-4o"


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


# ---------------------------------------------------------------------------
# T02 — project_intelligence.py deep branch coverage
# ---------------------------------------------------------------------------


# -- T02.1 — Project type detection coverage --


def test_detect_project_type_game_godot(tmp_path: Path) -> None:
    """A project.godot file at root should classify as Game."""
    (tmp_path / "project.godot").write_text("[application]", encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "Game"


def test_detect_project_type_game_uproject(tmp_path: Path) -> None:
    """A .uproject file at root should classify as Game."""
    (tmp_path / "MyGame.uproject").write_text("{}", encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "Game"


def test_detect_project_type_game_unity(tmp_path: Path) -> None:
    """A .unity file at root should classify as Game."""
    (tmp_path / "Scene.unity").write_text("{}", encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "Game"


def test_detect_project_type_mobile_android(tmp_path: Path) -> None:
    """Component with androidmanifest.xml in role should classify as Mobile app."""
    comp = Component(path="./", role="androidmanifest.xml")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Mobile app"


def test_detect_project_type_mobile_xcode(tmp_path: Path) -> None:
    """A .xcodeproj file at root should classify as Mobile app."""
    (tmp_path / "App.xcodeproj").mkdir()
    result = _detect_project_type(tmp_path, [])

    assert result == "Mobile app"


def test_detect_project_type_iac_main_tf(tmp_path: Path) -> None:
    """A main.tf file at root should classify as Infrastructure-as-Code."""
    (tmp_path / "main.tf").write_text("resource {}", encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "Infrastructure-as-Code"


def test_detect_project_type_iac_glob_tf(tmp_path: Path) -> None:
    """Any .tf file at root should classify as Infrastructure-as-Code."""
    (tmp_path / "variables.tf").write_text("", encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "Infrastructure-as-Code"


def test_detect_project_type_cli_role(tmp_path: Path) -> None:
    """Component with 'cli' in role text should classify as CLI tool."""
    comp = Component(path="./", role="CLI tool")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "CLI tool"


def test_detect_project_type_cli_entry_scripts(tmp_path: Path) -> None:
    """pyproject.toml with [project.scripts] should classify as CLI tool."""
    (tmp_path / "pyproject.toml").write_text(
        "[project.scripts]\ndemo = 'demo:main'\n", encoding="utf-8"
    )
    result = _detect_project_type(tmp_path, [])

    assert result == "CLI tool"


def test_detect_project_type_cli_bin(tmp_path: Path) -> None:
    """package.json with 'bin' should classify as CLI tool."""
    (tmp_path / "package.json").write_text('{"bin": {"cli": "index.js"}}', encoding="utf-8")
    result = _detect_project_type(tmp_path, [])

    assert result == "CLI tool"


def test_detect_project_type_backend_fastapi(tmp_path: Path) -> None:
    """Component with FastAPI framework should classify as Backend service."""
    comp = Component(path="./", framework="FastAPI")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Backend service / API"


def test_detect_project_type_backend_django(tmp_path: Path) -> None:
    """Component with Django framework should classify as Backend service."""
    comp = Component(path="./", framework="Django")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Backend service / API"


def test_detect_project_type_backend_express(tmp_path: Path) -> None:
    """Component with Express framework should classify as Backend service."""
    comp = Component(path="./", framework="Express")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Backend service / API"


def test_detect_project_type_fullstack_backend_plus_frontend(tmp_path: Path) -> None:
    """Backend + frontend frameworks should classify as Full-stack web."""
    comp = Component(path="./", framework="FastAPI", language="React")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Full-stack web"


def test_detect_project_type_fullstack_express_plus_vue(tmp_path: Path) -> None:
    """Express + Vue should classify as Full-stack web."""
    comp = Component(path="./", framework="Express", role="vue")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Full-stack web"


def test_detect_project_type_frontend_react(tmp_path: Path) -> None:
    """Component with React framework should classify as Frontend / SPA."""
    comp = Component(path="./", framework="React")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Frontend / SPA"


def test_detect_project_type_frontend_nextjs(tmp_path: Path) -> None:
    """Component with Next.js framework should classify as Frontend / SPA."""
    comp = Component(path="./", framework="Next.js")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Frontend / SPA"


def test_detect_project_type_frontend_svelte(tmp_path: Path) -> None:
    """Component with Svelte should classify as Frontend / SPA."""
    comp = Component(path="./", framework="Svelte")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Frontend / SPA"


def test_detect_project_type_ml_torch(tmp_path: Path) -> None:
    """Component with torch should classify as ML / AI project."""
    comp = Component(path="./", framework="torch")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "ML / AI project"


def test_detect_project_type_ml_tensorflow(tmp_path: Path) -> None:
    """Component with tensorflow should classify as ML / AI project."""
    comp = Component(path="./", role="tensorflow")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "ML / AI project"


def test_detect_project_type_ml_notebooks(tmp_path: Path) -> None:
    """Component with notebooks should classify as ML / AI project."""
    comp = Component(path="./", role="notebooks")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "ML / AI project"


def test_detect_project_type_library(tmp_path: Path) -> None:
    """Component with 'library' role should classify as Library / SDK / Package."""
    comp = Component(path="./", role="library")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Library / SDK / Package"


def test_detect_project_type_sdk(tmp_path: Path) -> None:
    """Component with 'sdk' role should classify as Library / SDK / Package."""
    comp = Component(path="./", role="sdk")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Library / SDK / Package"


def test_detect_project_type_mixed(tmp_path: Path) -> None:
    """Components without specific markers should classify as Mixed / Other."""
    comp = Component(path="./", language="Python")
    result = _detect_project_type(tmp_path, [comp])

    assert result == "Mixed / Other"


def test_detect_project_type_unknown(tmp_path: Path) -> None:
    """Empty components list should classify as Unknown."""
    result = _detect_project_type(tmp_path, [])

    assert result == "Unknown"


def test_detect_project_type_nonexistent_dir() -> None:
    """Non-existent project dir with components should still detect type."""
    comp = Component(path="./", role="CLI tool")
    result = _detect_project_type(Path("/nonexistent/path/xyz"), [comp])

    assert result == "CLI tool"


# -- T02.2 — Command and convention detection coverage --


def test_detect_commands_npm_scripts(tmp_path: Path) -> None:
    """package.json scripts should populate commands via npm run."""
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest", "lint": "eslint .", "build": "tsc"}}',
        encoding="utf-8",
    )
    result = _detect_commands(tmp_path, [])

    assert result["test"] == "npm run test"
    assert result["lint"] == "npm run lint"
    assert result["build"] == "npm run build"


def test_detect_commands_pytest_ruff_fallback(tmp_path: Path) -> None:
    """pyproject.toml with pytest/ruff should set test/lint commands."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n\n[tool.pytest]\n[tool.ruff]\n",
        encoding="utf-8",
    )
    result = _detect_commands(tmp_path, [])

    assert result["test"] == "pytest tests/ -v --tb=short"
    assert result["lint"] == "ruff check ."


def test_detect_commands_build_from_pyproject(tmp_path: Path) -> None:
    """Existing pyproject.toml should set build command."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    result = _detect_commands(tmp_path, [])

    assert result["build"] == "python -m build"


def test_detect_commands_component_test_overrides(tmp_path: Path) -> None:
    """Component test_command should take precedence over manifest detection."""
    comp = Component(
        path="./",
        test_command="pytest tests/ -v",
        lint_command="ruff check .",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    result = _detect_commands(tmp_path, [comp])

    assert result["test"] == "pytest tests/ -v"
    assert result["lint"] == "ruff check ."


def test_prefix_command_root_path_returns_command_as_is() -> None:
    """Root path should not add a cd prefix."""
    assert _prefix_command(".", "pytest") == "pytest"
    assert _prefix_command("./", "pytest") == "pytest"
    assert _prefix_command("", "pytest") == "pytest"


def test_prefix_command_nested_path_adds_cd() -> None:
    """Non-root path should add 'cd <path> &&' prefix."""
    assert _prefix_command("packages/cli/", "pytest") == "cd packages/cli && pytest"


def test_detect_conventions_pyproject(tmp_path: Path) -> None:
    """pyproject.toml should set python convention."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    result = _detect_conventions(tmp_path)

    assert "python" in result


def test_detect_conventions_package_json(tmp_path: Path) -> None:
    """package.json should set javascript convention."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    result = _detect_conventions(tmp_path)

    assert "javascript" in result


def test_detect_conventions_agents_md(tmp_path: Path) -> None:
    """AGENTS.md should set opencode_rules convention."""
    (tmp_path / "AGENTS.md").write_text("# Rules", encoding="utf-8")
    result = _detect_conventions(tmp_path)

    assert "opencode_rules" in result


def test_detect_conventions_claude_md(tmp_path: Path) -> None:
    """CLAUDE.md should set claude_rules convention."""
    (tmp_path / "CLAUDE.md").write_text("# Rules", encoding="utf-8")
    result = _detect_conventions(tmp_path)

    assert "claude_rules" in result


def test_detect_conventions_all_files(tmp_path: Path) -> None:
    """All convention files should produce all convention keys."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")
    result = _detect_conventions(tmp_path)

    assert set(result.keys()) == {"python", "javascript", "opencode_rules", "claude_rules"}


def test_detect_domains_no_capability_fallback(tmp_path: Path) -> None:
    """Components with no description/role/framework/language should produce fallback domain."""
    comp = Component(path="./")
    result = _detect_domains("my-project", [comp])

    assert len(result) == 1
    assert result[0]["name"] == "my-project"
    assert "not determined" in result[0]["capability"]


def test_detect_domains_with_capability(tmp_path: Path) -> None:
    """Components with a description should produce a domain entry."""
    comp = Component(path="./", description="Autonomous CLI")
    result = _detect_domains("my-project", [comp])

    assert len(result) == 1
    assert result[0]["name"] == "Root project"
    assert result[0]["capability"] == "Autonomous CLI"


def test_flows_with_edges() -> None:
    """Flows with edges should use the first edge's from/to as steps."""
    nodes = [{"id": "root"}]
    edges = [{"from": "root", "to": "frontend"}]
    result = _flows("CLI tool", nodes, edges)

    assert len(result) == 1
    assert result[0]["steps"] == ["root", "frontend"]


def test_flows_without_edges() -> None:
    """Flows without edges should use the first node's id as step."""
    nodes = [{"id": "root"}]
    result = _flows("CLI tool", nodes, [])

    assert len(result) == 1
    assert result[0]["steps"] == ["root"]


def test_flows_empty_nodes() -> None:
    """Flows with no nodes should return empty list."""
    result = _flows("CLI tool", [], [])

    assert result == []


# -- T02.3 — Error handling and validation coverage --


def test_is_valid_project_intelligence_not_dict() -> None:
    """A non-dict value should fail validation."""
    assert _is_valid_project_intelligence("string") is False
    assert _is_valid_project_intelligence(123) is False
    assert _is_valid_project_intelligence(None) is False
    assert _is_valid_project_intelligence([]) is False


def test_is_valid_project_intelligence_wrong_version() -> None:
    """A wrong version string should fail validation."""
    data = {"version": "0.1", "project": {}}
    assert _is_valid_project_intelligence(data) is False


def test_is_valid_project_intelligence_project_not_dict() -> None:
    """A project field that is not a dict should fail validation."""
    data = {"version": "1.0", "project": "not-a-dict"}
    assert _is_valid_project_intelligence(data) is False


def test_is_valid_project_intelligence_missing_required_list() -> None:
    """Missing a required list field should fail validation."""
    data = {
        "version": "1.0",
        "project": {},
        "nodes": [],
        "edges": [],
        "flows": [],
        "domains": [],
        "risks": [],
        "commands": {},
        "conventions": {},
    }
    # tech_stack is required but missing
    assert _is_valid_project_intelligence(data) is False


def test_is_valid_project_intelligence_commands_not_dict() -> None:
    """commands that is not a dict should fail validation."""
    data = {
        "version": "1.0",
        "project": {},
        "tech_stack": [],
        "nodes": [],
        "edges": [],
        "flows": [],
        "domains": [],
        "risks": [],
        "commands": "not-a-dict",
        "conventions": {},
    }
    assert _is_valid_project_intelligence(data) is False


def test_is_valid_project_intelligence_conventions_not_dict() -> None:
    """conventions that is not a dict should fail validation."""
    data = {
        "version": "1.0",
        "project": {},
        "tech_stack": [],
        "nodes": [],
        "edges": [],
        "flows": [],
        "domains": [],
        "risks": [],
        "commands": {},
        "conventions": [],
    }
    assert _is_valid_project_intelligence(data) is False


def test_is_valid_project_intelligence_valid_payload() -> None:
    """A complete valid payload should pass validation."""
    data = {
        "version": "1.0",
        "project": {"name": "demo"},
        "tech_stack": [],
        "nodes": [],
        "edges": [],
        "flows": [],
        "domains": [],
        "risks": [],
        "commands": {},
        "conventions": {},
    }
    assert _is_valid_project_intelligence(data) is True


def test_read_project_intelligence_oserror(tmp_path: Path) -> None:
    """read_project_intelligence should return None on OSError when file exists."""
    cache = tmp_path / ".architect" / "intelligence.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        result = read_project_intelligence(tmp_path)

    assert result is None


def test_read_text_oserror(tmp_path: Path) -> None:
    """_read_text should return empty string on OSError."""
    f = tmp_path / "secret.txt"
    f.write_text("data", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("boom")):
        result = _read_text(f)

    assert result == ""


def test_read_json_json_decode_error(tmp_path: Path) -> None:
    """_read_json should return empty dict on JSONDecodeError."""
    f = tmp_path / "bad.json"
    f.write_text("{invalid json!!!", encoding="utf-8")

    result = _read_json(f)

    assert result == {}


def test_read_json_non_dict_return(tmp_path: Path) -> None:
    """_read_json should return empty dict when JSON is not a dict."""
    f = tmp_path / "list.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")

    result = _read_json(f)

    assert result == {}


def test_read_toml_decode_error(tmp_path: Path) -> None:
    """_read_toml should return empty dict on TOMLDecodeError."""
    f = tmp_path / "bad.toml"
    f.write_text("this is not valid toml {{{", encoding="utf-8")

    result = _read_toml(f)

    assert result == {}


def test_read_toml_non_dict_return(tmp_path: Path) -> None:
    """_read_toml should return empty dict when TOML result is not a dict."""
    # tomllib.loads always returns a dict for valid TOML, so we mock the
    # import to return a non-dict to exercise the defensive isinstance guard.
    import sys
    from unittest.mock import MagicMock

    mock_tomllib = MagicMock()
    mock_tomllib.loads.return_value = [1, 2, 3]  # non-dict
    # The function imports tomllib inline, so patch the import mechanism.
    with patch(
        "builtins.__import__",
        side_effect=lambda name, *a, **k: (
            mock_tomllib if name == "tomllib" else __import__(name, *a, **k)
        ),
    ):
        # Ensure file exists so we pass the path.exists() check
        f = tmp_path / "x.toml"
        f.write_text("[table]", encoding="utf-8")
        result = _read_toml(f)

    assert result == {}
    sys.modules.pop("tomllib", None)


def test_node_id_empty_path() -> None:
    """_node_id with empty or whitespace-only path should return 'root'."""
    assert _node_id("") == "root"
    assert _node_id("   ") == "root"


def test_node_id_special_characters() -> None:
    """_node_id should sanitize special characters to kebab-case."""
    assert _node_id("my-component/path") == "my-component-path"
    assert _node_id("./foo/bar/") == "foo-bar"


def test_component_name_root_path() -> None:
    """_component_name with '.' or './' should return 'Root project'."""
    root_comp = Component(path=".")
    assert _component_name(root_comp) == "Root project"

    root_comp2 = Component(path="./")
    assert _component_name(root_comp2) == "Root project"


def test_component_name_nested_path() -> None:
    """_component_name with nested path should return last segment."""
    comp = Component(path="packages/cli/")
    assert _component_name(comp) == "cli"


def test_format_project_intelligence_for_prompt_none() -> None:
    """format_project_intelligence_for_prompt with None should return empty string."""
    assert format_project_intelligence_for_prompt(None) == ""


def test_format_project_intelligence_for_prompt_empty_dict() -> None:
    """format_project_intelligence_for_prompt with empty dict should return empty string."""
    assert format_project_intelligence_for_prompt({}) == ""


def test_format_project_intelligence_includes_edges(tmp_path: Path) -> None:
    """Formatted prompt should include edge relationships."""
    report = StructureReport(
        repo_type=RepoType.SINGLE_REPO,
        components=[
            Component(path="./", language="Python"),
            Component(path="frontend/", language="JavaScript"),
        ],
        dependencies=[Dependency(source="./", target="frontend/", via="import")],
    )
    write_project_intelligence(tmp_path, report)
    summary = format_project_intelligence_for_prompt(read_project_intelligence(tmp_path))

    assert "Detected relationships:" in summary
    assert "->" in summary


def test_format_project_intelligence_includes_gaps(tmp_path: Path) -> None:
    """Formatted prompt should include known gaps when present."""
    report = StructureReport(
        repo_type=RepoType.UNTRACKED,
        components=[],
    )
    write_project_intelligence(tmp_path, report)
    summary = format_project_intelligence_for_prompt(read_project_intelligence(tmp_path))

    assert "Known intelligence gaps:" in summary


def test_all_components_with_sub_components() -> None:
    """_all_components should recurse into sub_components."""
    child = Component(path="child/")
    parent = Component(path="parent/", sub_components=[child])
    result = _all_components([parent])

    assert len(result) == 2
    assert result[0].path == "parent/"
    assert result[1].path == "child/"


def test_dependency_to_edge() -> None:
    """_dependency_to_edge should convert Dependency to edge dict."""
    dep = Dependency(source="./", target="frontend/", via="import")
    result = _dependency_to_edge(dep)

    assert result["from"] == "root"
    assert result["to"] == "frontend"
    assert result["via"] == "import"


def test_component_to_node_defaults() -> None:
    """_component_to_node should set defaults for missing fields."""
    comp = Component(path="./")
    result = _component_to_node(comp)

    assert result["id"] == "root"
    assert result["name"] == "Root project"
    assert result["layer"] == "Project"
    assert result["purpose"] == "Detected project component"


def test_tech_stack_with_key_deps(tmp_path: Path) -> None:
    """_tech_stack should include key_deps as Dependency entries."""
    comp = Component(path="./", language="Python", key_deps=["click", "rich"])
    result = _tech_stack([comp])

    dep_entries = [e for e in result if e["kind"] == "Dependency"]
    assert any(e["tech"] == "click" for e in dep_entries)
    assert any(e["tech"] == "rich" for e in dep_entries)


def test_tech_stack_skips_duplicates(tmp_path: Path) -> None:
    """_tech_stack should not emit duplicate entries."""
    comp = Component(
        path="./",
        language="Python",
        framework="FastAPI",
        key_deps=["click"],
    )
    result = _tech_stack([comp])
    # No entry should appear more than once
    seen = {(e["layer"], e["tech"], e["kind"]) for e in result}
    assert len(seen) == len(result)


def test_tech_stack_skips_duplicate_across_components() -> None:
    """_tech_stack should skip duplicate (path, label, value) across components."""
    # Two components with the same path and language should deduplicate
    comp1 = Component(path="./", language="Python")
    comp2 = Component(path="./", language="Python")
    result = _tech_stack([comp1, comp2])

    lang_entries = [e for e in result if e["kind"] == "Language"]
    assert len(lang_entries) == 1


def test_known_gaps_no_test_command() -> None:
    """_known_gaps should flag missing test command."""
    gaps = _known_gaps(
        "CLI tool", {"test": "", "lint": "", "build": "", "dev": ""}, [{"id": "root"}], []
    )

    assert any("test command" in g for g in gaps)


def test_known_gaps_no_nodes() -> None:
    """_known_gaps should flag empty node list."""
    gaps = _known_gaps("CLI tool", {"test": "pytest", "lint": "", "build": "", "dev": ""}, [], [])

    assert any("component graph nodes" in g for g in gaps)


def test_known_gaps_multiple_nodes_no_edges() -> None:
    """_known_gaps should flag multiple nodes without edges."""
    gaps = _known_gaps(
        "CLI tool",
        {"test": "pytest", "lint": "", "build": "", "dev": ""},
        [{"id": "root"}, {"id": "frontend"}],
        [],
    )

    assert any("inter-component" in g for g in gaps)


def test_known_gaps_unknown_project_type() -> None:
    """_known_gaps should flag Unknown project type."""
    gaps = _known_gaps(
        "Unknown", {"test": "pytest", "lint": "", "build": "", "dev": ""}, [{"id": "root"}], []
    )

    assert any("Project type could not be determined" in g for g in gaps)


def test_known_gaps_no_gaps_when_complete() -> None:
    """_known_gaps should return empty list when everything is detected."""
    gaps = _known_gaps(
        "CLI tool",
        {"test": "pytest", "lint": "ruff", "build": "build", "dev": ""},
        [{"id": "root"}],
        [{"from": "root", "to": "cli"}],
    )

    assert gaps == []


def test_detect_project_name_from_package_json(tmp_path: Path) -> None:
    """_detect_project_name should fall back to package.json when pyproject.toml is absent."""
    (tmp_path / "package.json").write_text('{"name": "js-app"}', encoding="utf-8")
    result = _detect_project_name(tmp_path)

    assert result == "js-app"


def test_detect_project_name_from_dir_name(tmp_path: Path) -> None:
    """_detect_project_name should fall back to directory name."""
    result = _detect_project_name(tmp_path)

    assert result == tmp_path.name
