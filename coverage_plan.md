# Coverage Improvement Plan

**Last updated:** build 10423
**Current coverage:** 84.2% (3039 tests, 0 failures, Linux + macOS + Windows)
**Target:** 92%

---

## Why this matters

The Architect is designed to be self-developed by AI agents. At 84% coverage an agent can introduce
a bug in one of the uncovered paths, all tests pass, and the bug ships. At 92% the uncovered surface
is small enough that an agent can reason about it confidently. The uncovered 16% today is almost
entirely exception branches, provider error handling, structured output parsing, and TUI session
dispatch — exactly the code an agent is most likely to break when refactoring.

---

## What needs to be covered

The biggest gains come from four areas. Work them in this order.

**Provider error and version detection** (`gemini_cli_provider.py` at 69%, `codex_cli_provider.py`
at 80%). Both providers have nearly identical uncovered paths: the timeout and subprocess-error
branches in `get_version()` and `has_models()`, the npm-not-installed fallback in `install_hint()`,
the full `check_update_available()` flow including network error handling and version comparison
edge cases, the `get_resolved_model()` cache-hit branch, the `_read_*_model_from_config()` helpers
that read from `~/.gemini/settings.json` and `~/.codex/config.toml`, and the bundle/JS model
extraction in Gemini's `_list_models_from_bundle()`. For network calls, mock
`urllib.request.urlopen` — never make real HTTP requests in tests. For filesystem calls, use
`tmp_path`. These tests are pure Python with no Textual, so they are fast and straightforward.
Target files: `tests/test_gemini_cli_provider.py` and `tests/test_codex_cli_provider.py`.

**Structured task outcome parsing and runner error branches** (`runner.py` at 92%). The
`_extract_task_outcome_summary()` function has a structured-section parser (`=== TASK OUTCOME ===`)
that is completely untested — this is the function that feeds the circuit breaker's
downstream-impact signal, so bugs here directly cause wrong retry behaviour. Cover the full parsing
path including files extraction, verification extraction, and impact field detection. Also cover the
bad-env-var paths in `_get_provider_idle_timeout()` and `_get_provider_sleep_wake_gap()` (invalid
string → warning logged, default returned), the `OSError` branch in `_is_lock_stale()`, the
`ProcessLookupError` branch in `_kill_process_tree()`, the `on_task_start` callback exception path,
and the provider-update-required early-exit branch in `run_task()`. Target file:
`tests/test_runner.py`.

**TUI session dispatch** (`session.py` at 62%). Every method in `TuiSession` and `TuiWaitSession`
has two branches: `app is None` (no-op) and `app is not None` (delegates to app). Currently only
the `app is None` branch is tested. The `app is not None` branch is where real bugs would appear
— a refactored method signature, a renamed app method, a swallowed exception. Cover all dispatch
methods (`push_event`, `update_details`, `update_progress_tasks`, `update_settings`, `update_costs`,
`update_footer`, and all `TuiWaitSession` methods) by passing a `MagicMock()` as the app and
asserting the right method was called. Also cover the exception-swallowing paths by making the mock
raise. The `tui_execution_session()` runner-reuse branch (where an active `ArchitectAppRunner`
exists and its app is reused instead of creating a new one) is also uncovered. Target file:
`tests/test_tui_session.py`.

**Execution screen and TUI runner** (`execution.py` at 81%, `runner.py` at 85%). The execution
screen has uncovered pre-mount buffer-flush paths — when `update_footer()`, `push_diagnostic()`, or
`update_costs()` are called before the Textual DOM is mounted, they should be stored in pending
buffers and flushed on mount. These are important correctness paths because the CLI callbacks fire
immediately when a task completes, which can happen before the screen has finished mounting. Cover
these by calling the update methods on an unmounted `ExecutionScreen()` instance and asserting the
pending buffers are populated. The `_render_progress()` method branches for model, tokens,
current_op and last_activity fields are also uncovered. The TUI runner (`tui/runner.py`) has
uncovered SIGINT handler registration failures, the unexpected-app-exit path, atexit cleanup, and
the `_atexit_kill_subprocesses` hook. Target files: new `tests/test_tui_execution_screen.py` and
extended `tests/test_tui_phase17_runner.py`.

**Smaller gaps** worth closing before moving to harder work: `fileutil.py` (96%) is missing the
`os.close()` exception path when `os.fdopen` fails and the fd must be cleaned up manually before
unlinking the temp file. `terminal.py` (83%) is missing the `isatty()` exception path, the
non-TTY early return, and the write-exception handler. `monitor_state.py` (94%) is missing the
cost-estimation-raises exception paths, the model rotation counter, cooldown tz-naive datetime
handling, and the free-rotator exception handler. These are all small, targeted, pure-Python tests.

**Lower-priority work** (do last): `tui/app.py` (66%) has uncovered thread-safe delegation chains
that require a live Textual app with worker threads — complex to test but important for ensuring
refactors don't break the `_thread_safe_call` path. `tui/screens/pause.py` (69%) and `cli.py`
(64%) are similar — the CLI's 1121 uncovered lines are mostly the interactive `run` command
orchestration which is more of an integration test than a unit test. Use Click's `CliRunner` for
the helper functions and accept that the full run-command flow is tested by real use rather than
unit tests.

---

## Rules every new test must follow

All tests must pass on Linux, macOS, and Windows without modification. Use `tmp_path` (the pytest
fixture) for any file path that gets written to or read from — never hardcode `/tmp/...` or
`C:/...`. Always pass `encoding="utf-8"` to `write_text()`. For POSIX-only behaviour like
`os.killpg` or `signal.SIGKILL`, guard with `if not hasattr(os, "killpg"): pytest.skip(...)` or
`if sys.platform == "win32": pytest.skip(...)`. Never patch `sys.platform` without also patching
the platform-specific functions that branch on it (e.g. `is_windows()`, `_get_portable_shell()`).
Never make real network calls — mock `urllib.request.urlopen`. In Textual async tests, always use
`await pilot.pause(0.05)` with a real delay after any widget state mutation — plain
`await pilot.pause()` is a single-tick yield and races on slow Windows CI runners.

---

## Expected outcome

Completing the provider error paths, runner outcome parsing, and TUI session dispatch (the first
three areas above) will bring coverage to approximately 91%. Adding the execution screen, TUI
runner, and small gap closures will reach the 92% target. The lower-priority app and CLI work takes
it to 93–94% and can be done incrementally. The full effort is approximately 116–126 new tests.
