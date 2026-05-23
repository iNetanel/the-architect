"""Shared TUI constants.

Centralises magic numbers so every screen and widget uses the same values
for animation cadence, polling intervals, etc.
"""

from __future__ import annotations

# 10 FPS — fast enough to feel animated, slow enough not to chew CPU.
# Used by all spinner / rain animations across the TUI.
ANIMATION_TICK_INTERVAL: float = 0.1
