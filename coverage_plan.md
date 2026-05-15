# Coverage Improvement Plan

**Current:** 84% overall (3039 tests, 0 failures)  
**Target:** 92%+ overall — the threshold where an AI agent can safely self-modify without breaking
undetected paths.

**Guiding principle:** Every new test must pass on Linux, macOS, and Windows. No hardcoded POSIX paths
(`/tmp`, `/home`), no unguarded POSIX-only calls, `encoding="utf-8"` on all file writes.

---

## Why 92%?

The uncovered 16% is almost entirely:
1. **Exception/error branches** — the paths an AI agent is most likely to accidentally break when
   refactoring happy paths.
2. **Windows-specific code paths** — already exercised in CI but untested in unit tests.
3. **Provider version/update detection** — called on startup; a broken check silently degrades UX.
4. **Structured task outcome parsing** — the most complex text-parsing logic in the codebase; bugs
   here cause the circuit breaker to misfire.

---

## Priority Tiers

### Tier 1 — High value, easy to write (do first)

These are well-defined functions with clear inputs/outputs. Tests are straightforward mocks.
Estimated gain: **+4–5%**

#### 1a. `the_architect/core/gemini_cli_provider.py` (69% → 85%)

| Gap | What to test |
|-----|-------------|
| `get_version()` timeout/error branches (L131–133) | `subprocess.TimeoutExpired` → returns `"unknown"` |
| `has_models()` when not installed + subprocess error (L145–154) | not installed → `False`; subprocess raises → `False` |
| `install_hint()` non-npm fallback (L160) | no `npm` on PATH → returns URL string |
| `check_update_available()` full flow (L169–208) | mock `urllib.request.urlopen`; installed < latest → update string; installed == latest → `""`; network error → `""`; bad version string → `""` |
| `get_resolved_model()` cache hit (L285) | pre-populate cache key, verify immediate return |
| `list_models()` returns `""` fallback (L305) | no env var, no config, empty catalog → `""` |
| `_read_gemini_model_from_config()` (L650–658) | `tmp_path` with valid JSON → model name; missing file → `""`; bad JSON → `""` |
| `_list_models_from_bundle()` (L678–718) | no `gemini` binary → `[]`; mock binary path with fake JS files containing model names → returns sorted list; `OSError` on read → skips file |
| Output parsing: list content blocks (L532–537) | raw content as list of `{"text": "..."}` dicts → display lines extracted |
| Output parsing: tool call detail extraction (L560–569) | tool call with input dict → `→ tool detail` line rendered |

**File:** `tests/test_gemini_cli_provider.py`

---

#### 1b. `the_architect/core/codex_cli_provider.py` (80% → 92%)

| Gap | What to test |
|-----|-------------|
| `get_version()` timeout/error branches (L139–141) | same pattern as Gemini |
| `has_models()` not installed + error (L151–162) | same pattern |
| `install_hint()` non-npm fallback (L168) | no `npm` → URL string |
| `check_update_available()` full flow (L177–217) | same structure as Gemini — mock `urlopen` |
| `list_models()` subprocess debug fallback error (L265–272) | subprocess raises → logs and falls through |
| `list_agents()` returns `[]` (L293) | always returns `[]` |
| `get_resolved_model()` cache hit (L319) + config file path (L330–331) + empty fallback (L339) | pre-populated cache; env var match; no config → `""` |
| `_read_codex_model_from_config()` (L702–710) | `tmp_path` with valid TOML → model; missing file → `""`; bad TOML → `""` |
| Output parser: truncate long command (L613) | command > 80 chars → truncated with `…` |

**File:** `tests/test_codex_cli_provider.py`

---

#### 1c. `the_architect/core/runner.py` — structured outcome parsing (92% → 95%)

| Gap | What to test |
|-----|-------------|
| `_extract_task_outcome_summary()` with `=== TASK OUTCOME ===` section (L2220–2243) | text with structured section → parses Summary/Files/Verification/Downstream fields |
| `_extract_task_outcome_summary()` files extraction (L2250) | text contains modified file paths → `Files: a.py, b.py` |
| `_extract_task_outcome_summary()` verification extraction (L2258–2259) | text with "verified X" sentences → `Verification: ...` |
| `_get_provider_idle_timeout()` bad env var (L802–807) | set `ARCHITECT_PROVIDER_IDLE_TIMEOUT_SECONDS=abc` → logs warning, returns default |
| `_get_provider_sleep_wake_gap()` bad env var (L818–823) | same pattern |
| `_is_lock_stale()` OSError on `os.kill` (L496–497) | mock `os.kill` raises `OSError` → returns `False` |
| `_kill_process_tree()` `ProcessLookupError` on `proc.kill` (L775–778) | mock `proc.kill` raises `ProcessLookupError` → no crash |
| `run_task()` `on_task_start` callback raises (L3409–3412) | callback raises `RuntimeError` → run continues |
| `run_task()` provider update required branch (L3028–3032) | `check_update_available()` returns non-empty → task breaks after first attempt |

**File:** `tests/test_runner.py`

---

#### 1d. `the_architect/core/monitor_state.py` (94% → 98%)

| Gap | What to test |
|-----|-------------|
| `read_monitor_state()` valid file but no data key (L106) | write JSON without expected keys → returns `None` |
| `clear_flags()` OSError on unlink (L170–171) | mock `unlink` raises `OSError` → no crash |
| `on_task_done()` / `on_task_failed()` cost estimation raises (L293–294, L335–336) | mock `estimate_cost_detailed` raises → session cost stays 0 |
| `on_model_update()` model rotation counter (L349) | call twice with different models → `_model_rotation_count == 1` |
| `_flush()` free_rotator exception (L495–496) | mock `_free_rotator` raises on `remaining_count` → `free_remaining = 0` |
| `_flush()` cooldown tz-naive datetime (L505) | set `_cooldown_started_at` to naive ISO string → gets UTC tzinfo added |
| `_flush()` cooldown calculation exception (L509–510) | mock datetime parse raises → `cooldown_remaining_seconds = None` |

**File:** `tests/test_monitor.py` (extend existing class)

---

#### 1e. `the_architect/core/fileutil.py` (96% → 100%)

| Gap | What to test |
|-----|-------------|
| `atomic_write_text()` fd close on `os.fdopen` failure (L98–99) | mock `os.fdopen` raises; mock `os.close` raises `OSError` → no crash, temp file removed |

**File:** `tests/test_fileutil.py`

---

#### 1f. `the_architect/tui/terminal.py` (83% → 100%)

| Gap | What to test |
|-----|-------------|
| `_stream_is_tty()` exception path (L34–35) | mock stream with `isatty` raising `AttributeError` → returns `False` |
| `_write_restore_sequence()` early return when not TTY (L41) | non-TTY stream with `require_tty=True` → nothing written |
| `_write_restore_sequence()` write exception (L45–46) | stream raises on `write` → no crash |

**File:** `tests/test_tui_phase17_runner.py` or new `tests/test_terminal.py`

---

### Tier 2 — Medium value, requires Textual async harness (do second)

These need `async with app.run_test()` patterns. Estimated gain: **+3–4%**

#### 2a. `the_architect/tui/screens/execution.py` (81% → 92%)

Most uncovered lines are exception-swallowing branches in Textual widget callbacks — they only fire
when `query_one()` raises because the DOM isn't mounted yet (pre-mount buffer flush paths).

| Gap | What to test |
|-----|-------------|
| `_fmt_tokens()` < 1000 branch (L44) | `_fmt_tokens(500)` → `"500"` |
| `_idle_footer_text()` inside tmux (L57) | set `TMUX` env var → footer includes detach hint |
| Pre-mount buffer flush (L255–269) | call `update_footer()`, `push_diagnostic()`, `update_costs()` before mount; after mount verify they appear |
| `push_output_line()` → clears placeholder (L281–282) | verify placeholder is cleared on first real output |
| `push_diagnostic()` pre-mount path (L360–362) | call before mount → stored in `_pending_diagnostics` |
| `update_footer()` pre-mount path (L383–385) | call before mount → stored in `_pending_footer` |
| `_render_progress()` with model/tokens/current_op/last_activity (L493–499) | populate `_details` with all fields, verify each appears in render |
| `_render_costs()` "unknown model" branch (L584–585) | tokens > 0, cost == 0 → shows `—  (model not in pricing table)` |
| `_render_costs()` model short name with zero cost (L596) | model_costs dict with 0.0 cost → shows name without `$` |
| `action_pause_menu()` exception swallowed (L450–453) | call action on bare Screen (no `show_pause_menu`) → no crash |

**File:** `tests/test_tui_execution_screen.py` (new)

---

#### 2b. `the_architect/tui/runner.py` (85% → 95%)

| Gap | What to test |
|-----|-------------|
| `push_and_wait()` (L108) | call from worker thread while runner is active → returns dismiss value |
| SIGINT handler registration failure (L185–190) | mock `signal.signal` raises `ValueError` → `_prev_sigint = None`, no crash |
| Unexpected app exit path (L228–235) | app exits before `_flow_done` is set → `unexpected_app_exit=True`, subprocesses killed |
| SIGINT restore failure (L246–247) | mock `signal.signal` raises on restore → no crash |
| Subprocess cleanup exception (L261–262) | mock `kill_active_subprocesses` raises → no crash |
| Worker join after timeout (L270) | long-running worker → joins with timeout |
| `atexit` unregister exception (L280–281) | mock `atexit.unregister` raises → no crash |
| `_atexit_kill_subprocesses()` (L297–303) | call directly → invokes `kill_active_subprocesses` and `_restore_terminal_input_modes` |
| `_sigint_kill_handler()` subprocess kill exception (L320–321) | mock `kill_active_subprocesses` raises → still raises `KeyboardInterrupt` |

**File:** `tests/test_tui_phase17_runner.py` (extend)

---

#### 2c. `the_architect/tui/session.py` (62% → 82%)

All uncovered lines are the `app is not None` dispatch paths — the branches that actually call
through to the app. Currently only the `app is None` no-op path is tested.

| Gap | What to test |
|-----|-------------|
| `push_event()` with live app mock (L55–58) | mock app; call `push_event()` → `app.push_event_line` called |
| `update_details()` with live app mock (L64–67) | same pattern |
| `update_progress_tasks()` with live app mock (L71–76) | same |
| `update_settings()` with live app mock (L80–85) | same |
| `update_costs()` exception swallowed (L98–99) | mock app raises → no crash |
| `update_footer()` with live app mock (L105–108) | same pattern |
| `tui_execution_session()` reuses runner app (L137–160) | mock active runner → session uses runner's app, no new thread |
| `tui_execution_session()` ImportError fallback (L137–138) | the import path is always present; test the `runner = None` branch by mocking `active_runner` to raise |
| All `TuiWaitSession` dispatch methods (L236–395) | same mock-app pattern for `set_title`, `set_detail`, `append_log`, `show`, `hide`, `update` |

**File:** `tests/test_tui_session.py` (extend)

---

### Tier 3 — Lower ROI, complex setup (do last or skip)

| File | Current | Gap | Notes |
|------|---------|-----|-------|
| `tui/app.py` | 66% | Thread-safe delegation methods | All uncovered lines are `_thread_safe_call` → screen method chains. Hard to exercise without a live Textual app. Use `run_test()` harness + worker threads. Partial improvement achievable with 10–15 focused tests. |
| `tui/screens/pause.py` | 69% | Pause menu actions | Action handlers (`action_resume`, `action_stop`, etc.) need `ArchitectApp` running. Use `run_test()`. |
| `tui/screens/pre_run_tabbed.py` | 83% | Provider model loading callbacks | Async callbacks fired during mount. Use `run_test()` + `await pilot.pause()`. |
| `cli.py` | 64% | Interactive CLI commands | 1121 uncovered lines. Mostly the `run` command's full orchestration. Use Click's `CliRunner` for the helper functions (lines 804–922) and the config/model selection prompts. The full `run` command flow is an integration test, not a unit test. |
| `core/project_intelligence.py` | 82% | Intelligence extraction edge cases | Add tests for empty/malformed input to each extractor. |

---

## Implementation Order

1. **Tier 1** (all pure-Python, no Textual): write in one session, ~40–50 new tests
2. **Tier 2a** (`execution.py`): Textual `run_test()` harness, ~15 new tests
3. **Tier 2b** (`runner.py`): extend existing phase17 tests, ~10 new tests
4. **Tier 2c** (`session.py`): mock-app pattern, ~20 new tests
5. **Tier 3**: pick off `cli.py` helpers and `app.py` delegation methods as time permits

---

## Cross-Platform Rules for All New Tests

- Use `tmp_path` (pytest fixture) for all file paths — never `Path("/tmp/...")` or `Path("C:/...")`
- `write_text(..., encoding="utf-8")` on every `write_text` call
- Guard POSIX-only tests with `if not hasattr(os, "killpg"): pytest.skip(...)`
- Guard signal tests: `if sys.platform == "win32": pytest.skip(...)`
- Never patch `sys.platform` without also patching the platform-specific functions that depend on it
  (e.g. `shlex_quote`, `_get_portable_shell`, `is_windows`)
- Mock `urllib.request.urlopen` for any network-touching tests — never make real HTTP calls

---

## Tracking

| Tier | File(s) | Est. new tests | Est. coverage gain |
|------|---------|---------------|-------------------|
| 1a | `test_gemini_cli_provider.py` | 15 | +1.1% |
| 1b | `test_codex_cli_provider.py` | 12 | +0.8% |
| 1c | `test_runner.py` | 10 | +0.7% |
| 1d | `test_monitor.py` | 8 | +0.1% |
| 1e | `test_fileutil.py` | 2 | +0.1% |
| 1f | `test_terminal.py` | 4 | +0.1% |
| 2a | `test_tui_execution_screen.py` | 15 | +0.7% |
| 2b | `test_tui_phase17_runner.py` | 10 | +0.5% |
| 2c | `test_tui_session.py` | 20 | +1.3% |
| 3  | `test_cli.py`, `test_tui_app.py` | 20–30 | +1.5% |
| **Total** | | **~116–126** | **~7%** |

Target after Tier 1+2: **~91%**. After Tier 3: **~92–93%**.
