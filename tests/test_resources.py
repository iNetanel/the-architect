"""Tests for bundled package resources."""

from __future__ import annotations

import json


def test_architect_prompt_loads() -> None:
    """Verify architect prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("architect.md")
    assert len(prompt) > 100
    assert "goal" in prompt.lower()


def test_architect_prompt_requires_bounded_exploration_plans() -> None:
    """Architect prompt should guide focused research without prescribing internals."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("architect.md")
    assert "Exploration Plan" in prompt
    assert "guide, do not constrain" in prompt
    assert "Stop condition" in prompt
    assert "record the final contract in PROGRESS.md" in prompt


def test_execution_protocol_loads() -> None:
    """Verify execution protocol prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution-protocol.md")
    assert len(prompt) > 100
    assert "PROGRESS.md" in prompt
    assert "Done" in prompt


def test_execution_protocol_requires_focused_codebase_discovery() -> None:
    """Execution protocol should make executor discovery explicit and bounded."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution-protocol.md")
    assert "Focused Codebase Discovery Before Implementation" in prompt
    assert "smallest relevant part of the codebase" in prompt
    assert "Do not perform broad, unfocused repo exploration" in prompt
    assert "record the final contract in PROGRESS.md" in prompt


def test_reviewer_prompt_reviews_outcomes_not_suggested_implementation() -> None:
    """Reviewer should not punish correct implementation choices that differ from suggestions."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("reviewer.md")
    assert "Review outcomes first" in prompt
    assert "Do not create a fix-up task solely because the executor" in prompt
    assert "Missing documentation of a shared contract" in prompt
    assert "## Exploration Plan" in prompt


def test_opencode_template_loads() -> None:
    """Verify opencode.json template loads and parses correctly."""
    from the_architect.resources import get_opencode_template

    template = get_opencode_template()
    config = json.loads(template)
    assert "agent" in config
    assert "architect" in config["agent"]
    # The Architect only defines the architect agent — execution uses user's opencode
    assert "build" not in config["agent"]
