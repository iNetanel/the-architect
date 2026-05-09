"""Project structure detection for The Architect.

Scans a project directory to determine repo type, detect components,
identify languages and frameworks, build a dependency graph, and
infer component roles.  The result is written into ARCHITECT.md's
structure section and injected into the architect agent's planning prompt.

Detection always runs fresh on every ``architect --plan`` — never cached.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Repo type classification
# ---------------------------------------------------------------------------


class RepoType(StrEnum):
    """Classification of the project's repository structure."""

    MULTI_REPO = "Multi-repo"
    MONOREPO = "Monorepo"
    SINGLE_REPO = "Single repo"
    UNTRACKED = "Untracked"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class Component:
    """A distinct component detected within the project.

    Attributes:
        path: Relative path from project root (e.g. "frontend/").
        language: Primary language (e.g. "TypeScript", "Python", "Rust").
        framework: Detected framework (e.g. "Next.js", "FastAPI"), or empty string.
        role: Inferred role (e.g. "Web UI", "API server"), or empty string.
        description: Project description from pyproject.toml or package.json.
        key_deps: Most important dependencies (top 8), for quick stack overview.
        test_command: How to run tests (e.g. "pytest tests/ -v"), or empty string.
        lint_command: How to lint (e.g. "ruff check ."), or empty string.
        sub_components: Nested components (e.g. backend/ and frontend/ inside app/).
        signals: List of file signals that identified this component.
    """

    def __init__(
        self,
        path: str,
        language: str = "",
        framework: str = "",
        role: str = "",
        description: str = "",
        key_deps: list[str] | None = None,
        test_command: str = "",
        lint_command: str = "",
        sub_components: list[Component] | None = None,
        signals: list[str] | None = None,
    ) -> None:
        self.path = path
        self.language = language
        self.framework = framework
        self.role = role
        self.description = description
        self.key_deps = key_deps or []
        self.test_command = test_command
        self.lint_command = lint_command
        self.sub_components = sub_components or []
        self.signals = signals or []

    def to_dict(self) -> dict[str, str]:
        """Return a dict representation for serialisation.

        Returns:
            Dictionary with component metadata.
        """
        return {
            "path": self.path,
            "language": self.language or "—",
            "framework": self.framework or "—",
            "role": self.role or "—",
        }


class Dependency:
    """A directed relationship between two components.

    Attributes:
        source: Component path (e.g. "frontend/").
        target: Component path (e.g. "backend/").
        via: Detection source description (e.g. "docker-compose depends_on").
    """

    def __init__(self, source: str, target: str, via: str) -> None:
        self.source = source
        self.target = target
        self.via = via

    def __str__(self) -> str:
        return f"{self.source} → {self.target}  (via: {self.via})"


class StructureReport:
    """Complete project structure detection result.

    Attributes:
        repo_type: The classified repo type.
        components: List of detected components.
        dependencies: List of detected inter-component dependencies.
        shared_resources: List of shared directories or files.
        detected_at: ISO timestamp of when detection ran.
    """

    def __init__(
        self,
        repo_type: RepoType = RepoType.UNTRACKED,
        components: list[Component] | None = None,
        dependencies: list[Dependency] | None = None,
        shared_resources: list[str] | None = None,
        detected_at: str = "",
    ) -> None:
        self.repo_type = repo_type
        self.components = components or []
        self.dependencies = dependencies or []
        self.shared_resources = shared_resources or []
        self.detected_at = detected_at or datetime.now(tz=UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Component signal definitions — data-driven, easy to extend
# ---------------------------------------------------------------------------

# Maps signal filenames to the language they indicate
_LANGUAGE_SIGNALS: dict[str, str] = {
    "package.json": "JavaScript/TypeScript",
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java/Kotlin",
    "build.gradle": "Java/Kotlin",
    "composer.json": "PHP",
    "Gemfile": "Ruby",
}

_COMPONENT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".architect",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "tests",
    }
)

# File extensions for C# project detection
_CSProj_EXTENSIONS = {".csproj", ".vbproj", ".fsproj"}

# Framework detection rules — each entry maps a check function to a framework name
# Organised by language family


def _check_js_framework(component_dir: Path) -> str:
    """Detect JavaScript/TypeScript framework from package.json and config files.

    Args:
        component_dir: The component directory to check.

    Returns:
        Detected framework name, or empty string.
    """
    # Config-file-based detection (most reliable)
    config_frameworks: list[tuple[str, str]] = [
        ("next.config.js", "Next.js"),
        ("next.config.ts", "Next.js"),
        ("next.config.mjs", "Next.js"),
        ("nuxt.config.js", "Nuxt.js"),
        ("nuxt.config.ts", "Nuxt.js"),
        ("vite.config.js", "Vite"),
        ("vite.config.ts", "Vite"),
    ]
    for filename, framework in config_frameworks:
        if (component_dir / filename).exists():
            return framework

    # package.json dependency-based detection
    pkg_path = component_dir / "package.json"
    if not pkg_path.exists():
        return ""

    deps = _read_package_json_deps(pkg_path)
    dep_frameworks: list[tuple[str, str]] = [
        ("react-native", "React Native"),
        ("@angular/core", "Angular"),
        ("next", "Next.js"),
        ("nuxt", "Nuxt.js"),
        ("vue", "Vue"),
        ("svelte", "Svelte"),
        ("express", "Express"),
        ("fastify", "Fastify"),
        ("react", "React"),
    ]
    for dep_name, framework in dep_frameworks:
        if dep_name in deps:
            return framework

    return ""


def _check_python_framework(component_dir: Path) -> str:
    """Detect Python framework from pyproject.toml or requirements.txt.

    Args:
        component_dir: The component directory to check.

    Returns:
        Detected framework name, or empty string.
    """
    deps = set()

    # Read from pyproject.toml
    pyproject = component_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            deps.update(_extract_deps_from_text(content))
            # Also check for inline dependency lists: dependencies = ["fastapi", ...]
            import re

            for m in re.finditer(r"dependencies\s*=\s*\[([^\]]+)\]", content):
                dep_list = m.group(1)
                for dep_match in re.finditer(r"['\"]([^'\"]+)['\"]", dep_list):
                    dep_name = (
                        dep_match.group(1)
                        .split(">=")[0]
                        .split("<=")[0]
                        .split("==")[0]
                        .split(">")[0]
                        .split("<")[0]
                        .strip()
                    )
                    if dep_name:
                        deps.add(dep_name)
        except OSError:
            pass

    # Read from requirements.txt
    requirements = component_dir / "requirements.txt"
    if requirements.exists():
        try:
            content = requirements.read_text(encoding="utf-8").lower()
            deps.update(_extract_deps_from_text(content))
        except OSError:
            pass

    python_frameworks: list[tuple[str, str]] = [
        ("fastapi", "FastAPI"),
        ("django", "Django"),
        ("flask", "Flask"),
    ]
    for dep_name, framework in python_frameworks:
        if dep_name in deps:
            return framework

    return ""


def _check_rust_framework(component_dir: Path) -> str:
    """Detect Rust framework from Cargo.toml.

    Args:
        component_dir: The component directory to check.

    Returns:
        Detected framework name, or empty string.
    """
    cargo = component_dir / "Cargo.toml"
    if not cargo.exists():
        return ""

    try:
        content = cargo.read_text(encoding="utf-8").lower()
    except OSError:
        return ""

    rust_frameworks: list[tuple[str, str]] = [
        ("axum", "Axum"),
        ("actix-web", "Actix"),
    ]
    for dep_name, framework in rust_frameworks:
        if dep_name in content:
            return framework

    # Note: tokio as async runtime
    if "tokio" in content:
        return "async runtime (tokio)"

    return ""


def _check_go_framework(component_dir: Path) -> str:
    """Detect Go framework from go.mod.

    Args:
        component_dir: The component directory to check.

    Returns:
        Detected framework name, or empty string.
    """
    go_mod = component_dir / "go.mod"
    if not go_mod.exists():
        return ""

    try:
        content = go_mod.read_text(encoding="utf-8")
    except OSError:
        return ""

    go_frameworks: list[tuple[str, str]] = [
        ("gin-gonic/gin", "Gin"),
        ("labstack/echo", "Echo"),
    ]
    for dep_name, framework in go_frameworks:
        if dep_name in content:
            return framework

    return ""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _read_package_json_deps(pkg_path: Path) -> set[str]:
    """Read dependency names from a package.json file.

    Args:
        pkg_path: Path to package.json.

    Returns:
        Set of dependency package names.
    """
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    deps: set[str] = set()
    for section in ("dependencies", "devDependencies"):
        section_deps = data.get(section, {})
        if isinstance(section_deps, dict):
            deps.update(section_deps.keys())
    return deps


def _extract_deps_from_text(content: str) -> set[str]:
    """Extract dependency-like names from text content.

    Looks for common patterns: ``name =`` in TOML, ``name==`` in requirements,
    and bare names in requirements files.

    Args:
        content: Lowercased text content to scan.

    Returns:
        Set of dependency name strings.
    """
    import re

    deps: set[str] = set()

    # TOML-style: name = "version" or name = {version = "..."}
    for m in re.finditer(r"([a-z0-9_-]+)\s*=", content):
        name = m.group(1)
        if name not in (
            "version",
            "description",
            "requires-python",
            "readme",
            "license",
            "name",
            "build-backend",
            "classifiers",
            "keywords",
            "authors",
            "urls",
            "homepage",
            "repository",
            "documentation",
        ):
            deps.add(name)

    # Requirements-style: name== or name>= or name<
    for m in re.finditer(r"^([a-z0-9_-]+)\s*[=><~!]", content, re.MULTILINE):
        deps.add(m.group(1))

    return deps


def _detect_csharp_project(subdir: Path) -> bool:
    """Check if a directory contains a C# project file.

    Args:
        subdir: The directory to check.

    Returns:
        True if a .csproj/.vbproj/.fsproj file exists in the directory.
    """
    try:
        for f in subdir.iterdir():
            if f.is_file() and f.suffix in _CSProj_EXTENSIONS:
                return True
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Project metadata enrichment (pyproject.toml, package.json)
# ---------------------------------------------------------------------------


# Dependencies that are not interesting for a stack overview
_SKIP_DEPS: frozenset[str] = frozenset(
    {
        # Python build/packaging
        "setuptools",
        "wheel",
        "pip",
        "build",
        "twine",
        "flit-core",
        "hatchling",
        "hatch-vcs",
        "setuptools-scm",
        # Python type stubs
        "types-",
        "mypy",
        "pyright",
        # Lint/format (shown in lint_command instead)
        "ruff",
        "black",
        "isort",
        "pylint",
        "flake8",
        # Test (shown in test_command instead)
        "pytest",
        "pytest-cov",
        "pytest-asyncio",
        "pytest-mock",
        "coverage",
        "nox",
        "tox",
    }
)


def _enrich_from_pyproject_toml(component_dir: Path, component: Component) -> None:
    """Enrich component metadata from pyproject.toml.

    Extracts description, key dependencies, and test/lint commands.

    Args:
        component_dir: The component directory containing pyproject.toml.
        component: The Component to enrich in place.
    """
    pyproject = component_dir / "pyproject.toml"
    if not pyproject.exists():
        return

    try:
        content = pyproject.read_text(encoding="utf-8")
    except OSError:
        return

    import re

    # Extract description
    desc_match = re.search(r'description\s*=\s*["\'](.+?)["\']', content)
    if desc_match:
        component.description = desc_match.group(1)

    # Extract dependencies list
    deps: list[str] = []
    for m in re.finditer(r"dependencies\s*=\s*\[([^\]]+)\]", content, re.DOTALL):
        dep_list = m.group(1)
        # Each dependency is a quoted string like "fastapi>=0.100.0"
        # or "tomli>=2.0.0; python_version < '3.11'"
        # Extract just the package name before any version/conditional
        for dep_match in re.finditer(r'["\']\s*([a-zA-Z][a-zA-Z0-9_.-]+)', dep_list):
            dep_name = dep_match.group(1).strip().lower()
            if dep_name and not any(dep_name.startswith(skip) for skip in _SKIP_DEPS):
                deps.append(dep_name)

    # Also check optional-dependency groups for key framework deps
    for m in re.finditer(r"\[project\.optional-dependencies\.\w+\]\s*\n((?:[^\[]\S.*)*)", content):
        group_content = m.group(1)
        for dep_match in re.finditer(r'["\']([^"\']+)["\']', group_content):
            dep_name = (
                dep_match.group(1)
                .split(">=")[0]
                .split("<=")[0]
                .split("==")[0]
                .split("~=")[0]
                .split(">")[0]
                .split("<")[0]
                .split("[")[0]
                .strip()
                .lower()
            )
            if dep_name and not any(dep_name.startswith(skip) for skip in _SKIP_DEPS):
                if dep_name not in deps:
                    deps.append(dep_name)

    # Keep top 8 meaningful deps (filter empty strings from conditional markers)
    component.key_deps = [d for d in deps if d][:8]

    # Detect test command from scripts or common patterns
    if "pytest" in content.lower():
        component.test_command = "pytest tests/ -v --tb=short"
    if "ruff" in content.lower():
        component.lint_command = "ruff check ."


def _enrich_from_package_json(component_dir: Path, component: Component) -> None:
    """Enrich component metadata from package.json.

    Extracts description, key dependencies, and test/lint commands.

    Args:
        component_dir: The component directory containing package.json.
        component: The Component to enrich in place.
    """
    pkg_path = component_dir / "package.json"
    if not pkg_path.exists():
        return

    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    # Description
    if isinstance(data.get("description"), str) and data["description"]:
        component.description = data["description"]

    # Key dependencies (skip dev/test tooling)
    skip_js = {
        "typescript",
        "eslint",
        "prettier",
        "jest",
        "vitest",
        "@types/",
        "tailwindcss",
        "postcss",
        "autoprefixer",
        "@next/eslint",
        "@typescript-eslint/",
        "eslint-config-",
        "eslint-plugin-",
    }
    deps: list[str] = []
    for section in ("dependencies", "devDependencies"):
        section_deps = data.get(section, {})
        if not isinstance(section_deps, dict):
            continue
        for name in section_deps:
            if not any(name.startswith(s) or name == s for s in skip_js):
                deps.append(name)

    component.key_deps = deps[:8]

    # Test command from scripts
    scripts = data.get("scripts", {})
    if isinstance(scripts, dict):
        if "test" in scripts:
            component.test_command = "npm test"
        if "lint" in scripts:
            component.lint_command = "npm run lint"


# ---------------------------------------------------------------------------
# Detection steps
# ---------------------------------------------------------------------------


def _detect_repo_type(project_dir: Path) -> RepoType:
    """Step 1: Determine the repo type.

    Scans immediate subdirectories for .git folders.

    Args:
        project_dir: The project root directory.

    Returns:
        The classified RepoType.
    """
    root_git = project_dir / ".git"
    subdirs_with_git = 0

    try:
        for entry in project_dir.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                if (entry / ".git").exists():
                    subdirs_with_git += 1
    except OSError:
        pass

    if subdirs_with_git >= 2:
        return RepoType.MULTI_REPO

    if not root_git.exists() and subdirs_with_git == 0:
        return RepoType.UNTRACKED

    return RepoType.SINGLE_REPO  # Will be refined to MONOREPO in step 2


def _detect_component_signals(component_dir: Path) -> tuple[list[str], str]:
    """Return signal files and primary language detected in a directory.

    Args:
        component_dir: Directory to inspect.

    Returns:
        Tuple of detected signal filenames and language name.
    """
    signals: list[str] = []
    language = ""

    for signal_file, lang in _LANGUAGE_SIGNALS.items():
        if (component_dir / signal_file).exists():
            signals.append(signal_file)
            language = lang

    if _detect_csharp_project(component_dir):
        signals.append("*.csproj")
        language = "C#"

    return signals, language


def _detect_root_python_package_components(project_dir: Path) -> list[Component]:
    """Detect import packages owned by a root-level Python project.

    A common single-repo shape has ``pyproject.toml`` at the root and the real
    source package in a sibling directory such as ``the_architect/`` or ``src/app/``.
    Without this pass, the root project is detected but the code location that
    agents need most remains hidden.

    Args:
        project_dir: Project root.

    Returns:
        Python package components detected from build config or package markers.
    """
    if not (project_dir / "pyproject.toml").exists():
        return []

    package_paths: list[Path] = []

    try:
        content = (project_dir / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        content = ""

    if content:
        import re

        # Hatchling commonly records packages as: packages = ["the_architect"]
        for match in re.finditer(r"packages\s*=\s*\[([^\]]+)\]", content, re.DOTALL):
            for quoted in re.finditer(r"[\"']([^\"']+)[\"']", match.group(1)):
                candidate = quoted.group(1).strip()
                if candidate:
                    package_paths.append(project_dir / candidate)

    # Fallback for setuptools/src-layout projects and simple packages.
    search_roots = [project_dir]
    src_dir = project_dir / "src"
    if src_dir.is_dir():
        search_roots.insert(0, src_dir)

    for search_root in search_roots:
        try:
            entries = sorted(search_root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if (
                entry.is_dir()
                and entry.name not in _COMPONENT_SKIP_DIRS
                and not entry.name.startswith(".")
                and not entry.name.startswith("architect_eval_")
                and (entry / "__init__.py").exists()
            ):
                package_paths.append(entry)

    components: list[Component] = []
    seen: set[str] = set()
    for package_dir in package_paths:
        try:
            if not package_dir.is_dir():
                continue
            rel = package_dir.relative_to(project_dir).as_posix() + "/"
        except ValueError:
            continue
        if rel in seen:
            continue
        seen.add(rel)

        role = "CLI package" if (package_dir / "cli.py").exists() else "Python package"
        components.append(
            Component(
                path=rel,
                language="Python",
                role=role,
                description="Python import package owned by the root pyproject.toml",
                signals=["__init__.py", "pyproject.toml package"],
            )
        )

    return components


def _detect_components(project_dir: Path, repo_type: RepoType) -> list[Component]:
    """Step 2: Detect distinct components.

    For MULTI_REPO, each subdirectory with .git is a component.
    For SINGLE_REPO/UNTRACKED, scan for component signal files.

    After detection, each component is enriched with metadata from
    pyproject.toml or package.json (description, key deps, test/lint
    commands).  Directories that have no signal files at the top level
    but contain sub-directories with signal files get sub-components
    instead (e.g. app/backend + app/frontend).

    Args:
        project_dir: The project root directory.
        repo_type: The classified repo type.

    Returns:
        List of detected Component objects.
    """
    components: list[Component] = []

    if repo_type == RepoType.MULTI_REPO:
        # Each subdirectory with .git is a component
        try:
            for entry in sorted(project_dir.iterdir()):
                if entry.name.startswith("architect_eval_"):
                    continue
                if entry.is_dir() and not entry.name.startswith(".") and (entry / ".git").exists():
                    comp = Component(path=entry.name + "/")
                    _enrich_component(entry, comp)
                    components.append(comp)
        except OSError:
            pass
        return components

    # SINGLE_REPO or UNTRACKED — scan root and then child component signals.
    # Root-level manifests are the dominant single-package layout for Python,
    # Rust, Go, and JavaScript projects; treating only subdirectories as
    # components hides the actual app in many real repositories.
    try:
        root_signals, root_language = _detect_component_signals(project_dir)
        if root_signals:
            root_component = Component(
                path="./",
                language=root_language,
                signals=root_signals,
            )
            _enrich_component(project_dir, root_component)
            components.append(root_component)

            if root_language == "Python":
                components.extend(_detect_root_python_package_components(project_dir))

        for entry in sorted(project_dir.iterdir()):
            if entry.name.startswith("architect_eval_"):
                continue
            if (
                not entry.is_dir()
                or entry.name.startswith(".")
                or entry.name in _COMPONENT_SKIP_DIRS
            ):
                continue

            signals, language = _detect_component_signals(entry)

            if signals:
                comp = Component(
                    path=entry.name + "/",
                    language=language,
                    signals=signals,
                )
                _enrich_component(entry, comp)
                components.append(comp)
            else:
                # No signal files at top level — check for sub-components
                sub_comps = _detect_sub_components(entry)
                if sub_comps:
                    comp = Component(
                        path=entry.name + "/",
                        sub_components=sub_comps,
                    )
                    _infer_role_from_subs(comp)
                    components.append(comp)
    except OSError:
        pass

    return components


def _enrich_component(component_dir: Path, component: Component) -> None:
    """Run all detection and enrichment steps for a single component.

    Detects language, framework, role, and enriches with metadata
    from pyproject.toml or package.json.

    Args:
        component_dir: The component directory.
        component: The Component to enrich in place.
    """
    _detect_language(component_dir, component)
    _detect_framework(component_dir, component)
    _infer_role(component)
    _enrich_from_pyproject_toml(component_dir, component)
    _enrich_from_package_json(component_dir, component)

    # Re-infer role if enrichment gave us new info
    if not component.role and (component.framework or component.description):
        _infer_role(component)

    # Check for sub-components (e.g. app/backend, app/frontend)
    sub_comps = _detect_sub_components(component_dir)
    if sub_comps:
        component.sub_components = sub_comps
        # If the parent has no role, infer from its children
        if not component.role:
            _infer_role_from_subs(component)


def _detect_sub_components(parent_dir: Path) -> list[Component]:
    """Detect sub-components one level deep inside a parent directory.

    Scans immediate subdirectories for language signal files.
    Used for directories like app/ that contain backend/ and frontend/.

    Args:
        parent_dir: The parent directory to scan inside.

    Returns:
        List of sub-Component objects, or empty list if none found.
    """
    sub_components: list[Component] = []
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", ".architect", ".pytest_cache"}

    try:
        for entry in sorted(parent_dir.iterdir()):
            if entry.name.startswith("architect_eval_"):
                continue
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in skip_dirs:
                continue

            signals: list[str] = []
            language = ""

            for signal_file, lang in _LANGUAGE_SIGNALS.items():
                if (entry / signal_file).exists():
                    signals.append(signal_file)
                    language = lang

            if _detect_csharp_project(entry):
                signals.append("*.csproj")
                language = "C#"

            if signals:
                comp = Component(
                    path=entry.name + "/",
                    language=language,
                    signals=signals,
                )
                _enrich_component(entry, comp)
                sub_components.append(comp)
    except OSError:
        pass

    return sub_components


def _infer_role_from_subs(component: Component) -> None:
    """Infer a parent component's role from its sub-components.

    If sub-components include both frontend and backend, the parent
    is a "Full-stack application". If only one type, use that.

    Args:
        component: The parent Component to update in place.
    """
    sub_roles = {sc.role for sc in component.sub_components if sc.role}
    if "Web UI" in sub_roles and "API server" in sub_roles:
        component.role = "Full-stack application"
    elif sub_roles:
        component.role = next(iter(sub_roles))
    else:
        # Fallback: use directory name
        name = component.path.rstrip("/").lower()
        name_roles: list[tuple[str, str]] = [
            ("app", "Application"),
            ("dev", "Development environment"),
            ("documentation", "Documentation"),
        ]
        for prefix, role in name_roles:
            if name == prefix or name.startswith(prefix + "-") or name.startswith(prefix + "_"):
                component.role = role
                return


def _detect_language(component_dir: Path, component: Component) -> None:
    """Step 3: Detect primary language for a component.

    Updates the component's language field in place.

    Args:
        component_dir: The component directory.
        component: The Component to update.
    """
    for signal_file, language in _LANGUAGE_SIGNALS.items():
        if (component_dir / signal_file).exists():
            component.language = language
            component.signals.append(signal_file)
            return

    if _detect_csharp_project(component_dir):
        component.language = "C#"
        component.signals.append("*.csproj")


def _detect_framework(component_dir: Path, component: Component) -> None:
    """Step 4: Detect framework for a component.

    Updates the component's framework field in place.

    Args:
        component_dir: The component directory.
        component: The Component to update.
    """
    lang = component.language

    if "JavaScript" in lang or "TypeScript" in lang:
        component.framework = _check_js_framework(component_dir)
    elif lang == "Python":
        component.framework = _check_python_framework(component_dir)
    elif lang == "Rust":
        component.framework = _check_rust_framework(component_dir)
    elif lang == "Go":
        component.framework = _check_go_framework(component_dir)


def _infer_role(component: Component) -> None:
    """Step 6: Infer a human-readable role for a component.

    Uses directory name and framework to infer the role.
    Updates the component's role field in place.

    Args:
        component: The Component to update.
    """
    name = component.path.rstrip("/").lower()
    framework = component.framework.lower()

    # Directory-name-based inference
    name_roles: list[tuple[str, str]] = [
        ("frontend", "Web UI"),
        ("web", "Web UI"),
        ("client", "Web UI"),
        ("mobile", "Mobile UI"),
        ("app", "Application"),
        ("backend", "API server"),
        ("api", "API server"),
        ("server", "API server"),
        ("engine", "Core library"),
        ("core", "Core library"),
        ("worker", "Background worker"),
        ("jobs", "Background worker"),
        ("packages", "Shared library"),
        ("shared", "Shared library"),
        ("common", "Shared library"),
        ("libs", "Shared library"),
        ("infra", "Infrastructure"),
        ("deploy", "Infrastructure"),
        ("terraform", "Infrastructure"),
    ]
    for prefix, role in name_roles:
        if name == prefix or name.startswith(prefix + "-") or name.startswith(prefix + "_"):
            component.role = role
            return

    # Framework-based inference
    framework_roles: list[tuple[str, str]] = [
        ("next.js", "Web UI"),
        ("nuxt.js", "Web UI"),
        ("react", "Web UI"),
        ("vue", "Web UI"),
        ("svelte", "Web UI"),
        ("angular", "Web UI"),
        ("react native", "Mobile UI"),
        ("fastapi", "API server"),
        ("django", "API server"),
        ("flask", "API server"),
        ("express", "API server"),
        ("fastify", "API server"),
        ("axum", "API server"),
        ("actix", "API server"),
        ("gin", "API server"),
        ("echo", "API server"),
    ]
    for fw_prefix, role in framework_roles:
        if framework.startswith(fw_prefix):
            component.role = role
            return


# ---------------------------------------------------------------------------
# Dependency graph detection (Step 5)
# ---------------------------------------------------------------------------


def _detect_dependencies(
    project_dir: Path,
    components: list[Component],
) -> tuple[list[Dependency], list[str]]:
    """Step 5: Detect inter-component dependencies.

    Checks docker-compose, package.json workspaces, Cargo path deps,
    and shared directories.

    Args:
        project_dir: The project root directory.
        components: List of detected components.

    Returns:
        Tuple of (dependencies, shared_resources).
    """
    dependencies: list[Dependency] = []
    shared_resources: list[str] = []
    component_paths = {c.path.rstrip("/"): c for c in components}

    # 1. Docker-compose dependency graph
    for filename in ("docker-compose.yml", "docker-compose.yaml"):
        compose_path = project_dir / filename
        if compose_path.exists():
            deps, shared = _parse_docker_compose(compose_path, component_paths)
            dependencies.extend(deps)
            if shared:
                shared_resources.append(f"{filename} — wires all components for local development")

    # 2. package.json workspace / dependency references
    for comp in components:
        comp_dir = project_dir / comp.path.rstrip("/")
        pkg_path = comp_dir / "package.json"
        if pkg_path.exists():
            deps = _check_package_json_deps(pkg_path, comp.path, component_paths, project_dir)
            dependencies.extend(deps)

    # 3. Cargo.toml path dependencies
    for comp in components:
        comp_dir = project_dir / comp.path.rstrip("/")
        cargo_path = comp_dir / "Cargo.toml"
        if cargo_path.exists():
            deps = _check_cargo_path_deps(cargo_path, comp.path, component_paths, project_dir)
            dependencies.extend(deps)

    # 4. pyproject.toml / requirements.txt local path references
    for comp in components:
        comp_dir = project_dir / comp.path.rstrip("/")
        for dep_file in ("pyproject.toml", "requirements.txt"):
            dep_path = comp_dir / dep_file
            if dep_path.exists():
                deps = _check_python_path_deps(dep_path, comp.path, component_paths, project_dir)
                dependencies.extend(deps)

    # 5. Shared directories at root
    shared_dir_names = {"packages", "shared", "common", "libs"}
    try:
        for entry in project_dir.iterdir():
            if entry.name.startswith("architect_eval_"):
                continue
            if entry.is_dir() and entry.name in shared_dir_names:
                shared_resources.append(
                    f"{entry.name}/ — shared directory referenced by multiple components"
                )
    except OSError:
        pass

    return dependencies, shared_resources


def _parse_docker_compose(
    compose_path: Path,
    component_paths: dict[str, Component],
) -> tuple[list[Dependency], bool]:
    """Parse docker-compose YAML for service dependencies.

    Args:
        compose_path: Path to docker-compose.yml.
        component_paths: Map of component name to Component.

    Returns:
        Tuple of (dependencies, has_shared_resources).
    """
    dependencies: list[Dependency] = []
    has_shared = False

    try:
        content = compose_path.read_text(encoding="utf-8")
    except OSError:
        return dependencies, False

    try:
        import yaml  # type: ignore[import-untyped]  # noqa: F811

        data = yaml.safe_load(content)
    except ImportError:
        # yaml not available — try a simple regex-based parse
        return _parse_docker_compose_regex(content, component_paths)
    except Exception:
        return dependencies, False

    if not isinstance(data, dict):
        return dependencies, False

    services = data.get("services", {})
    if not isinstance(services, dict):
        return dependencies, False

    for service_name, service_config in services.items():
        if not isinstance(service_config, dict):
            continue

        depends_on = service_config.get("depends_on", [])
        if isinstance(depends_on, dict):
            # New format: depends_on: {service: {condition: ...}}
            dep_services = list(depends_on.keys())
        elif isinstance(depends_on, list):
            dep_services = depends_on
        else:
            continue

        # Map service names to component paths
        source_path = _service_to_component(service_name, component_paths)
        if not source_path:
            has_shared = True
            continue

        for dep_service in dep_services:
            target_path = _service_to_component(dep_service, component_paths)
            if target_path:
                dependencies.append(
                    Dependency(
                        source=source_path,
                        target=target_path,
                        via=f"docker-compose depends_on ({compose_path.name})",
                    )
                )

    return dependencies, has_shared


def _parse_docker_compose_regex(
    content: str,
    component_paths: dict[str, Component],
) -> tuple[list[Dependency], bool]:
    """Fallback docker-compose parser using regex when PyYAML is unavailable.

    Parses the docker-compose YAML structure with regex to extract
    ``depends_on`` relationships between services.  Handles both list
    and dict forms of ``depends_on``.

    Strategy: scan the file line by line, tracking the current service
    name (lines at 2-space indent ending with ``:``) and accumulating
    ``depends_on`` targets (lines at 6-space indent starting with ``-``).

    Args:
        content: Raw docker-compose file content.
        component_paths: Map of component name → Component.

    Returns:
        Tuple of (dependencies, has_shared).
    """
    import re

    dependencies: list[Dependency] = []
    has_shared = False

    # Parse line-by-line: track service blocks and their depends_on lists.
    # docker-compose uses consistent 2-space indentation:
    #   services:          (0 indent)
    #     frontend:        (2 indent) ← service name
    #       depends_on:    (4 indent)
    #         - backend    (8 indent) ← dependency target
    #
    # We detect service names as lines with exactly 2 leading spaces that
    # end with ":" and contain no spaces in the name part.

    service_re = re.compile(r"^  (\S+):\s*$")
    depends_re = re.compile(r"^\s+depends_on:\s*$")
    dep_item_re = re.compile(r"^\s+-\s+(\S+)\s*$")
    dep_dict_key_re = re.compile(r"^\s{8,}(\S+):\s*$")  # dict-style depends_on key

    current_service: str = ""
    in_depends_on: bool = False
    # Track indent level of depends_on block to detect when we leave it
    depends_indent: int = 0

    for line in content.splitlines():
        # New top-level service (2-space indent)
        m = service_re.match(line)
        if m:
            current_service = m.group(1)
            in_depends_on = False
            continue

        # Start of depends_on block
        if depends_re.match(line):
            in_depends_on = True
            depends_indent = len(line) - len(line.lstrip())
            continue

        if in_depends_on:
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)

            # Left the depends_on block (back to same or lower indent)
            if stripped and current_indent <= depends_indent:
                in_depends_on = False
                # Don't continue — this line may be a new key
            else:
                # List item: "- service_name"
                item_m = dep_item_re.match(line)
                if item_m:
                    dep_service = item_m.group(1)
                    source_path = _service_to_component(current_service, component_paths)
                    target_path = _service_to_component(dep_service, component_paths)
                    if source_path and target_path:
                        dependencies.append(
                            Dependency(
                                source=source_path,
                                target=target_path,
                                via="docker-compose depends_on",
                            )
                        )
                    elif not source_path or not target_path:
                        has_shared = True
                    continue

                # Dict-style key: "    service_name:" (condition-based depends_on)
                dict_m = dep_dict_key_re.match(line)
                if dict_m:
                    dep_service = dict_m.group(1)
                    source_path = _service_to_component(current_service, component_paths)
                    target_path = _service_to_component(dep_service, component_paths)
                    if source_path and target_path:
                        dependencies.append(
                            Dependency(
                                source=source_path,
                                target=target_path,
                                via="docker-compose depends_on",
                            )
                        )
                    elif not source_path or not target_path:
                        has_shared = True
                    continue

    return dependencies, has_shared


def _service_to_component(
    service_name: str,
    component_paths: dict[str, Component],
) -> str:
    """Map a docker-compose service name to a component path.

    Args:
        service_name: The docker-compose service name.
        component_paths: Map of component name to Component.

    Returns:
        Component path string (e.g. "frontend/"), or empty string if no match.
    """
    # Direct match
    clean = service_name.lower().replace("-", "").replace("_", "")
    for comp_name in component_paths:
        if comp_name.lower().replace("-", "").replace("_", "") == clean:
            return comp_name + "/"

    # Prefix match
    for comp_name in component_paths:
        if clean.startswith(comp_name.lower().replace("-", "").replace("_", "")):
            return comp_name + "/"

    return ""


def _check_package_json_deps(
    pkg_path: Path,
    source_path: str,
    component_paths: dict[str, Component],
    project_dir: Path,
) -> list[Dependency]:
    """Check package.json for workspace or local path dependencies.

    Args:
        pkg_path: Path to package.json.
        source_path: The source component path.
        component_paths: Map of component name to Component.
        project_dir: The project root directory.

    Returns:
        List of detected Dependency objects.
    """
    dependencies: list[Dependency] = []

    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dependencies

    # Check workspaces
    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, list):
        for ws in workspaces:
            if isinstance(ws, str):
                # Workspace patterns like "packages/*" — resolve to components
                for comp_name in component_paths:
                    if ws.startswith("packages/") or comp_name in ws:
                        target = comp_name + "/"
                        if target != source_path:
                            dep = Dependency(
                                source=source_path,
                                target=target,
                                via="package.json workspace",
                            )
                            # Avoid duplicates
                            if not any(
                                d.source == dep.source and d.target == dep.target
                                for d in dependencies
                            ):
                                dependencies.append(dep)

    # Check dependencies for local path references
    for section in ("dependencies", "devDependencies"):
        section_deps = data.get(section, {})
        if not isinstance(section_deps, dict):
            continue
        for dep_name, dep_value in section_deps.items():
            if isinstance(dep_value, str) and dep_value.startswith(("file:", "link:")):
                # Local path reference
                for comp_name in component_paths:
                    if comp_name in dep_name.lower() or comp_name in dep_value.lower():
                        target = comp_name + "/"
                        if target != source_path:
                            dep = Dependency(
                                source=source_path,
                                target=target,
                                via=f"package.json {section} (local path)",
                            )
                            if not any(
                                d.source == dep.source and d.target == dep.target
                                for d in dependencies
                            ):
                                dependencies.append(dep)

    return dependencies


def _check_cargo_path_deps(
    cargo_path: Path,
    source_path: str,
    component_paths: dict[str, Component],
    project_dir: Path,
) -> list[Dependency]:
    """Check Cargo.toml for path dependencies pointing to other components.

    Args:
        cargo_path: Path to Cargo.toml.
        source_path: The source component path.
        component_paths: Map of component name to Component.
        project_dir: The project root directory.

    Returns:
        List of detected Dependency objects.
    """
    import re

    dependencies: list[Dependency] = []

    try:
        content = cargo_path.read_text(encoding="utf-8")
    except OSError:
        return dependencies

    # Look for path = "..." in [dependencies] section
    path_pattern = re.compile(r'path\s*=\s*"([^"]+)"')
    for match in path_pattern.finditer(content):
        ref_path = match.group(1)
        for comp_name in component_paths:
            if ref_path.startswith(comp_name) or ref_path.startswith("../" + comp_name):
                target = comp_name + "/"
                if target != source_path:
                    dep = Dependency(
                        source=source_path,
                        target=target,
                        via="Cargo path dependency",
                    )
                    if not any(
                        d.source == dep.source and d.target == dep.target for d in dependencies
                    ):
                        dependencies.append(dep)

    return dependencies


def _check_python_path_deps(
    dep_path: Path,
    source_path: str,
    component_paths: dict[str, Component],
    project_dir: Path,
) -> list[Dependency]:
    """Check pyproject.toml or requirements.txt for local path references.

    Args:
        dep_path: Path to pyproject.toml or requirements.txt.
        source_path: The source component path.
        component_paths: Map of component name to Component.
        project_dir: The project root directory.

    Returns:
        List of detected Dependency objects.
    """
    dependencies: list[Dependency] = []

    try:
        content = dep_path.read_text(encoding="utf-8")
    except OSError:
        return dependencies

    # Look for path references like -e ./other-component or path = "../other"
    import re

    for comp_name in component_paths:
        # Check for relative path references to this component
        patterns = [
            rf"\.\.?/{re.escape(comp_name)}",
            rf"path\s*=\s*[\"']\.\.?/{re.escape(comp_name)}",
            rf"-e\s+\.\.?/{re.escape(comp_name)}",
        ]
        for pattern in patterns:
            if re.search(pattern, content):
                target = comp_name + "/"
                if target != source_path:
                    dep = Dependency(
                        source=source_path,
                        target=target,
                        via=f"{dep_path.name} path dependency",
                    )
                    if not any(
                        d.source == dep.source and d.target == dep.target for d in dependencies
                    ):
                        dependencies.append(dep)
                break

    return dependencies


# ---------------------------------------------------------------------------
# Main detection entry point
# ---------------------------------------------------------------------------


def detect_structure(project_dir: Path) -> StructureReport:
    """Run the full project structure detection pipeline.

    Steps:
        1. Determine repo type
        2. Detect distinct components
        3. Detect language per component
        4. Detect framework per component
        5. Build dependency graph
        6. Infer component roles

    Args:
        project_dir: The project root directory.

    Returns:
        A StructureReport with all detection results.
    """
    # Step 1
    repo_type = _detect_repo_type(project_dir)

    # Step 2 — also does steps 3, 4, and 6 for each component
    components = _detect_components(project_dir, repo_type)

    # Refine to MONOREPO only when there are 2+ implementation components.
    # Root-level package manifests and import package directories are common in
    # single-package repos and should not make a repo look like a monorepo.
    monorepo_components = [
        component
        for component in components
        if component.path != "./"
        and component.role != "Development environment"
        and "pyproject.toml package" not in component.signals
    ]
    if repo_type == RepoType.SINGLE_REPO and len(monorepo_components) >= 2:
        repo_type = RepoType.MONOREPO

    # Step 5
    dependencies, shared_resources = _detect_dependencies(project_dir, components)

    report = StructureReport(
        repo_type=repo_type,
        components=components,
        dependencies=dependencies,
        shared_resources=shared_resources,
    )

    logger.debug(
        f"Structure detection: {repo_type.value}, {len(components)} component(s), "
        f"{len(dependencies)} dependencies"
    )

    return report


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_structure_report(report: StructureReport) -> str:
    """Format a StructureReport as a markdown string for ARCHITECT.md.

    Produces a rich component block for each component with description,
    stack, test/lint commands, and sub-components — everything an agent
    needs to understand the project at a glance.

    Args:
        report: The structure report to format.

    Returns:
        Markdown string for the Project Structure section.
    """
    lines: list[str] = []

    lines.append(f"**Type:** {report.repo_type.value}")
    if report.repo_type == RepoType.MONOREPO:
        lines.append("**Detected:** multiple components detected")
    elif report.repo_type == RepoType.MULTI_REPO:
        lines.append("**Detected:** multiple repositories detected")
    elif report.repo_type == RepoType.UNTRACKED:
        lines.append("**Detected:** no .git found")
    lines.append(f"**Scanned:** {report.detected_at}")
    lines.append("")

    # Components — rich blocks instead of a flat table
    if report.components:
        lines.append("### Components")
        lines.append("")
        for comp in report.components:
            _format_component_block(lines, comp)

    # Dependency graph
    if report.dependencies:
        lines.append("### Dependency Graph")
        lines.append("")
        for dep in report.dependencies:
            lines.append(f"- {dep}")
        lines.append("")

    # Shared resources
    if report.shared_resources:
        lines.append("### Shared Resources")
        lines.append("")
        for res in report.shared_resources:
            lines.append(f"- {res}")
        lines.append("")

    return "\n".join(lines)


def _format_component_block(lines: list[str], comp: Component, indent: str = "") -> None:
    """Format a single component as a rich markdown block.

    Args:
        lines: The output lines list to append to.
        comp: The component to format.
        indent: Indentation prefix for sub-components.
    """
    name = comp.path.rstrip("/")
    parts: list[str] = []
    if comp.language:
        parts.append(comp.language)
    if comp.framework:
        parts.append(comp.framework)
    if comp.role:
        parts.append(comp.role)

    # Header line: `the_architect/` — Python · CLI/TUI tool
    header = f"{indent}**{name}/**"
    if parts:
        header += f" — {' · '.join(parts)}"
    lines.append(header)

    # Description
    if comp.description:
        lines.append(f"{indent}> {comp.description}")

    # Key dependencies
    if comp.key_deps:
        deps_str = ", ".join(comp.key_deps)
        lines.append(f"{indent}> Stack: {deps_str}")

    # Test and lint commands
    commands: list[str] = []
    if comp.test_command:
        commands.append(f"test: `{comp.test_command}`")
    if comp.lint_command:
        commands.append(f"lint: `{comp.lint_command}`")
    if commands:
        lines.append(f"{indent}> {' | '.join(commands)}")

    # Sub-components
    if comp.sub_components:
        lines.append(f"{indent}>")
        lines.append(f"{indent}> Sub-components:")
        for sub in comp.sub_components:
            _format_component_block(lines, sub, indent=indent + "> ")

    lines.append("")


def format_structure_for_prompt(report: StructureReport) -> str:
    """Format a StructureReport as a concise string for the architect prompt.

    More compact than the ARCHITECT.md version — optimised for injection
    into the planning instruction.

    Args:
        report: The structure report to format.

    Returns:
        Concise string for prompt injection.
    """
    lines: list[str] = []
    lines.append(f"Repo type: {report.repo_type.value}")

    if report.components:
        lines.append("Components:")
        for comp in report.components:
            _format_component_prompt(lines, comp, indent="  ")

    if report.dependencies:
        lines.append("Dependencies:")
        for dep in report.dependencies:
            lines.append(f"  - {dep}")

    if report.shared_resources:
        lines.append("Shared resources:")
        for res in report.shared_resources:
            lines.append(f"  - {res}")

    return "\n".join(lines)


def _format_component_prompt(lines: list[str], comp: Component, indent: str = "  ") -> None:
    """Format a single component for the concise prompt version.

    Args:
        lines: The output lines list to append to.
        comp: The component to format.
        indent: Indentation prefix.
    """
    parts = [comp.path.rstrip("/")]
    if comp.language:
        parts.append(f"lang={comp.language}")
    if comp.framework:
        parts.append(f"fw={comp.framework}")
    if comp.role:
        parts.append(f"role={comp.role}")
    if comp.description:
        parts.append(f"desc={comp.description}")
    if comp.key_deps:
        parts.append(f"stack={', '.join(comp.key_deps[:5])}")
    lines.append(f"{indent}- {' | '.join(parts)}")

    if comp.test_command:
        lines.append(f"{indent}  test: {comp.test_command}")
    if comp.lint_command:
        lines.append(f"{indent}  lint: {comp.lint_command}")

    for sub in comp.sub_components:
        _format_component_prompt(lines, sub, indent=indent + "  ")
