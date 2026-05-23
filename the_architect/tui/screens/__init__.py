"""Screens package for the Textual TUI."""

from __future__ import annotations

from the_architect.tui.screens.circuit_screen import CircuitApp, run_circuit_screen
from the_architect.tui.screens.config import ConfigApp, run_config_screen
from the_architect.tui.screens.cost_screen import CostApp, run_cost_screen
from the_architect.tui.screens.deps_screen import DepsApp, run_deps_screen
from the_architect.tui.screens.diff_screen import DiffApp, run_diff_screen
from the_architect.tui.screens.doctor_screen import DoctorApp, run_doctor_screen
from the_architect.tui.screens.execution import ExecutionScreen
from the_architect.tui.screens.history_screen import HistoryApp, run_history_screen
from the_architect.tui.screens.list_screen import ListApp, run_list_screen
from the_architect.tui.screens.logs_screen import LogsApp, run_logs_screen
from the_architect.tui.screens.mode_selection import ModeSelectionApp, run_mode_selection
from the_architect.tui.screens.monitor_screen import MonitorApp, run_monitor_screen
from the_architect.tui.screens.pre_run_tabbed import PreRunScreen, PreRunValues, run_pre_run_tabbed
from the_architect.tui.screens.resume import ResumeApp, run_resume_screen
from the_architect.tui.screens.status_screen import StatusApp, run_status_screen
from the_architect.tui.screens.wait import WaitApp

__all__ = [
    "CircuitApp",
    "ConfigApp",
    "CostApp",
    "DepsApp",
    "DiffApp",
    "DoctorApp",
    "ExecutionScreen",
    "HistoryApp",
    "ListApp",
    "LogsApp",
    "ModeSelectionApp",
    "MonitorApp",
    "PreRunScreen",
    "PreRunValues",
    "ResumeApp",
    "StatusApp",
    "WaitApp",
    "run_circuit_screen",
    "run_config_screen",
    "run_cost_screen",
    "run_deps_screen",
    "run_diff_screen",
    "run_doctor_screen",
    "run_history_screen",
    "run_list_screen",
    "run_logs_screen",
    "run_mode_selection",
    "run_monitor_screen",
    "run_pre_run_tabbed",
    "run_resume_screen",
    "run_status_screen",
]
