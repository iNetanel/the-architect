# Contributing to The Architect

Thank you for considering a contribution. The Architect is built to give developers
their time back — every contribution makes it better for everyone.

---

## Before You Start

- Check [existing issues](https://github.com/inetanel/the-architect/issues) — your idea or bug may already be tracked
- For large changes, open an issue first to discuss the approach
- Read this document fully — especially the build number section below

---

## Development Setup

```bash
# Clone
git clone https://github.com/inetanel/the-architect
cd the-architect

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Verify everything works
pytest tests/
architect --version
```

**Requirements:** Python 3.11+ and at least one supported provider installed and configured (`OpenCode`, `Codex CLI`, `Claude Code`, or `Gemini CLI`).

---

## The Build Number — Read This First

The Architect uses a **global build counter** in the project-root `version.py` that tracks
every agent operation across all sessions. It is not just a release counter — it is a
continuous record of cumulative effort and change history.

**Every PR must increment `__build__` in `version.py` (at the repo root — NOT
`the_architect/version.py`).**

This includes:
- Bug fixes
- New features
- Renamed files
- Removed comments
- Documentation updates
- Dependency bumps
- Any change at all — no exceptions

```python
# version.py (repo root) — bump __build__ for every PR
__version__ = "1.0.0"
__build__   = 10043   # <- always increment this
```

> `the_architect/version.py` is a different file. It reads the SemVer from
> installed package metadata at runtime and should not be edited by hand.

The build number **never resets** — not between patch versions, not between major releases.
It is a monotonically increasing integer that tells the full story of the project's history.

If you are using an AI agent to contribute, instruct it to increment `__build__` for
every file it reads, writes, or modifies. This is the intended and encouraged workflow.

---

## Making Changes

### Bug fix or maintenance

```bash
git checkout -b fix/short-description
# make your changes
# bump __build__ in version.py
pytest tests/
git commit -m "fix: short description (build XXXX)"
git push origin fix/short-description
# open a PR
```

### New feature

```bash
git checkout -b feat/short-description
# make your changes
# bump __build__ in version.py
# add tests
# update CHANGELOG.md
pytest tests/
git commit -m "feat: short description (build XXXX)"
git push origin feat/short-description
# open a PR
```

---

## Commit Message Format

```
type: short description (build XXXX)
```

Types: `fix`, `feat`, `docs`, `refactor`, `test`, `chore`

Include the build number in every commit message — it makes the history traceable.

---

## Code Standards

- **Python 3.11+** — use `tomllib` (built-in), not `tomli`
- **Type hints** on all public functions — no exceptions
- **Loguru** for logging — never `print()`
- **Pydantic v2** — use `model_validate`, not `parse_obj`
- **Ruff** for linting
- **Mypy** for type checking

```bash
# Run all checks before committing
ruff check .
mypy the_architect/
pytest tests/
```

---

## Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=the_architect --cov-report=term-missing

# Run a specific file
pytest tests/test_circuit.py -v
```

New behaviour needs test coverage. PRs without tests for new code will be asked to add them.

---

## What Makes a Good PR

- One concern per PR — do not bundle unrelated changes
- Tests included for new behaviour
- Build number bumped — always
- CHANGELOG updated for user-facing changes
- Clear description — what changed and why

---

## Reporting Bugs

Use the [bug report template](https://github.com/inetanel/the-architect/issues/new?template=bug_report.md).

Always include:
- `architect --version` output — version and build number
- Provider and its version
- Relevant terminal output or `SUCCESS.md` contents

---

## Questions

Open a [GitHub Discussion](https://github.com/inetanel/the-architect/discussions)
or reach out: [inetanel@me.com](mailto:inetanel@me.com)

---

## Attribution

By contributing, you agree your contributions are licensed under Apache License 2.0.
You will be credited in the project's contributor history.

The Architect was created by [Netanel Eliav](https://inetanel.com).
Original repository: [github.com/inetanel/the-architect](https://github.com/inetanel/the-architect)
