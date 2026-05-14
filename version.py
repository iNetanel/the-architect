# The Architect — Version
# Single source of truth for version, build, and release metadata.
# All other modules import from here — never hardcode versions elsewhere.
#
# Versioning scheme:
#   MAJOR.MINOR.PATCH (build BUILD)
#
#   MAJOR — breaking changes. Build floor jumps to MAJOR * 10000.
#   MINOR — new features, backwards compatible
#   PATCH — bug fixes
#   BUILD — global counter, increments with every completed task/change.
#           Never resets. Ever. Always 5 digits.
#
# Build floor by major version:
#   v1.x.x  build 10000+
#   v2.0.0  build 20000+
#   v3.0.0  build 30000+
#
# PyPI shows : 1.2.3
# CLI shows  : The Architect v1.2.3 (build 10261)
#
# To release:
#   1. Bump __version__ here and version in pyproject.toml
#   2. Bump __build__ here
#   3. Add entry to CHANGELOG.md
#   4. Push to main — if SemVer changed, CI creates clean tag v<ver>, creates
#      GitHub release "v<ver> (build <build>)", and requests PyPI approval.
#   5. Build-only pushes to main run CI only; no tag, GitHub release, or PyPI.
#   6. After PyPI upload — record SHA256 hashes in NOTICE (first release only)
#
# CI is intentionally not triggered by tag pushes. The main release workflow
# creates the clean version tag so one release produces one workflow run.
#
# For every PR — including docs and maintenance — increment __build__.
# If using an AI agent to contribute, instruct it to increment __build__
# once for each completed task/change. This is the intended workflow.

__version__ = "1.2.11"
__build__ = 10407
__author__ = "Netanel Eliav"
__email__ = "inetanel@me.com"
__repo__ = "https://github.com/inetanel/the-architect"
__website__ = "https://inetanel.com/projects/the-architect"
__license__ = "Apache-2.0"

# Full version string — shown in CLI --version output
__full_version__ = f"{__version__} (build {__build__})"

# User-facing banner
__banner__ = f"The Architect v{__full_version__}"
