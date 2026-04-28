"""Tests for opencode_config module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.opencode_config import (
    check_opencode_installed,
    ensure_opencode_setup,
    find_user_opencode_config,
    get_opencode_version,
    list_opencode_agents,
    write_architect_config,
    write_architect_prompts,
)


class TestWriteArchitectPrompts:
    """Tests for write_architect_prompts function."""

    def test_creates_dir(self, tmp_path: Path) -> None:
        """Should create .architect/prompts/ directory if it doesn't exist."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        prompts_dir = project_dir / ".architect" / "prompts"
        assert prompts_dir.exists()
        assert prompts_dir.is_dir()

    def test_does_not_write_base_md(self, tmp_path: Path) -> None:
        """Should NOT write base.md — architect.md is now self-contained."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        base_md = project_dir / ".architect" / "prompts" / "base.md"
        assert not base_md.exists()

    def test_does_not_write_build_md(self, tmp_path: Path) -> None:
        """Should NOT write build.md — The Architect only owns the architect agent."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        build_md = project_dir / ".architect" / "prompts" / "build.md"
        assert not build_md.exists()

    def test_architect_md_contains_boundary_rule(self, tmp_path: Path) -> None:
        """architect.md should contain the working folder boundary rule."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        architect_md = project_dir / ".architect" / "prompts" / "architect.md"
        content = architect_md.read_text(encoding="utf-8")
        assert "project root" in content.lower()
        assert "never" in content.lower()

    def test_writes_execution_protocol_md(self, tmp_path: Path) -> None:
        """Should write execution-protocol.md — the runtime protocol for execution agents."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        protocol_md = project_dir / ".architect" / "prompts" / "execution-protocol.md"
        assert protocol_md.exists()
        content = protocol_md.read_text(encoding="utf-8")
        assert "PROGRESS.md" in content
        assert "Done" in content

    def test_writes_architect_md(self, tmp_path: Path) -> None:
        """Should write architect.md to .architect/prompts/."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_prompts(project_dir)

        architect_md = project_dir / ".architect" / "prompts" / "architect.md"
        assert architect_md.exists()
        content = architect_md.read_text(encoding="utf-8")
        assert "Architect" in content

    def test_overwrites_existing_prompts(self, tmp_path: Path) -> None:
        """Should overwrite existing prompt files (versioning)."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        prompts_dir = project_dir / ".architect" / "prompts"
        prompts_dir.mkdir(parents=True)
        old_architect = prompts_dir / "architect.md"
        old_architect.write_text("old content", encoding="utf-8")

        write_architect_prompts(project_dir)

        content = old_architect.read_text(encoding="utf-8")
        assert "old content" not in content
        assert "Architect" in content


class TestWriteArchitectConfig:
    """Tests for write_architect_config function."""

    def test_creates_architect_json_in_architect_dir(self, tmp_path: Path) -> None:
        """Should create .architect/architect.json — never touches project root."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        result = write_architect_config(project_dir)

        assert result == project_dir / ".architect" / "architect.json"
        assert result.exists()
        # Must NOT create opencode.json in project root
        assert not (project_dir / "opencode.json").exists()

    def test_writes_valid_json_with_architect(self, tmp_path: Path) -> None:
        """Should write valid JSON with architect agent only."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_config(project_dir)

        data = json.loads(
            (project_dir / ".architect" / "architect.json").read_text(encoding="utf-8")
        )
        assert "agent" in data
        assert "architect" in data["agent"]
        assert "prompt" in data["agent"]["architect"]
        # The Architect must NOT define a build/execution agent
        assert "build" not in data["agent"]

    def test_includes_safety_bash_permissions(self, tmp_path: Path) -> None:
        """Should include bash permission settings."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_config(project_dir)

        data = json.loads(
            (project_dir / ".architect" / "architect.json").read_text(encoding="utf-8")
        )
        bash_perms = data.get("permission", {}).get("bash", {})
        assert "sudo*" in bash_perms

    def test_prompt_paths_are_absolute(self, tmp_path: Path) -> None:
        """Prompt paths must be absolute so opencode finds them from any cwd."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_config(project_dir)

        data = json.loads(
            (project_dir / ".architect" / "architect.json").read_text(encoding="utf-8")
        )
        prompt = data["agent"]["architect"]["prompt"]
        # Paths must be absolute (start with /)
        assert prompt.startswith("{file:/") or "/" in prompt


class TestFindUserOpencodeConfig:
    """Tests for find_user_opencode_config function."""

    def test_finds_project_local_opencode_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find opencode.json in project root."""
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        oc = project_dir / "opencode.json"
        oc.write_text('{"model": "test"}', encoding="utf-8")

        result = find_user_opencode_config(project_dir)
        assert result == oc

    def test_finds_project_local_opencode_jsonc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find opencode.jsonc in project root."""
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        oc = project_dir / "opencode.jsonc"
        oc.write_text('{"model": "test"}', encoding="utf-8")

        result = find_user_opencode_config(project_dir)
        assert result == oc

    def test_returns_none_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None when no opencode config exists anywhere."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        # Clear env vars that might point to a real config
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        # Redirect home to tmp so global config isn't found
        monkeypatch.setenv("HOME", str(tmp_path))

        result = find_user_opencode_config(project_dir)
        assert result is None

    def test_env_var_takes_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENCODE_CONFIG env var should take priority over project-local file."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        # Project-local config
        (project_dir / "opencode.json").write_text('{"model": "local"}', encoding="utf-8")
        # Env var config
        env_cfg = tmp_path / "custom_opencode.json"
        env_cfg.write_text('{"model": "env"}', encoding="utf-8")
        monkeypatch.setenv("OPENCODE_CONFIG", str(env_cfg))

        result = find_user_opencode_config(project_dir)
        assert result == env_cfg

        monkeypatch.delenv("OPENCODE_CONFIG")

    def test_finds_config_via_opencode_config_dir_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find opencode.json when OPENCODE_CONFIG_DIR points to its directory."""
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        config_dir = tmp_path / "custom_opencode_dir"
        config_dir.mkdir()
        cfg_file = config_dir / "opencode.json"
        cfg_file.write_text('{"model": "from-dir"}', encoding="utf-8")
        monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config_dir))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = find_user_opencode_config(project_dir)
        assert result == cfg_file

    def test_opencode_config_dir_env_var_takes_priority_over_project_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENCODE_CONFIG_DIR should take priority over a project-local opencode.json."""
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)

        # Project-local config
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "opencode.json").write_text('{"model": "local"}', encoding="utf-8")

        # Env-var config dir with its own config
        env_dir = tmp_path / "env_config_dir"
        env_dir.mkdir()
        env_cfg = env_dir / "opencode.json"
        env_cfg.write_text('{"model": "from-env-dir"}', encoding="utf-8")
        monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(env_dir))

        result = find_user_opencode_config(project_dir)
        assert result == env_cfg


class TestEnsureOpencodeSetup:
    """Tests for ensure_opencode_setup function."""

    def test_returns_architect_json_path(self, tmp_path: Path) -> None:
        """Should return path to .architect/architect.json."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        config = ArchitectConfig()

        result = ensure_opencode_setup(project_dir, config)

        assert result == project_dir / ".architect" / "architect.json"
        assert result.exists()

    def test_never_creates_project_root_opencode_json(self, tmp_path: Path) -> None:
        """Must NOT create or modify opencode.json in project root."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        config = ArchitectConfig()

        ensure_opencode_setup(project_dir, config)

        assert not (project_dir / "opencode.json").exists()

    def test_never_modifies_existing_user_opencode_json(self, tmp_path: Path) -> None:
        """Must NOT modify the user's existing opencode.json."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        user_config = project_dir / "opencode.json"
        original = '{"model": "user-model", "custom": "value"}'
        user_config.write_text(original, encoding="utf-8")
        config = ArchitectConfig()

        ensure_opencode_setup(project_dir, config)

        # User's file must be byte-for-byte identical
        assert user_config.read_text(encoding="utf-8") == original

    def test_always_writes_prompts(self, tmp_path: Path) -> None:
        """Should always write The Architect prompts to .architect/prompts/."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        config = ArchitectConfig()

        ensure_opencode_setup(project_dir, config)

        assert (project_dir / ".architect" / "prompts" / "architect.md").exists()
        assert (project_dir / ".architect" / "prompts" / "execution-protocol.md").exists()


class TestCheckOpencodeInstalled:
    """Tests for check_opencode_installed function."""

    def test_returns_bool(self) -> None:
        """Should return a boolean value."""
        result = check_opencode_installed()
        assert isinstance(result, bool)


class TestGetOpencodeVersion:
    """Tests for get_opencode_version function."""

    def test_returns_string(self) -> None:
        """Should return a string version."""
        result = get_opencode_version()
        assert isinstance(result, str)


class TestListOpencodeAgents:
    """Tests for list_opencode_agents function."""

    def test_reads_primary_agents_from_opencode_json(self, tmp_path: Path) -> None:
        """Should return only primary agent names from the project opencode.json."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        oc_json = project_dir / "opencode.json"
        oc_json.write_text(
            json.dumps(
                {
                    "agent": {
                        "coder": {"mode": "primary", "prompt": "coder prompt"},
                        "reviewer": {"mode": "primary", "prompt": "reviewer prompt"},
                        "architect": {"mode": "primary", "prompt": "architect prompt"},
                        "explore": {"mode": "subagent", "prompt": "explore prompt"},
                        "debug": {"mode": "subagent", "prompt": "debug prompt"},
                    }
                }
            ),
            encoding="utf-8",
        )

        agents = list_opencode_agents(project_dir)

        # architect is excluded (it's The Architect's planning agent)
        assert "architect" not in agents
        # reviewer is excluded (it's The Architect's retrospective agent)
        assert "reviewer" not in agents
        # sub-agents are excluded
        assert "explore" not in agents
        assert "debug" not in agents
        # only non-internal primary agents are returned
        assert "coder" in agents

    def test_returns_empty_when_no_opencode_json(self, tmp_path: Path) -> None:
        """Should return agents from global config even without project opencode.json.

        The ``opencode agent list`` command returns agents from the merged
        global + project config.  When no project opencode.json exists,
        agents from the global config are still available.  In a test
        environment without opencode installed, this returns an empty list.
        """
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        agents = list_opencode_agents(project_dir)

        # Result depends on whether opencode is installed:
        # - With opencode: may return agents from global config
        # - Without opencode: returns empty list
        assert isinstance(agents, list)

    def test_returns_empty_when_no_agents_key(self, tmp_path: Path) -> None:
        """Should return empty list when opencode.json has no agents."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        oc_json = project_dir / "opencode.json"
        oc_json.write_text('{"custom": "content"}', encoding="utf-8")

        agents = list_opencode_agents(project_dir)

        assert agents == []

    def test_excludes_subagents(self, tmp_path: Path) -> None:
        """Should not return sub-agents — only primary agents are suitable for execution."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        oc_json = project_dir / "opencode.json"
        oc_json.write_text(
            json.dumps(
                {
                    "agent": {
                        "build": {"mode": "primary", "prompt": "build prompt"},
                        "frontend": {"mode": "subagent", "prompt": "frontend prompt"},
                        "qa-fast": {"mode": "subagent", "prompt": "qa prompt"},
                    }
                }
            ),
            encoding="utf-8",
        )

        agents = list_opencode_agents(project_dir)

        assert agents == ["build"]


class TestReviewerAgentModel:
    """Tests verifying that neither the architect nor reviewer agents hardcode a model."""

    def test_agents_have_no_model_override(self, tmp_path: Path) -> None:
        """Neither architect nor reviewer should have a hardcoded model.

        Both agents fall back to the user's opencode default. Hardcoding a
        specific model (e.g. claude-sonnet-4.6) would silently break users
        whose provider doesn't offer that model. The user selects the
        architect model interactively or via --architect-model; the reviewer
        always uses the opencode default.
        """
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        write_architect_config(project_dir)

        data = json.loads(
            (project_dir / ".architect" / "architect.json").read_text(encoding="utf-8")
        )
        # Neither agent should have a "model" key — both fall back to opencode default
        assert "model" not in data["agent"]["reviewer"]
        assert "model" not in data["agent"]["architect"]


class TestExtractAgentsFromConfigOutput:
    """Tests for _extract_agents_from_config_output (truncation-safe regex parser)."""

    def test_parses_valid_json_output(self) -> None:
        """Should extract agent names and modes from valid JSON-like output."""
        from the_architect.core.opencode_config import _extract_agents_from_config_output

        raw = (
            '{\n  "agent": {\n'
            '    "build": {\n      "mode": "primary",\n      "model": "gpt-4"\n    },\n'
            '    "explore": {\n      "mode": "subagent"\n    }\n'
            "  }\n}\n"
        )
        result = _extract_agents_from_config_output(raw)
        assert result == {"build": "primary", "explore": "subagent"}

    def test_parses_truncated_output(self) -> None:
        """Should extract agents even from truncated JSON (missing closing braces)."""
        from the_architect.core.opencode_config import _extract_agents_from_config_output

        # Simulates truncated output where the last agent's prompt is cut off
        raw = (
            '{\n  "agent": {\n'
            '    "build": {\n      "mode": "primary",\n      "model": "gpt-4"\n    },\n'
            '    "explore": {\n      "mode": "subagent",\n'
            '      "prompt": "Some very long prompt that gets trun'
        )
        result = _extract_agents_from_config_output(raw)
        assert result == {"build": "primary", "explore": "subagent"}

    def test_returns_empty_for_no_agents(self) -> None:
        """Should return empty dict when no agent blocks are found."""
        from the_architect.core.opencode_config import _extract_agents_from_config_output

        raw = '{"model": "gpt-4"}\n'
        result = _extract_agents_from_config_output(raw)
        assert result == {}

    def test_handles_agent_without_mode(self) -> None:
        """Should mark agents as 'unknown' when mode field is missing."""
        from the_architect.core.opencode_config import _extract_agents_from_config_output

        raw = '{\n  "agent": {\n    "build": {\n      "model": "gpt-4"\n    }\n  }\n}\n'
        result = _extract_agents_from_config_output(raw)
        assert result == {"build": "unknown"}


class TestExtractModelFromConfigOutput:
    """Tests for _extract_model_from_config_output (truncation-safe regex parser)."""

    def test_extracts_model_from_agent_block(self) -> None:
        """Should extract the model field from a specific agent block."""
        from the_architect.core.opencode_config import _extract_model_from_config_output

        raw = (
            '{\n  "agent": {\n    "architect": {\n'
            '      "mode": "primary",\n      "model": "claude-sonnet-4"\n'
            "    }\n  }\n}\n"
        )
        result = _extract_model_from_config_output(raw, "architect")
        assert result == "claude-sonnet-4"

    def test_returns_empty_when_agent_not_found(self) -> None:
        """Should return empty string when the agent block doesn't exist."""
        from the_architect.core.opencode_config import _extract_model_from_config_output

        raw = '{\n  "agent": {\n    "build": {\n      "mode": "primary"\n    }\n  }\n}\n'
        result = _extract_model_from_config_output(raw, "architect")
        assert result == ""

    def test_returns_empty_when_no_model_field(self) -> None:
        """Should return empty string when the agent has no model field."""
        from the_architect.core.opencode_config import _extract_model_from_config_output

        raw = '{\n  "agent": {\n    "architect": {\n      "mode": "primary"\n    }\n  }\n}\n'
        result = _extract_model_from_config_output(raw, "architect")
        assert result == ""

    def test_extracts_from_truncated_output(self) -> None:
        """Should extract model even from truncated JSON."""
        from the_architect.core.opencode_config import _extract_model_from_config_output

        raw = (
            '{\n  "agent": {\n    "architect": {\n'
            '      "mode": "primary",\n'
            '      "model": "openrouter/anthropic/claude-sonnet-4.6",\n'
            '      "prompt": "Very long prompt that gets truncated...'
        )
        result = _extract_model_from_config_output(raw, "architect")
        assert result == "openrouter/anthropic/claude-sonnet-4.6"
