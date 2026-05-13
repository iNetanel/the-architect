"""Deep project intelligence pass for pre-planning memory curation."""

from __future__ import annotations

import importlib.resources as resources
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from the_architect.config import ArchitectConfig
from the_architect.core.architect_md import read_architect_md
from the_architect.core.provider import ArchitectProvider
from the_architect.core.runner import StreamRenderer, stream_provider
from the_architect.core.structure import StructureReport, format_structure_for_prompt


@dataclass(frozen=True)
class IntelligenceAssessment:
    """Decision for whether model-based project intelligence should run."""

    should_run: bool
    reasons: list[str] = field(default_factory=list)


def assess_architect_md_quality(project_dir: Path, content: str) -> IntelligenceAssessment:
    """Assess whether ``ARCHITECT.md`` needs a deep model-based refresh.

    The deterministic pass already captures manifests, docs, CI, and runtime
    locations. This gate asks for a provider-model pass only when durable memory
    is still likely too shallow for complex planning.

    Args:
        project_dir: Project root.
        content: Current ``ARCHITECT.md`` content.

    Returns:
        Assessment containing a run decision and human-readable reasons.
    """
    reasons: list[str] = []
    normalized = content.lower()

    if not content.strip():
        return IntelligenceAssessment(True, ["ARCHITECT.md is missing or empty"])

    placeholder_count = normalized.count("_no ") + normalized.count("not recorded yet")
    if placeholder_count >= 3:
        reasons.append("several durable memory sections are still placeholders")

    evidence_checks = [
        (project_dir / "pyproject.toml", "root python project", "root project metadata missing"),
        (project_dir / "package.json", "javascript", "root package metadata missing"),
        (project_dir / "documentation", "documentation/", "documentation directory missing"),
        (project_dir / "docs", "docs/", "docs directory missing"),
        (project_dir / ".github" / "workflows", ".github/workflows", "CI workflow memory missing"),
        (project_dir / "tests", "tests/", "test suite memory missing"),
    ]
    for path, expected_text, reason in evidence_checks:
        if path.exists() and expected_text not in normalized:
            reasons.append(reason)

    # If generated memory exists but no human/model-curated durable knowledge
    # remains in important sections, ask the model to inspect architecture.
    if "### auto-detected project intelligence" in normalized:
        for section in ("## Key Flows", "## Shared Contracts", "## Known Constraints"):
            body = _section_body(content, section)
            body_without_generated = body.split("### Auto-Detected Project Intelligence", 1)[0]
            if "_No " in body_without_generated or len(body_without_generated.strip()) < 40:
                reasons.append(f"{section.removeprefix('## ')} has little curated knowledge")

    return IntelligenceAssessment(bool(reasons), reasons)


def build_intelligence_instruction(
    project_dir: Path,
    structure_report: StructureReport,
    project_context: str,
    architect_md_content: str,
    reasons: list[str],
) -> str:
    """Build the instruction sent to the project intelligence curator.

    Args:
        project_dir: Project root.
        structure_report: Deterministic structure report.
        project_context: Bounded project context from the normal planner gatherer.
        architect_md_content: Current durable memory content.
        reasons: Quality-gate reasons that triggered the pass.

    Returns:
        Provider instruction string.
    """
    reason_block = "\n".join(f"- {reason}" for reason in reasons) or "- Manual refresh requested."
    structure_prompt = format_structure_for_prompt(structure_report)
    return f"""# Project Intelligence Refresh

Project root: `{project_dir}`

The deterministic pre-planning quality gate found these issues:

{reason_block}

Update `ARCHITECT.md` so the next planner run starts with accurate durable project knowledge.

## Current ARCHITECT.md

```md
{architect_md_content}
```

## Deterministic Structure Report

{structure_prompt}

## Bounded Project Context

{project_context}

Remember: edit only `ARCHITECT.md`; do not create task files or implementation changes.
"""


async def refresh_project_intelligence(
    *,
    project_dir: Path,
    config: ArchitectConfig,
    provider: ArchitectProvider,
    structure_report: StructureReport,
    model_override: str | None = None,
    log_path: Path | None = None,
    renderer: StreamRenderer | None = None,
) -> IntelligenceAssessment:
    """Run the optional provider-model project intelligence pass when needed.

    The pass is intentionally non-fatal: if the provider fails, normal planning
    continues with deterministic memory rather than blocking the user.

    Args:
        project_dir: Project root.
        config: Architect configuration.
        provider: Active provider.
        structure_report: Deterministic structure report already written to memory.
        model_override: Optional model selected for high-reasoning architect work.
        log_path: Optional log path for the intelligence run.
        renderer: Optional renderer for live output.

    Returns:
        The assessment that decided whether a deep pass was needed.
    """
    architect_md_content = read_architect_md(project_dir) or ""
    assessment = assess_architect_md_quality(project_dir, architect_md_content)
    if not assessment.should_run:
        logger.info("Project intelligence pass skipped; ARCHITECT.md passed quality gate")
        return assessment

    from the_architect.core.planner import gather_project_context
    from the_architect.core.provider_setup import ensure_provider_setup

    logger.info(f"Running project intelligence pass: {', '.join(assessment.reasons)}")
    ensure_provider_setup(provider, project_dir, config)
    project_context = gather_project_context(project_dir, provider=provider)
    instruction = build_intelligence_instruction(
        project_dir=project_dir,
        structure_report=structure_report,
        project_context=project_context,
        architect_md_content=architect_md_content,
        reasons=assessment.reasons,
    )

    config_override: Path | None = None
    agent_override: str | None = None
    if provider.supports_agents():
        config_override = project_dir / ".architect" / "architect.json"
        agent_override = "intelligence"
    else:
        prompt = _read_intelligence_prompt()
        instruction = f"{prompt}\n\n---\n\n{instruction}"

    stream_result = await stream_provider(
        instruction=instruction,
        project_dir=project_dir,
        provider=provider,
        model_override=model_override or config.standalone_mode or None,
        agent_override=agent_override,
        log_path=log_path,
        config_override=config_override,
        renderer=renderer,
    )
    if stream_result.exit_code != 0:
        logger.warning(
            f"Project intelligence pass exited with code {stream_result.exit_code}; "
            "continuing with deterministic memory"
        )
    return assessment


def _read_intelligence_prompt() -> str:
    """Read the packaged intelligence prompt."""
    package_prompts = resources.files("the_architect.resources.prompts")
    source = package_prompts / "intelligence.md"
    return source.read_text(encoding="utf-8")


def _section_body(content: str, heading: str) -> str:
    """Return the body under a level-two Markdown heading."""
    marker = f"{heading}\n"
    if marker not in content:
        return ""
    after = content.split(marker, 1)[1]
    if "\n## " in after:
        return after.split("\n## ", 1)[0]
    return after
