"""Tests for the success summary writer."""

from __future__ import annotations

from pathlib import Path

from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.success import (
    RetrospectiveRound,
    _fmt_duration,
    _fmt_model,
    _fmt_tokens,
    notify_run_completion,
    print_success_summary,
    write_success_md,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFmtTokens:
    """Tests for _fmt_tokens()."""

    def test_small_number(self) -> None:
        """Should return raw number for < 1000."""
        assert _fmt_tokens(42) == "42"

    def test_zero(self) -> None:
        """Should return '0' for zero."""
        assert _fmt_tokens(0) == "0"

    def test_thousands(self) -> None:
        """Should return K format for >= 1000."""
        assert _fmt_tokens(12345) == "12.3K"

    def test_exact_thousand(self) -> None:
        """Should return K format for exactly 1000."""
        assert _fmt_tokens(1000) == "1.0K"


class TestFmtDuration:
    """Tests for _fmt_duration()."""

    def test_negative_seconds(self) -> None:
        """Should return dash for negative duration."""
        assert _fmt_duration(-1.0) == "—"

    def test_zero_seconds(self) -> None:
        """Should return '0:00' for zero."""
        assert _fmt_duration(0.0) == "0:00"

    def test_minutes_seconds(self) -> None:
        """Should return M:SS format for durations < 1 hour."""
        assert _fmt_duration(125.0) == "2:05"

    def test_hours_format(self) -> None:
        """Should return H:MM:SS format for durations >= 1 hour."""
        assert _fmt_duration(3661.0) == "1:01:01"

    def test_exact_hour(self) -> None:
        """Should return H:MM:SS format for exactly 1 hour."""
        assert _fmt_duration(3600.0) == "1:00:00"


class TestFmtModel:
    """Tests for _fmt_model()."""

    def test_empty_string(self) -> None:
        """Should return dash for empty model."""
        assert _fmt_model("") == "—"

    def test_anthropic_prefix(self) -> None:
        """Should strip anthropic/ prefix."""
        assert _fmt_model("anthropic/claude-sonnet-4") == "claude-sonnet-4"

    def test_openai_prefix(self) -> None:
        """Should strip openai/ prefix."""
        assert _fmt_model("openai/gpt-4o") == "gpt-4o"

    def test_openrouter_prefix(self) -> None:
        """Should strip openrouter/ prefix."""
        assert _fmt_model("openrouter/z-ai/glm-5.1") == "z-ai/glm-5.1"

    def test_no_prefix(self) -> None:
        """Should return full name when no known prefix."""
        assert _fmt_model("claude-sonnet-4") == "claude-sonnet-4"


# ---------------------------------------------------------------------------
# SUMMARY.md writer
# ---------------------------------------------------------------------------


class TestWriteSuccessMd:
    """Tests for write_success_md()."""

    def test_writes_summary_md_file(self, tmp_path: Path) -> None:
        """Should write SUMMARY.md inside the tasks directory."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=120.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        path = write_success_md(tmp_path, results, 120.0, total_tokens)

        assert path == tmp_path / "tasks" / "SUMMARY.md"
        assert path.name == "SUMMARY.md"
        assert path.exists()

        content = path.read_text(encoding="utf-8")
        assert "# The Architect — Run Summary" in content
        assert "T01" in content
        assert "5.5K" in content  # 5500 total tokens formatted

    def test_includes_original_goal_when_provided(self, tmp_path: Path) -> None:
        """Should include the package goal in SUMMARY.md when provided."""
        path = write_success_md(
            tmp_path,
            [TaskResult(prefix="T01", title="Test", status="done")],
            1.0,
            TokenUsage(),
            original_goal="Build the thing",
        )
        content = path.read_text(encoding="utf-8")
        assert "## Goal" in content
        assert "Build the thing" in content

    def test_includes_attempts_and_model_columns(self, tmp_path: Path) -> None:
        """Should include Attempts and Model columns in the task table."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=3,
                tokens=TokenUsage(input_tokens=1000, output_tokens=500),
                model="anthropic/claude-sonnet-4",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=1000, output_tokens=500)
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "| Attempts |" in content
        assert "| Model |" in content
        assert "3" in content  # attempts count
        assert "claude-sonnet-4" in content  # model name (stripped prefix)

    def test_includes_token_breakdown(self, tmp_path: Path) -> None:
        """Should include token breakdown when total > 0."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(
                    input_tokens=10000,
                    output_tokens=2000,
                    cache_read_tokens=5000,
                ),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(
            input_tokens=10000,
            output_tokens=2000,
            cache_read_tokens=5000,
        )
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "Token breakdown" in content
        assert "input" in content
        assert "output" in content
        assert "cache read" in content

    def test_includes_cache_write_tokens(self, tmp_path: Path) -> None:
        """Should include cache write in token breakdown when > 0."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(
                    input_tokens=10000,
                    output_tokens=2000,
                    cache_read_tokens=5000,
                    cache_write_tokens=3000,
                ),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(
            input_tokens=10000,
            output_tokens=2000,
            cache_read_tokens=5000,
            cache_write_tokens=3000,
        )
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "cache write" in content

    def test_no_token_breakdown_when_zero(self, tmp_path: Path) -> None:
        """Should NOT include token breakdown when total is 0."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage()
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "Token breakdown" not in content

    def test_includes_insights_with_tokens(self, tmp_path: Path) -> None:
        """Should include token-related insights when tokens > 0."""
        results = [
            TaskResult(
                prefix="T01",
                title="First task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
            TaskResult(
                prefix="T02",
                title="Second task",
                status="done",
                duration_seconds=120.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=10000, output_tokens=1000),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=15000, output_tokens=1500)
        path = write_success_md(tmp_path, results, 180.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "Avg tokens per task" in content
        assert "Throughput" in content
        assert "Most tokens" in content

    def test_skipped_tasks_show_dash_tokens(self, tmp_path: Path) -> None:
        """Skipped tasks should show '—' for tokens in the table."""
        results = [
            TaskResult(
                prefix="T01",
                title="Skipped task",
                status="skipped",
                tokens=TokenUsage(),
            ),
        ]
        total_tokens = TokenUsage()
        path = write_success_md(tmp_path, results, 0.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        # Skipped task row should have — for tokens
        assert "○ Skipped" in content

    def test_includes_rate_limit_hits(self, tmp_path: Path) -> None:
        """Should include rate limit hit count in Totals."""
        results = [
            TaskResult(
                prefix="T01",
                title="Rate limited task",
                status="done",
                duration_seconds=60.0,
                attempts=2,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
                rate_limit_hit=True,
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "Rate limits hit" in content
        assert "T01" in content

    def test_includes_retries_in_totals(self, tmp_path: Path) -> None:
        """Should include retry count in Totals section."""
        results = [
            TaskResult(
                prefix="T01",
                title="First task",
                status="done",
                duration_seconds=60.0,
                attempts=3,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
            TaskResult(
                prefix="T02",
                title="Second task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=10000, output_tokens=1000)
        path = write_success_md(tmp_path, results, 120.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        # 3 + 1 = 4 total attempts, 4 - 2 tasks = 2 retries
        assert "Retries:** 2 across 2 tasks" in content

    def test_most_retries_insight(self, tmp_path: Path) -> None:
        """Should include Most retries insight when a task had >1 attempt."""
        results = [
            TaskResult(
                prefix="T01",
                title="First task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
            TaskResult(
                prefix="T02",
                title="Retried task",
                status="done",
                duration_seconds=120.0,
                attempts=4,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=10000, output_tokens=1000)
        path = write_success_md(tmp_path, results, 180.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "Most retries" in content
        assert "T02" in content
        assert "4 attempts" in content


class TestWriteSuccessMdRetrospective:
    """Tests for retrospective section in SUMMARY.md."""

    def test_retrospective_section_present(self, tmp_path: Path) -> None:
        """Should include Retrospective section when rounds are provided."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=2,
                fixes_planned=2,
                tasks_created=["R01", "R02"],
                duration_seconds=90.0,
            ),
        ]
        path = write_success_md(
            tmp_path, results, 60.0, total_tokens, retrospective_rounds=retro_rounds
        )
        content = path.read_text(encoding="utf-8")

        assert "## Retrospective" in content
        assert "R01" in content
        assert "R02" in content
        assert "2" in content  # issues found

    def test_retrospective_no_issues(self, tmp_path: Path) -> None:
        """Should show dash for fix-up tasks when no issues found."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
                duration_seconds=45.0,
            ),
        ]
        path = write_success_md(
            tmp_path, results, 60.0, total_tokens, retrospective_rounds=retro_rounds
        )
        content = path.read_text(encoding="utf-8")

        assert "## Retrospective" in content
        assert "—" in content  # no fix-up tasks

    def test_retrospective_validation_failure_details(self, tmp_path: Path) -> None:
        """Should include validation failure reasons and unresolved tasks."""
        results = [TaskResult(prefix="T01", title="Test task", status="failed")]
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
                validation_passed=False,
                validation_reason="Original tasks failed or were blocked.",
                unresolved_tasks=["T01 Test task (Failed)"],
            ),
        ]

        path = write_success_md(
            tmp_path,
            results,
            60.0,
            TokenUsage(),
            retrospective_rounds=retro_rounds,
        )
        content = path.read_text(encoding="utf-8")

        assert "Validation Details" in content
        assert "Original tasks failed or were blocked." in content
        assert "T01 Test task (Failed)" in content

    def test_no_retrospective_section_when_none(self, tmp_path: Path) -> None:
        """Should NOT include Retrospective section when no rounds provided."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        path = write_success_md(tmp_path, results, 60.0, total_tokens)
        content = path.read_text(encoding="utf-8")

        assert "## Retrospective" not in content

    def test_multiple_retrospective_rounds(self, tmp_path: Path) -> None:
        """Should show multiple retrospective rounds."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=1,
                fixes_planned=1,
                tasks_created=["R01"],
                duration_seconds=90.0,
            ),
            RetrospectiveRound(
                round_number=2,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
                duration_seconds=30.0,
            ),
        ]
        path = write_success_md(
            tmp_path, results, 60.0, total_tokens, retrospective_rounds=retro_rounds
        )
        content = path.read_text(encoding="utf-8")

        assert "| 1 |" in content  # round 1
        assert "| 2 |" in content  # round 2


# ---------------------------------------------------------------------------
# Terminal summary printer
# ---------------------------------------------------------------------------


class TestPrintSuccessSummary:
    """Tests for print_success_summary()."""

    def test_does_not_crash_with_tokens(self) -> None:
        """Should print without error when token data is present."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        # Should not raise
        print_success_summary(results, 60.0, total_tokens)

    def test_does_not_crash_without_tokens(self) -> None:
        """Should print without error when token data is zero."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(),
            ),
        ]
        total_tokens = TokenUsage()
        # Should not raise
        print_success_summary(results, 60.0, total_tokens)

    def test_does_not_crash_with_retrospective(self) -> None:
        """Should print without error when retrospective data is provided."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=1,
                fixes_planned=1,
                tasks_created=["R01"],
                duration_seconds=90.0,
            ),
        ]
        # Should not raise
        print_success_summary(results, 60.0, total_tokens, retrospective_rounds=retro_rounds)

    def test_shows_retries_in_header(self) -> None:
        """Should show retry count in header summary line."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=3,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        # Should not raise — 3 attempts = 2 retries
        print_success_summary(results, 60.0, total_tokens)

    def test_shows_rate_limit_in_header(self) -> None:
        """Should show rate limit count in header summary line."""
        results = [
            TaskResult(
                prefix="T01",
                title="Rate limited task",
                status="done",
                duration_seconds=60.0,
                attempts=2,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
                rate_limit_hit=True,
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        # Should not raise
        print_success_summary(results, 60.0, total_tokens)

    def test_failed_task_header(self) -> None:
        """Should print failure header when tasks have failed status."""
        results = [
            TaskResult(
                prefix="T01",
                title="Failed task",
                status="failed",
                duration_seconds=30.0,
                attempts=2,
                tokens=TokenUsage(input_tokens=1000, output_tokens=100),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=1000, output_tokens=100)
        # Should not raise — covers failed header, failed status row, failed count
        print_success_summary(results, 30.0, total_tokens)

    def test_skipped_task_in_table(self) -> None:
        """Should show Skip status for skipped tasks in terminal table."""
        results = [
            TaskResult(
                prefix="T01",
                title="Skipped task",
                status="skipped",
                tokens=TokenUsage(),
            ),
        ]
        total_tokens = TokenUsage()
        # Should not raise — covers else/skipped branch in table
        print_success_summary(results, 0.0, total_tokens)

    def test_retrospective_no_issues_found(self) -> None:
        """Should print 'no issues found' for retrospective round with 0 issues."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
                duration_seconds=45.0,
            ),
        ]
        # Should not raise — covers line 359
        print_success_summary(results, 60.0, total_tokens, retrospective_rounds=retro_rounds)

    def test_shows_success_md_path(self) -> None:
        """Should print path to SUMMARY.md when provided."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        success_path = Path("/tmp/project/tasks/SUMMARY.md")
        # Should not raise — covers lines 379-380
        print_success_summary(results, 60.0, total_tokens, success_md_path=success_path)


# ---------------------------------------------------------------------------
# T01.1 — validation_passed=True branch (line 224)
# ---------------------------------------------------------------------------


class TestWriteSuccessMdValidationPassed:
    """Tests for the validation_passed=True branch in SUMMARY.md."""

    def test_validation_passed_shows_checkmark(self, tmp_path: Path) -> None:
        """Should show '✓ Passed' when validation_passed=True."""
        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        retro_rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
                duration_seconds=30.0,
                validation_passed=True,
            ),
        ]
        path = write_success_md(
            tmp_path, results, 60.0, total_tokens, retrospective_rounds=retro_rounds
        )
        content = path.read_text(encoding="utf-8")

        assert "## Retrospective" in content
        assert "\u2713 Passed" in content


# ---------------------------------------------------------------------------
# T01.2 — write_summary_md wrapper (line 306)
# ---------------------------------------------------------------------------


class TestWriteSummaryMd:
    """Tests for write_summary_md() wrapper function."""

    def test_write_summary_md_calls_write_success_md(self, tmp_path: Path) -> None:
        """Should call write_summary_md directly and produce a valid SUMMARY.md."""
        from the_architect.core.success import write_summary_md

        results = [
            TaskResult(
                prefix="T01",
                title="Test task",
                status="done",
                duration_seconds=60.0,
                attempts=1,
                tokens=TokenUsage(input_tokens=5000, output_tokens=500),
                model="test-model",
            ),
        ]
        total_tokens = TokenUsage(input_tokens=5000, output_tokens=500)
        path = write_summary_md(tmp_path, results, 60.0, total_tokens)

        assert path == tmp_path / "tasks" / "SUMMARY.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# The Architect — Run Summary" in content
        assert "T01" in content


# ---------------------------------------------------------------------------
# Notification hook
# ---------------------------------------------------------------------------


class TestNotifyRunCompletion:
    """Tests for notify_run_completion()."""

    def test_success_notification_fires(self) -> None:
        """Should send desktop notification and bell on success."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="done"),
            TaskResult(prefix="T02", title="Task 2", status="done"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=True,
                notify_on_fail=True,
                results=results,
                total_duration=120.0,
            )

        mock_notify.assert_called_once_with(
            "The Architect — Run Complete",
            "2/2 tasks done in 2:00",
        )
        mock_bell.assert_called_once()

    def test_failure_notification_fires(self) -> None:
        """Should send failure notification when tasks failed."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="done"),
            TaskResult(prefix="T02", title="Task 2", status="failed"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=True,
                notify_on_fail=True,
                results=results,
                total_duration=60.0,
            )

        mock_notify.assert_called_once_with(
            "The Architect — Run Failed",
            "1/2 done, 1 failed. Duration: 1:00",
        )
        mock_bell.assert_called_once()

    def test_notify_on_complete_false_suppresses(self) -> None:
        """Should skip notification when notify_on_complete is False."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="done"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=False,
                notify_on_fail=True,
                results=results,
                total_duration=30.0,
            )

        mock_notify.assert_not_called()
        mock_bell.assert_not_called()

    def test_notify_on_fail_false_suppresses(self) -> None:
        """Should skip notification when notify_on_fail is False and run failed."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="failed"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=True,
                notify_on_fail=False,
                results=results,
                total_duration=30.0,
            )

        mock_notify.assert_not_called()
        mock_bell.assert_not_called()

    def test_both_disabled_no_notification(self) -> None:
        """Should skip notification when both flags are False."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="done"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=False,
                notify_on_fail=False,
                results=results,
                total_duration=30.0,
            )

        mock_notify.assert_not_called()
        mock_bell.assert_not_called()

    def test_empty_results_all_done(self) -> None:
        """Should handle empty results list as success (0/0 done)."""
        from unittest.mock import patch

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=True,
                notify_on_fail=True,
                results=[],
                total_duration=0.0,
            )

        mock_notify.assert_called_once_with(
            "The Architect — Run Complete",
            "0/0 tasks done in 0:00",
        )
        mock_bell.assert_called_once()

    def test_all_failed_notification(self) -> None:
        """Should send failure notification when all tasks failed."""
        from unittest.mock import patch

        results = [
            TaskResult(prefix="T01", title="Task 1", status="failed"),
            TaskResult(prefix="T02", title="Task 2", status="failed"),
        ]

        with (
            patch("the_architect.core.notifications.send_desktop_notification") as mock_notify,
            patch("the_architect.core.notifications.ring_terminal_bell") as mock_bell,
        ):
            notify_run_completion(
                notify_on_complete=True,
                notify_on_fail=True,
                results=results,
                total_duration=300.0,
            )

        mock_notify.assert_called_once_with(
            "The Architect — Run Failed",
            "0/2 done, 2 failed. Duration: 5:00",
        )
        mock_bell.assert_called_once()
