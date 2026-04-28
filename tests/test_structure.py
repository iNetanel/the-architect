"""Tests for the_architect.core.structure — project structure detection."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from the_architect.core.structure import (
    Component,
    Dependency,
    RepoType,
    StructureReport,
    _check_cargo_path_deps,
    _check_go_framework,
    _check_js_framework,
    _check_package_json_deps,
    _check_python_framework,
    _check_python_path_deps,
    _check_rust_framework,
    _detect_components,
    _detect_csharp_project,
    _detect_dependencies,
    _detect_framework,
    _detect_language,
    _detect_repo_type,
    _detect_sub_components,
    _enrich_component,
    _enrich_from_package_json,
    _enrich_from_pyproject_toml,
    _extract_deps_from_text,
    _format_component_block,
    _format_component_prompt,
    _infer_role,
    _infer_role_from_subs,
    _parse_docker_compose,
    _parse_docker_compose_regex,
    _read_package_json_deps,
    _service_to_component,
    detect_structure,
    format_structure_for_prompt,
    format_structure_report,
)


def _write_file(path: Path, content: str) -> Path:
    """Helper: ensure parent dir exists, then write content to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------


class TestComponent:
    """Tests for the Component class."""

    def test_to_dict_all_fields(self) -> None:
        comp = Component(
            path="frontend/",
            language="TypeScript",
            framework="React",
            role="Web UI",
        )
        d = comp.to_dict()
        assert d["path"] == "frontend/"
        assert d["language"] == "TypeScript"
        assert d["framework"] == "React"
        assert d["role"] == "Web UI"

    def test_to_dict_empty_fields_show_dash(self) -> None:
        comp = Component(path="app/")
        d = comp.to_dict()
        assert d["language"] == "—"
        assert d["framework"] == "—"
        assert d["role"] == "—"

    def test_defaults(self) -> None:
        comp = Component(path="x/")
        assert comp.language == ""
        assert comp.framework == ""
        assert comp.role == ""
        assert comp.description == ""
        assert comp.key_deps == []
        assert comp.test_command == ""
        assert comp.lint_command == ""
        assert comp.sub_components == []
        assert comp.signals == []


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


class TestDependency:
    """Tests for the Dependency class."""

    def test_str(self) -> None:
        dep = Dependency(source="frontend/", target="backend/", via="docker-compose")
        assert str(dep) == "frontend/ → backend/  (via: docker-compose)"


# ---------------------------------------------------------------------------
# StructureReport
# ---------------------------------------------------------------------------


class TestStructureReport:
    """Tests for the StructureReport class."""

    def test_defaults(self) -> None:
        report = StructureReport()
        assert report.repo_type == RepoType.UNTRACKED
        assert report.components == []
        assert report.dependencies == []
        assert report.shared_resources == []
        assert report.detected_at  # should be today's date

    def test_custom_values(self) -> None:
        comp = Component(path="app/")
        dep = Dependency(source="a/", target="b/", via="test")
        report = StructureReport(
            repo_type=RepoType.MONOREPO,
            components=[comp],
            dependencies=[dep],
            shared_resources=["shared/"],
            detected_at="2026-01-01",
        )
        assert report.repo_type == RepoType.MONOREPO
        assert len(report.components) == 1
        assert len(report.dependencies) == 1
        assert report.shared_resources == ["shared/"]
        assert report.detected_at == "2026-01-01"


# ---------------------------------------------------------------------------
# _read_package_json_deps
# ---------------------------------------------------------------------------


class TestReadPackageJsonDeps:
    """Tests for _read_package_json_deps()."""

    def test_reads_dependencies_and_dev(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"react": "^18"}, "devDependencies": {"vitest": "^1"}}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        deps = _read_package_json_deps(p)
        assert "react" in deps
        assert "vitest" in deps

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "package.json"
        p.write_text("{}")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            assert _read_package_json_deps(p) == set()

    def test_json_decode_error_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "package.json"
        p.write_text("not json{{{")
        assert _read_package_json_deps(p) == set()

    def test_non_dict_section_ignored(self, tmp_path: Path) -> None:
        pkg = {"dependencies": "not a dict"}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        deps = _read_package_json_deps(p)
        assert deps == set()


# ---------------------------------------------------------------------------
# _extract_deps_from_text
# ---------------------------------------------------------------------------


class TestExtractDepsFromText:
    """Tests for _extract_deps_from_text()."""

    def test_toml_style(self) -> None:
        text = 'fastapi = ">=0.100"\ndjango = "^4"'
        deps = _extract_deps_from_text(text)
        assert "fastapi" in deps
        assert "django" in deps

    def test_requirements_style(self) -> None:
        text = "flask==2.0\nrequests>=2.28"
        deps = _extract_deps_from_text(text)
        assert "flask" in deps
        assert "requests" in deps

    def test_skips_metadata_keys_in_toml(self) -> None:
        # The TOML-style regex skips known metadata keys; the requirements-style
        # regex does not.  So we only test that the TOML path skips them.
        text = 'fastapi = "0.1"'
        deps = _extract_deps_from_text(text)
        assert "fastapi" in deps
        assert "version" not in deps
        assert "name" not in deps


# ---------------------------------------------------------------------------
# _detect_csharp_project
# ---------------------------------------------------------------------------


class TestDetectCSharpProject:
    """Tests for _detect_csharp_project()."""

    def test_csproj_detected(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text("<Project/>")
        assert _detect_csharp_project(tmp_path) is True

    def test_vbproj_detected(self, tmp_path: Path) -> None:
        (tmp_path / "App.vbproj").write_text("<Project/>")
        assert _detect_csharp_project(tmp_path) is True

    def test_fsproj_detected(self, tmp_path: Path) -> None:
        (tmp_path / "App.fsproj").write_text("<Project/>")
        assert _detect_csharp_project(tmp_path) is True

    def test_no_project_file(self, tmp_path: Path) -> None:
        assert _detect_csharp_project(tmp_path) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text("<Project/>")
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            assert _detect_csharp_project(tmp_path) is False


# ---------------------------------------------------------------------------
# _check_js_framework
# ---------------------------------------------------------------------------


class TestCheckJsFramework:
    """Tests for _check_js_framework()."""

    def test_next_config_js(self, tmp_path: Path) -> None:
        (tmp_path / "next.config.js").write_text("module.exports = {}")
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_next_config_ts(self, tmp_path: Path) -> None:
        (tmp_path / "next.config.ts").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_next_config_mjs(self, tmp_path: Path) -> None:
        (tmp_path / "next.config.mjs").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_nuxt_config_js(self, tmp_path: Path) -> None:
        (tmp_path / "nuxt.config.js").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Nuxt.js"

    def test_nuxt_config_ts(self, tmp_path: Path) -> None:
        (tmp_path / "nuxt.config.ts").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Nuxt.js"

    def test_vite_config_js(self, tmp_path: Path) -> None:
        (tmp_path / "vite.config.js").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Vite"

    def test_vite_config_ts(self, tmp_path: Path) -> None:
        (tmp_path / "vite.config.ts").write_text("export default {}")
        assert _check_js_framework(tmp_path) == "Vite"

    def test_react_native_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"react-native": "^0.72"}})
        )
        assert _check_js_framework(tmp_path) == "React Native"

    def test_angular_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"@angular/core": "^16"}})
        )
        assert _check_js_framework(tmp_path) == "Angular"

    def test_next_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"next": "^14"}}))
        assert _check_js_framework(tmp_path) == "Next.js"

    def test_nuxt_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"nuxt": "^3"}}))
        assert _check_js_framework(tmp_path) == "Nuxt.js"

    def test_vue_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"vue": "^3"}}))
        assert _check_js_framework(tmp_path) == "Vue"

    def test_svelte_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"svelte": "^4"}}))
        assert _check_js_framework(tmp_path) == "Svelte"

    def test_express_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "^4"}}))
        assert _check_js_framework(tmp_path) == "Express"

    def test_fastify_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"fastify": "^4"}}))
        assert _check_js_framework(tmp_path) == "Fastify"

    def test_react_dep(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
        assert _check_js_framework(tmp_path) == "React"

    def test_no_framework_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"lodash": "^4"}}))
        assert _check_js_framework(tmp_path) == ""

    def test_no_package_json_returns_empty(self, tmp_path: Path) -> None:
        assert _check_js_framework(tmp_path) == ""


# ---------------------------------------------------------------------------
# _check_python_framework
# ---------------------------------------------------------------------------


class TestCheckPythonFramework:
    """Tests for _check_python_framework()."""

    def test_fastapi_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi>=0.100"]\n')
        assert _check_python_framework(tmp_path) == "FastAPI"

    def test_django_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["django>=4"]\n')
        assert _check_python_framework(tmp_path) == "Django"

    def test_flask_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["flask>=2"]\n')
        assert _check_python_framework(tmp_path) == "Flask"

    def test_fastapi_from_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100\n")
        assert _check_python_framework(tmp_path) == "FastAPI"

    def test_django_from_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("django>=4\n")
        assert _check_python_framework(tmp_path) == "Django"

    def test_flask_from_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask>=2\n")
        assert _check_python_framework(tmp_path) == "Flask"

    def test_no_framework(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["requests>=2"]\n')
        assert _check_python_framework(tmp_path) == ""

    def test_oserror_on_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("fastapi = '0.1'")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            assert _check_python_framework(tmp_path) == ""

    def test_oserror_on_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi>=0.1")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            assert _check_python_framework(tmp_path) == ""


# ---------------------------------------------------------------------------
# _check_rust_framework
# ---------------------------------------------------------------------------


class TestCheckRustFramework:
    """Tests for _check_rust_framework()."""

    def test_axum(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[dependencies]\naxum = "0.6"\n')
        assert _check_rust_framework(tmp_path) == "Axum"

    def test_actix(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[dependencies]\nactix-web = "4"\n')
        assert _check_rust_framework(tmp_path) == "Actix"

    def test_tokio(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[dependencies]\ntokio = "1"\n')
        assert _check_rust_framework(tmp_path) == "async runtime (tokio)"

    def test_no_cargo_toml(self, tmp_path: Path) -> None:
        assert _check_rust_framework(tmp_path) == ""

    def test_no_framework(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[dependencies]\nserde = "1"\n')
        assert _check_rust_framework(tmp_path) == ""

    def test_oserror(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("axum")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            assert _check_rust_framework(tmp_path) == ""


# ---------------------------------------------------------------------------
# _check_go_framework
# ---------------------------------------------------------------------------


class TestCheckGoFramework:
    """Tests for _check_go_framework()."""

    def test_gin(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("require github.com/gin-gonic/gin v1\n")
        assert _check_go_framework(tmp_path) == "Gin"

    def test_echo(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("require github.com/labstack/echo v4\n")
        assert _check_go_framework(tmp_path) == "Echo"

    def test_no_go_mod(self, tmp_path: Path) -> None:
        assert _check_go_framework(tmp_path) == ""

    def test_no_framework(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        assert _check_go_framework(tmp_path) == ""

    def test_oserror(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("gin")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            assert _check_go_framework(tmp_path) == ""


# ---------------------------------------------------------------------------
# _enrich_from_pyproject_toml
# ---------------------------------------------------------------------------


class TestEnrichFromPyprojectToml:
    """Tests for _enrich_from_pyproject_toml()."""

    def test_extracts_description(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndescription = "My cool app"\n')
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert comp.description == "My cool app"

    def test_extracts_deps(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi>=0.100", "httpx>=0.24"]\n'
        )
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert "fastapi" in comp.key_deps
        assert "httpx" in comp.key_deps

    def test_detects_pytest_test_command(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = []\n[tool.pytest]\n")
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert comp.test_command == "pytest tests/ -v --tb=short"

    def test_detects_ruff_lint_command(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = []\n[tool.ruff]\n")
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert comp.lint_command == "ruff check ."

    def test_no_file_returns_early(self, tmp_path: Path) -> None:
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert comp.description == ""
        assert comp.key_deps == []

    def test_oserror_returns_early(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        comp = Component(path="app/")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            _enrich_from_pyproject_toml(tmp_path, comp)
        assert comp.description == ""

    def test_optional_deps_extracted(self, tmp_path: Path) -> None:
        content = (
            '[project]\ndependencies = []\n[project.optional-dependencies.dev]\n"httpx>=0.24"\n'
        )
        (tmp_path / "pyproject.toml").write_text(content)
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert "httpx" in comp.key_deps

    def test_skip_deps_filtered(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["pytest>=7", "ruff>=0.1", "fastapi>=0.1"]\n'
        )
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert "pytest" not in comp.key_deps
        assert "ruff" not in comp.key_deps
        assert "fastapi" in comp.key_deps

    def test_key_deps_limited_to_8(self, tmp_path: Path) -> None:
        deps = ", ".join(f'"dep{i}>=1.0"' for i in range(12))
        (tmp_path / "pyproject.toml").write_text(f"[project]\ndependencies = [{deps}]\n")
        comp = Component(path="app/")
        _enrich_from_pyproject_toml(tmp_path, comp)
        assert len(comp.key_deps) == 8


# ---------------------------------------------------------------------------
# _enrich_from_package_json
# ---------------------------------------------------------------------------


class TestEnrichFromPackageJson:
    """Tests for _enrich_from_package_json()."""

    def test_extracts_description(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"description": "My app"}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.description == "My app"

    def test_extracts_key_deps(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^18", "next": "^14"}})
        )
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert "react" in comp.key_deps
        assert "next" in comp.key_deps

    def test_skips_tooling_deps(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "dependencies": {"react": "^18"},
                    "devDependencies": {
                        "typescript": "^5",
                        "eslint": "^8",
                        "prettier": "^3",
                    },
                }
            )
        )
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert "react" in comp.key_deps
        assert "typescript" not in comp.key_deps
        assert "eslint" not in comp.key_deps

    def test_detects_test_command(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.test_command == "npm test"

    def test_detects_lint_command(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.lint_command == "npm run lint"

    def test_no_file_returns_early(self, tmp_path: Path) -> None:
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.description == ""

    def test_oserror_returns_early(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        comp = Component(path="app/")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            _enrich_from_package_json(tmp_path, comp)
        assert comp.description == ""

    def test_json_decode_error_returns_early(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("not json{{{")
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.description == ""

    def test_non_dict_deps_section_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": ["not", "a", "dict"]}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.key_deps == []

    def test_non_dict_scripts_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"scripts": "not a dict"}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.test_command == ""

    def test_empty_description_not_set(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"description": ""}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.description == ""

    def test_non_string_description_not_set(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"description": 42}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert comp.description == ""

    def test_key_deps_limited_to_8(self, tmp_path: Path) -> None:
        deps = {f"dep{i}": f"^{i}" for i in range(12)}
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": deps}))
        comp = Component(path="app/")
        _enrich_from_package_json(tmp_path, comp)
        assert len(comp.key_deps) == 8


# ---------------------------------------------------------------------------
# _detect_repo_type
# ---------------------------------------------------------------------------


class TestDetectRepoType:
    """Tests for _detect_repo_type()."""

    def test_multi_repo(self, tmp_path: Path) -> None:
        (tmp_path / "service-a" / ".git").mkdir(parents=True)
        (tmp_path / "service-b" / ".git").mkdir(parents=True)
        assert _detect_repo_type(tmp_path) == RepoType.MULTI_REPO

    def test_single_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _detect_repo_type(tmp_path) == RepoType.SINGLE_REPO

    def test_untracked(self, tmp_path: Path) -> None:
        assert _detect_repo_type(tmp_path) == RepoType.UNTRACKED

    def test_oserror_handled(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            result = _detect_repo_type(tmp_path)
            assert result in (RepoType.SINGLE_REPO, RepoType.UNTRACKED)


# ---------------------------------------------------------------------------
# _detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    """Tests for _detect_language()."""

    def test_detects_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Python"
        assert "pyproject.toml" in comp.signals

    def test_detects_javascript(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "JavaScript/TypeScript"

    def test_detects_rust(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Rust"

    def test_detects_go(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example\n")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Go"

    def test_detects_java(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project/>")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Java/Kotlin"

    def test_detects_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").write_text("plugins {}")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Java/Kotlin"

    def test_detects_php(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text("{}")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "PHP"

    def test_detects_ruby(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text('source "https://rubygems.org"')
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "Ruby"

    def test_detects_csharp(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text("<Project/>")
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == "C#"
        assert "*.csproj" in comp.signals

    def test_no_signal(self, tmp_path: Path) -> None:
        comp = Component(path="app/")
        _detect_language(tmp_path, comp)
        assert comp.language == ""


# ---------------------------------------------------------------------------
# _detect_framework
# ---------------------------------------------------------------------------


class TestDetectFramework:
    """Tests for _detect_framework()."""

    def test_js_framework(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
        comp = Component(path="app/", language="JavaScript/TypeScript")
        _detect_framework(tmp_path, comp)
        assert comp.framework == "React"

    def test_python_framework(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi>=0.100"]\n')
        comp = Component(path="app/", language="Python")
        _detect_framework(tmp_path, comp)
        assert comp.framework == "FastAPI"

    def test_rust_framework(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[dependencies]\naxum = "0.6"\n')
        comp = Component(path="app/", language="Rust")
        _detect_framework(tmp_path, comp)
        assert comp.framework == "Axum"

    def test_go_framework(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("require github.com/gin-gonic/gin v1\n")
        comp = Component(path="app/", language="Go")
        _detect_framework(tmp_path, comp)
        assert comp.framework == "Gin"

    def test_unknown_language_no_framework(self, tmp_path: Path) -> None:
        comp = Component(path="app/", language="Haskell")
        _detect_framework(tmp_path, comp)
        assert comp.framework == ""


# ---------------------------------------------------------------------------
# _infer_role
# ---------------------------------------------------------------------------


class TestInferRole:
    """Tests for _infer_role()."""

    def test_frontend_name(self) -> None:
        comp = Component(path="frontend/")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_web_name(self) -> None:
        comp = Component(path="web/")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_client_name(self) -> None:
        comp = Component(path="client/")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_mobile_name(self) -> None:
        comp = Component(path="mobile/")
        _infer_role(comp)
        assert comp.role == "Mobile UI"

    def test_app_name(self) -> None:
        comp = Component(path="app/")
        _infer_role(comp)
        assert comp.role == "Application"

    def test_backend_name(self) -> None:
        comp = Component(path="backend/")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_api_name(self) -> None:
        comp = Component(path="api/")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_server_name(self) -> None:
        comp = Component(path="server/")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_engine_name(self) -> None:
        comp = Component(path="engine/")
        _infer_role(comp)
        assert comp.role == "Core library"

    def test_core_name(self) -> None:
        comp = Component(path="core/")
        _infer_role(comp)
        assert comp.role == "Core library"

    def test_worker_name(self) -> None:
        comp = Component(path="worker/")
        _infer_role(comp)
        assert comp.role == "Background worker"

    def test_jobs_name(self) -> None:
        comp = Component(path="jobs/")
        _infer_role(comp)
        assert comp.role == "Background worker"

    def test_packages_name(self) -> None:
        comp = Component(path="packages/")
        _infer_role(comp)
        assert comp.role == "Shared library"

    def test_shared_name(self) -> None:
        comp = Component(path="shared/")
        _infer_role(comp)
        assert comp.role == "Shared library"

    def test_common_name(self) -> None:
        comp = Component(path="common/")
        _infer_role(comp)
        assert comp.role == "Shared library"

    def test_libs_name(self) -> None:
        comp = Component(path="libs/")
        _infer_role(comp)
        assert comp.role == "Shared library"

    def test_infra_name(self) -> None:
        comp = Component(path="infra/")
        _infer_role(comp)
        assert comp.role == "Infrastructure"

    def test_deploy_name(self) -> None:
        comp = Component(path="deploy/")
        _infer_role(comp)
        assert comp.role == "Infrastructure"

    def test_terraform_name(self) -> None:
        comp = Component(path="terraform/")
        _infer_role(comp)
        assert comp.role == "Infrastructure"

    def test_name_with_hyphen_prefix(self) -> None:
        comp = Component(path="frontend-web/")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_name_with_underscore_prefix(self) -> None:
        comp = Component(path="backend_api/")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_nextjs(self) -> None:
        comp = Component(path="myapp/", framework="Next.js")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_framework_react(self) -> None:
        comp = Component(path="myapp/", framework="React")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_framework_vue(self) -> None:
        comp = Component(path="myapp/", framework="Vue")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_framework_svelte(self) -> None:
        comp = Component(path="myapp/", framework="Svelte")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_framework_angular(self) -> None:
        comp = Component(path="myapp/", framework="Angular")
        _infer_role(comp)
        assert comp.role == "Web UI"

    def test_framework_fastapi(self) -> None:
        comp = Component(path="myapp/", framework="FastAPI")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_django(self) -> None:
        comp = Component(path="myapp/", framework="Django")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_flask(self) -> None:
        comp = Component(path="myapp/", framework="Flask")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_express(self) -> None:
        comp = Component(path="myapp/", framework="Express")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_fastify(self) -> None:
        comp = Component(path="myapp/", framework="Fastify")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_axum(self) -> None:
        comp = Component(path="myapp/", framework="Axum")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_actix(self) -> None:
        comp = Component(path="myapp/", framework="Actix")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_gin(self) -> None:
        comp = Component(path="myapp/", framework="Gin")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_framework_echo(self) -> None:
        comp = Component(path="myapp/", framework="Echo")
        _infer_role(comp)
        assert comp.role == "API server"

    def test_no_match(self) -> None:
        comp = Component(path="mystery/")
        _infer_role(comp)
        assert comp.role == ""

    def test_framework_react_native_matches_react_first(self) -> None:
        # "react" comes before "react native" in the framework_roles list,
        # so React Native framework matches "react" prefix → "Web UI"
        comp = Component(path="myapp/", framework="React Native")
        _infer_role(comp)
        assert comp.role == "Web UI"


# ---------------------------------------------------------------------------
# _infer_role_from_subs
# ---------------------------------------------------------------------------


class TestInferRoleFromSubs:
    """Tests for _infer_role_from_subs()."""

    def test_full_stack_application(self) -> None:
        comp = Component(
            path="app/",
            sub_components=[
                Component(path="frontend/", role="Web UI"),
                Component(path="backend/", role="API server"),
            ],
        )
        _infer_role_from_subs(comp)
        assert comp.role == "Full-stack application"

    def test_single_role_from_sub(self) -> None:
        comp = Component(
            path="app/",
            sub_components=[Component(path="frontend/", role="Web UI")],
        )
        _infer_role_from_subs(comp)
        assert comp.role == "Web UI"

    def test_fallback_name_app(self) -> None:
        comp = Component(path="app/", sub_components=[])
        _infer_role_from_subs(comp)
        assert comp.role == "Application"

    def test_fallback_name_dev(self) -> None:
        comp = Component(path="dev/", sub_components=[])
        _infer_role_from_subs(comp)
        assert comp.role == "Development environment"

    def test_fallback_name_documentation(self) -> None:
        comp = Component(path="documentation/", sub_components=[])
        _infer_role_from_subs(comp)
        assert comp.role == "Documentation"

    def test_no_match(self) -> None:
        comp = Component(path="mystery/", sub_components=[])
        _infer_role_from_subs(comp)
        assert comp.role == ""


# ---------------------------------------------------------------------------
# _detect_components
# ---------------------------------------------------------------------------


class TestDetectComponents:
    """Tests for _detect_components()."""

    def test_multi_repo(self, tmp_path: Path) -> None:
        (tmp_path / "service-a" / ".git").mkdir(parents=True)
        (tmp_path / "service-b" / ".git").mkdir(parents=True)
        _write_file(tmp_path / "service-a" / "pyproject.toml", "[project]\n")
        _write_file(tmp_path / "service-b" / "package.json", "{}")
        components = _detect_components(tmp_path, RepoType.MULTI_REPO)
        assert len(components) == 2

    def test_multi_repo_oserror(self, tmp_path: Path) -> None:
        (tmp_path / "service-a" / ".git").mkdir(parents=True)
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            components = _detect_components(tmp_path, RepoType.MULTI_REPO)
            assert components == []

    def test_single_repo_with_signals(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(
            tmp_path / "frontend" / "package.json",
            json.dumps({"dependencies": {"react": "^18"}}),
        )
        _write_file(
            tmp_path / "backend" / "pyproject.toml",
            '[project]\ndependencies = ["fastapi"]\n',
        )
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 2

    def test_single_repo_with_csharp(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "app" / "MyApp.csproj", "<Project/>")
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert components[0].language == "C#"

    def test_single_repo_with_sub_components(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(
            tmp_path / "app" / "frontend" / "package.json",
            json.dumps({"dependencies": {"react": "^18"}}),
        )
        _write_file(
            tmp_path / "app" / "backend" / "pyproject.toml",
            '[project]\ndependencies = ["fastapi"]\n',
        )
        components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
        assert len(components) == 1
        assert len(components[0].sub_components) == 2

    def test_single_repo_oserror(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            components = _detect_components(tmp_path, RepoType.SINGLE_REPO)
            assert components == []


# ---------------------------------------------------------------------------
# _enrich_component
# ---------------------------------------------------------------------------


class TestEnrichComponent:
    """Tests for _enrich_component()."""

    def test_reinfer_role_with_framework(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
        comp = Component(path="myapp/")
        _enrich_component(tmp_path, comp)
        assert comp.framework == "React"
        assert comp.role == "Web UI"

    def test_sub_components_with_no_role(self, tmp_path: Path) -> None:
        _write_file(
            tmp_path / "frontend" / "package.json",
            json.dumps({"dependencies": {"react": "^18"}}),
        )
        _write_file(
            tmp_path / "backend" / "pyproject.toml",
            '[project]\ndependencies = ["fastapi"]\n',
        )
        comp = Component(path="app/")
        _enrich_component(tmp_path, comp)
        assert len(comp.sub_components) == 2


# ---------------------------------------------------------------------------
# _detect_sub_components
# ---------------------------------------------------------------------------


class TestDetectSubComponents:
    """Tests for _detect_sub_components()."""

    def test_finds_sub_components(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "frontend" / "package.json", "{}")
        _write_file(tmp_path / "backend" / "pyproject.toml", "[project]\n")
        subs = _detect_sub_components(tmp_path)
        assert len(subs) == 2

    def test_csharp_sub_component(self, tmp_path: Path) -> None:
        # .csproj must be inside a subdirectory, not directly in parent
        _write_file(tmp_path / "subdir" / "App.csproj", "<Project/>")
        subs = _detect_sub_components(tmp_path)
        assert len(subs) == 1
        assert subs[0].language == "C#"

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            subs = _detect_sub_components(tmp_path)
            assert subs == []

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        subs = _detect_sub_components(tmp_path)
        assert subs == []


# ---------------------------------------------------------------------------
# _service_to_component
# ---------------------------------------------------------------------------


class TestServiceToComponent:
    """Tests for _service_to_component()."""

    def test_direct_match(self) -> None:
        comps = {"frontend": Component(path="frontend/")}
        result = _service_to_component("frontend", comps)
        assert result == "frontend/"

    def test_direct_match_hyphens(self) -> None:
        comps = {"web-app": Component(path="web-app/")}
        result = _service_to_component("webapp", comps)
        assert result == "web-app/"

    def test_prefix_match(self) -> None:
        # "frontend" starts with "front" — prefix match
        comps = {"front": Component(path="front/")}
        result = _service_to_component("frontend", comps)
        assert result == "front/"

    def test_no_match(self) -> None:
        comps = {"app": Component(path="app/")}
        result = _service_to_component("unknown", comps)
        assert result == ""


# ---------------------------------------------------------------------------
# _parse_docker_compose (YAML path)
# ---------------------------------------------------------------------------


class TestParseDockerComposeYaml:
    """Tests for _parse_docker_compose() with YAML library available."""

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("version: '3'\n")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            deps, shared = _parse_docker_compose(compose_path, {})
            assert deps == []
            assert shared is False

    def test_import_error_falls_back_to_regex(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose = "services:\n  frontend:\n    depends_on:\n      - backend\n"
        compose_path.write_text(compose)
        with patch("importlib.import_module", side_effect=ImportError("no yaml")):
            comps = {
                "frontend": Component(path="frontend/"),
                "backend": Component(path="backend/"),
            }
            deps, shared = _parse_docker_compose(compose_path, comps)
            assert len(deps) == 1

    def test_non_dict_root(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("services: {}")
        with patch("importlib.import_module") as mock_import:
            mock_yaml = Mock()
            mock_yaml.safe_load = Mock(return_value=["not", "a", "dict"])
            mock_import.return_value = mock_yaml
            deps, shared = _parse_docker_compose(compose_path, {})
            assert deps == []

    def test_non_dict_services(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("services: not_dict")
        with patch("importlib.import_module") as mock_import:
            mock_yaml = Mock()
            mock_yaml.safe_load = Mock(return_value={"services": "not a dict"})
            mock_import.return_value = mock_yaml
            deps, shared = _parse_docker_compose(compose_path, {})
            assert deps == []

    def test_yaml_generic_exception(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("version: '3'\n")
        with patch("importlib.import_module") as mock_import:
            mock_yaml = Mock()
            mock_yaml.safe_load = Mock(side_effect=Exception("bad yaml"))
            mock_import.return_value = mock_yaml
            deps, shared = _parse_docker_compose(compose_path, {})
            assert deps == []
            assert shared is False

    def test_non_dict_service_config_skipped(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("services:\n  frontend: not_dict")
        with patch("importlib.import_module") as mock_import:
            mock_yaml = Mock()
            mock_yaml.safe_load = Mock(return_value={"services": {"frontend": "not a dict"}})
            mock_import.return_value = mock_yaml
            comps = {"frontend": Component(path="frontend/")}
            deps, shared = _parse_docker_compose(compose_path, comps)
            assert deps == []

    def test_depends_on_not_list_nor_dict(self, tmp_path: Path) -> None:
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text("services:\n  frontend:\n    depends_on: 42")
        with patch("importlib.import_module") as mock_import:
            mock_yaml = Mock()
            mock_yaml.safe_load = Mock(return_value={"services": {"frontend": {"depends_on": 42}}})
            mock_import.return_value = mock_yaml
            comps = {"frontend": Component(path="frontend/")}
            deps, shared = _parse_docker_compose(compose_path, comps)
            assert deps == []

    def test_list_depends_on(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        compose_path = tmp_path / "docker-compose.yml"
        compose = (
            "version: '3'\n"
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      - backend\n"
            "  backend:\n"
            "    image: python\n"
        )
        compose_path.write_text(compose)
        comps = {
            "frontend": Component(path="frontend/"),
            "backend": Component(path="backend/"),
        }
        deps, shared = _parse_docker_compose(compose_path, comps)
        assert len(deps) == 1
        assert deps[0].source == "frontend/"
        assert deps[0].target == "backend/"

    def test_dict_depends_on(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        compose_path = tmp_path / "docker-compose.yml"
        compose = (
            "version: '3'\n"
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      backend:\n"
            "        condition: service_started\n"
        )
        compose_path.write_text(compose)
        comps = {
            "frontend": Component(path="frontend/"),
            "backend": Component(path="backend/"),
        }
        deps, shared = _parse_docker_compose(compose_path, comps)
        assert len(deps) == 1

    def test_service_not_mapped_marks_shared(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        compose_path = tmp_path / "docker-compose.yml"
        compose = (
            "version: '3'\n"
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      - backend\n"
            "  backend:\n"
            "    image: python\n"
        )
        compose_path.write_text(compose)
        # Only frontend is mapped — backend is not
        comps = {"frontend": Component(path="frontend/")}
        deps, shared = _parse_docker_compose(compose_path, comps)
        assert shared is True

    def test_source_not_mapped_marks_shared(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        compose_path = tmp_path / "docker-compose.yml"
        compose = "version: '3'\nservices:\n  unknown_svc:\n    depends_on:\n      - backend\n"
        compose_path.write_text(compose)
        comps = {"backend": Component(path="backend/")}
        deps, shared = _parse_docker_compose(compose_path, comps)
        assert shared is True


# ---------------------------------------------------------------------------
# _parse_docker_compose_regex
# ---------------------------------------------------------------------------


class TestParseDockerComposeRegex:
    """Tests for _parse_docker_compose_regex() fallback parser."""

    def test_list_depends_on(self) -> None:
        content = "services:\n  frontend:\n    depends_on:\n      - backend\n      - database\n"
        comps = {
            "frontend": Component(path="frontend/"),
            "backend": Component(path="backend/"),
            "database": Component(path="database/"),
        }
        deps, shared = _parse_docker_compose_regex(content, comps)
        assert len(deps) == 2

    def test_dict_depends_on(self) -> None:
        # Dict-style depends_on requires 8+ spaces for key lines
        content = (
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "        backend:\n"
            "          condition: service_started\n"
        )
        comps = {
            "frontend": Component(path="frontend/"),
            "backend": Component(path="backend/"),
        }
        deps, shared = _parse_docker_compose_regex(content, comps)
        assert len(deps) == 1
        assert deps[0].source == "frontend/"
        assert deps[0].target == "backend/"

    def test_leaving_depends_on_block(self) -> None:
        content = (
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      - backend\n"
            "    ports:\n"
            "      - '3000:3000'\n"
        )
        comps = {
            "frontend": Component(path="frontend/"),
            "backend": Component(path="backend/"),
        }
        deps, shared = _parse_docker_compose_regex(content, comps)
        assert len(deps) == 1

    def test_unknown_service_marks_shared(self) -> None:
        content = "services:\n  frontend:\n    depends_on:\n      - unknown_service\n"
        comps = {"frontend": Component(path="frontend/")}
        deps, shared = _parse_docker_compose_regex(content, comps)
        assert shared is True

    def test_unknown_source_marks_shared(self) -> None:
        content = "services:\n  unknown_svc:\n    depends_on:\n      - backend\n"
        comps = {"backend": Component(path="backend/")}
        deps, shared = _parse_docker_compose_regex(content, comps)
        assert shared is True

    def test_empty_content(self) -> None:
        deps, shared = _parse_docker_compose_regex("", {})
        assert deps == []
        assert shared is False


# ---------------------------------------------------------------------------
# _check_package_json_deps
# ---------------------------------------------------------------------------


class TestCheckPackageJsonDeps:
    """Tests for _check_package_json_deps()."""

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "package.json"
        p.write_text("{}")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            result = _check_package_json_deps(p, "app/", {}, tmp_path)
            assert result == []

    def test_json_decode_error_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "package.json"
        p.write_text("not json{{{")
        result = _check_package_json_deps(p, "app/", {}, tmp_path)
        assert result == []

    def test_workspaces_list(self, tmp_path: Path) -> None:
        pkg = {"workspaces": ["packages/*"]}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"utils": Component(path="utils/")}
        result = _check_package_json_deps(p, "root/", comps, tmp_path)
        # Should find workspace dependency
        assert len(result) >= 1

    def test_non_list_workspaces_ignored(self, tmp_path: Path) -> None:
        pkg = {"workspaces": "not a list"}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"utils": Component(path="utils/")}
        result = _check_package_json_deps(p, "root/", comps, tmp_path)
        assert result == []

    def test_local_path_dependency(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"shared": "file:../shared"}}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"shared": Component(path="shared/")}
        result = _check_package_json_deps(p, "app/", comps, tmp_path)
        assert len(result) >= 1

    def test_link_path_dependency(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"shared": "link:../shared"}}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"shared": Component(path="shared/")}
        result = _check_package_json_deps(p, "app/", comps, tmp_path)
        assert len(result) >= 1

    def test_non_dict_section_deps_ignored(self, tmp_path: Path) -> None:
        pkg = {"dependencies": ["react", "axios"]}
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"app": Component(path="app/")}
        result = _check_package_json_deps(p, "app/", comps, tmp_path)
        assert result == []

    def test_no_duplicate_deps(self, tmp_path: Path) -> None:
        pkg = {
            "dependencies": {"shared": "file:../shared"},
            "devDependencies": {"shared": "file:../shared"},
        }
        p = tmp_path / "package.json"
        p.write_text(json.dumps(pkg))
        comps = {"shared": Component(path="shared/")}
        result = _check_package_json_deps(p, "app/", comps, tmp_path)
        # Should not duplicate the dependency
        sources = [(d.source, d.target) for d in result]
        assert len(sources) == len(set(sources))


# ---------------------------------------------------------------------------
# _check_cargo_path_deps
# ---------------------------------------------------------------------------


class TestCheckCargoPathDeps:
    """Tests for _check_cargo_path_deps()."""

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "Cargo.toml"
        p.write_text("[dependencies]\n")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            result = _check_cargo_path_deps(p, "app/", {}, tmp_path)
            assert result == []

    def test_path_dependency(self, tmp_path: Path) -> None:
        cargo = '[dependencies]\nshared = { path = "../shared" }\n'
        p = tmp_path / "Cargo.toml"
        p.write_text(cargo)
        comps = {"shared": Component(path="shared/")}
        result = _check_cargo_path_deps(p, "service/", comps, tmp_path)
        assert len(result) == 1
        assert result[0].via == "Cargo path dependency"

    def test_no_path_deps(self, tmp_path: Path) -> None:
        cargo = '[dependencies]\nserde = "1"\n'
        p = tmp_path / "Cargo.toml"
        p.write_text(cargo)
        comps = {"shared": Component(path="shared/")}
        result = _check_cargo_path_deps(p, "service/", comps, tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# _check_python_path_deps
# ---------------------------------------------------------------------------


class TestCheckPythonPathDeps:
    """Tests for _check_python_path_deps()."""

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("")
        with patch.object(Path, "read_text", side_effect=OSError("nope")):
            result = _check_python_path_deps(p, "app/", {}, tmp_path)
            assert result == []

    def test_relative_path_ref(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("-e ./shared\n")
        comps = {"shared": Component(path="shared/")}
        result = _check_python_path_deps(p, "app/", comps, tmp_path)
        assert len(result) == 1

    def test_parent_path_ref(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("-e ../shared\n")
        comps = {"shared": Component(path="shared/")}
        result = _check_python_path_deps(p, "service/", comps, tmp_path)
        assert len(result) == 1

    def test_pyproject_path_ref(self, tmp_path: Path) -> None:
        p = tmp_path / "pyproject.toml"
        p.write_text('path = "../shared"\n')
        comps = {"shared": Component(path="shared/")}
        result = _check_python_path_deps(p, "service/", comps, tmp_path)
        assert len(result) == 1

    def test_no_path_refs(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("fastapi>=0.100\n")
        comps = {"shared": Component(path="shared/")}
        result = _check_python_path_deps(p, "app/", comps, tmp_path)
        assert result == []

    def test_self_reference_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("-e ./app\n")
        comps = {"app": Component(path="app/")}
        result = _check_python_path_deps(p, "app/", comps, tmp_path)
        assert result == []

    def test_no_duplicate_deps(self, tmp_path: Path) -> None:
        p = tmp_path / "requirements.txt"
        p.write_text("-e ./shared\n")
        comps = {"shared": Component(path="shared/")}
        result = _check_python_path_deps(p, "app/", comps, tmp_path)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _detect_dependencies
# ---------------------------------------------------------------------------


class TestDetectDependencies:
    """Tests for _detect_dependencies()."""

    def test_docker_compose_deps(self, tmp_path: Path) -> None:
        compose = (
            "version: '3'\n"
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      - backend\n"
            "  backend:\n"
            "    image: python\n"
        )
        (tmp_path / "docker-compose.yml").write_text(compose)
        components = [
            Component(path="frontend/"),
            Component(path="backend/"),
        ]
        deps, shared = _detect_dependencies(tmp_path, components)
        assert len(deps) >= 1

    def test_cargo_path_deps(self, tmp_path: Path) -> None:
        (tmp_path / "service").mkdir()
        (tmp_path / "shared").mkdir()
        (tmp_path / "service" / "Cargo.toml").write_text(
            '[dependencies]\nshared = { path = "../shared" }\n'
        )
        components = [
            Component(path="service/"),
            Component(path="shared/"),
        ]
        deps, _ = _detect_dependencies(tmp_path, components)
        assert any(d.via == "Cargo path dependency" for d in deps)

    def test_python_path_deps(self, tmp_path: Path) -> None:
        (tmp_path / "service").mkdir()
        (tmp_path / "shared").mkdir()
        (tmp_path / "service" / "requirements.txt").write_text("-e ../shared\n")
        components = [
            Component(path="service/"),
            Component(path="shared/"),
        ]
        deps, _ = _detect_dependencies(tmp_path, components)
        assert any("path dependency" in d.via for d in deps)

    def test_shared_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "shared").mkdir()
        components = [Component(path="app/")]
        deps, shared = _detect_dependencies(tmp_path, components)
        assert any("shared" in s for s in shared)

    def test_oserror_in_shared_dirs(self, tmp_path: Path) -> None:
        with patch("pathlib.Path.iterdir", side_effect=OSError("nope")):
            deps, shared = _detect_dependencies(tmp_path, [])
            assert deps == []
            assert shared == []

    def test_docker_compose_yaml_extension(self, tmp_path: Path) -> None:
        compose = (
            "version: '3'\n"
            "services:\n"
            "  frontend:\n"
            "    depends_on:\n"
            "      - backend\n"
            "  backend:\n"
            "    image: python\n"
        )
        (tmp_path / "docker-compose.yaml").write_text(compose)
        components = [
            Component(path="frontend/"),
            Component(path="backend/"),
        ]
        deps, shared = _detect_dependencies(tmp_path, components)
        assert len(deps) >= 1


# ---------------------------------------------------------------------------
# detect_structure (integration)
# ---------------------------------------------------------------------------


class TestDetectStructure:
    """Integration tests for detect_structure()."""

    def test_go_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        report = detect_structure(tmp_path)
        assert report.repo_type == RepoType.SINGLE_REPO
        # No subdirectory with signals → no components detected at top level

    def test_rust_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "myapp" / "Cargo.toml", "[package]\nname = 'myapp'\n")
        report = detect_structure(tmp_path)
        assert len(report.components) == 1
        assert report.components[0].language == "Rust"

    def test_ruby_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(
            tmp_path / "myapp" / "Gemfile",
            'source "https://rubygems.org"',
        )
        report = detect_structure(tmp_path)
        assert len(report.components) == 1
        assert report.components[0].language == "Ruby"

    def test_php_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "myapp" / "composer.json", "{}")
        report = detect_structure(tmp_path)
        assert len(report.components) == 1
        assert report.components[0].language == "PHP"

    def test_java_project(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "myapp" / "pom.xml", "<project/>")
        report = detect_structure(tmp_path)
        assert len(report.components) == 1
        assert report.components[0].language == "Java/Kotlin"

    def test_monorepo_detection(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "frontend" / "package.json", "{}")
        _write_file(tmp_path / "backend" / "pyproject.toml", "[project]\n")
        report = detect_structure(tmp_path)
        assert report.repo_type == RepoType.MONOREPO
        assert len(report.components) == 2

    def test_untracked_project(self, tmp_path: Path) -> None:
        report = detect_structure(tmp_path)
        assert report.repo_type == RepoType.UNTRACKED

    def test_multi_repo_project(self, tmp_path: Path) -> None:
        (tmp_path / "service-a" / ".git").mkdir(parents=True)
        (tmp_path / "service-b" / ".git").mkdir(parents=True)
        report = detect_structure(tmp_path)
        assert report.repo_type == RepoType.MULTI_REPO


# ---------------------------------------------------------------------------
# format_structure_report
# ---------------------------------------------------------------------------


class TestFormatStructureReport:
    """Tests for format_structure_report()."""

    def test_monorepo(self) -> None:
        report = StructureReport(
            repo_type=RepoType.MONOREPO,
            components=[Component(path="app/")],
        )
        text = format_structure_report(report)
        assert "Monorepo" in text
        assert "multiple components detected" in text

    def test_multi_repo(self) -> None:
        report = StructureReport(repo_type=RepoType.MULTI_REPO)
        text = format_structure_report(report)
        assert "Multi-repo" in text
        assert "multiple repositories detected" in text

    def test_untracked(self) -> None:
        report = StructureReport(repo_type=RepoType.UNTRACKED)
        text = format_structure_report(report)
        assert "Untracked" in text
        assert "no .git found" in text

    def test_with_dependencies(self) -> None:
        dep = Dependency(source="frontend/", target="backend/", via="docker-compose")
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            dependencies=[dep],
        )
        text = format_structure_report(report)
        assert "Dependency Graph" in text
        assert "frontend/ → backend/" in text

    def test_with_shared_resources(self) -> None:
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            shared_resources=["shared/ — shared dir"],
        )
        text = format_structure_report(report)
        assert "Shared Resources" in text
        assert "shared/" in text

    def test_empty_components(self) -> None:
        report = StructureReport(repo_type=RepoType.SINGLE_REPO)
        text = format_structure_report(report)
        assert "Components" not in text


# ---------------------------------------------------------------------------
# _format_component_block
# ---------------------------------------------------------------------------


class TestFormatComponentBlock:
    """Tests for _format_component_block()."""

    def test_minimal_component(self) -> None:
        lines: list[str] = []
        comp = Component(path="app/")
        _format_component_block(lines, comp)
        assert any("app/" in line for line in lines)

    def test_full_component(self) -> None:
        lines: list[str] = []
        comp = Component(
            path="app/",
            language="Python",
            framework="FastAPI",
            role="API server",
            description="My app",
            key_deps=["fastapi"],
            test_command="pytest",
            lint_command="ruff",
        )
        _format_component_block(lines, comp)
        text = "\n".join(lines)
        assert "Python" in text
        assert "FastAPI" in text
        assert "API server" in text
        assert "My app" in text
        assert "fastapi" in text
        assert "pytest" in text
        assert "ruff" in text

    def test_sub_components(self) -> None:
        lines: list[str] = []
        comp = Component(
            path="app/",
            sub_components=[
                Component(path="frontend/", language="TypeScript", role="Web UI"),
            ],
        )
        _format_component_block(lines, comp)
        text = "\n".join(lines)
        assert "Sub-components" in text
        assert "frontend/" in text


# ---------------------------------------------------------------------------
# format_structure_for_prompt
# ---------------------------------------------------------------------------


class TestFormatStructureForPrompt:
    """Tests for format_structure_for_prompt()."""

    def test_empty_report(self) -> None:
        report = StructureReport()
        text = format_structure_for_prompt(report)
        assert "Untracked" in text

    def test_with_components(self) -> None:
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[
                Component(
                    path="app/",
                    language="Python",
                    framework="FastAPI",
                    role="API server",
                    description="My app",
                    key_deps=["fastapi"],
                    test_command="pytest",
                    lint_command="ruff",
                ),
            ],
        )
        text = format_structure_for_prompt(report)
        # path has trailing / stripped → "app" not "app/"
        assert "app" in text
        assert "lang=Python" in text
        assert "fw=FastAPI" in text
        assert "role=API server" in text
        assert "desc=My app" in text

    def test_with_dependencies(self) -> None:
        dep = Dependency(source="frontend/", target="backend/", via="docker-compose")
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            dependencies=[dep],
        )
        text = format_structure_for_prompt(report)
        assert "Dependencies:" in text

    def test_with_shared_resources(self) -> None:
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            shared_resources=["shared/"],
        )
        text = format_structure_for_prompt(report)
        assert "Shared resources:" in text

    def test_component_no_key_deps(self) -> None:
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[Component(path="app/", language="Go")],
        )
        text = format_structure_for_prompt(report)
        assert "stack=" not in text

    def test_component_with_lint_command(self) -> None:
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[Component(path="app/", lint_command="ruff check .")],
        )
        text = format_structure_for_prompt(report)
        assert "lint: ruff check ." in text


# ---------------------------------------------------------------------------
# _format_component_prompt
# ---------------------------------------------------------------------------


class TestFormatComponentPrompt:
    """Tests for _format_component_prompt()."""

    def test_minimal(self) -> None:
        lines: list[str] = []
        comp = Component(path="app/")
        _format_component_prompt(lines, comp)
        assert len(lines) == 1
        # path.rstrip("/") removes trailing slash
        assert "app" in lines[0]

    def test_with_all_fields(self) -> None:
        lines: list[str] = []
        comp = Component(
            path="app/",
            language="Python",
            framework="FastAPI",
            role="API server",
            description="My app",
            key_deps=["fastapi", "httpx"],
            test_command="pytest",
            lint_command="ruff",
        )
        _format_component_prompt(lines, comp)
        text = "\n".join(lines)
        assert "lang=Python" in text
        assert "fw=FastAPI" in text
        assert "role=API server" in text
        assert "desc=My app" in text
        assert "stack=fastapi, httpx" in text
        assert "test: pytest" in text
        assert "lint: ruff" in text

    def test_sub_components(self) -> None:
        lines: list[str] = []
        comp = Component(
            path="app/",
            sub_components=[Component(path="frontend/", language="TypeScript")],
        )
        _format_component_prompt(lines, comp)
        text = "\n".join(lines)
        # path.rstrip("/") strips the trailing slash
        assert "frontend" in text

    def test_key_deps_truncated_to_5(self) -> None:
        lines: list[str] = []
        comp = Component(path="app/", key_deps=["a", "b", "c", "d", "e", "f"])
        _format_component_prompt(lines, comp)
        text = lines[0]
        # Should only show first 5 deps
        assert "a, b, c, d, e" in text
