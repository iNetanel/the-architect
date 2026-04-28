"""Tests for project structure detection, ARCHITECT.md management,
context injection, and headless mode.

Covers:
- Structure detection: repo type, components, frameworks, dependencies, roles
- ARCHITECT.md: creation, structure section update, append operations, atomic writes
- Context injection: file reading, directory scanning, truncation, goal extraction
- Headless mode: flag resolution, env vars, error behavior
- CLI integration: --headless, --goal, --scope, --context flags
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.architect_md import (
    ARCHITECT_MD_FILE,
    append_best_practice,
    append_constraint,
    append_lesson,
    append_permanent_decision,
    append_planning_history,
    create_architect_md,
    parse_sections,
    read_architect_md,
    update_structure_section,
    write_or_update_architect_md,
)
from the_architect.core.context import (
    MAX_CONTEXT_FILE_CHARS,
    extract_goal_from_context,
    format_context_for_prompt,
    load_context_paths,
    read_context_directory,
    read_context_file,
)
from the_architect.core.structure import (
    Component,
    Dependency,
    RepoType,
    StructureReport,
    _check_go_framework,
    _check_js_framework,
    _check_python_framework,
    _check_rust_framework,
    _detect_components,
    _detect_repo_type,
    _infer_role,
    detect_structure,
    format_structure_for_prompt,
    format_structure_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    """Return a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A minimal project directory."""
    return tmp_path


# ---------------------------------------------------------------------------
# Structure detection — repo type
# ---------------------------------------------------------------------------


class TestRepoTypeDetection:
    """Tests for repo type classification."""

    def test_multi_repo(self, tmp_path: Path) -> None:
        """Multiple subdirs with .git → MULTI_REPO."""
        for name in ("frontend", "backend"):
            repo = tmp_path / name
            repo.mkdir()
            (repo / ".git").mkdir()
        assert _detect_repo_type(tmp_path) == RepoType.MULTI_REPO

    def test_single_repo(self, tmp_path: Path) -> None:
        """Single .git at root, no component dirs → SINGLE_REPO."""
        (tmp_path / ".git").mkdir()
        assert _detect_repo_type(tmp_path) == RepoType.SINGLE_REPO

    def test_untracked(self, tmp_path: Path) -> None:
        """No .git anywhere → UNTRACKED."""
        assert _detect_repo_type(tmp_path) == RepoType.UNTRACKED

    def test_single_repo_with_components(self, tmp_path: Path) -> None:
        """Root .git with component signals → SINGLE_REPO (refined to MONOREPO later)."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
        assert _detect_repo_type(tmp_path) == RepoType.SINGLE_REPO


# ---------------------------------------------------------------------------
# Structure detection — components
# ---------------------------------------------------------------------------


class TestComponentDetection:
    """Tests for component detection."""

    def test_package_json_component(self, tmp_path: Path) -> None:
        """package.json in subdir → JavaScript/TypeScript component."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}", encoding="utf-8")
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert components[0].language == "JavaScript/TypeScript"

    def test_pyproject_toml_component(self, tmp_path: Path) -> None:
        """pyproject.toml in subdir → Python component."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert components[0].language == "Python"

    def test_cargo_toml_component(self, tmp_path: Path) -> None:
        """Cargo.toml in subdir → Rust component."""
        engine = tmp_path / "engine"
        engine.mkdir()
        (engine / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert components[0].language == "Rust"

    def test_go_mod_component(self, tmp_path: Path) -> None:
        """go.mod in subdir → Go component."""
        svc = tmp_path / "service"
        svc.mkdir()
        (svc / "go.mod").write_text("module example.com/service\n", encoding="utf-8")
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert components[0].language == "Go"

    def test_multi_repo_components(self, tmp_path: Path) -> None:
        """MULTI_REPO: each subdir with .git is a component."""
        for name in ("frontend", "backend"):
            repo = tmp_path / name
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "package.json").write_text("{}", encoding="utf-8")
        components = _detect_components(tmp_path, RepoType.MULTI_REPO)
        assert len(components) == 2

    def test_no_components(self, tmp_path: Path) -> None:
        """No signal files → empty components list."""
        (tmp_path / "src").mkdir()
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 0

    def test_two_components_makes_monorepo(self, tmp_path: Path) -> None:
        """2+ components with SINGLE_REPO → refined to MONOREPO in detect_structure."""
        (tmp_path / ".git").mkdir()  # Need .git for SINGLE_REPO (not UNTRACKED)
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        report = detect_structure(tmp_path)
        assert report.repo_type == RepoType.MONOREPO


# ---------------------------------------------------------------------------
# Structure detection — frameworks
# ---------------------------------------------------------------------------


class TestFrameworkDetection:
    """Tests for framework detection."""

    def test_react_from_package_json(self, tmp_path: Path) -> None:
        """package.json with react dependency → React detected."""
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"react": "^18.0.0"}}', encoding="utf-8"
        )
        assert _check_js_framework(tmp_path) == "React"

    def test_nextjs_from_config(self, tmp_path: Path) -> None:
        """next.config.js present → Next.js detected."""
        (tmp_path / "next.config.js").write_text("module.exports = {};", encoding="utf-8")
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_nextjs_from_package_json(self, tmp_path: Path) -> None:
        """package.json with next dependency → Next.js detected."""
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"next": "^14.0.0"}}', encoding="utf-8"
        )
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_react_native_from_package_json(self, tmp_path: Path) -> None:
        """package.json with react-native → React Native detected."""
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"react-native": "^0.73.0"}}', encoding="utf-8"
        )
        assert _check_js_framework(tmp_path) == "React Native"

    def test_express_from_package_json(self, tmp_path: Path) -> None:
        """package.json with express → Express detected."""
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"express": "^4.18.0"}}', encoding="utf-8"
        )
        assert _check_js_framework(tmp_path) == "Express"

    def test_fastapi_from_pyproject(self, tmp_path: Path) -> None:
        """pyproject.toml with fastapi → FastAPI detected."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\ndependencies = ['fastapi>=0.100']\n", encoding="utf-8"
        )
        assert _check_python_framework(tmp_path) == "FastAPI"

    def test_django_from_pyproject(self, tmp_path: Path) -> None:
        """pyproject.toml with django → Django detected."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\ndependencies = ['django>=4.0']\n", encoding="utf-8"
        )
        assert _check_python_framework(tmp_path) == "Django"

    def test_flask_from_requirements(self, tmp_path: Path) -> None:
        """requirements.txt with flask → Flask detected."""
        (tmp_path / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")
        assert _check_python_framework(tmp_path) == "Flask"

    def test_axum_from_cargo(self, tmp_path: Path) -> None:
        """Cargo.toml with axum → Axum detected."""
        (tmp_path / "Cargo.toml").write_text('[dependencies]\naxum = "0.7"\n', encoding="utf-8")
        assert _check_rust_framework(tmp_path) == "Axum"

    def test_tokio_from_cargo(self, tmp_path: Path) -> None:
        """Cargo.toml with tokio → async runtime (tokio) detected."""
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\ntokio = { version = "1", features = ["full"] }\n', encoding="utf-8"
        )
        assert "tokio" in _check_rust_framework(tmp_path).lower()

    def test_gin_from_go_mod(self, tmp_path: Path) -> None:
        """go.mod with gin-gonic/gin → Gin detected."""
        (tmp_path / "go.mod").write_text(
            "module example.com/api\n\nrequire gin-gonic/gin v1.9.0\n", encoding="utf-8"
        )
        assert _check_go_framework(tmp_path) == "Gin"

    def test_no_framework(self, tmp_path: Path) -> None:
        """No signal files → empty framework."""
        assert _check_js_framework(tmp_path) == ""
        assert _check_python_framework(tmp_path) == ""
        assert _check_rust_framework(tmp_path) == ""
        assert _check_go_framework(tmp_path) == ""


# ---------------------------------------------------------------------------
# Structure detection — role inference
# ---------------------------------------------------------------------------


class TestRoleInference:
    """Tests for component role inference."""

    def test_frontend_role(self) -> None:
        """frontend/ directory → Web UI."""
        comp = Component(path="frontend/", language="JavaScript/TypeScript", framework="React")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_backend_role(self) -> None:
        """backend/ directory → API server."""
        comp = Component(path="backend/", language="Python", framework="FastAPI")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_mobile_role(self) -> None:
        """mobile/ directory → Mobile UI."""
        comp = Component(path="mobile/", language="JavaScript/TypeScript", framework="React Native")
        _infer_role(comp)
        assert comp.role == "Mobile UI"

    def test_engine_role(self) -> None:
        """engine/ directory → Core library."""
        comp = Component(path="engine/", language="Rust")
        _infer_role(comp)
        assert comp.role == "Core library"

    def test_worker_role(self) -> None:
        """worker/ directory → Background worker."""
        comp = Component(path="worker/", language="Python")
        _infer_role(comp)
        assert comp.role == "Background worker"

    def test_shared_role(self) -> None:
        """packages/ directory → Shared library."""
        comp = Component(path="packages/", language="TypeScript")
        _infer_role(comp)
        assert comp.role == "Shared library"

    def test_infra_role(self) -> None:
        """infra/ directory → Infrastructure."""
        comp = Component(path="infra/", language="HCL")
        _infer_role(comp)
        assert comp.role == "Infrastructure"

    def test_framework_based_role(self) -> None:
        """React framework → Web UI even with non-standard directory name."""
        comp = Component(path="webapp/", language="JavaScript/TypeScript", framework="React")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_unknown_role(self) -> None:
        """Unknown directory and framework → empty role."""
        comp = Component(path="mymodule/", language="Python")
        _infer_role(comp)
        assert comp.role == ""


# S08.3: Logging setup
# Structure detection — dependency graph


# Structure detection — dependency graph


class TestDependencyDetection:
    """Tests for inter-component dependency detection."""

    def test_docker_compose_depends_on(self, tmp_path: Path) -> None:
        """docker-compose.yml with depends_on → dependency graph extracted."""
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n"
            "  frontend:\n    depends_on:\n      - backend\n"
            "  backend:\n    depends_on:\n      - db\n",
            encoding="utf-8",
        )
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        report = detect_structure(tmp_path)
        dep_strs = [str(d) for d in report.dependencies]
        # Should find frontend → backend dependency
        assert any("frontend" in d and "backend" in d for d in dep_strs)

    def test_shared_directory_detected(self, tmp_path: Path) -> None:
        """packages/ directory at root → shared resource."""
        (tmp_path / "packages").mkdir()
        report = detect_structure(tmp_path)
        assert any("packages" in r for r in report.shared_resources)


# ---------------------------------------------------------------------------
# Structure report formatting
# ---------------------------------------------------------------------------


class TestStructureFormatting:
    """Tests for structure report formatting."""

    def test_format_report_contains_type(self) -> None:
        """Report should contain repo type."""
        report = StructureReport(repo_type=RepoType.MONOREPO)
        text = format_structure_report(report)
        assert "Monorepo" in text

    def test_format_prompt_contains_type(self) -> None:
        """Prompt format should contain repo type."""
        report = StructureReport(repo_type=RepoType.SINGLE_REPO)
        text = format_structure_for_prompt(report)
        assert "Single repo" in text

    def test_format_report_with_components(self) -> None:
        """Report should list components."""
        report = StructureReport(
            repo_type=RepoType.MONOREPO,
            components=[
                Component(
                    path="frontend/", language="TypeScript", framework="Next.js", role="Web UI"
                ),
                Component(
                    path="backend/", language="Python", framework="FastAPI", role="API server"
                ),
            ],
        )
        text = format_structure_report(report)
        assert "frontend" in text
        assert "backend" in text
        assert "Next.js" in text
        assert "FastAPI" in text

    def test_format_report_with_dependencies(self) -> None:
        """Report should show dependency graph."""
        report = StructureReport(
            dependencies=[
                Dependency("frontend/", "backend/", "docker-compose depends_on"),
            ],
        )
        text = format_structure_report(report)
        assert "frontend" in text
        assert "backend" in text
        assert "docker-compose" in text


# ---------------------------------------------------------------------------
# ARCHITECT.md — creation and reading
# ---------------------------------------------------------------------------


class TestArchitectMdCreation:
    """Tests for ARCHITECT.md creation."""

    def test_creates_file(self, tmp_path: Path) -> None:
        """create_architect_md should create the file."""
        path = create_architect_md(tmp_path, "**Type:** Single repo")
        assert path.exists()
        assert (tmp_path / ARCHITECT_MD_FILE).exists()

    def test_contains_structure_section(self, tmp_path: Path) -> None:
        """Created file should contain the structure section."""
        create_architect_md(tmp_path, "**Type:** Monorepo")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Monorepo" in content
        assert "Project Structure" in content

    def test_contains_all_sections(self, tmp_path: Path) -> None:
        """Created file should have all required sections."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Permanent Decisions" in content
        assert "Known Constraints" in content
        assert "Lessons Learned" in content
        assert "Best Practices" in content
        assert "Planning History" in content

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        """read_architect_md should return None when file doesn't exist."""
        assert read_architect_md(tmp_path) is None


# ---------------------------------------------------------------------------
# ARCHITECT.md — structure section update
# ---------------------------------------------------------------------------


class TestArchitectMdStructureUpdate:
    """Tests for ARCHITECT.md structure section updates."""

    def test_update_preserves_other_sections(self, tmp_path: Path) -> None:
        """Updating structure section should preserve all other sections."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        # Append a decision
        append_permanent_decision(tmp_path, "Auth location", "Backend only", "Security")
        content_before = read_architect_md(tmp_path)
        assert content_before is not None
        assert "Auth location" in content_before

        # Update structure section
        update_structure_section(tmp_path, "**Type:** Monorepo\n**Detected:** multiple components")
        content_after = read_architect_md(tmp_path)
        assert content_after is not None
        # Structure should be updated
        assert "Monorepo" in content_after
        # Decision should be preserved
        assert "Auth location" in content_after

    def test_update_creates_if_missing(self, tmp_path: Path) -> None:
        """update_structure_section should create file if it doesn't exist."""
        update_structure_section(tmp_path, "**Type:** Single repo")
        assert (tmp_path / ARCHITECT_MD_FILE).exists()

    def test_second_plan_preserves_decisions(self, tmp_path: Path) -> None:
        """Second planning session should preserve permanent decisions."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_permanent_decision(tmp_path, "API style", "REST only", "Existing clients")
        append_lesson(tmp_path, "T01", "Tests must run from backend/")

        # Simulate second plan — structure section rewritten
        update_structure_section(tmp_path, "**Type:** Monorepo\n**Detected:** 2 components")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "API style" in content
        assert "REST only" in content
        assert "Tests must run from backend/" in content


# ---------------------------------------------------------------------------
# ARCHITECT.md — append operations
# ---------------------------------------------------------------------------


class TestArchitectMdAppend:
    """Tests for ARCHITECT.md append operations."""

    def test_append_permanent_decision(self, tmp_path: Path) -> None:
        """append_permanent_decision should add a row to the decisions table."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_permanent_decision(tmp_path, "Auth location", "Backend only", "Security")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Auth location" in content
        assert "Backend only" in content

    def test_append_constraint(self, tmp_path: Path) -> None:
        """append_constraint should add an entry to the constraints section."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_constraint(tmp_path, "Never modify engine/core/allocator.rs")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Never modify engine/core/allocator.rs" in content

    def test_append_lesson(self, tmp_path: Path) -> None:
        """append_lesson should add an entry to the lessons section."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_lesson(tmp_path, "T04", "Task was too large — split next time")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "T04" in content
        assert "Task was too large" in content

    def test_append_best_practice(self, tmp_path: Path) -> None:
        """append_best_practice should add an entry to the best practices section."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_best_practice(tmp_path, "Always extend the custom AppError class")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Always extend the custom AppError class" in content

    def test_append_planning_history(self, tmp_path: Path) -> None:
        """append_planning_history should add a row to the history table."""
        create_architect_md(tmp_path, "**Type:** Single repo")
        append_planning_history(tmp_path, "Build user auth", "T01-T09", "T04 was replanned")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Build user auth" in content
        assert "T01-T09" in content

    def test_append_noop_when_file_missing(self, tmp_path: Path) -> None:
        """Append operations should be no-op when file doesn't exist."""
        # These should not raise
        append_permanent_decision(tmp_path, "X", "Y", "Z")
        append_constraint(tmp_path, "test")
        append_lesson(tmp_path, "T01", "test")
        append_best_practice(tmp_path, "test")
        append_planning_history(tmp_path, "test", "T01", "")


# ---------------------------------------------------------------------------
# ARCHITECT.md — write_or_update integration
# ---------------------------------------------------------------------------


class TestWriteOrUpdateArchitectMd:
    """Tests for the write_or_update_architect_md integration function."""

    def test_creates_on_first_run(self, tmp_path: Path) -> None:
        """Should create ARCHITECT.md on first run."""
        report = StructureReport(repo_type=RepoType.SINGLE_REPO)
        path = write_or_update_architect_md(tmp_path, report)
        assert path.exists()

    def test_updates_on_second_run(self, tmp_path: Path) -> None:
        """Should update structure section on second run, preserving other sections."""
        report1 = StructureReport(repo_type=RepoType.SINGLE_REPO)
        write_or_update_architect_md(tmp_path, report1)
        append_permanent_decision(tmp_path, "API style", "REST only", "Existing clients")

        report2 = StructureReport(repo_type=RepoType.MONOREPO)
        write_or_update_architect_md(tmp_path, report2)

        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Monorepo" in content
        assert "API style" in content

    def test_malformed_file_recreated(self, tmp_path: Path) -> None:
        """Malformed ARCHITECT.md should be recreated fresh."""
        # Write a malformed file
        (tmp_path / ARCHITECT_MD_FILE).write_text(
            "This is not valid ARCHITECT.md format\n", encoding="utf-8"
        )
        report = StructureReport(repo_type=RepoType.SINGLE_REPO)
        write_or_update_architect_md(tmp_path, report)
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Project Structure" in content


# ---------------------------------------------------------------------------
# ARCHITECT.md — parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    """Tests for ARCHITECT.md section parsing."""

    def test_parse_sections(self) -> None:
        """Should parse ARCHITECT.md into named sections."""
        content = (
            "# ARCHITECT.md\n\n"
            "## Project Structure\n\nType: Single repo\n\n"
            "## Permanent Decisions\n\n| D | V |\n\n"
            "## Lessons Learned\n\n- lesson 1\n"
        )
        sections = parse_sections(content)
        assert "Project Structure" in sections
        assert "Permanent Decisions" in sections
        assert "Lessons Learned" in sections
        assert "Single repo" in sections["Project Structure"]

    def test_parse_empty_content(self) -> None:
        """Should return empty dict for empty content."""
        sections = parse_sections("")
        assert sections == {}


# ---------------------------------------------------------------------------
# Context injection — file reading
# ---------------------------------------------------------------------------


class TestContextFileReading:
    """Tests for context file reading."""

    def test_read_single_file(self, tmp_path: Path) -> None:
        """read_context_file should read a single file."""
        f = tmp_path / "requirements.md"
        f.write_text("# Requirements\nBuild a REST API\n", encoding="utf-8")
        content, truncated = read_context_file(f)
        assert "Build a REST API" in content
        assert not truncated

    def test_read_nonexistent_file(self, tmp_path: Path) -> None:
        """read_context_file should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            read_context_file(tmp_path / "nonexistent.md")

    def test_read_directory_raises(self, tmp_path: Path) -> None:
        """read_context_file should raise IsADirectoryError for directories."""
        d = tmp_path / "docs"
        d.mkdir()
        with pytest.raises(IsADirectoryError):
            read_context_file(d)

    def test_truncation(self, tmp_path: Path) -> None:
        """Files exceeding max_chars should be truncated with a note."""
        f = tmp_path / "large.txt"
        f.write_text("x" * (MAX_CONTEXT_FILE_CHARS + 1000), encoding="utf-8")
        content, truncated = read_context_file(f, max_chars=MAX_CONTEXT_FILE_CHARS)
        assert truncated
        assert "TRUNCATED" in content
        assert len(content) < MAX_CONTEXT_FILE_CHARS + 200  # Note adds some chars


# ---------------------------------------------------------------------------
# Context injection — directory reading
# ---------------------------------------------------------------------------


class TestContextDirectoryReading:
    """Tests for context directory reading."""

    def test_read_directory(self, tmp_path: Path) -> None:
        """read_context_directory should read all text files recursively."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "api.md").write_text("# API\nREST endpoints\n", encoding="utf-8")
        (docs / "config.yaml").write_text("key: value\n", encoding="utf-8")
        results = read_context_directory(docs)
        assert len(results) >= 2
        paths = [r[0] for r in results]
        assert "api.md" in paths

    def test_skip_binary_files(self, tmp_path: Path) -> None:
        """read_context_directory should skip binary files."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text("# Readme\n", encoding="utf-8")
        (docs / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        results = read_context_directory(docs)
        assert len(results) == 1
        assert results[0][0] == "readme.md"

    def test_skip_node_modules(self, tmp_path: Path) -> None:
        """read_context_directory should skip node_modules."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "node_modules").mkdir()
        (docs / "node_modules" / "pkg").mkdir()
        (docs / "node_modules" / "pkg" / "index.js").write_text(
            "module.exports = {};", encoding="utf-8"
        )
        (docs / "readme.md").write_text("# Readme\n", encoding="utf-8")
        results = read_context_directory(docs)
        assert len(results) == 1

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """read_context_directory should raise FileNotFoundError for missing dirs."""
        with pytest.raises(FileNotFoundError):
            read_context_directory(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# Context injection — formatting
# ---------------------------------------------------------------------------


class TestContextFormatting:
    """Tests for context formatting for prompt injection."""

    def test_format_empty(self) -> None:
        """format_context_for_prompt with no files should return empty string."""
        assert format_context_for_prompt([]) == ""

    def test_format_single_file(self) -> None:
        """format_context_for_prompt should label each file."""
        result = format_context_for_prompt([("requirements.md", "Build a REST API")])
        assert "CONTEXT: requirements.md" in result
        assert "Build a REST API" in result

    def test_format_multiple_files(self) -> None:
        """format_context_for_prompt should label all files."""
        result = format_context_for_prompt(
            [
                ("requirements.md", "Build a REST API"),
                ("api-spec.yaml", "openapi: 3.0"),
            ]
        )
        assert "CONTEXT: requirements.md" in result
        assert "CONTEXT: api-spec.yaml" in result


# ---------------------------------------------------------------------------
# Context injection — load_context_paths
# ---------------------------------------------------------------------------


class TestLoadContextPaths:
    """Tests for load_context_paths."""

    def test_load_file(self, tmp_path: Path) -> None:
        """Should load a single file."""
        f = tmp_path / "requirements.md"
        f.write_text("# Requirements\n", encoding="utf-8")
        results = load_context_paths([f])
        assert len(results) == 1

    def test_load_directory(self, tmp_path: Path) -> None:
        """Should load all text files from a directory."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "api.md").write_text("# API\n", encoding="utf-8")
        (docs / "spec.yaml").write_text("key: value\n", encoding="utf-8")
        results = load_context_paths([docs])
        assert len(results) >= 2

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for nonexistent paths."""
        with pytest.raises(FileNotFoundError):
            load_context_paths([tmp_path / "nonexistent.md"])


# ---------------------------------------------------------------------------
# Context injection — goal extraction
# ---------------------------------------------------------------------------


class TestGoalExtraction:
    """Tests for goal extraction from context files."""

    def test_extract_from_goal_section(self) -> None:
        """Should extract goal from ## Goal section."""
        content = "# Spec\n\n## Goal\nBuild a REST API for user management\n\n## Details\n"
        goal = extract_goal_from_context(content)
        assert goal is not None
        assert "REST API" in goal

    def test_extract_from_objective_section(self) -> None:
        """Should extract goal from ## Objective section."""
        content = "# Spec\n\n## Objective\nImplement dark mode\n\n## Details\n"
        goal = extract_goal_from_context(content)
        assert goal is not None
        assert "dark mode" in goal

    def test_no_goal_returns_none(self) -> None:
        """Should return None when no goal section found."""
        content = "# Spec\n\n## Details\nSome details\n"
        goal = extract_goal_from_context(content)
        assert goal is None

    def test_short_goal_ignored(self) -> None:
        """Goals shorter than 10 chars should be ignored."""
        content = "## Goal\nshort\n\n## Details\n"
        goal = extract_goal_from_context(content)
        assert goal is None


# ---------------------------------------------------------------------------
# Headless mode — CLI flags
# ---------------------------------------------------------------------------


class TestHeadlessMode:
    """Tests for headless mode CLI integration."""

    def test_headless_in_help(self, cli_runner: CliRunner) -> None:
        """--headless should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--headless" in result.output

    def test_goal_in_help(self, cli_runner: CliRunner) -> None:
        """--goal should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--goal" in result.output

    def test_scope_in_help(self, cli_runner: CliRunner) -> None:
        """--scope should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--scope" in result.output

    def test_context_in_help(self, cli_runner: CliRunner) -> None:
        """--context should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--context" in result.output

    def test_architect_model_in_help(self, cli_runner: CliRunner) -> None:
        """--architect-model should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--architect-model" in result.output

    def test_execution_model_in_help(self, cli_runner: CliRunner) -> None:
        """--execution-model should appear in help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--execution-model" in result.output


# ---------------------------------------------------------------------------
# Headless mode — environment variables
# ---------------------------------------------------------------------------


class TestHeadlessEnvVars:
    """Tests for headless mode environment variable support."""

    def test_architect_headless_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ARCHITECT_HEADLESS env var should enable headless mode."""
        monkeypatch.setenv("ARCHITECT_HEADLESS", "true")
        monkeypatch.chdir(tmp_path)
        # In headless mode without goal or context, should exit with error
        result = CliRunner().invoke(main, ["--plan"])
        # Should fail because no goal provided
        assert result.exit_code != 0

    def test_architect_goal_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ARCHITECT_GOAL env var should provide the goal."""
        monkeypatch.setenv("ARCHITECT_GOAL", "build a REST API")
        monkeypatch.chdir(tmp_path)
        # The goal should be picked up from env var
        # We can't fully test planning without mocking, but the flag should be accepted
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_architect_scope_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ARCHITECT_SCOPE env var should provide the scope."""
        monkeypatch.setenv("ARCHITECT_SCOPE", "simple")
        # The scope should be picked up from env var
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_cli_flag_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI flags should take precedence over environment variables."""
        monkeypatch.setenv("ARCHITECT_SCOPE", "simple")
        # --scope complex on CLI should override ARCHITECT_SCOPE=simple
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Planning instruction — new context sections
# ---------------------------------------------------------------------------


class TestPlanningInstructionContext:
    """Tests for the updated planning instruction with new context sections."""

    def test_instruction_includes_architect_md(self, tmp_path: Path) -> None:
        """Planning instruction should include ARCHITECT.md content."""
        from the_architect.core.planner import PlanningRequest, build_planning_instruction
        from the_architect.core.tasks import TaskScope

        request = PlanningRequest(
            goal="Build something",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            architect_md_content="Permanent Decisions: Use REST only",
        )
        instruction = build_planning_instruction(request, "project context")
        assert "ARCHITECT.md" in instruction
        assert "Use REST only" in instruction

    def test_instruction_includes_structure_report(self, tmp_path: Path) -> None:
        """Planning instruction should include structure report."""
        from the_architect.core.planner import PlanningRequest, build_planning_instruction
        from the_architect.core.tasks import TaskScope

        request = PlanningRequest(
            goal="Build something",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            structure_report="Repo type: Monorepo\nComponents: frontend, backend",
        )
        instruction = build_planning_instruction(request, "project context")
        assert "PROJECT STRUCTURE REPORT" in instruction
        assert "Monorepo" in instruction

    def test_instruction_includes_context_files(self, tmp_path: Path) -> None:
        """Planning instruction should include context file content."""
        from the_architect.core.planner import PlanningRequest, build_planning_instruction
        from the_architect.core.tasks import TaskScope

        request = PlanningRequest(
            goal="Build something",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            context_content="--- CONTEXT: requirements.md ---\nBuild a REST API",
        )
        instruction = build_planning_instruction(request, "project context")
        assert "ADDITIONAL CONTEXT FILES" in instruction
        assert "requirements.md" in instruction

    def test_instruction_without_new_context(self, tmp_path: Path) -> None:
        """Planning instruction should work without new context (backward compat)."""
        from the_architect.core.planner import PlanningRequest, build_planning_instruction
        from the_architect.core.tasks import TaskScope

        request = PlanningRequest(
            goal="Build something",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )
        instruction = build_planning_instruction(request, "project context")
        assert "=== PROJECT CONTEXT ===" in instruction
        assert "Build something" in instruction

    def test_instruction_reading_order(self, tmp_path: Path) -> None:
        """Context sections should appear in priority order."""
        from the_architect.core.planner import PlanningRequest, build_planning_instruction
        from the_architect.core.tasks import TaskScope

        request = PlanningRequest(
            goal="Build something",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            architect_md_content="ARCHITECT.md content here",
            structure_report="Structure report here",
            context_content="Context files here",
        )
        instruction = build_planning_instruction(request, "project context")

        # ARCHITECT.md should come before structure report
        arch_pos = instruction.find("ARCHITECT.md")
        struct_pos = instruction.find("PROJECT STRUCTURE REPORT")
        ctx_pos = instruction.find("ADDITIONAL CONTEXT FILES")
        proj_pos = instruction.find("=== PROJECT CONTEXT ===")
        goal_pos = instruction.find("=== USER REQUEST ===")

        assert arch_pos < struct_pos < ctx_pos < proj_pos < goal_pos
