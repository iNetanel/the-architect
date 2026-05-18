"""Tests for the goal template storage module and CLI command.

Covers:
- GoalTemplate Pydantic model validation
- list_templates() — empty, multiple, corrupted entries, missing file
- show_template() — found, not found, case sensitivity
- create_template() — create, duplicate raises ValueError, auto-extract variables
- delete_template() — found, not found, preserves others
- extract_variables() — simple, multiple, no variables, duplicates, special chars
- substitute_variables() — all, partial, none, extra, empty values
- architect template create — basic, config overrides, duplicate error
- architect template list — empty, with templates, --json output
- architect template show — found, not found, --json output
- architect template delete — found, not found
- architect template run — variable substitution, --headless, missing vars
- Edge cases: corrupted JSON, non-list JSON, empty file, OS errors
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.templates import (
    _TEMPLATES_FILE,
    GoalTemplate,
    create_template,
    delete_template,
    extract_variables,
    list_templates,
    show_template,
    substitute_variables,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _write_raw_templates(project: Path, data: object) -> None:
    """Write raw template data directly to disk."""
    templates_path = project / _TEMPLATES_FILE
    templates_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        templates_path.write_text(data, encoding="utf-8")
    else:
        templates_path.write_text(json.dumps(data), encoding="utf-8")


def _make_template_dict(
    name: str = "bugfix",
    goal_text: str = "Fix {issue} in {module}",
    description: str = "Bug fix template",
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a valid template dict for test data."""
    return {
        "name": name,
        "goal_text": goal_text,
        "description": description,
        "config_overrides": overrides or {},
        "variables": ["issue", "module"],
        "created_at": "2026-05-18T10:00:00+00:00",
        "updated_at": "2026-05-18T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# GoalTemplate model
# ---------------------------------------------------------------------------


class TestGoalTemplateModel:
    """Tests for the GoalTemplate Pydantic model."""

    def test_create_full(self) -> None:
        t = GoalTemplate(
            name="test",
            goal_text="Fix {bug}",
            description="A test template",
            config_overrides={"max_retries": 3},
            variables=["bug"],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert t.name == "test"
        assert t.goal_text == "Fix {bug}"
        assert t.description == "A test template"
        assert t.config_overrides == {"max_retries": 3}
        assert t.variables == ["bug"]

    def test_empty_overrides_default(self) -> None:
        t = GoalTemplate(
            name="empty",
            goal_text="no vars",
            description="no overrides",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert t.config_overrides == {}
        assert t.variables == []

    def test_model_roundtrip(self) -> None:
        original = GoalTemplate(
            name="round",
            goal_text="Add {feature}",
            description="trip test",
            config_overrides={"integrity": False, "max_retries": 7},
            variables=["feature"],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )
        restored = GoalTemplate.model_validate(original.model_dump())
        assert restored.name == original.name
        assert restored.config_overrides == original.config_overrides
        assert restored.variables == original.variables
        assert restored.created_at == original.created_at

    def test_model_dump_contains_all_fields(self) -> None:
        t = GoalTemplate(
            name="dump",
            goal_text="goal",
            description="desc",
            config_overrides={"key": "val"},
            variables=["x"],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        dump = t.model_dump()
        assert dump["name"] == "dump"
        assert dump["goal_text"] == "goal"
        assert dump["variables"] == ["x"]
        assert dump["config_overrides"] == {"key": "val"}


# ---------------------------------------------------------------------------
# list_templates()
# ---------------------------------------------------------------------------


class TestListTemplates:
    """Tests for list_templates()."""

    def test_empty_when_file_missing(self, tmp_path: Path) -> None:
        assert list_templates(tmp_path) == []

    def test_empty_when_architect_dir_missing(self, tmp_path: Path) -> None:
        assert list_templates(tmp_path) == []

    def test_returns_templates_when_file_exists(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, [_make_template_dict("bugfix")])
        result = list_templates(tmp_path)
        assert len(result) == 1
        assert result[0].name == "bugfix"
        assert result[0].description == "Bug fix template"

    def test_returns_multiple_templates(self, tmp_path: Path) -> None:
        _write_raw_templates(
            tmp_path,
            [
                _make_template_dict("bugfix", description="Fix bugs"),
                _make_template_dict("feature", goal_text="Add {feat}", description="Add features"),
            ],
        )
        result = list_templates(tmp_path)
        assert len(result) == 2
        names = [t.name for t in result]
        assert "bugfix" in names
        assert "feature" in names

    def test_skips_corrupted_entries(self, tmp_path: Path) -> None:
        """Corrupted entries are skipped, valid entries are returned."""
        _write_raw_templates(
            tmp_path,
            [
                _make_template_dict("good"),
                {"name": "bad"},  # missing required fields
                _make_template_dict("also_good"),
            ],
        )
        result = list_templates(tmp_path)
        assert len(result) == 2
        assert all(t.name in ("good", "also_good") for t in result)

    def test_returns_empty_on_invalid_json(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, "not valid json {{{")
        assert list_templates(tmp_path) == []

    def test_returns_empty_on_corrupted_data(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, "CORRUPTED")
        assert list_templates(tmp_path) == []

    def test_returns_empty_on_empty_file(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, "")
        assert list_templates(tmp_path) == []

    def test_returns_empty_on_non_list_json(self, tmp_path: Path) -> None:
        """A JSON object (not array) is treated as empty."""
        _write_raw_templates(tmp_path, {"templates": []})
        assert list_templates(tmp_path) == []

    def test_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        templates_path = tmp_path / _TEMPLATES_FILE
        templates_path.parent.mkdir(parents=True, exist_ok=True)
        templates_path.write_text("[]", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = list_templates(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# create_template()
# ---------------------------------------------------------------------------


class TestCreateTemplate:
    """Tests for create_template()."""

    def test_creates_new_template(self, tmp_path: Path) -> None:
        t = create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        assert t.name == "bugfix"
        assert t.goal_text == "Fix {issue}"
        assert t.description == "Bug fix"
        assert t.variables == ["issue"]
        assert t.created_at == t.updated_at

    def test_creates_architect_dir(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        create_template(tmp_path, "t", "goal", "desc")
        assert (tmp_path / ".architect").exists()

    def test_raises_value_error_on_duplicate(self, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        with pytest.raises(ValueError, match="already exists"):
            create_template(tmp_path, "bugfix", "Fix {bug}", "Bug fix v2")

    def test_auto_extracts_variables(self, tmp_path: Path) -> None:
        t = create_template(tmp_path, "multi", "Add {feature} to {module}", "Multi-var")
        assert t.variables == ["feature", "module"]

    def test_stores_config_overrides(self, tmp_path: Path) -> None:
        t = create_template(
            tmp_path,
            "config",
            "goal",
            "desc",
            config_overrides={"max_retries": 5, "integrity": False},
        )
        assert t.config_overrides == {"max_retries": 5, "integrity": False}

    def test_allows_empty_overrides(self, tmp_path: Path) -> None:
        t = create_template(tmp_path, "empty", "goal", "desc")
        assert t.config_overrides == {}

    def test_multiple_templates_coexist(self, tmp_path: Path) -> None:
        create_template(tmp_path, "a", "goal a", "desc a")
        create_template(tmp_path, "b", "goal b", "desc b")
        all_t = list_templates(tmp_path)
        assert len(all_t) == 2

    def test_writes_valid_json_to_disk(self, tmp_path: Path) -> None:
        create_template(tmp_path, "test", "Fix {bug}", "desc")
        templates_path = tmp_path / _TEMPLATES_FILE
        data = json.loads(templates_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test"

    def test_no_temp_files_left(self, tmp_path: Path) -> None:
        create_template(tmp_path, "cleanup", "goal", "desc")
        temp_files = list((tmp_path / ".architect").glob(".templates_tmp_*"))
        assert temp_files == []

    def test_no_variables_when_none_in_goal(self, tmp_path: Path) -> None:
        t = create_template(tmp_path, "plain", "Just a plain goal", "no vars")
        assert t.variables == []


# ---------------------------------------------------------------------------
# show_template()
# ---------------------------------------------------------------------------


class TestShowTemplate:
    """Tests for show_template()."""

    def test_returns_template_when_found(self, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = show_template(tmp_path, "bugfix")
        assert result is not None
        assert result.name == "bugfix"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = show_template(tmp_path, "feature")
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = show_template(tmp_path, "any")
        assert result is None

    def test_case_sensitive_matching(self, tmp_path: Path) -> None:
        create_template(tmp_path, "Bugfix", "Fix {issue}", "Bug fix")
        assert show_template(tmp_path, "Bugfix") is not None
        assert show_template(tmp_path, "bugfix") is None
        assert show_template(tmp_path, "BUGFIX") is None


# ---------------------------------------------------------------------------
# delete_template()
# ---------------------------------------------------------------------------


class TestDeleteTemplate:
    """Tests for delete_template()."""

    def test_deletes_existing_template(self, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = delete_template(tmp_path, "bugfix")
        assert result is True
        assert show_template(tmp_path, "bugfix") is None

    def test_returns_false_when_not_found(self, tmp_path: Path) -> None:
        result = delete_template(tmp_path, "nonexistent")
        assert result is False

    def test_no_error_when_file_missing(self, tmp_path: Path) -> None:
        result = delete_template(tmp_path, "any")
        assert result is False

    def test_deletes_one_preserves_others(self, tmp_path: Path) -> None:
        create_template(tmp_path, "a", "goal a", "desc a")
        create_template(tmp_path, "b", "goal b", "desc b")
        delete_template(tmp_path, "a")
        assert show_template(tmp_path, "a") is None
        assert show_template(tmp_path, "b") is not None
        assert len(list_templates(tmp_path)) == 1


# ---------------------------------------------------------------------------
# extract_variables()
# ---------------------------------------------------------------------------


class TestExtractVariables:
    """Tests for extract_variables()."""

    def test_single_variable(self) -> None:
        assert extract_variables("Fix {issue}") == ["issue"]

    def test_multiple_variables(self) -> None:
        assert extract_variables("Add {feature} to {module}") == ["feature", "module"]

    def test_no_variables(self) -> None:
        assert extract_variables("Just a plain goal") == []

    def test_duplicate_variables_sorted_unique(self) -> None:
        assert extract_variables("Fix {b} in {a} and {b}") == ["a", "b"]

    def test_variables_sorted_alphabetically(self) -> None:
        assert extract_variables("Fix {z} then {a} then {m}") == ["a", "m", "z"]

    def test_underscore_in_variable_name(self) -> None:
        assert extract_variables("Fix {issue_id}") == ["issue_id"]

    def test_variable_starts_with_underscore(self) -> None:
        assert extract_variables("Fix {_internal}") == ["_internal"]

    def test_numeric_suffix_in_variable(self) -> None:
        assert extract_variables("Fix {issue2}") == ["issue2"]

    def test_nested_braces_inner_matched(self) -> None:
        """{{double}} contains {double} which the regex will match."""
        assert extract_variables("Fix {{double}}") == ["double"]

    def test_special_chars_in_goal_text(self) -> None:
        assert extract_variables("Fix {x}! @#$%^&*()") == ["x"]

    def test_empty_string(self) -> None:
        assert extract_variables("") == []

    def test_braces_without_valid_name(self) -> None:
        """{123} and {abc!} are not valid variable patterns."""
        assert extract_variables("Fix {123}") == []
        assert extract_variables("Fix {abc!}") == []

    def test_mixed_valid_and_invalid(self) -> None:
        assert extract_variables("Fix {valid} and {123} and {also_valid}") == [
            "also_valid",
            "valid",
        ]


# ---------------------------------------------------------------------------
# substitute_variables()
# ---------------------------------------------------------------------------


class TestSubstituteVariables:
    """Tests for substitute_variables()."""

    def test_all_variables_provided(self) -> None:
        result = substitute_variables(
            "Fix {issue} in {module}",
            {"issue": "null pointer", "module": "auth"},
        )
        assert result == "Fix null pointer in auth"

    def test_partial_variables(self) -> None:
        result = substitute_variables(
            "Fix {issue} in {module}",
            {"issue": "null pointer"},
        )
        assert result == "Fix null pointer in {module}"

    def test_no_variables_provided(self) -> None:
        result = substitute_variables(
            "Fix {issue} in {module}",
            {},
        )
        assert result == "Fix {issue} in {module}"

    def test_extra_variables_ignored(self) -> None:
        result = substitute_variables(
            "Fix {issue}",
            {"issue": "bug", "extra": "value"},
        )
        assert result == "Fix bug"

    def test_empty_values(self) -> None:
        result = substitute_variables(
            "Fix {issue}",
            {"issue": ""},
        )
        assert result == "Fix "

    def test_no_placeholders_in_text(self) -> None:
        result = substitute_variables(
            "Just a plain goal",
            {"issue": "bug"},
        )
        assert result == "Just a plain goal"

    def test_multiple_occurrences_replaced(self) -> None:
        result = substitute_variables(
            "Fix {issue} and also {issue}",
            {"issue": "bug"},
        )
        assert result == "Fix bug and also bug"

    def test_special_chars_in_values(self) -> None:
        result = substitute_variables(
            "Fix {issue}",
            {"issue": "null pointer (critical)"},
        )
        assert result == "Fix null pointer (critical)"

    def test_mixed_substitution(self) -> None:
        result = substitute_variables(
            "Add {feature} to {module}, fix {bug}",
            {"feature": "login", "bug": "auth leak"},
        )
        assert result == "Add login to {module}, fix auth leak"


# ---------------------------------------------------------------------------
# CLI — architect template command
# ---------------------------------------------------------------------------


class TestTemplateCLI:
    """Tests for the ``architect template`` CLI command group.

    Note: The ``-p`` / ``--project`` option lives on each sub-command, not
    on the ``template`` group.  So the correct invocation is:
    ``template <sub-cmd> -p <path> ...``
    """

    def test_template_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "template" in result.output

    # -- template create -----------------------------------------------------

    def test_create_basic(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "bugfix",
                "-g",
                "Fix {issue}",
                "-d",
                "Bug fix",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "created" in result.output
        assert "Variables: 1" in result.output
        t = show_template(tmp_path, "bugfix")
        assert t is not None
        assert t.description == "Bug fix"

    def test_create_with_config_overrides(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "sprint",
                "-g",
                "Sprint {goal}",
                "-d",
                "Sprint",
                "-c",
                "max_retries=5",
                "-c",
                "integrity=false",
            ],
        )
        assert result.exit_code == 0, result.output
        t = show_template(tmp_path, "sprint")
        assert t is not None
        assert t.config_overrides["max_retries"] == 5
        assert t.config_overrides["integrity"] is False

    def test_create_duplicate_raises_error(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        cli_runner.invoke(
            main,
            ["template", "create", "-p", str(tmp_path), "bugfix", "-g", "Fix {issue}"],
        )
        result = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "bugfix",
                "-g",
                "Fix {bug}",
            ],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_create_invalid_config_format(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "bad",
                "-g",
                "goal",
                "-c",
                "no-equals-sign",
            ],
        )
        assert result.exit_code == 1
        assert "KEY=VALUE" in result.output

    def test_create_unknown_config_field(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "x",
                "-g",
                "goal",
                "-c",
                "nonexistent_field=1",
            ],
        )
        assert result.exit_code == 1
        assert "unknown config field" in result.output

    # -- template list -------------------------------------------------------

    def test_list_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "No templates saved" in result.output

    def test_list_with_templates(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "bugfix" in result.output
        assert "Bug fix" in result.output

    def test_list_json_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["templates"] == []
        assert "project" in payload

    def test_list_json_with_templates(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["templates"]) == 1
        assert payload["templates"][0]["name"] == "bugfix"
        assert payload["templates"][0]["variables"] == ["issue"]

    # -- template show -------------------------------------------------------

    def test_show_template(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "bugfix"],
        )
        assert result.exit_code == 0, result.output
        assert "bugfix" in result.output
        assert "Fix {issue}" in result.output
        assert "issue" in result.output

    def test_show_missing_template(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_json(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "bugfix", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["template"]["name"] == "bugfix"
        assert payload["template"]["goal_text"] == "Fix {issue}"
        assert "project" in payload

    def test_show_json_missing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "nope", "--json"],
        )
        assert result.exit_code == 1

    def test_show_no_variables(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "plain", "Just a goal", "Plain")
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "plain"],
        )
        assert result.exit_code == 0, result.output
        assert "No variables" in result.output

    def test_show_no_config_overrides(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "plain", "Just a goal", "Plain")
        result = cli_runner.invoke(
            main,
            ["template", "show", "-p", str(tmp_path), "plain"],
        )
        assert result.exit_code == 0, result.output
        assert "No config overrides" in result.output

    # -- template delete -----------------------------------------------------

    def test_delete_existing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "delete", "-p", str(tmp_path), "bugfix"],
        )
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output
        assert show_template(tmp_path, "bugfix") is None

    def test_delete_missing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "delete", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    # -- template run --------------------------------------------------------

    def test_run_missing_template(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["template", "run", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_run_headless_missing_vars_fails(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            ["template", "run", "-p", str(tmp_path), "bugfix", "--headless"],
        )
        assert result.exit_code == 1
        assert "unsatisfied variables" in result.output

    def test_run_headless_with_all_vars(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        with patch("the_architect.cli._run_main") as mock_run:
            cli_runner.invoke(
                main,
                [
                    "template",
                    "run",
                    "-p",
                    str(tmp_path),
                    "bugfix",
                    "--headless",
                    "--var",
                    "issue=null pointer",
                ],
            )
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs[1]["goal_text"] == "Fix null pointer"

    def test_run_var_format_error(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "bugfix", "Fix {issue}", "Bug fix")
        result = cli_runner.invoke(
            main,
            [
                "template",
                "run",
                "-p",
                str(tmp_path),
                "bugfix",
                "--var",
                "no-equals",
            ],
        )
        assert result.exit_code == 1
        assert "VAR=VALUE" in result.output

    def test_run_substitutes_variables(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(tmp_path, "multi", "Add {feature} to {module}", "Multi")
        with patch("the_architect.cli._run_main") as mock_run:
            cli_runner.invoke(
                main,
                [
                    "template",
                    "run",
                    "-p",
                    str(tmp_path),
                    "multi",
                    "--var",
                    "feature=login",
                    "--var",
                    "module=auth",
                ],
            )
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs[1]["goal_text"] == "Add login to auth"

    def test_run_applies_config_overrides(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        create_template(
            tmp_path,
            "cfg",
            "Fix {issue}",
            "Config template",
            config_overrides={"max_retries": 5},
        )
        with (
            patch("the_architect.cli._run_main") as mock_run,
            patch("the_architect.cli.write_config") as mock_write,
        ):
            cli_runner.invoke(
                main,
                [
                    "template",
                    "run",
                    "-p",
                    str(tmp_path),
                    "cfg",
                    "--var",
                    "issue=bug",
                ],
            )
            mock_write.assert_called_once()
            mock_run.assert_called_once()

    # -- Roundtrip -----------------------------------------------------------

    def test_full_lifecycle(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Create -> list -> show -> delete -> verify gone."""
        # Create
        r = cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "lifecycle",
                "-g",
                "Fix {issue}",
                "-d",
                "test",
            ],
        )
        assert r.exit_code == 0

        # List
        r = cli_runner.invoke(main, ["template", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert len(data["templates"]) == 1

        # Show
        r = cli_runner.invoke(
            main, ["template", "show", "-p", str(tmp_path), "lifecycle", "--json"]
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["template"]["name"] == "lifecycle"

        # Delete
        r = cli_runner.invoke(main, ["template", "delete", "-p", str(tmp_path), "lifecycle"])
        assert r.exit_code == 0

        # Verify gone
        r = cli_runner.invoke(main, ["template", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["templates"] == []

    # -- Short flag ----------------------------------------------------------

    def test_project_short_flag(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """-p short flag works for all sub-commands."""
        cli_runner.invoke(
            main,
            [
                "template",
                "create",
                "-p",
                str(tmp_path),
                "short",
                "-g",
                "goal",
            ],
        )
        r = cli_runner.invoke(main, ["template", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert len(data["templates"]) == 1
        assert data["templates"][0]["name"] == "short"


# ---------------------------------------------------------------------------
# Storage edge cases
# ---------------------------------------------------------------------------


class TestStorageEdgeCases:
    """Tests for storage-level edge cases."""

    def test_atomic_write_no_temp_files(self, tmp_path: Path) -> None:
        create_template(tmp_path, "atomic", "goal", "desc")
        temp_files = list((tmp_path / ".architect").glob(".templates_tmp_*"))
        assert temp_files == []

    def test_corrupted_file_does_not_crash_list(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Even with corrupted JSON on disk, list_templates returns empty."""
        _write_raw_templates(tmp_path, "CORRUPTED{{{")
        result = list_templates(tmp_path)
        assert result == []

    def test_empty_array_file(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, "[]")
        assert list_templates(tmp_path) == []

    def test_single_template_in_file(self, tmp_path: Path) -> None:
        _write_raw_templates(tmp_path, [_make_template_dict("only")])
        result = list_templates(tmp_path)
        assert len(result) == 1
        assert result[0].name == "only"

    def test_template_with_special_chars_in_goal(self, tmp_path: Path) -> None:
        t = create_template(
            tmp_path,
            "special",
            "Fix {issue}! @#$%^&*()",
            "Special chars",
        )
        assert t.goal_text == "Fix {issue}! @#$%^&*()"
        assert t.variables == ["issue"]

    def test_template_with_no_description(self, tmp_path: Path) -> None:
        t = create_template(tmp_path, "nodosc", "Fix {issue}", "")
        assert t.description == ""
