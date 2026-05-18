"""Tests for the preset storage module and CLI command.

Covers:
- Preset Pydantic model validation
- list_presets() — empty, multiple, corrupted entries, missing file
- get_preset() — found, not found, case sensitivity
- save_preset() — create, update (preserve created_at), empty overrides
- delete_preset() — found, not found
- clear_presets() — some presets, no presets
- Edge cases: corrupted JSON, non-list JSON, empty file, OS errors
- architect preset create — basic, type coercion, update, invalid field format, unknown field
- architect preset list — empty, with presets, --json output
- architect preset show — found, not found, --json output
- architect preset apply — with overrides, no overrides, missing preset
- architect preset delete — found, not found
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.presets import (
    PRESETS_FILE,
    Preset,
    clear_presets,
    delete_preset,
    get_preset,
    list_presets,
    save_preset,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _write_raw_presets(project: Path, data: object) -> None:
    """Write raw preset data directly to disk."""
    presets_path = project / PRESETS_FILE
    presets_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        presets_path.write_text(data, encoding="utf-8")
    else:
        presets_path.write_text(json.dumps(data), encoding="utf-8")


def _make_preset_dict(
    name: str = "sprint",
    description: str = "Fast iteration",
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a valid preset dict for test data."""
    return {
        "name": name,
        "description": description,
        "config_overrides": overrides or {"max_retries": 5},
        "created_at": "2026-05-17T10:00:00+00:00",
        "updated_at": "2026-05-17T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Preset model
# ---------------------------------------------------------------------------


class TestPresetModel:
    """Tests for the Preset Pydantic model."""

    def test_create_minimal(self) -> None:
        p = Preset(
            name="test",
            description="a preset",
            config_overrides={"max_retries": 3},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert p.name == "test"
        assert p.description == "a preset"
        assert p.config_overrides == {"max_retries": 3}

    def test_empty_overrides_default(self) -> None:
        p = Preset(
            name="empty",
            description="no overrides",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert p.config_overrides == {}

    def test_model_roundtrip(self) -> None:
        original = Preset(
            name="round",
            description="trip test",
            config_overrides={"integrity": False, "max_retries": 7},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )
        restored = Preset.model_validate(original.model_dump())
        assert restored.name == original.name
        assert restored.config_overrides == original.config_overrides
        assert restored.created_at == original.created_at


# ---------------------------------------------------------------------------
# list_presets()
# ---------------------------------------------------------------------------


class TestListPresets:
    """Tests for list_presets()."""

    def test_empty_when_file_missing(self, tmp_path: Path) -> None:
        assert list_presets(tmp_path) == []

    def test_empty_when_architect_dir_missing(self, tmp_path: Path) -> None:
        """Even when .architect/ doesn't exist, returns empty list."""
        assert list_presets(tmp_path) == []

    def test_returns_presets_when_file_exists(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, [_make_preset_dict("sprint")])
        result = list_presets(tmp_path)
        assert len(result) == 1
        assert result[0].name == "sprint"
        assert result[0].description == "Fast iteration"

    def test_returns_multiple_presets(self, tmp_path: Path) -> None:
        _write_raw_presets(
            tmp_path,
            [
                _make_preset_dict("sprint", overrides={"max_retries": 5}),
                _make_preset_dict("deep", overrides={"persistent": True}),
            ],
        )
        result = list_presets(tmp_path)
        assert len(result) == 2
        names = [p.name for p in result]
        assert "sprint" in names
        assert "deep" in names

    def test_skips_corrupted_entries(self, tmp_path: Path) -> None:
        """Corrupted entries are skipped, valid entries are returned."""
        _write_raw_presets(
            tmp_path,
            [
                _make_preset_dict("good"),
                {"name": "bad"},  # missing required fields
                _make_preset_dict("also_good"),
            ],
        )
        result = list_presets(tmp_path)
        assert len(result) == 2
        assert all(p.name in ("good", "also_good") for p in result)

    def test_returns_empty_on_invalid_json(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, "not valid json {{{")
        assert list_presets(tmp_path) == []

    def test_returns_empty_on_corrupted_data(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, "CORRUPTED")
        assert list_presets(tmp_path) == []

    def test_returns_empty_on_empty_file(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, "")
        assert list_presets(tmp_path) == []

    def test_returns_empty_on_non_list_json(self, tmp_path: Path) -> None:
        """A JSON object (not array) is treated as empty."""
        _write_raw_presets(tmp_path, {"presets": []})
        assert list_presets(tmp_path) == []

    def test_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        presets_path = tmp_path / PRESETS_FILE
        presets_path.parent.mkdir(parents=True, exist_ok=True)
        presets_path.write_text("[]", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = list_presets(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# get_preset()
# ---------------------------------------------------------------------------


class TestGetPreset:
    """Tests for get_preset()."""

    def test_returns_preset_when_found(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, [_make_preset_dict("sprint")])
        result = get_preset(tmp_path, "sprint")
        assert result is not None
        assert result.name == "sprint"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, [_make_preset_dict("sprint")])
        result = get_preset(tmp_path, "deep")
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = get_preset(tmp_path, "any")
        assert result is None

    def test_case_sensitive_matching(self, tmp_path: Path) -> None:
        _write_raw_presets(tmp_path, [_make_preset_dict("Sprint")])
        assert get_preset(tmp_path, "Sprint") is not None
        assert get_preset(tmp_path, "sprint") is None
        assert get_preset(tmp_path, "SPRINT") is None


# ---------------------------------------------------------------------------
# save_preset()
# ---------------------------------------------------------------------------


class TestSavePreset:
    """Tests for save_preset()."""

    def test_creates_new_preset(self, tmp_path: Path) -> None:
        preset = save_preset(tmp_path, "sprint", "Fast iteration", {"max_retries": 5})
        assert preset.name == "sprint"
        assert preset.description == "Fast iteration"
        assert preset.config_overrides == {"max_retries": 5}
        assert preset.created_at == preset.updated_at

    def test_creates_architect_dir(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        save_preset(tmp_path, "sprint", "desc", {})
        assert (tmp_path / ".architect").exists()

    def test_preserves_created_at_on_update(self, tmp_path: Path) -> None:
        first = save_preset(tmp_path, "sprint", "v1", {"max_retries": 3})
        original_created = first.created_at

        second = save_preset(tmp_path, "sprint", "v2", {"max_retries": 5})
        assert second.created_at == original_created
        assert second.updated_at != second.created_at
        assert second.description == "v2"
        assert second.config_overrides == {"max_retries": 5}

    def test_update_does_not_duplicate(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "v1", {"max_retries": 3})
        save_preset(tmp_path, "sprint", "v2", {"max_retries": 5})
        all_presets = list_presets(tmp_path)
        assert len(all_presets) == 1
        assert all_presets[0].description == "v2"

    def test_allows_empty_overrides(self, tmp_path: Path) -> None:
        preset = save_preset(tmp_path, "empty", "no fields", {})
        assert preset.config_overrides == {}

    def test_multiple_presets_coexist(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        save_preset(tmp_path, "deep", "Slow", {"persistent": True})
        all_presets = list_presets(tmp_path)
        assert len(all_presets) == 2

    def test_writes_valid_json_to_disk(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "test", "desc", {"max_retries": 5})
        presets_path = tmp_path / PRESETS_FILE
        data = json.loads(presets_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test"

    def test_no_temp_files_left(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "cleanup", "desc", {})
        temp_files = list((tmp_path / ".architect").glob(".presets_tmp_*"))
        assert temp_files == []


# ---------------------------------------------------------------------------
# delete_preset()
# ---------------------------------------------------------------------------


class TestDeletePreset:
    """Tests for delete_preset()."""

    def test_deletes_existing_preset(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        result = delete_preset(tmp_path, "sprint")
        assert result is True
        assert get_preset(tmp_path, "sprint") is None

    def test_returns_false_when_not_found(self, tmp_path: Path) -> None:
        result = delete_preset(tmp_path, "nonexistent")
        assert result is False

    def test_no_error_when_file_missing(self, tmp_path: Path) -> None:
        result = delete_preset(tmp_path, "any")
        assert result is False

    def test_deletes_one_preserves_others(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        save_preset(tmp_path, "deep", "Slow", {"persistent": True})
        delete_preset(tmp_path, "sprint")
        assert get_preset(tmp_path, "sprint") is None
        assert get_preset(tmp_path, "deep") is not None
        assert len(list_presets(tmp_path)) == 1


# ---------------------------------------------------------------------------
# clear_presets()
# ---------------------------------------------------------------------------


class TestClearPresets:
    """Tests for clear_presets()."""

    def test_clears_all_presets(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        save_preset(tmp_path, "deep", "Slow", {"persistent": True})
        count = clear_presets(tmp_path)
        assert count == 2
        assert list_presets(tmp_path) == []

    def test_returns_zero_when_empty(self, tmp_path: Path) -> None:
        count = clear_presets(tmp_path)
        assert count == 0

    def test_no_error_when_file_missing(self, tmp_path: Path) -> None:
        count = clear_presets(tmp_path)
        assert count == 0

    def test_writes_empty_array(self, tmp_path: Path) -> None:
        save_preset(tmp_path, "only", "one", {})
        clear_presets(tmp_path)
        presets_path = tmp_path / PRESETS_FILE
        data = json.loads(presets_path.read_text(encoding="utf-8"))
        assert data == []


# ---------------------------------------------------------------------------
# CLI — architect preset command
# ---------------------------------------------------------------------------


class TestPresetCLI:
    """Tests for the ``architect preset`` CLI command group.

    Note: The ``-p`` / ``--project`` option lives on each sub-command, not
    on the ``preset`` group.  So the correct invocation is:
    ``preset <sub-cmd> -p <path> ...``
    """

    def test_preset_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "preset" in result.output

    # -- preset create -------------------------------------------------------

    def test_create_basic(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "sprint", "-d", "Fast iteration"],
        )
        assert result.exit_code == 0, result.output
        assert "created" in result.output
        preset = get_preset(tmp_path, "sprint")
        assert preset is not None
        assert preset.description == "Fast iteration"

    def test_create_with_fields(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "preset",
                "create",
                "-p",
                str(tmp_path),
                "sprint",
                "-d",
                "Fast",
                "-f",
                "max_retries=5",
                "-f",
                "integrity=false",
            ],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "sprint")
        assert preset is not None
        assert preset.config_overrides["max_retries"] == 5
        assert preset.config_overrides["integrity"] is False

    def test_create_type_coercion_int(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "num", "-f", "max_retries=42"],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "num")
        assert preset is not None
        assert preset.config_overrides["max_retries"] == 42
        assert isinstance(preset.config_overrides["max_retries"], int)

    def test_create_type_coercion_bool_true(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "b", "-f", "persistent=true"],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "b")
        assert preset is not None
        assert preset.config_overrides["persistent"] is True

    def test_create_type_coercion_bool_false(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "b", "-f", "persistent=false"],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "b")
        assert preset is not None
        assert preset.config_overrides["persistent"] is False

    def test_create_type_coercion_float(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "f", "-f", "retry_pause=3.14"],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "f")
        assert preset is not None
        assert preset.config_overrides["retry_pause"] == 3.14
        assert isinstance(preset.config_overrides["retry_pause"], float)

    def test_create_type_coercion_string(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "s", "-f", "standalone_mode=my-model"],
        )
        assert result.exit_code == 0, result.output
        preset = get_preset(tmp_path, "s")
        assert preset is not None
        assert preset.config_overrides["standalone_mode"] == "my-model"

    def test_create_updates_existing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "sprint", "-d", "v1"],
        )
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "sprint", "-d", "v2"],
        )
        assert result.exit_code == 0, result.output
        assert "updated" in result.output
        preset = get_preset(tmp_path, "sprint")
        assert preset is not None
        assert preset.description == "v2"

    def test_create_invalid_field_format(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "bad", "-f", "no-equals-sign"],
        )
        assert result.exit_code == 1
        assert "KEY=VALUE" in result.output

    def test_create_unknown_field(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "x", "-f", "nonexistent_field=1"],
        )
        assert result.exit_code == 1
        assert "unknown config field" in result.output

    def test_create_path_field_rejected(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "x", "-f", "tasks_dir=custom"],
        )
        assert result.exit_code == 1
        assert "unknown config field" in result.output

    def test_create_shows_field_count(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "preset",
                "create",
                "-p",
                str(tmp_path),
                "multi",
                "-f",
                "max_retries=5",
                "-f",
                "integrity=false",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Fields: 2" in result.output

    # -- preset list ---------------------------------------------------------

    def test_list_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "No presets saved" in result.output

    def test_list_with_presets(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast iteration", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "sprint" in result.output
        assert "Fast iteration" in result.output

    def test_list_json_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["presets"] == []
        assert "project" in payload

    def test_list_json_with_presets(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["presets"]) == 1
        assert payload["presets"][0]["name"] == "sprint"
        assert payload["presets"][0]["config_overrides"]["max_retries"] == 5

    # -- preset show ---------------------------------------------------------

    def test_show_preset(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast iteration", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "show", "-p", str(tmp_path), "sprint"],
        )
        assert result.exit_code == 0, result.output
        assert "sprint" in result.output
        assert "Fast iteration" in result.output
        assert "max_retries" in result.output

    def test_show_missing_preset(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "show", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_json(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "show", "-p", str(tmp_path), "sprint", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["preset"]["name"] == "sprint"
        assert payload["preset"]["config_overrides"]["max_retries"] == 5
        assert "project" in payload

    def test_show_json_missing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "show", "-p", str(tmp_path), "nope", "--json"],
        )
        assert result.exit_code == 1

    def test_show_empty_overrides(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "empty", "no fields", {})
        result = cli_runner.invoke(
            main,
            ["preset", "show", "-p", str(tmp_path), "empty"],
        )
        assert result.exit_code == 0, result.output
        assert "No config overrides" in result.output

    # -- preset apply --------------------------------------------------------

    def test_apply_preset(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "apply", "-p", str(tmp_path), "sprint"],
        )
        assert result.exit_code == 0, result.output
        assert "Applied preset" in result.output
        # Verify config was written
        toml_content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "max_retries = 5" in toml_content

    def test_apply_creates_toml(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        assert not (tmp_path / "architect.toml").exists()
        cli_runner.invoke(
            main,
            ["preset", "apply", "-p", str(tmp_path), "sprint"],
        )
        assert (tmp_path / "architect.toml").exists()

    def test_apply_missing_preset(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "apply", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_apply_no_overrides(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "empty", "no fields", {})
        result = cli_runner.invoke(
            main,
            ["preset", "apply", "-p", str(tmp_path), "empty"],
        )
        assert result.exit_code == 0, result.output
        assert "no config overrides" in result.output

    def test_apply_merges_with_existing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        # Pre-existing config
        (tmp_path / "architect.toml").write_text("[architect]\nmax_retries = 3\n", encoding="utf-8")
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 10})
        cli_runner.invoke(
            main,
            ["preset", "apply", "-p", str(tmp_path), "sprint"],
        )
        # Value should be updated to preset's value
        toml_content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "max_retries = 10" in toml_content

    # -- preset delete -------------------------------------------------------

    def test_delete_existing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_preset(tmp_path, "sprint", "Fast", {"max_retries": 5})
        result = cli_runner.invoke(
            main,
            ["preset", "delete", "-p", str(tmp_path), "sprint"],
        )
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output
        assert get_preset(tmp_path, "sprint") is None

    def test_delete_missing(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["preset", "delete", "-p", str(tmp_path), "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    # -- Roundtrip -----------------------------------------------------------

    def test_full_lifecycle(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Create -> list -> show -> apply -> delete -> verify gone."""
        # Create
        r = cli_runner.invoke(
            main,
            [
                "preset",
                "create",
                "-p",
                str(tmp_path),
                "lifecycle",
                "-d",
                "test",
                "-f",
                "max_retries=7",
            ],
        )
        assert r.exit_code == 0

        # List
        r = cli_runner.invoke(main, ["preset", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert len(data["presets"]) == 1

        # Show
        r = cli_runner.invoke(main, ["preset", "show", "-p", str(tmp_path), "lifecycle", "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["preset"]["name"] == "lifecycle"

        # Apply
        r = cli_runner.invoke(main, ["preset", "apply", "-p", str(tmp_path), "lifecycle"])
        assert r.exit_code == 0

        # Delete
        r = cli_runner.invoke(main, ["preset", "delete", "-p", str(tmp_path), "lifecycle"])
        assert r.exit_code == 0

        # Verify gone
        r = cli_runner.invoke(main, ["preset", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["presets"] == []

    # -- Short flag ----------------------------------------------------------

    def test_project_short_flag(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """-p short flag works for all sub-commands."""
        cli_runner.invoke(
            main,
            ["preset", "create", "-p", str(tmp_path), "short", "-d", "test"],
        )
        r = cli_runner.invoke(main, ["preset", "list", "-p", str(tmp_path), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert len(data["presets"]) == 1
        assert data["presets"][0]["name"] == "short"
