"""Tests for config loading and validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from the_architect.config import ArchitectConfig, find_opencode_json, load_config, write_config


class TestArchitectConfig:
    """Tests for ArchitectConfig model."""

    def test_default_config_values(self) -> None:
        """Should have sensible defaults for all settings."""
        config = ArchitectConfig()

        assert config.tasks_dir == Path("tasks")
        assert config.progress_file == Path("tasks/PROGRESS.md")
        assert config.log_dir == Path(".architect/logs")
        assert config.max_retries == 3
        assert config.retry_pause == 30
        assert config.pause_between_tasks == 10
        assert config.standalone_mode == ""
        assert config.execution_agent == ""
        assert config.integrity is True
        assert config.force_reassessment is True

    def test_config_resolve(self) -> None:
        """Should make paths absolute when resolve is called."""
        config = ArchitectConfig(
            tasks_dir=Path("tasks"),
            progress_file=Path("tasks/PROGRESS.md"),
            log_dir=Path(".architect/logs"),
        )

        resolved = config.resolve(Path("/project/root"))

        assert resolved.tasks_dir.is_absolute()
        assert resolved.progress_file.is_absolute()
        assert resolved.log_dir.is_absolute()
        assert str(resolved.tasks_dir).endswith("tasks")
        assert str(resolved.progress_file).endswith("PROGRESS.md")

    def test_config_resolve_with_string(self) -> None:
        """Should accept string and convert to Path."""
        config = ArchitectConfig()
        resolved = config.resolve("/project/root")

        assert resolved.tasks_dir.is_absolute()

    def test_config_extra_fields_ignored(self) -> None:
        """Should ignore extra fields in input."""
        config = ArchitectConfig(unknown_field="ignored", another=123)

        assert not hasattr(config, "unknown_field")

    def test_config_project_root_property(self, tmp_path: Path) -> None:
        """project_root should return the parent of progress_file."""
        config = ArchitectConfig().resolve(tmp_path)

        assert config.project_root == tmp_path
        assert config.project_root == config.tasks_dir.parent


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_no_file_returns_defaults(self) -> None:
        """Should return defaults when architect.toml doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            config = load_config(project_dir)

            # Paths are resolved to absolute
            assert config.tasks_dir.is_absolute()
            assert config.progress_file.is_absolute()
            # Non-path defaults
            assert config.max_retries == 3
            assert config.standalone_mode == ""

    def test_load_config_no_file_resolves_paths(self) -> None:
        """Should resolve paths even with defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            config = load_config(project_dir)

            assert config.tasks_dir.is_absolute()
            assert config.progress_file.is_absolute()

    def test_load_config_with_string_project_dir(self) -> None:
        """Should accept a string project_dir and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(tmpdir)  # pass str, not Path

            assert config.tasks_dir.is_absolute()
            assert config.max_retries == 3

    def test_load_config_with_toml_file(self) -> None:
        """Should load values from architect.toml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            toml_content = """
[architect]
tasks_dir = "my_sessions"
max_retries = 5
standalone_mode = "claude-sonnet-4-20250514"
"""
            (project_dir / "architect.toml").write_text(toml_content, encoding="utf-8")

            config = load_config(project_dir)

            assert str(config.tasks_dir).endswith("my_sessions")
            assert config.max_retries == 5
            assert config.standalone_mode == "claude-sonnet-4-20250514"


class TestFindOpencodeJson:
    """Tests for find_opencode_json function."""

    def test_find_opencode_json_in_project_dir(self) -> None:
        """Should find opencode.json in project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "opencode.json").touch()

            result = find_opencode_json(project_dir)

            assert result is not None
            assert result.name == "opencode.json"

    def test_find_opencode_json_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return None when opencode.json doesn't exist in project_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmpdir) / "xdg"))

            result = find_opencode_json(project_dir)

            assert result is None

    def test_find_opencode_json_does_not_walk_to_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should NOT find opencode.json in a parent directory.

        A parent opencode.json belongs to a different project. Walking up
        would cause The Architect to create tasks/ in the wrong directory.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "subproject"
            project_dir.mkdir()
            (Path(tmpdir) / "opencode.json").touch()  # parent has one — must be ignored
            monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmpdir) / "xdg"))

            result = find_opencode_json(project_dir)

            assert result is None

    def test_find_opencode_json_does_not_walk_past_git(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None even when parent has opencode.json and a .git exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "subproject"
            project_dir.mkdir()
            (project_dir / ".git").mkdir()
            (Path(tmpdir) / "opencode.json").touch()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmpdir) / "xdg"))

            result = find_opencode_json(project_dir)

            assert result is None

    def test_find_opencode_json_with_string_project_dir(self) -> None:
        """Should accept a string project_dir and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "opencode.json").touch()

            result = find_opencode_json(str(project_dir))  # pass str, not Path

            assert result is not None
            assert result.name == "opencode.json"


# ---------------------------------------------------------------------------
# IMP-10 — New config fields
# ---------------------------------------------------------------------------


class TestNewConfigFields:
    """Tests for carry_context and retry_prompt_mode fields."""

    def test_carry_context_default_true(self) -> None:
        """carry_context should default to True."""
        config = ArchitectConfig()
        assert config.carry_context is True

    def test_retry_prompt_mode_default_focused(self) -> None:
        """retry_prompt_mode should default to 'focused'."""
        config = ArchitectConfig()
        assert config.retry_prompt_mode == "focused"

    def test_carry_context_can_be_set_false(self) -> None:
        """carry_context should accept False."""
        config = ArchitectConfig(carry_context=False)
        assert config.carry_context is False

    def test_retry_prompt_mode_same(self) -> None:
        """retry_prompt_mode should accept 'same'."""
        config = ArchitectConfig(retry_prompt_mode="same")
        assert config.retry_prompt_mode == "same"

    def test_new_fields_survive_resolve(self, tmp_path) -> None:
        """New fields should be preserved through resolve()."""
        config = ArchitectConfig(
            carry_context=False,
            retry_prompt_mode="same",
            integrity=False,
        )
        resolved = config.resolve(tmp_path)
        assert resolved.carry_context is False
        assert resolved.retry_prompt_mode == "same"
        assert resolved.integrity is False

    def test_new_fields_load_from_toml(self, tmp_path) -> None:
        """Should load carry_context and retry_prompt_mode from architect.toml."""
        (tmp_path / "architect.toml").write_text(
            '[architect]\ncarry_context = false\nretry_prompt_mode = "same"\nintegrity = false\n',
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config.carry_context is False
        assert config.retry_prompt_mode == "same"
        assert config.integrity is False


# ---------------------------------------------------------------------------
# write_config tests
# ---------------------------------------------------------------------------


class TestWriteConfig:
    """Tests for write_config()."""

    def test_creates_toml_file(self, tmp_path) -> None:
        """Should create architect.toml when it doesn't exist."""
        result = write_config(tmp_path, {"max_retries": 5})
        assert result.exists()
        assert result.name == "architect.toml"

    def test_writes_int_value(self, tmp_path) -> None:
        """Should write integer values correctly."""
        write_config(tmp_path, {"max_retries": 10})
        content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "max_retries = 10" in content

    def test_writes_bool_true(self, tmp_path) -> None:
        """Should write bool True as 'true'."""
        write_config(tmp_path, {"carry_context": True})
        content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "carry_context = true" in content

    def test_writes_bool_false(self, tmp_path) -> None:
        """Should write bool False as 'false'."""
        write_config(tmp_path, {"carry_context": False})
        content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "carry_context = false" in content

    def test_writes_string_value(self, tmp_path) -> None:
        """Should write string values with quotes."""
        write_config(tmp_path, {"retry_prompt_mode": "same"})
        content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert 'retry_prompt_mode = "same"' in content

    def test_merges_with_existing(self, tmp_path) -> None:
        """Should merge with existing architect.toml, not overwrite."""
        (tmp_path / "architect.toml").write_text("[architect]\nmax_retries = 5\n", encoding="utf-8")
        write_config(tmp_path, {"retry_pause": 60})
        config = load_config(tmp_path)
        assert config.max_retries == 5
        assert config.retry_pause == 60

    def test_updates_existing_key(self, tmp_path) -> None:
        """Should update an existing key in architect.toml."""
        (tmp_path / "architect.toml").write_text("[architect]\nmax_retries = 3\n", encoding="utf-8")
        write_config(tmp_path, {"max_retries": 10})
        config = load_config(tmp_path)
        assert config.max_retries == 10

    def test_rejects_unknown_field(self, tmp_path) -> None:
        """Should raise ValueError for unknown field names."""
        import pytest

        with pytest.raises(ValueError, match="Unknown config field"):
            write_config(tmp_path, {"nonexistent_field": 42})

    def test_rejects_path_field(self, tmp_path) -> None:
        """Should raise ValueError for path fields."""
        import pytest

        with pytest.raises(ValueError, match="path field"):
            write_config(tmp_path, {"tasks_dir": "custom"})

    def test_rejects_unsupported_type(self, tmp_path) -> None:
        """Should raise TypeError for unsupported value types."""
        import pytest

        with pytest.raises(TypeError):
            write_config(tmp_path, {"max_retries": [1, 2, 3]})  # type: ignore

    def test_multiple_values(self, tmp_path) -> None:
        """Should write multiple values in one call."""
        write_config(
            tmp_path,
            {"max_retries": 7, "carry_context": False, "integrity": False},
        )
        config = load_config(tmp_path)
        assert config.max_retries == 7
        assert config.carry_context is False
        assert config.integrity is False

    def test_section_header_present(self, tmp_path) -> None:
        """Written file should have [architect] section header."""
        write_config(tmp_path, {"max_retries": 5})
        content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "[architect]" in content

    def test_write_config_with_string_project_dir(self, tmp_path) -> None:
        """Should accept a string project_dir and convert to Path."""
        result = write_config(str(tmp_path), {"max_retries": 5})
        assert result.exists()
        config = load_config(tmp_path)
        assert config.max_retries == 5


# ── Phase A: New config fields ──────────────────────────────────────────


class TestPhaseAConfigFields:
    """Tests for architect_model and last_scope fields added in Phase A."""

    def test_architect_model_default(self) -> None:
        """architect_model should default to empty string."""
        config = ArchitectConfig()
        assert config.architect_model == ""

    def test_last_scope_default(self) -> None:
        """last_scope should default to empty string."""
        config = ArchitectConfig()
        assert config.last_scope == ""

    def test_architect_model_persisted(self, tmp_path: Path) -> None:
        """architect_model should round-trip through write_config/load_config."""
        write_config(tmp_path, {"architect_model": "openrouter/test-model"})
        config = load_config(tmp_path)
        assert config.architect_model == "openrouter/test-model"

    def test_last_scope_persisted(self, tmp_path: Path) -> None:
        """last_scope should round-trip through write_config/load_config."""
        write_config(tmp_path, {"last_scope": "complex"})
        config = load_config(tmp_path)
        assert config.last_scope == "complex"

    def test_resolve_carries_new_fields(self) -> None:
        """resolve() should preserve architect_model and last_scope."""
        config = ArchitectConfig(architect_model="test-model", last_scope="simple")
        resolved = config.resolve(Path("/project/root"))
        assert resolved.architect_model == "test-model"
        assert resolved.last_scope == "simple"

    def test_token_budget_per_run_default(self) -> None:
        """token_budget_per_run should default to 0 (disabled)."""
        config = ArchitectConfig()
        assert config.token_budget_per_run == 0

    def test_token_budget_per_run_positive(self) -> None:
        """token_budget_per_run accepts positive values."""
        config = ArchitectConfig(token_budget_per_run=1_000_000)
        assert config.token_budget_per_run == 1_000_000

    def test_token_budget_per_run_rejects_negative(self) -> None:
        """token_budget_per_run rejects negative values (ge=0)."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            ArchitectConfig(token_budget_per_run=-1)

    def test_token_budget_per_run_persisted(self, tmp_path: Path) -> None:
        """token_budget_per_run round-trips through write_config/load_config."""
        write_config(tmp_path, {"token_budget_per_run": 500_000})
        config = load_config(tmp_path)
        assert config.token_budget_per_run == 500_000

    def test_token_budget_per_run_in_resolve(self) -> None:
        """resolve() preserves token_budget_per_run."""
        config = ArchitectConfig(token_budget_per_run=250_000)
        resolved = config.resolve(Path("/project/root"))
        assert resolved.token_budget_per_run == 250_000


# ---------------------------------------------------------------------------
# Notification config fields
# ---------------------------------------------------------------------------


class TestNotificationConfigFields:
    """Tests for notify_on_complete and notify_on_fail config fields."""

    def test_notify_on_complete_default_true(self) -> None:
        """notify_on_complete should default to True."""
        config = ArchitectConfig()
        assert config.notify_on_complete is True

    def test_notify_on_fail_default_true(self) -> None:
        """notify_on_fail should default to True."""
        config = ArchitectConfig()
        assert config.notify_on_fail is True

    def test_notify_on_complete_can_be_false(self) -> None:
        """notify_on_complete should accept False."""
        config = ArchitectConfig(notify_on_complete=False)
        assert config.notify_on_complete is False

    def test_notify_on_fail_can_be_false(self) -> None:
        """notify_on_fail should accept False."""
        config = ArchitectConfig(notify_on_fail=False)
        assert config.notify_on_fail is False

    def test_notify_fields_survive_resolve(self) -> None:
        """notify_on_complete and notify_on_fail should survive resolve()."""
        config = ArchitectConfig(notify_on_complete=False, notify_on_fail=False)
        resolved = config.resolve(Path("/project/root"))
        assert resolved.notify_on_complete is False
        assert resolved.notify_on_fail is False

    def test_notify_fields_load_from_toml(self, tmp_path: Path) -> None:
        """Should load notify flags from architect.toml."""
        (tmp_path / "architect.toml").write_text(
            "[architect]\nnotify_on_complete = false\nnotify_on_fail = false\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config.notify_on_complete is False
        assert config.notify_on_fail is False

    def test_notify_fields_round_trip(self, tmp_path: Path) -> None:
        """notify_on_complete and notify_on_fail round-trip through write/load."""
        write_config(tmp_path, {"notify_on_complete": False, "notify_on_fail": False})
        config = load_config(tmp_path)
        assert config.notify_on_complete is False
        assert config.notify_on_fail is False
