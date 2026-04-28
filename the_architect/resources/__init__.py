"""Package resource loader for The Architect bundled files."""

from __future__ import annotations

from importlib.resources import files


def get_prompt(name: str) -> str:
    """Load a bundled prompt file by name (e.g. 'architect.md')."""
    return files("the_architect.resources.prompts").joinpath(name).read_text(encoding="utf-8")


def get_opencode_template() -> str:
    """Load the bundled opencode.json template."""
    return (
        files("the_architect.resources")
        .joinpath("opencode_template.json")
        .read_text(encoding="utf-8")
    )
