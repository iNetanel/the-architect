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


def test_architect_prompt_requires_memory_enrichment() -> None:
    """Architect prompt should require durable project intelligence updates."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("architect.md")
    assert "Minimum ARCHITECT.md enrichment contract" in prompt
    assert "Each repo/component's mission, ownership boundary, and authority" in prompt
    assert "Build, test, lint, typecheck, run, preview" in prompt
    assert "what not to do" in prompt
    assert "ARCHITECT.md is project-level memory, not current-goal memory" in prompt


def test_architect_prompt_requires_goal_specific_instructions() -> None:
    """Architect prompt should keep INSTRUCTIONS.md focused on the current goal."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("architect.md")
    assert "Goal-Specific INSTRUCTIONS.md" in prompt
    assert "current task package only" in prompt
    assert "do not duplicate project-level memory" in prompt
    assert "Cross-task dependencies" in prompt


def test_execution_protocol_loads() -> None:
    """Verify execution protocol prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution.md")
    assert len(prompt) > 100
    assert "PROGRESS.md" in prompt
    assert "Done" in prompt


def test_intelligence_prompt_loads() -> None:
    """Verify project intelligence prompt loads correctly."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("intelligence.md")
    assert len(prompt) > 100
    assert "Project Intelligence Curator" in prompt
    assert "Edit only `ARCHITECT.md`" in prompt
    assert "Do not create task files" in prompt


def test_execution_protocol_requires_focused_codebase_discovery() -> None:
    """Execution protocol should make executor discovery explicit and bounded."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution.md")
    assert "Focused Codebase Discovery Before Implementation" in prompt
    assert "smallest relevant part of the codebase" in prompt
    assert "Do not perform broad, unfocused repo exploration" in prompt
    assert "record the final contract in PROGRESS.md" in prompt


def test_execution_protocol_requires_strong_verification() -> None:
    """Execution protocol should forbid assumed success and require UI checks."""
    from the_architect.resources import get_prompt

    prompt = get_prompt("execution.md")
    assert "Verification Discipline" in prompt
    assert "Do not assume anything works" in prompt
    assert "If a required verification tool or dependency is missing" in prompt
    assert "UI and Frontend Changes" in prompt
    assert "leave the task Pending if the unverified behaviour is central" in prompt


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
    assert "intelligence" in config["agent"]
    assert "reviewer" in config["agent"]
    # Execution uses user's opencode agents, not The Architect's internal agents.
    assert "build" not in config["agent"]
