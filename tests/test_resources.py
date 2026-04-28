"""Tests for bundled package resources."""

from __future__ import annotations

import json


def test_architect_prompt_loads() -> None:
    """Verify architect prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("architect.md")
    assert len(prompt) > 100
    assert "goal" in prompt.lower()


def test_execution_protocol_loads() -> None:
    """Verify execution protocol prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution-protocol.md")
    assert len(prompt) > 100
    assert "PROGRESS.md" in prompt
    assert "Done" in prompt


def test_opencode_template_loads() -> None:
    """Verify opencode.json template loads and parses correctly."""
    from the_architect.resources import get_opencode_template

    template = get_opencode_template()
    config = json.loads(template)
    assert "agent" in config
    assert "architect" in config["agent"]
    # The Architect only defines the architect agent — execution uses user's opencode
    assert "build" not in config["agent"]
