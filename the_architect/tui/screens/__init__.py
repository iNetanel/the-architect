"""Screens package for the Textual TUI."""

from __future__ import annotations

from the_architect.tui.screens.circuit_screen import CircuitApp, run_circuit_screen
from the_architect.tui.screens.config import ConfigApp, run_config_screen
from the_architect.tui.screens.execution import ExecutionScreen
from the_architect.tui.screens.list_screen import ListApp, run_list_screen
from the_architect.tui.screens.logs_screen import LogsApp, run_logs_screen
from the_architect.tui.screens.mode_selection import ModeSelectionApp, run_mode_selection
from the_architect.tui.screens.monitor_screen import MonitorApp, run_monitor_screen
from the_architect.tui.screens.resume import ResumeApp, run_resume_screen
from the_architect.tui.screens.status_screen import StatusApp, run_status_screen
from the_architect.tui.screens.wait import WaitApp

__all__ = [
    "CircuitApp",
    "ConfigApp",
    "ExecutionScreen",
    "ListApp",
    "LogsApp",
    "ModeSelectionApp",
    "MonitorApp",
    "ResumeApp",
    "StatusApp",
    "WaitApp",
    "run_circuit_screen",
    "run_config_screen",
    "run_list_screen",
    "run_logs_screen",
    "run_mode_selection",
    "run_monitor_screen",
    "run_resume_screen",
    "run_status_screen",
]
