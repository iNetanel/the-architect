# Changelog

All notable changes to The Architect are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with a global build counter.
See [README — Versioning](README.md#versioning) for the full scheme.
Full rules in [`documentation/PRACTICES.md`](documentation/PRACTICES.md).

---

## [Unreleased]

### Changed

- **Tabbed pre-run screen no longer shows redundant `Configure run` heading (build 10212):**
  - Removed the extra title line because the visible tabs already define the setup context.

- **Existing-task TUI now hides new-goal fields until Replan is selected (build 10210):**
  - Pending-task runs now start on a dedicated `Run` tab instead of mixing the action choice with
    always-visible goal/scope inputs.
  - `Execute existing tasks` keeps the screen focused on execution settings only.
  - `Replan from a new goal` reveals the scope and goal fields, making the archive-and-replan path
    explicit without confusing it with the normal execute path.

- **Existing-task execution now uses the main tabbed pre-run screen (build 10209):**
  - When pending tasks already exist, the TUI now opens the same tabbed setup screen used for a
    fresh goal instead of the narrow resume-only form.
  - Users can choose `Execute existing tasks` or `Replan from a new goal` while also changing
    provider, models/agents, free/persistent mode, integrity, and token budget in the same screen.
  - Replan from this screen feeds the normal planning flow and keeps the existing archive sequence:
    the old tasks and `tasks/INSTRUCTIONS.md` are archived by the planner before fresh tasks are
    written.

- **Execution TUI tabs now separate live output, overall progress, and diagnostics (build 10208):**
  - Renamed `Live Output` to `Live` for the raw provider stream.
  - Replaced the vague `Events` / `Status` split with `Progress` and `Diagnostics`.
  - `Progress` now shows the current phase, current task, attempt/model metadata, and the full
    task list with running/done/pending/failed statuses so the user can see the overall goal state.
  - `Diagnostics` now owns retry, cooldown, model-switch, replan, and circuit-breaker events.
  - Removed the built-in Textual footer from the execution screen to reduce noisy keybinding chrome.

- **All providers detect models from the local CLI installation only — no external URLs (build 10162):**
  - **Gemini CLI**: removed the `googleapis.com` API call from `list_models`.  Models are now
    extracted from the installed Gemini CLI's JS bundle chunks by resolving `shutil.which("gemini")`
    and scanning sibling `*.js` files for `gemini-<version>` string literals.  This is
    OS-agnostic (works with nvm, Homebrew, system npm, Windows, etc.) and reflects exactly what
    the installed CLI version supports — including custom or enterprise builds.  Falls back to
    `~/.gemini/settings.json`, then to the static list.

- **All providers now fetch model lists live from the provider instead of hardcoded lists (build 10161):**
  - **Codex CLI**: `list_models` now calls `codex debug models` (JSON catalog built into the
    binary), filters to visible entries, and sorts by `priority` so the dropdown order matches
    the Codex UI.  `get_resolved_model` picks the first visible model from the same catalog.
    The static `_FALLBACK_CODEX_MODELS` list is only used when the binary call fails entirely.
  - **Claude Code**: `list_models` now calls `claude models` first (per-account live list,
    returns only models the user's API key can actually access) and falls back to binary
    extraction only when the network call fails.  `get_resolved_model` uses `claude models`
    as the primary source for the default model, with binary extraction as offline fallback.
  - **Gemini CLI**: `list_models` now queries
    `https://generativelanguage.googleapis.com/v1beta/models` using `GEMINI_API_KEY`,
    filters to models that support `generateContent`, and returns the live per-account list.
    Falls back to `~/.gemini/settings.json` model, then static list.
  - **OpenCode**: already called `opencode models` live — no change needed.

### Fixed

- **Confirmed ESC exit could leave a blank TUI while shutdown cleanup continued (build 10216):**
  - Pause-menu `Exit` now routes through the same branded shutdown path as Ctrl+C.
  - The animated splash remains visible while active provider subprocesses are killed on a background
    cleanup thread, and the app exits only after cleanup plus a short minimum display window.
  - The persistent TUI runner no longer forces an immediate app exit when a user-requested shutdown
    is already showing the shutdown splash.

- **Pre-run footer said `Ctrl+Tab` even though tab navigation should use `Tab` (build 10215):**
  - Added `Tab` / `Shift+Tab` as the primary tab-switch shortcuts on the setup screen.
  - Kept `Ctrl+Tab` / `Ctrl+Shift+Tab` as hidden aliases for compatibility.
  - Updated the custom setup footer to match the actual keys.

- **Pre-run screen showed two footers and execution-only shortcuts (build 10214):**
  - Removed the built-in Textual footer from the tabbed pre-run screen, leaving only the custom
    setup footer.
  - Moved `l`/`p`/`d` execution tab shortcuts off the global app bindings and onto
    `ExecutionScreen`, so non-execution screens no longer advertise Diagnostics/Live/Progress keys.

- **Pre-run left/right arrows sometimes needed two presses to switch tabs (build 10213):**
  - The tabbed setup screen now changes `TabbedContent.active` directly from the known visible tab
    order instead of delegating to the inner `Tabs` widget, avoiding focus/tab event races where the
    first arrow press could update focus without landing the active tab.

- **Pre-run up/down arrows skipped options inside radio and model lists (build 10211):**
  - The tabbed setup screen now moves selection inside focused `RadioSet` and `ListView` controls
    before moving focus to the next section. This makes arrow navigation work consistently for
    execute/replan, scope, provider, model, and execution-agent choices.

- **Execution Live Output tab only showed task banners, not provider text/tool output (build 10206):**
  - The TUI created a `TextualStreamRenderer` for execution, but `run_task` did not pass that
    renderer into `run_task_once`. Provider output therefore fell back to stdout while the
    Textual alternate screen was active, leaving the visible Live Output tab with only the
    task/attempt/done lines written directly by the TUI callbacks. `run_task` now forwards the
    renderer through to `run_task_once`, so parsed provider text and tool-call lines are appended
    to the execution screen.

- **Blinking cursor visible in the middle of the Live Output / wait log area (build 10205):**
  - Textual's `RichLog` widget is focusable by default (`can_focus=True`).  When it received
    focus during execution or planning, it rendered a blinking text cursor in the middle of the
    output area — making the display look broken even when content was present.  Fixed by setting
    `can_focus = False` on both `RichLog` instances in `ExecutionScreen` (`on_mount`) and the
    `RichLog` in `WaitScreen` (`on_mount`).  The logs are display-only; keyboard interaction is
    never needed.

- **Claude Code execution output missing from Live Output tab — tool calls never displayed (build 10202):**
  - During execution with Claude Code, the agent's tool calls (Read, Write, Bash, etc.) are
    emitted as `tool_use` content parts nested inside `assistant` JSON events.  The parser was
    explicitly silencing these parts with a `# skip silently` comment, so every file read, write,
    edit, and bash command the agent made produced zero output lines.  The `text` content parts
    (the agent's prose commentary) were displayed, but agents that work mostly through tools
    show very little text during task execution — resulting in a completely blank Live Output tab.
  - Fixed by handling `tool_use` content parts in `assistant` events: each part now produces a
    `→ ToolName path/detail` line, matching the display style already used for OpenCode and
    Gemini CLI tool calls.  `thinking` parts continue to be silently dropped.
  - Verified against Claude Code's actual `stream-json` output: tool calls only ever appear as
    content parts inside `assistant` events — there are no top-level `tool_use` events.
  - Updated tests to assert that `tool_use` content parts produce `→ ToolName` display lines.

- **Execution stage showed almost no provider output in the TUI Live Output tab (build 10168):**
  - All four providers (OpenCode, Claude Code, Codex CLI, Gemini CLI) were affected.
  - Root cause: `ArchitectApp.push_output_line` and `append_wait_log` used `call_from_thread`
    (Textual's blocking thread→event-loop bridge) for every provider output line.
    `call_from_thread` internally calls `asyncio.run_coroutine_threadsafe(...).result()`, which
    blocks the calling thread until Textual acknowledges each individual line.  Because the
    stdout reader (`_read_stdout`) runs as a coroutine on the worker thread's asyncio event loop,
    blocking that thread with a synchronous wait froze the entire worker event loop on every line.
    With 100+ output lines at ~50 ms round-trip each, this easily exceeded the 5-second
    `asyncio.wait_for(reader_task, timeout=5.0)` limit in `stream_provider`, causing
    `reader_task` to be cancelled and all remaining provider output to be silently discarded.
  - Fix 1 (`tui/app.py`): `push_output_line` and `append_wait_log` now use
    `loop.call_soon_threadsafe` (non-blocking fire-and-forget) when called from a foreign thread.
    This eliminates the per-line round-trip wait while keeping strict ordering — Textual's
    event loop processes lines in submission order.
  - Fix 2 (`tui/session.py`): `TuiWaitSession.append_log` for the standalone `WaitApp` path
    (used during early planning before the main `ArchitectApp` is active) also switched to
    `call_soon_threadsafe` for the same reason.
  - Fix 3 (`core/runner.py`): the `asyncio.wait_for(reader_task, timeout=5.0)` budget was
    raised to 30 seconds as a defence-in-depth measure against any future slow path.

- **Provider update check showed wrong provider after user selected one in the tabbed pre-run screen (build 10166):**
  - When the user selected a provider (e.g. Codex) in the tabbed pre-run screen and then submitted
    their goal, the update check still ran against the originally auto-detected provider (e.g. OpenCode)
    instead of the one they chose.  The root cause was that `_active_provider` was never re-resolved
    after `_collect_planning_prompts` stored the user's selection in `config.provider`.  Fixed by
    re-resolving `_active_provider` via `detect_provider` immediately after `_collect_planning_prompts`
    returns, when `config.provider` differs from the current active provider.  All downstream calls
    (update check, planning, execution) now use the correct provider object.

- **Codex CLI model list showed non-existent model names (build 10160):**
  - The `_FALLBACK_CODEX_MODELS` list contained `gpt-5.3`, `gpt-4.1`, `gpt-4.1-mini`,
    `gpt-4.1-nano`, `o3-mini`, and `o4-mini` — none of which exist in Codex CLI 0.125.0.
    The API returned `"The requested model 'gpt-5.3' does not exist."`.
  - Fixed by extracting the actual model slugs baked into the Codex 0.125.0 binary:
    `gpt-5.4` (default), `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.2`, `o3`.
    The default (`_FALLBACK_CODEX_MODELS[0]`) is `gpt-5.4` to match the binary's own default.

- **Provider output silently dropped — execution screen showed nothing (build 10159):**
  - **Codex CLI**: `item.completed` events with `agent_message` text were always discarded
    because the parser assumed `item.delta` events had already streamed the text live.
    In practice many Codex builds (and the version used in this project) never emit
    `item.delta` at all — all agent text arrives only in `item.completed`. Fixed by
    tracking a per-turn `_delta_text_seen` flag: `item.completed` text is shown when no
    delta events were received in the turn, and suppressed (de-duplicated) when deltas were.
    The flag is reset on every `turn.started` event.
  - **Gemini CLI**: `message` events where `content` is a list of parts
    (`[{"text": "..."}]`) were silently dropped because the parser only handled the
    plain-string shape. Fixed by handling both the string and list-of-parts shapes.
  - **Claude Code**: `assistant` events with multiple `text` content parts only showed
    the first part (`break` after first match). Fixed by iterating all parts.
  - Tests updated: `test_item_completed_agent_message` split into two cases —
    `test_item_completed_agent_message_no_prior_delta` (must show text) and
    `test_item_completed_agent_message_suppressed_after_delta` (must suppress).

- **Live Output tab stuck on "Waiting for provider output…" placeholder (build 10156):**
  - Provider output arriving before the execution screen's DOM was fully mounted was queued
    in `_pending_output`, then `_flush_pending` drained it — but `_write_default_placeholders`
    ran in LIFO order after `_flush_pending`, overwriting the real output with the placeholder.
  - Fixed by: (1) swapping the `call_after_refresh` registration order so `_flush_pending`
    is always scheduled second (and therefore runs last in LIFO), and (2) guarding
    `_write_default_placeholders` with `_output_received` so it skips the output placeholder
    entirely when real content has already arrived.
  - Regression test added: `test_output_before_mount_does_not_show_placeholder`.

- **Events tab crash: `[$accent]` Textual CSS variable replaced with literal brand-green hex color in `RichLog` markup (build 10155):**
  - Clicking the Events tab during execution crashed because `[$accent]` is a Textual CSS
    variable, not a valid Rich markup color tag. `RichLog` uses Rich markup exclusively,
    causing a `MarkupError` whenever an event line was rendered on screen.
  - Fixed by replacing `[$accent]` / `[/$accent]` with `[#7cc800]` / `[/#7cc800]` (the
    brand-green hex color) in `_write_event_line`.
  - Regression test added to `test_tui_app.py` that switches to the Events tab and asserts
    no unresolved `$accent` tokens appear in the rendered output.

- **Codex provider: live output now streams in real time via `item.delta` events (build 10154):**
  - The Codex parser previously only captured text from `item.completed` agent_message events,
    meaning the TUI showed nothing until a full turn finished. Now handles `item.delta` with
    both `text_delta` and `content_delta` shapes so output appears token-by-token.
  - `item.completed` agent_message no longer duplicates the already-streamed text.
  - `command_execution` items now show the actual command (with `$` prefix) rather than just the exec ID.

- **Execution screen: "Waiting for provider output…" placeholder now clears on first real output (build 10154):**
  - The placeholder was permanently prepended to every provider run's log. The screen now
    tracks whether any output has arrived and clears the log on the first write so only
    real provider content is visible.

- **Gemini provider: tool_use events now show input details alongside the tool name (build 10154):**
  - Previously only `→ tool_name` was shown. Now the most relevant input field (path, command,
    pattern, query, etc.) is appended for better visibility.

- **Removed legacy S-prefix (standalone) task support (build 10149):**
  - The `S` prefix was a legacy concept never generated by the planner. Removed from
    `discover_tasks` pattern, `task_prefix`, `task_number`, `_extract_title`,
    `reconcile_task_status` regex, `check_pending_tasks`, `archive_previous_run`,
    the promise-tag pattern, and all prompts and tests.
  - Sort order simplified: T-tasks sort before R-tasks at the same number (explicit
    priority map replaces the old string comparison which had `R < S < T`).

- **`outcome_summary` dropped in `run_task` success path — inter-task reassessment never fired (build 10148):**
  - `run_task` constructed a new `TaskResult` on success without forwarding `outcome_summary`
    from `run_task_once`, so `_result_needs_reassessment` always saw an empty string and
    never triggered. Added `outcome_summary=result.outcome_summary` to both the success
    and failure return paths in `run_task`.

- **Reassessment agent had no project memory — ARCHITECT.md not injected (build 10148):**
  - `run_task_reassessment` built its instruction without reading ARCHITECT.md, so the
    architect agent updating pending tasks had no knowledge of permanent decisions,
    constraints, or lessons learned. Now reads ARCHITECT.md via `read_architect_md` and
    injects it as a section in the reassessment prompt.

- **Reassessment gate asymmetry — inner check was looser than outer check (build 10148):**
  - The outer guard (`_result_needs_reassessment`) required `"Downstream impact: possible"`,
    but the inner gate in `run_task_reassessment` only excluded `"Downstream impact: none"`,
    meaning any non-empty summary without "none" would pass through. Both gates now
    consistently require the explicit `"Downstream impact: possible"` signal.

- **`asyncio.run()` inside sync callback could nest event loops (build 10148):**
  - `on_task_done` called `asyncio.run(run_task_reassessment(...))` directly, which is
    fragile when called from within an async context. Replaced with
    `_run_reassessment_in_thread()` which runs the coroutine in a dedicated
    `ThreadPoolExecutor` thread, giving it a clean event loop in all contexts.

- **Execution agents clobbered `## Task Outcomes` table when rewriting PROGRESS.md (build 10148):**
  - The PROGRESS.md template shown in `execution-protocol.md` was missing the
    `## Task Outcomes` and separator sections that `PROGRESS_TEMPLATE` writes. Agents
    following the protocol example literally would drop these sections on rewrite, silently
    breaking `_record_task_outcome`. Added both sections to the protocol template and
    added an explicit rule: "Preserve the `## Task Outcomes` table — copy all existing rows".

- **R-tasks with same number as T-tasks sorted before them — wrong execution order (build 10148):**
  - `discover_tasks` sorted by `(number, prefix)` string, and `"R" < "T"` lexicographically,
    so an R05 fix task would execute before a pending T05 original task. Replaced string
    comparison with an explicit priority map: T/S tasks always sort before R-tasks at the
    same number.

### Changed

- **Graceful shutdown screen on Ctrl+C (build 10144):**
  - Instead of a blank terminal during the cleanup window, `action_quit` now pops
    back to the `SplashScreen` with subtitle "Shutting down…", lets the Matrix rain
    animate for 1 second, then calls `exit()`. The `_quit_event` is set immediately
    so any splash-hold sleep on the worker thread wakes up at once — no double delay.

- **SplashScreen animation + minimum display time (build 10142):**
  - Animation was not visible: worker started via `call_after_refresh` and immediately
    called `push_and_wait(PreRunScreen)` before a single `MatrixRain._tick` had fired.
  - Fix: moved the minimum-hold gate into `ArchitectApp.push_and_wait`. The worker
    thread sleeps for the remaining time (`1.5s - elapsed`) before pushing the next
    screen, keeping the event loop completely free so the animation runs at full 10 FPS.
    Reverted runner back to `call_later` (no need for `call_after_refresh` now).

- **Ctrl+C no longer flashes SplashScreen before exit (build 10143):**
  - Root cause: `PreRunScreen` had `ctrl+c → action_cancel (priority=True)` which
    called `self.dismiss(None)`, popping the screen and revealing the SplashScreen
    underneath for a visible flash before `SystemExit` fired.
  - Fix: removed the `ctrl+c` binding from `PreRunScreen.BINDINGS` entirely. The
    app-level `ArchitectApp` binding (`ctrl+c → action_quit → app.exit()`) now handles
    it directly, terminating without unwinding the screen stack.

- **SplashScreen now visible at startup — worker deferred past first render (build 10142):**
  - Root cause: `ArchitectAppRunner` scheduled the worker thread via `call_later`,
    which fires in the same event loop tick as `on_mount`. The worker immediately called
    `switch_and_wait(PreRunScreen)`, pushing it onto the app before the compositor had
    painted a single frame of `SplashScreen` — so the splash was silently buried and
    never visible.
  - Fix: changed `call_later` → `call_after_refresh` in `runner.py`. This guarantees
    one complete render cycle of `SplashScreen` before the worker thread is started,
    so the centered animated card is always visible during the startup window.

- **Startup SplashScreen restored as a proper centered card (build 10141):**
  - The previous consolidation replaced `SplashScreen` with a `WaitScreen` alias.
    `WaitScreen` is a full-screen log-viewer (title at top-left, scrolling log below)
    — visually identical to the terminal background with no border, so the startup
    screen was effectively invisible.
  - `SplashScreen` is now its own class again: a small centered card
    (`align: center middle` on the Screen, fixed 48×13 body with `border: round $panel`
    and `background: $panel 20%`) containing the app title, Matrix rain block, and
    subtitle — the same visual pattern as `ModeSelectionScreen` and other pre-run dialogs.
  - Added `test_splash_is_centered` to pin the layout: body must be at `x=16` (centered
    in an 80-wide terminal) and `y > 1` (not stuck at the top-left).

- **Tabbed scope picker now explains task breadth instead of implying fixed task counts (build 10140):**
  - Reworded the Goal tab scope hint to explain that scope controls how wide each planned task is, not how many tasks the planner should produce.
  - Replaced the misleading `2-4`, `5-10`, and `10+` task labels with descriptions aligned to actual planner behavior: atomic task, feature-area task, or subsystem task.

- **Loading screen invisible inside tmux — true-color forwarding (build 10139):**
  - Root cause: tmux overrides `TERM` to its own `default-terminal` value
    (`screen-256color`) for every pane it creates, and drops `COLORTERM` / `TERM_PROGRAM`
    entirely. Textual therefore detected a non-true-color terminal and rendered the
    animated `WaitScreen` with near-invisible colours (green on near-black background
    became indistinguishable from the background).
  - Fix 1: `_configure_terminal_colors()` is now called immediately after session
    creation. It sets `default-terminal xterm-256color` and `terminal-overrides *:Tc`
    at session scope (`-s -t SESSION`) so every pane starts with a proper xterm-compatible
    `TERM` and true-color (`Tc`) capability.
  - Fix 2: `TERM`, `COLORTERM`, `TERM_PROGRAM`, and `TERM_PROGRAM_VERSION` added to
    `_FORWARD_ENV_VARS` so the parent terminal's capability values are forwarded as a
    belt-and-braces fallback alongside the tmux option changes.

- **Startup screen now uses the same WaitScreen as between-stage waits (build 10138):**
  - Removed the separate `SplashScreen` class entirely. The startup screen is now
    `WaitScreen(title="Starting up…")` — the identical animated surface used during
    planning, retrospective, and reassessment waits. One consistent animated screen
    across the whole app lifecycle instead of two divergent implementations.
  - `SplashScreen` kept as a backwards-compatible alias (`SplashScreen = WaitScreen`)
    so existing imports and `isinstance` checks in tests continue to work.

- **Left/right arrow keys now reliably switch tabs (build 10138):**
  - Root cause: `action_next_tab` / `action_prev_tab` called `_auto_focus_active_tab()`
    synchronously, which focused a widget inside the new tab before the tab-switch event
    had settled. Textual's `TabbedContent._on_tab_pane_focused` then fired for the old
    focused widget and reset `active` back — making every first arrow press a no-op.
  - Fix: defer `_auto_focus_active_tab()` via `call_after_refresh` so the tab switch
    lands before focus moves. Same fix applied to `_try_activate_tab` (number-key jumps).
  - Added regression test `test_right_arrow_actually_switches_tab_from_radioset` that
    presses the right key while a `RadioSet` has focus and asserts the tab changes.


  - Set theme `primary` to architect green (`#7cc800`) so all interactive widget highlights (ListView rows, RadioSet focus, DataTable cursor, Tabs underline) use green instead of the default Textual blue.
  - All standalone TUI apps (`ConfigApp`, `ListApp`, `StatusApp`, `MonitorApp`, `CircuitApp`, `LogsApp`, `WaitApp`) now apply the architect theme on mount — previously only `ArchitectApp` applied it.
  - Added green tab hover/active styling (`Tab.-active`, `Tab:hover`, `Underline > .underline--bar`) to `ExecutionScreen` (was already present on `PreRunScreen`).
  - Exported `apply_architect_theme()` helper from `the_architect.tui.app` for any future standalone app.

- **Splash screen animation now renders correctly (build 10137):**
  - Replaced `align: center middle` on the screen root (which conflicts with docked `Header`/`Footer`) with a `1fr` container using `align: center middle` internally. The MatrixRain grid now has a guaranteed region to paint into on all terminal sizes.

- **Left/right arrows always switch tabs (build 10137):**
  - Removed the "smart" deferral that let left/right act as text-cursor movement when the Goal TextArea had focus. Both keys now unconditionally switch tabs on any pre-run screen, matching the user's intent.
  - `action_next_tab_smart`, `action_prev_tab_smart`, and `_focus_is_in_goal_textarea` removed from `PreRunScreen`.
  - Up/down key interception removed from `GoalTextArea._on_key` (screen-level priority bindings already handle vertical focus movement).

- **Focus movement (`up`/`down`) bug in Goal tab (build 10137):**
  - Fixed `_move_focus_within_active_tab` incorrectly calling `.first()` on a `query()` result (raises `NoMatches` in Textual 8.x, not return `None`) — the outer exception handler was swallowing the error and returning `False`, leaving focus unchanged.
  - Fixed `RadioSet` focus: `RadioSet` has `can_focus_children=False`, so focusing inner `RadioButton` widgets directly is silently ignored. Now focus the `RadioSet` container itself.
  - Simplified `_auto_focus_active_tab` to use the same approach: focus the first `focusable` child widget in the active tab (RadioSet or otherwise) without special-casing inner radio buttons.

### Changed

- **Renamed the pre-run `Run mode` tab to `Options` (build 10130):**
  - The tab now uses the clearer `Options` label throughout the tabbed pre-run UI.
  - The Options tab now also follows the same arrow-key contract as the rest of the app: left/right change tabs, up/down move between controls on the current page.
  - The Goal text area now explicitly hands up/down back to the screen so section navigation stays consistent there too, while left/right remain text-cursor movement.

### Added

- **Self-update check at startup (build 10152):**
  - The Architect now checks PyPI for a newer version during the splash screen
    animation (`https://pypi.org/pypi/the-architect/json`, 5 s timeout, silent
    on network failure). Runs at the very start of `_tui_flow` — before provider
    selection or any other prompts — so it never blocks the normal flow.
  - If an update is found, a styled notification screen is shown with the
    current and latest version numbers. `Enter` continues with the installed
    version; `U` runs `pip install --upgrade the-architect` and re-executes
    the original command via `os.execvp` so the user lands in the updated
    version seamlessly. Skipped in `--headless` mode.
  - New module: `the_architect/core/self_update.py` — `check_self_update()`,
    `run_self_update()`, `_is_newer()`.
  - New TUI screen: `SelfUpdateScreen` in `tui/screens/pre_run.py` — architect
    green title, same visual language as `UpdateActionScreen`.
  - 12 new unit tests in `tests/test_self_update.py`.


- **Pre-run UI Phase A — back navigation, pre-fill, persistence (build 10109):**
  - Backspace on any pre-run TUI screen (Goal, Scope, Model, Agent, Mode) now navigates back to the previous screen instead of cancelling the entire run. The orchestration loop in `_collect_planning_prompts` is now a state machine: pressing Back on the Scope screen returns to Goal, on the Model screen returns to Scope, and so on.
  - Scope, architect model, and execution agent selections are **persisted** to `architect.toml` after each run and **pre-filled** on the next run. Repeat runs become "edit the goal, hit Enter" instead of re-answering every prompt.
  - New `architect_model` and `last_scope` fields on `ArchitectConfig` store the user's last selections. Goal text is intentionally NOT persisted (risk of accidental re-submission).
  - `BACK_SENTINEL` module-level object distinguishes "go back" from "cancel" (`None`) in screen dismiss values.
  - New constructor parameters on all pre-run screens: `ScopeScreen(initial_scope=)`, `ProviderSelectionScreen(initial_provider_name=)`, `StringListPickerScreen(initial_value=)`, `ModeSelectionScreen(initial_free=, initial_persistent=, initial_integrity=, initial_budget=)`.
  - 12 new tests covering back navigation, pre-fill, and config persistence.

### Fixed

- **Legacy stdout-ANSI spinners and countdowns removed — TUI is the only UI (build 10136):**
  - Root cause of the leftover-animation-after-Ctrl+C: `_start_live_spinner` / `_start_wait_spinner` / `_spin` / `_countdown` / `_start_tui_startup_animation` all spawned background threads or blocked the main thread writing ANSI escape sequences to stdout **while the Textual app was mounted**. The two surfaces fought for the screen: Textual repaints overdrew the spinner, and when the Textual app tore down on Ctrl+C the still-running spinner thread's final frame (or its `\r\033[2K` erase sequence) briefly appeared before the shell prompt returned.
  - Deleted: `_SpinnerHandle`, `_BlockAnimationHandle`, `_scanner_animate`, `_wait_animate`, `_tui_startup_animate`, `_start_live_spinner`, `_start_wait_spinner`, `_start_tui_startup_animation`, `_live_spinner`, `_spin`, `_countdown`, `_stop_spinner`. ~378 lines of legacy pre-TUI animation machinery.
  - Deleted call sites: `_prompt_architect_model` (model list fetch), `_prompt_exec_agent` (agent list fetch), `run_planning_mode` (project context setup, planning start), `_run_tasks_raw` (task-start spinner, reassessment, between-task countdown), free-mode fetch, retrospective round spinner.
  - Result: only the Textual `SplashScreen` / `WaitScreen` / `ExecutionScreen` paint animations now. Nothing outside Textual writes animated ANSI to stdout. No threads painting concurrently with the TUI, no leftover frames after app teardown.
  - Tests: the three test classes that validated spinner behaviour (`TestCountdownANSI`, `TestSpinANSI`, `TestExecutionStartupStatus`, `TestSpinnersSilentInTuiMode`) are now empty placeholders documenting the removal so nobody re-introduces spinners without noticing.
  - `_ansi_supported` is kept (still used by `alternate_screen` and TUI gating).

- **Startup and wait loading screens consistently show The Architect animation (build 10131-10135):**
  - **Build 10135:** Two user-visible regressions addressed.
    - Removed the plain-ANSI pre-Textual startup loader that wrote a Matrix-rain block directly to stdout before the Textual app mounted. In practice it produced a left-aligned green flash for ~0.2s that users found distracting. The real `SplashScreen` already covers the gap once Textual takes over.
    - Rain trail palette rebalanced for dark terminal backgrounds. The old palette used `dim $accent` / `dim $text-muted` which rendered as near-invisible dark green on VSCode / Ghostty dark themes — the rain was being painted, but at a brightness level close enough to the background that users saw an empty block. New palette keeps every trail cell at a readable brightness (`#5a9400` mid-green tail) so the animation is visible on any dark theme without relying on surrounding chrome for contrast. `$text-muted` is no longer emitted at all (it also couldn't be parsed by Rich — see build 10132 note below).
  - Build 10134: Locked `#splash_body` to a fixed `48 × 13` size so the splash layout matches the working planning-screen pattern. Earlier `height: auto` + `align: center middle` caused the rain row to collapse in live terminals.
  - Build 10133: `MatrixRain` is a `Static` subclass that calls `self.update(frame)` on mount and every tick instead of overriding `Widget.render()`. Earlier versions returned a `rich.text.Text` that some Textual releases skipped painting when its shape didn't match the widget region.
  - Build 10132: `MatrixRain._resolve_style` never leaks Textual CSS tokens (`$accent`, `$accent-muted`) into Rich. Textual's `text-muted` theme variable is `"auto 60%"`, not a colour; Rich was raising `StyleSyntaxError` for every trail cell and Textual was swallowing the error.
  - Regression tests: `test_splash_rain_styles_are_rich_parseable` (every emitted style parses as a Rich style), `test_splash_animation_fits_short_startup_panes` (rain region sits between title and subtitle).
  - Planning, reassessment, and other wait screens now include the same Architect animation instead of relying only on a single spinner glyph.
  - Added regression coverage for visible splash animation in short startup panes and wait-screen animation rendering.

- **Planning and execution provider output no longer gets hidden behind screen switches (build 10117):**
  - The TUI app's internal execution-screen helper was switching the active screen as a side effect of ordinary output/footer/detail updates.
  - During planning, retrospective, and reassessment, those background writes could replace the visible wait overlay for a frame, producing a brief blink while leaving the provider log effectively invisible.
  - Fixed by making execution-screen creation lazy without changing the active screen; only explicit transitions such as `switch_to_execution()` now replace the current screen.
  - Added a regression test that verifies streamed execution output does not dislodge a visible wait overlay.

- **Planning wait screens now keep early provider output instead of dropping it during mount (build 10118):**
  - The standalone `WaitApp` path used during planning could receive provider log lines immediately after the background app thread started but before `WaitScreen` had mounted its `RichLog` widget.
  - Those early `append_log()` and `set_detail()` calls were swallowed by broad exception handling, which made the planning window look empty except for an occasional blink.
  - Fixed by buffering wait-screen detail/log updates until after the first refresh, then flushing them into the mounted widgets.
  - Added regression tests for both standalone planning wait screens and in-app wait overlays.

- **Planning start regression fixed after a TUI gating refactor (build 10120):**
  - A follow-up change incorrectly switched `run_planning_mode()` to a `use_tui` gate even though that function does not own a `use_tui` parameter.
  - That made planning unstable right at startup.
  - Fixed by restoring the original planning gate so the planner again follows the established Textual-mode detection path.

- **Wait-overlay teardown no longer pops unrelated top screens (build 10121):**
  - `ArchitectApp.hide_wait()` previously called `pop_screen()` unconditionally.
  - If another overlay was stacked above the wait screen, hiding wait would dismiss the wrong screen and corrupt the visible lifecycle.
  - Fixed by only popping when the wait screen is actually the active top screen, and added a regression test for the stacked-overlay case.

- **Arrow-key navigation is now consistent across the tabbed pre-run UI (build 10126):**
  - Left/right arrows are now reserved for moving between tabs, except inside the goal text field where they still move the text cursor.
  - Up/down arrows now move between sections and selectable controls on the active page.
  - Initial focus now lands on concrete selectable controls instead of container widgets, so vertical navigation works consistently in resume and pre-run flows.
  - Resume-screen up/down bindings now run at high priority so focused radio controls do not consume them for in-group navigation.
  - The tabbed pre-run screen now scopes vertical focus movement to the active tab, including the Options tab, so left/right no longer act like option-selection keys there.
  - Added regression coverage for both the binding map and real focus movement.

- **PreRunScreen was blocking the event loop, freezing splash animation AND hiding planning/execution output (build 10116):**
  - `on_mount()` called `provider.list_models()` and `provider.list_agents()` synchronously. Both shell out to `opencode models` / `claude agents` via `subprocess.run(... timeout=15)`, so the Textual event loop was frozen for up to 15s per provider while the screen mounted. During that freeze:
    - the splash-screen Matrix-rain animation behind the pre-run screen stopped ticking,
    - the user saw a blank-looking screen with no title / no animation,
    - the subsequent planning/execution wait-screen RichLog stopped receiving updates because the event loop was still catching up to the queued work when the worker thread started streaming.
  - Fixed by moving provider data fetching into a Textual ``run_worker(thread=True)`` worker. The UI now shows a "Loading models…" hint immediately; once the worker returns, ``call_from_thread`` hops the list update back onto the event loop. Same change for provider-switch refreshes.
  - This one fix explains issues (1) "splash has no animation / no title" and (4) "planning screen not showing progress from agents" — both were downstream symptoms of the same blocked event loop.

- **Arrow keys now switch tabs on the pre-run screen (build 10116):**
  - Plain ``right`` / ``left`` switch to the next / previous tab. When focus is on the Goal TextArea the actions defer to the TextArea's cursor movement, so editing the goal text still works exactly like a normal editor. ``Ctrl+Tab`` / ``Ctrl+←`` / ``Ctrl+→`` still force a tab switch even from inside the TextArea.
  - Implemented via two new screen actions — ``next_tab_smart`` / ``prev_tab_smart`` — that check the focused widget before delegating.
  - Regression guards: ``test_right_key_binding_wired_to_smart_action`` and ``test_smart_arrow_defers_when_focus_in_goal_textarea``.

- **Tab selector colour — fixed CSS selector so the override actually applies (build 10116):**
  - Previous build (10115) tried ``PreRunScreen Tabs Underline > .underline--bar`` but Textual's selector matching on component classes needs the rule *without* the intermediate ``Tabs`` parent. The selector is now ``PreRunScreen Underline > .underline--bar`` and ``PreRunScreen Tab.-active``, which correctly overrides the default blue / grey with ``$accent`` (brand green).
  - Added explanatory comment in the DEFAULT_CSS block so the next editor doesn't "tidy" the selectors back into a broken form.

- **Architect model prompt shown again after tabbed PreRunScreen submit (build 10115):**
  - When the user picked "(use provider default)" for the architect model in the tabbed screen, the value returned as empty string. `run_planning_mode` then saw a falsy string at its `if architect_model_override:` check and prompted again — exactly the behaviour the tabbed screen was meant to replace.
  - Fixed by adding a `_tabbed_model_collected` flag on `ArchitectConfig` (same pattern as `_tabbed_mode_collected`). When set, `run_planning_mode` resolves the provider default explicitly instead of prompting.

- **Tab selector colour matches The Architect green (build 10115):**
  - The `TabbedContent` underline and active-tab text used Textual's default blue / grey. Overridden to `$accent` (brand green) via scoped CSS selectors on `Tabs Underline > .underline--bar` and `Tabs Tab.-active`. `Tabs Tab:hover` also picks up `$accent` so the hover state tracks the brand.

- **Enter now submits the pre-run form — Shift+Enter inserts a newline (build 10115):**
  - Matches chat-app convention (Claude, ChatGPT, etc.) so users coming from any modern prompt UX get the expected behaviour. The Goal tab's hint line now reads `Enter = submit, Shift+Enter = newline`.
  - The screen-level `Enter` binding is `priority=True`, so Enter fires the submit regardless of focus. A new `GoalTextArea` subclass intercepts `shift+enter` to insert a literal `\n` into the TextArea.
  - Added `test_enter_submits_from_goal_textarea` and `test_shift_enter_inserts_newline_in_goal_textarea` as regression guards.

- **Scope moved back into the Goal tab (above the goal text) (build 10115):**
  - User feedback: a separate Scope tab felt like over-fragmentation; Scope + Goal are tightly coupled concerns. Scope RadioSet now sits above the Goal TextArea inside the Goal tab. Number-key hotkeys reverted to `1=Goal, 2=Provider (or Models), 3=Models (or Options), 4=Options`.
  - Regression guard: `test_scope_radio_lives_in_goal_tab` asserts there is no `tab_scope` and that the RadioSet + TextArea both live in the Goal tab pane.
  - Also added `test_only_one_provider_radio_selected_at_a_time` that clicks multiple provider radio buttons in sequence and asserts `RadioSet.pressed_button` stays pinned to exactly one.

- **PreRunScreen crash when switching to non-OpenCode provider (build 10114):**
  - Selecting Codex / Claude Code / Gemini after OpenCode caused a crash because the Models tab used `remove_children() + mount()` to rebuild its contents on every provider change — a Textual mount/unmount race that left query_one lookups targeting widgets that were simultaneously being torn down and re-added.
  - Fixed by switching the Models tab to in-place `ListView.clear() + append()` updates with `display` toggles for agent widgets. The tab is now composed once during `compose()` and only its contents change, never its widget tree.
  - Also fixed `_show_agent` being computed once across all providers in `__init__` instead of recomputing per-active-provider. The tab now correctly hides the "Execution agent" ListView when the active provider doesn't support agents (e.g. Codex), regardless of what other installed providers support.
  - Added regression test `test_switching_provider_does_not_crash` that switches from OpenCode to Codex and asserts no exception and correct list refresh.

- **PreRunScreen Models tab was empty (build 10113):**
  - `_rebuild_models_tab()` queried for `Vertical` but `TabPane` is not a `Vertical` subclass, so the query silently failed and the loading placeholder was never replaced with model/agent lists.
  - Fixed by querying for `TabPane` instead. Added `loguru` logger calls in key failure paths so similar issues surface in debug logs.

- **Mode selection screen shown again after tabbed PreRunScreen submit (build 10113):**
  - The separate `_prompt_mode_selection()` call still fired even when the tabbed screen had already collected mode settings, because the guard condition only checked `persistent or free_mode` — both default to False.
  - Fixed by adding `_tabbed_mode_collected` flag on `ArchitectConfig` after the tabbed screen returns, and checking it in both mode selection guard conditions.

- **TUI — ESC pause menu during execution and wait screens (build 10100):**
  - ESC on the execution or wait screen now opens a modal pause menu with three choices: `[C]ontinue` (dismiss and resume), `[D]etach` (tmux detach-client so the run keeps going and can be reattached with `tmux attach`), or `[E]xit` (hard-kill the run, same as Ctrl+C).
  - Previously ESC on these screens did nothing (execution) or quit the whole app (wait). Neither matched the principle that a stray keystroke must never tear down a multi-minute planning or execution run.
  - Ctrl+C remains the immediate hard-stop path — no confirmation, matching terminal convention. Inside the menu, Ctrl+C still resolves to "exit" so users can escalate mid-decision.
  - The Detach button is disabled when not inside a tmux session (with tooltip explaining why), and the footer hint updates accordingly. On detach failure (tmux not installed, command non-zero) the menu falls back to "continue" rather than stranding the user.
  - New `the_architect/tui/screens/pause.py` houses `PauseMenuScreen` and `_tmux_detach_client`. `ArchitectApp.show_pause_menu()` and `WaitApp.show_pause_menu()` expose the overlay trigger; both guard against stacking multiple menus on repeated ESC presses.
  - Execution screen footer text rewritten: `(idle)  [o]utput / [e]vents / [d]etails  ·  Esc=pause menu  ·  Ctrl+C=stop` (plus `Ctrl+B D detaches` when running under tmux).

- **TUI — version string in the Header on every screen (build 10097):**
  - The Textual `Header` now shows `"The Architect  v1.2.0 (build 10097)"` on every screen, next to the Matrix-rain title on the splash and above every pre-run prompt, execution tab, and diagnostic view.
  - Implemented by setting the reactive `App.title` in `on_mount` (not the `TITLE` class attribute — a test pins that to `"The Architect"` exactly, and changing the attribute would also duplicate the version into the splash's big brand line). `sub_title` stays reserved for `set_status()` run-scoped updates like `"T03 · starting · Build API"`.
  - New helper `_architect_header_version()`: returns SemVer from `the_architect.version.__version__` (installed builds), and when running from the repo it probes the project-root `version.py` for `__build__` so developers also see the build counter. Falls back silently to just the SemVer on any failure — the root `version.py` is dev-only and isn't packaged into the wheel.
  - `run_single_screen`'s fallback harness app gets the same treatment so standalone screen flows (tests, one-off calls) also show the version.

### Fixed

- **TUI — execution and planning windows now actually display output (build 10100):**
  - `tui_execution_session` and `tui_wait_session` used to launch a second `ArchitectApp` / `WaitApp` in a background thread whenever they were called from inside a worker running under `ArchitectAppRunner`. The second app was invisible (Textual can only render one app on a terminal), so all stream output and spinner updates went to an off-screen instance while the visible app was still showing `SplashScreen` ("Starting up…").
  - Both sessions now detect an active `ArchitectAppRunner` via `active_runner()` and reuse its already-running app: the execution session calls `app.switch_to_execution()` and binds its `TextualStreamRenderer` to the live app; the wait session reuses the same app as its overlay host. No extra thread is spawned in this path, no extra app is created, and the user sees the actual stream from the first line.
  - Legacy callers with no runner still get the previous dedicated-app behaviour as a fallback.

- **Runner — Ctrl+C now actually stops the backend provider (build 10100):**
  - `stream_provider` used to kill the child subprocess only in its generic `except` branch. If the Textual event loop exited (user hit Ctrl+C / app quit) while the worker thread was blocked on `process.wait()`, the daemon thread was abandoned but the spawned `opencode` / `claude` child kept running in the background — the exact symptom the user reported.
  - Fixes:
    - Subprocesses are now spawned with `start_new_session=True` on POSIX so the whole provider process tree can be killed via `killpg`.
    - A new `_kill_process_tree` helper signals the whole session on shutdown, with `proc.kill` as a Windows backstop.
    - A new registry (`_register_process` / `kill_active_subprocesses`) lets the outer shutdown path terminate every live provider subprocess, and `ArchitectAppRunner.run()` calls it in its `finally` block. `stream_provider` itself now has a `finally` block that kills any still-running child and unregisters it, plus an explicit `CancelledError` handler that kills before re-raising — so the subprocess cannot outlive the function no matter which path wins the race.

- **TUI — ESC during execution no longer risks aborting a run by accident (build 10100):**
  - See the ESC pause menu entry above — stray ESC keystrokes now open a deliberate Continue / Detach / Exit overlay instead of falling through to any quit action.

- **TUI — splash no longer renders blank; rain now genuinely centred (build 10095):**
  - Build 10092's attempt to centre the rain by making `#splash_body` `width: auto` and giving its children `width: 100%` created a circular layout constraint — the parent waited for the widest child to size itself, the children waited for the parent to give them a width, and everything collapsed to size `(4, 4)`. The title, subtitle, and rain row all rendered as 0×0 regions, so the splash appeared empty.
  - Verified the new layout with a `run_test` harness at 80×24: body is 48-wide and centred on the terminal (16-cell gap on each side); the 24-wide rain sits at offset 10 inside the 44-wide inner row, i.e. dead centre; title and subtitle occupy the full inner width with centred text.
  - Fix: back to a fixed-width `#splash_body` (48) so `width: 100%` children have something real to expand against. Rain row height pinned to 7 (matches `MatrixRain.ROWS`) so the layout doesn't re-flow frame to frame. `align-horizontal: center` on the `Horizontal` wrapper actually does the centring — which is the behaviour that `Horizontal` supports but `Vertical` apparently doesn't in current Textual.

### Changed

- **TUI — resume screen now uses a radio group for Execute/Replan instead of Buttons (build 10091):**
  - The resume screen (shown when The Architect detects pending tasks) was a hybrid: mode toggles as Checkboxes, plus two Textual `Button` widgets for Execute / Replan with custom keyboard shortcuts (Enter, Ctrl+R). That made it feel unlike every other options screen in the app.
  - Replaced the Buttons with a `RadioSet` at the top of the form:
    - **Execute** — run the pending tasks as-is (default).
    - **Replan** — discard pending tasks and plan again.
  - Arrow keys move between the RadioSet, mode Checkboxes, and the budget Input. Space toggles the focused item. Enter submits the whole form using whichever action is currently selected. Esc cancels. Same keyboard model as `ModeSelectionScreen`.
  - Added `BlankOffRadioButton` to `the_architect/tui/widgets.py` — same fix as `BlankOffCheckbox`: the stock Textual `RadioButton` renders its `●` glyph in both states, communicating selection only through colour. The new widget shows an empty slot for unselected options so the selected one is unambiguous.
  - Kept `action_execute` / `action_replan` as thin submit shims to preserve the existing test API. `on_button_pressed` is gone along with the Buttons.
  - CSS removes the default RadioSet border so the radio group reads as a form field rather than a boxed-off widget.

### Changed

- **TUI — screens no longer repeat "The Architect" below the Header (build 10090):**
  - Textual's `Header` widget already renders the app title (`TITLE = "The Architect"`) and sub-title on every screen. Every screen was then duplicating "The Architect" again as the first `Static` line of its body — e.g. `"The Architect  select provider"`, `"The Architect  configure run"`, `"The Architect config"`.
  - Dropped the prefix. Screen titles now state the screen purpose only:
    - Provider: `"Select provider"`
    - Goal: `"What do you want to build?"`
    - Scope: `"Task scope"`
    - Mode selection: `"Configure run"`
    - Resume: `"Resume run"`
    - Config: `"Configuration"`
    - Status / Monitor / Circuit / Logs / List: `"Status — {project}"`, `"Monitor — {project}"`, `"Circuit breaker — {project}"`, `"Logs — {project}"`, `"Tasks — {project}"`.
  - `SplashScreen` keeps its standalone `"The Architect"` title — that screen is the brand moment and has no other body content to label.

### Fixed

- **TUI — Matrix-rain splash animation now centred like the old spinner (build 10089):**
  - When the braille spinner was replaced with `MatrixRain` (build 10088), the splash composed its children directly under the `Screen` rather than inside a centring container. `MatrixRain` uses `width: auto`, so it sized to its grid and hugged the left edge of the screen instead of sitting centred under the title.
  - Wrapped the splash children back in a `Vertical(id="splash_body")` with `align-horizontal: center` so the title, rain block, and subtitle stack centred together — matching the original braille-spinner layout. Cleaned up stale CSS that referenced `#splash_body` before it existed.

### Changed

- **TUI — branded theme and Matrix-rain loading animation (build 10088):**
  - Registered an `architect-dark` Textual theme that overrides the default accent colour. Textual's default `$accent` is orange (`#ffa62b`); The Architect's original Rich/questionary CLI used a vivid lime green (`ARCHITECT_GREEN = #7cc800`). Every screen title, spinner, header highlight, and any `$accent` reference is now that green instead of orange.
  - `$warning` is kept as the default Textual orange so warning titles (update-warning, pending-tasks) still read as warnings rather than being visually conflated with the brand colour.
  - `$success` is kept as Textual's `#4EBF71` so on-state checkbox markers and success banners stay distinct from the brand green.
  - Theme is registered and applied inside `ArchitectApp.on_mount` before the first screen is pushed, so the splash never flashes orange. The `run_single_screen` fallback harness applies the same theme.
  - **Replaced the braille spinner with a Matrix digital-rain animation** across both the splash screen and the wait screen. The Architect is a character from The Matrix, so the loading animation now nods to that origin instead of using Textual's generic braille dots.
    - New `MatrixRain` widget in `the_architect/tui/widgets.py` — a 24×7 grid of falling half-width katakana, digits, and punctuation with bright-green heads and fading trails.
    - New `next_matrix_frame(index)` function returns a deterministic glyph per frame index; the wait-screen single-character spinner uses this so every Matrix-rain surface stays in sync.
    - `SplashScreen` now yields `MatrixRain` in place of its braille `Static`; `WaitScreen._tick_spinner` pulls glyphs from `next_matrix_frame` instead of cycling braille frames.
    - `WaitScreen.SPINNER_FRAMES` kept as a class constant for backward compatibility with tests that reference it.

### Fixed

- **TUI — arrow keys now actually move focus on mode-selection and resume screens (build 10085):**
  - The `up` / `down` bindings pointed at `focus_previous` / `focus_next`, which are plain methods on Textual's `Screen`, not `action_*` handlers. Bindings silently no-op when the referenced action doesn't exist, so arrow keys did nothing — users had to Tab between Checkboxes and the budget Input.
  - Added `action_focus_previous` / `action_focus_next` shims on `ModeSelectionScreen` and `ResumeScreen` that delegate to the screen's built-in focus-movement methods.
  - Tightened the existing regression test: it now asserts the focused widget's id genuinely changes (was `assert ... or second_focused is not None`, which let the regression slip through). Mirrored test added for `ResumeScreen`.

- **TUI — off-state Checkboxes now render blank instead of a dim "X" (build 10087):**
  - Textual's stock `Checkbox` always renders the same `X` glyph and communicates on/off only through colour. On dark themes the off-state `X` was still visible and easy to misread as selected.
  - Introduced `the_architect/tui/widgets.py::BlankOffCheckbox`, a `Checkbox` subclass that overrides the `_button` property to render a space when `value` is false and `X` when true — the glyph itself changes, not just its colour, so the fix is theme-independent.
  - `ModeSelectionScreen` and `ResumeScreen` now use `BlankOffCheckbox` for all their toggles. On-state is painted bold green (`$success`) via scoped CSS so selections remain unambiguous.
  - Earlier attempt (build 10086) painted the off-state `X` in the background colour via CSS only — replaced because it relied on theme colours matching and left the glyph physically present. Superseded by the widget-level fix.

### Changed

- **TUI Phase 24 — consistent keybindings and hint language across all screens (build 10083):**
  - **Enter universally submits / confirms.** Reverted the Phase-23 detour that replaced Enter with `Ctrl+S`. On every screen:
    - ListView screens (provider, scope, model picker, agent picker): Enter selects the highlighted item.
    - Mode selection: Enter submits. Priority binding so it fires before the focused Checkbox's toggle handler — Space remains the way to toggle a Checkbox.
    - Resume: Enter executes (priority), Ctrl+R replans.
    - Goal: Ctrl+Enter / Ctrl+S submit because TextArea owns Enter for newlines (only exception, unavoidable).
    - Update warning and pending tasks: Enter confirms / continues.
  - **Removed the Submit / Cancel buttons** from the mode-selection screen. They were an inconsistency — no other screen had them. Now the screen is a pure keyboard form like all the others.
  - **Arrow keys navigate form fields.** Added `up` / `down` → `focus_previous` / `focus_next` bindings on the mode-selection and resume screens so users can move between Checkboxes and Input fields with arrows, matching the arrow navigation the ListView-based screens use.
  - **Unified hint-text language.** Every screen's footer hint now follows one structure — navigation · primary action · cancel — using the same separator (`·`), same capitalization, same abbreviations. Example: `↑↓ navigate · Space toggle · Enter submit · Esc cancel`.
  - Added test `test_arrow_keys_move_focus_between_fields` covering the new arrow navigation on mode selection.
  - 92 TUI tests pass. Full suite: **2414 passed, 4 skipped**. Lint clean, mypy clean on 45 source files.

- **TUI Phase 23 — Enter / Space keybinding conflicts and late-message `ScreenStackError` (build 10082):**
  - **The real cause of the `ScreenStackError`**: pressing Enter on the mode-selection or resume screen fired TWO handlers — the screen's `action_submit` (which dismissed the screen) AND the focused Checkbox's toggle handler (which queued a `Footer.recompose` via `InvokeLater`). When the queued recompose fired, the screen was already gone and `self.app.screen` crashed with `No screens on stack`.
  - **Fix**: switched Enter-as-submit to `Ctrl+S` / `Ctrl+Enter` / `F10` with `priority=True` on forms that contain Checkboxes or TextAreas. Space still toggles the focused Checkbox (Textual default), Enter no longer submits or conflicts with widget bindings. The resume screen switched Enter-to-execute to `Ctrl+S` / `F10`. Goal screen already used Ctrl+Enter; made it priority.
  - Added explicit **Start** / **Cancel** buttons to the mode-selection screen and **Execute** / **Replan** buttons to the resume screen so users don't need to remember the key shortcuts — click them, Tab to them, or use the priority keys.
  - Added a defensive `_handle_exception` override on `ArchitectApp` that swallows `ScreenStackError` specifically. This catches any remaining late-message races between dismiss and next push without hiding real errors (anything other than `ScreenStackError` still propagates).
  - Updated hints on both screens to reflect the new keybindings.
  - 91 TUI tests pass. Full suite: **2413 passed, 4 skipped**. Lint clean, mypy clean on 45 source files.

- **TUI Phase 22 — `ScreenStackError` after provider selection (build 10081):**
  - `switch_and_wait` monkey-patched `Screen.dismiss` to fire a completion event, then called Textual's `switch_screen`. When the last pre-run prompt dismissed, Textual popped the screen stack — but because `switch_screen` replaces rather than stacks, the stack could end up empty, and the next message (Footer recompose, cursor position, anything) would hit `ScreenStackError: No screens on stack`.
  - Reverted `switch_and_wait` to a thin wrapper around `push_and_wait`. The brief return to the animated branded `SplashScreen` between pre-run prompts is visually acceptable (and honestly looks like an intentional loading moment) since the splash is not the tabbed execution viewport anymore. No more monkey-patching of `Screen.dismiss`, no more stack corruption.
  - Full suite: 2413 passed, 4 skipped. Lint clean, mypy clean on 45 source files.

- **TUI Phase 21 — no more flash between pre-run screens (build 10080):**
  - The root cause of the between-screen flash: every `run_*_screen()` call was booting a fresh harness app when no runner was active, and when a runner *was* active, consecutive prompts pushed screens onto the tabbed execution viewport that `ArchitectApp` had eagerly mounted on startup. Between dismiss and the next push, the execution screen became visible again and looked like "exiting to tabs".
  - `ArchitectApp` now mounts a new `SplashScreen` by default — a simple branded surface with "The Architect", an animated braille spinner at 10 FPS, and an updatable subtitle. The execution screen is **lazy** — created only when the worker actually starts task execution (first output line, footer update, or explicit `switch_to_execution()`).
  - Added `ArchitectApp.switch_and_wait(screen)` — like `push_and_wait` but uses `switch_screen` semantics so consecutive prompts *replace* the current screen instead of stacking on top of it. `run_single_screen()` now uses `switch_and_wait` when a runner is active, which means the pre-run flow becomes a seamless chain: provider → goal → scope → model → agent → mode, each replacing the last with no flash.
  - Finished Phase 16: `ResumeApp` and `ModeSelectionApp` were still `App` subclasses that required their own `App.run()`. Converted both to `Screen` subclasses (`ResumeScreen`, `ModeSelectionScreen`) with legacy aliases preserved. They now dismiss with their value and route through the active runner's `switch_and_wait`.
  - Moved all pre-run interactive work (provider selection, `_collect_planning_prompts`, mode selection) inside the same `ArchitectAppRunner` as `_run_main` via a new `_tui_flow` closure in `main()`. One app instance, one alt-screen takeover, every screen swaps in-place.
  - `ExecutionScreen` now buffers output/event/footer updates when called before mount completes, so callers that push content while the screen is still switching don't silently lose messages.
  - `ArchitectApp._thread_safe_call` replaces direct `call_from_thread` calls across the app's public hooks — it prefers `call_from_thread` when invoked from a foreign thread and falls back to a synchronous call when already on the event loop (makes the app's hooks safe to use from unit tests as well as from the worker).
  - Updated tests: `test_tui_app.py` now reflects lazy-execution-screen creation; `test_tui_resume.py` and `test_tui_mode_selection.py` use a small harness app to mount each screen. Full suite: **2413 passed, 4 skipped**. Lint clean, mypy clean on 45 source files.

- **TUI Phases 18 + 19 — persistent status bar and help overlay (build 10079):**
  - **Phase 18** — `ArchitectApp.set_status(text)` updates the app's `sub_title`, which Textual's `Header` widget renders next to the title on every screen. The TUI execution callbacks (`on_task_start`, `on_attempt_start`) now call it so users see the current task / phase / attempt / model in the header regardless of which screen is active. Thread-safe via `call_from_thread`. 2 new tests verify the sub-title update and clear paths.
  - **Phase 19** — new `HelpScreen` modal. Press `?` on any TUI screen to see a scrollable table of every key binding active on the screen underneath, plus the app-wide globals (`?`, `q`, `ctrl+c`). Dismisses on `?` or `Esc`. Added `collect_screen_bindings()` helper that introspects a screen's `BINDINGS` and de-duplicates keys. 5 new tests (3 for the collector, 2 for the screen + action wiring).
  - Full suite: 2412 passed, 4 skipped. Lint clean, mypy clean on 45 source files.

### Deferred

- **TUI Phase 20 — remove prompt_toolkit / questionary fallbacks:**
  Originally planned for this round but deferred after a careful audit. These libraries are still the fallback path for non-TTY, `NO_COLOR=1`, `TERM=dumb`, and `--no-tui` interactive use. Removing them would require either:
  1. rewriting all fallbacks (~500 lines, touches 50 references across `cli.py`), or
  2. dropping interactive fallback support entirely and documenting that `--no-tui` now requires `--goal` / `--scope` / `--context` flags — a real capability regression.
  Neither option lands cleanly in a single round, and the existing fallbacks are working correctly. The TUI is the default on TTY as of Phase 8 and delivers the one-app experience as of Phase 17, so the perceived value of removing the fallbacks is architectural cleanliness rather than user-facing improvement. Will revisit when the plain-CLI test suite has been migrated.

- **TUI Phase 17 — one persistent app for the entire run (build 10078):**
  - New `the_architect/tui/runner.py` with `ArchitectAppRunner`: hosts a single `ArchitectApp` while a worker thread drives the CLI flow. The app stays alive from the moment `architect` starts until the run finishes — no more alt-screen flash between every pre-run prompt.
  - The `main()` entry point now wraps the `_run_main(...)` call in `ArchitectAppRunner(flow=_run_main, kwargs=...).run()` whenever the TUI is active and not in headless mode. The flow runs on a background worker, and the main thread runs `app.run()` exactly once.
  - Module-level `active_runner()` lets any code path detect whether a runner is currently hosting the flow.
  - `run_single_screen()` now checks `active_runner()` first. When a runner exists it pushes the screen onto the running app via `ArchitectApp.push_and_wait` (no new `App.run()` boot). Falls back to a minimal harness app when no runner is bound — that path still matters for a handful of direct-call sites and the non-TTY tests.
  - Worker spawning is deferred via `app.call_later` so the worker only starts after the app's event loop is ready — otherwise `call_from_thread` would hang waiting for a loop that hadn't started yet.
  - Flow exceptions propagate cleanly: the worker captures them, the main thread exits the app, `run()` re-raises after cleanup, and `main()`'s error handler sees the same exception it would have seen in the pre-Phase-17 inline path.
  - 6 new tests in `test_tui_phase17_runner.py` covering: worker-thread execution, `active_runner` lifecycle, return-value passthrough, exception re-raising, `run_single_screen` prefers the active runner, and the harness-fallback path when no runner exists.
  - Full suite: **2405 passed, 4 skipped**. Lint clean, mypy clean on 44 source files.
  - **Result**: on a TTY, `architect` now enters alt-screen once, shows every pre-run prompt → planning → execution → retrospective as screens inside the same Textual app, and exits alt-screen exactly once when the run completes. No flicker between stages.

- **TUI Phase 16 — one app architecture (build 10077):**
  - Every pre-run `*App` subclass has been converted to a proper `Screen` subclass. The previous design used a separate `App.run()` per prompt (provider, goal, scope, model, agent, pending-tasks, update-action, mode selection, resume), producing a visible alt-screen flash between each stage. Now they are all `Screen` classes designed to live inside one persistent app.
  - New classes: `ProviderSelectionScreen`, `GoalScreen`, `ScopeScreen`, `StringListPickerScreen`, `UpdateActionScreen`, `PendingTasksScreen`. The old `*App` class names are gone; no backward compatibility shims are kept since the tests have been migrated to the new names.
  - Added `ArchitectApp.push_and_wait(screen)` — a thread-safe helper that lets a worker thread push a screen on the running app, wait for its `dismiss(value)` call, and return the value. This is the correct Textual pattern for orchestrating a sequence of screens inside one app.
  - Added `run_single_screen(screen)` as a stepping-stone helper that boots a minimal harness app for a single screen. Used internally by the existing `run_goal_screen()`, `run_provider_selection()`, `run_scope_screen()`, `run_model_picker()`, `run_agent_picker()`, `run_update_action_screen()`, `run_pending_tasks_screen()` wrappers so the existing sequential CLI flow in `cli.py` keeps working unchanged during the migration.
  - `ArchitectApp.TITLE` is now set to "The Architect".
  - Migrated all existing per-app tests in `test_tui_pre_run.py` and `test_tui_pre_run_extra.py` to use a small `App` harness that pushes each screen. New tests in `test_tui_phase16_single_app.py` cover `push_and_wait` from a worker thread and the app title. Full suite: 2399 passed.
  - The follow-up work to drive the whole CLI flow through a single long-lived `ArchitectApp` instance (instead of booting the harness once per stage via `run_single_screen`) is a separate refactor that belongs in a later phase — it touches the sequential orchestration in `main()` / `_run_main()` and will be landed when we can migrate those paths safely. The architectural seam is in place today via `push_and_wait`.

- **TUI Phase 15 — remove the redundant "No tasks / All done" welcome screens (build 10076):**
  - `architect` (no `--plan`) used to show a plain-terminal `questionary.select` with two branches:
    - "No tasks found in this project — Plan / Exit"
    - "All tasks complete — Plan / Exit"
  - Both were redundant. If the user runs `architect` without `--plan` in a project that has no tasks or where everything is already done, planning is the only useful action. The menu just added an extra plain-terminal step before the TUI opened.
  - Removed both. In those two cases `plan = True` is now set silently and the flow goes straight into the planning screens (provider → goal → scope → model → agent → mode selection → planning → execution), all inside the TUI.
  - Users who explicitly want to exit can press `Ctrl+C` on any TUI screen.
  - Headless mode behaviour is unchanged.

### Fixed

- **TUI Phase 14 — close the plain-terminal leak gaps (build 10075):**
  - `_start_live_spinner` and `_start_wait_spinner` are now silent no-ops when `ARCHITECT_TUI=1` is set. Before this fix, the "loading models from OpenCode…", "loading agents from OpenCode…", "preparing project context…", and "fetching free-tier models…" spinners still animated in the plain terminal while the TUI was about to open — producing a visible flash of plain-terminal output before and between TUI screens. The TUI's own wait overlay and live footer now own all in-run animation.
  - **Pending-tasks warning** is now a Textual `PendingTasksApp` screen with `[Y] Continue / [N] Abort` bindings. Previously, the banner printed via `console.print` and the confirmation was `questionary.confirm()` — both flashing plain terminal output before the rest of the TUI took over. The plain-terminal banner + questionary confirmation remain as the non-TTY fallback.
  - Wired TUI fast-paths in both pending-task check paths (planning mode gate and resume-flow gate) so there's only one warning per run and it renders inside the TUI.
  - 4 new tests (pending-tasks confirm/abort, spinners-silent-in-TUI-mode). Full suite: 2395 passed.

### Added

- **TUI Phase 13 — all remaining pre-run prompts move into the TUI (build 10074):**
  - **Architect model picker** — `run_model_picker()` renders as a Textual `ListView` with the current model pre-selected and a "use provider default" option at the bottom. Replaces the plain-terminal `questionary.select` inside `_prompt_architect_model`.
  - **Execution agent picker** — `run_agent_picker()` renders the list of available agents with a "use provider default" fallback. Replaces the `questionary.select` inside `_prompt_exec_agent`.
  - **Outdated-provider warning** — new `UpdateActionApp` with `[C] Continue` / `[Q] Exit` bindings replaces the legacy prompt_toolkit single-keypress screen inside `_prompt_update_action`.
  - Added a shared `_StringListPickerApp` base in `pre_run.py` so list-pick screens reuse the same CSS, bindings, cancel semantics, and key navigation without duplicating layout code.
  - All TUI fast-paths fall back to the existing plain-terminal implementation on any exception so non-TTY and error paths stay intact.
  - 5 new tests in `test_tui_pre_run_extra.py` covering default confirm, selected-index confirm, cancel paths, and the update-action continue/exit flow. Full suite: 2391 passed.
  - There is now no interactive prompt that runs before the TUI opens on a TTY — the entire pre-run experience (provider → goal → scope → model → agent → mode selection → planning → execution) stays inside the Textual app.

- **TUI Phase 12 — pre-run prompts move into the TUI (build 10073):**
  - New Textual screens for the three pre-run prompts that previously ran in the plain terminal before the main TUI opened:
    - **ProviderSelectionApp** — replaces the prompt_toolkit provider picker when multiple AI CLIs are installed.
    - **GoalApp** — multi-line goal input via `TextArea`. Ctrl+Enter / Ctrl+S submits. Empty input is rejected.
    - **ScopeApp** — `Standard` / `Simple` / `Complex` list picker.
  - Each screen returns the same value shape the legacy prompt was producing, so every downstream code path in `cli.py` is unchanged.
  - Wired TUI fast-paths into `_prompt_provider_selection`, `_prompt_goal`, and `_prompt_scope`. Plain terminal paths (prompt_toolkit / questionary) remain as the fallback when the TUI is disabled or when the Textual app fails to start.
  - 9 new tests in `test_tui_pre_run.py` covering all three screens plus cancel paths. Full suite: 2386 passed.
  - The pre-run experience is now fully TUI-native: no more plain-terminal prompts appearing before the main Textual app opens.

### Changed

- **TUI Phase 11 — single `--no-tui` opt-out flag (build 10072):**
  - Removed the redundant `--tui` force-on flag. The TUI is now the default on TTY, and `--no-tui` is the only way to force plain CLI output from the command line. Other opt-out paths continue to work exactly as before: `NO_COLOR=1`, `TERM=dumb`, `--headless`, and non-TTY pipes.
  - Updated help text so users see one simple opt-out knob instead of a three-state `--tui/--no-tui/auto` flag that implied the TUI was still opt-in.
  - No behaviour change for users who were relying on the default auto-detection.

- **TUI Phase 10 — documentation update (build 10071):**
  - Added a dedicated "TUI (default)" section to `README.md` explaining the Output/Events/Details viewport, wait-screen overlays, mode/resume screens, inspection commands, key bindings, and all opt-out paths (`--no-tui`, `NO_COLOR=1`, `TERM=dumb`, `--headless`, piped stdout).
  - Documented how to survive SSH disconnects under the new default: wrap in `tmux new -s arch 'architect'` or similar. The tmux dashboard remains available as the automatic fallback when TUI is off.
  - Updated "Key Features" to reflect that the TUI is the new default and tmux is the non-TUI fallback.
  - `AGENTS.md` directory layout now lists the `the_architect/tui/` package (app.py, session.py, renderer.py, screens/).
  - No behaviour changes; this build is documentation-only. 2377 tests still passing.
  - Note: the original plan to remove prompt_toolkit/questionary paths was deliberately NOT done in this phase. Those paths are the non-TTY fallback that makes `--no-tui`, `--headless`, and piped output work. They will be migrated in a future phase after the plain-CLI test suite has been adapted.

- **TUI Phase 9 — tmux dashboard spawn is skipped when TUI is active (build 10070):**
  - When the Textual TUI is running (the new default on TTY), `architect` no longer spawns a tmux session with a split-pane dashboard. The TUI's persistent Output / Events / Details tabs already carry every field the tmux side-panel would show.
  - Users who prefer the classic tmux split-pane experience get it automatically when they opt out of the TUI — pass `--no-tui`, set `NO_COLOR=1`, set `TERM=dumb`, or pipe stdout to a file.
  - `architect monitor --tui` still reads `.architect/monitor_state.json` for anyone who wants to reattach to a running project, and the tmux `monitor` command still works when the TUI is off.
  - **Surviving SSH disconnect**: under the new default, wrap The Architect in your own `tmux new -s arch` / `screen` if you need the run to survive closing the terminal. A proper `--detach` option is tracked for a later phase.

### Added

- **TUI Phase 8 — TUI becomes the default on TTY (build 10069):**
  - `--tui/--no-tui` is now a three-state flag on `architect`. Default is `auto`:
    - **TUI on** when stdout is a TTY, `NO_COLOR` is unset, and `TERM` is interactive (not `dumb`).
    - **TUI off** when output is piped/redirected (non-TTY), when `NO_COLOR` is set, when `TERM=dumb`, when `--no-tui` is passed, or when `--headless` / `ARCHITECT_HEADLESS=1` is set.
  - New helper `_resolve_tui_default(explicit, headless)` in `cli.py` makes the resolution logic explicit and testable.
  - Users get richer default experience automatically; CI / cron / pipelines stay plain exactly as before.
  - Added 7 tests (`test_tui_default_resolution.py`) covering every branch: explicit true/false wins, headless override, auto-enable on TTY+color, auto-disable on pipe/NO_COLOR/dumb TERM. Full suite: 2377 passed.
  - Prompt_toolkit / questionary interactive paths are **preserved** as the non-TTY fallback during this phase. They will be removed in Phase 10 after the CLI test suite has been migrated over.

- **TUI Phase 7 — unified app shell with wait-screen overlays (build 10068):**
  - Extracted the wait UI into a proper `WaitScreen` (Textual `Screen`) so it can be pushed onto an already-running app. `WaitApp` is now a thin standalone wrapper around `WaitScreen` for cases where no main app is active (e.g. early planning).
  - `ArchitectApp` now has `show_wait(title, detail)`, `update_wait(...)`, `append_wait_log(...)`, and `hide_wait()` APIs. When called from any thread, they render a wait overlay on top of the execution screen and pop it cleanly — no second terminal takeover.
  - `tui_wait_session(...)` accepts an optional `overlay_app` argument. When provided, the wait session renders as an overlay on the already-running `ArchitectApp` instead of launching a second Textual app in a new thread. Same public API, correct teardown via context-manager exit.
  - Wired inline reassessment (between-task) to pass the running `ArchitectApp` as the overlay host, so the user sees a smooth "execution → reassessment overlay → back to execution" flow instead of the app flickering off and on between tasks.
  - Added a mutable `tui_overlay_app` container in `_run_tasks_raw` that publishes the live app while execution is in progress and clears it on exit.
  - 3 new tests in `test_tui_wait.py` covering overlay push/pop and in-place title/detail updates. Full suite: 2370 passed.

- **TUI Phase 6 — correctness fix for empty tabs (build 10067):**
  - The Output tab now populates with human-readable banners as the run progresses:
    - Task-start: `══ T01  Implement auth`
    - Attempt-start: `→ attempt 1/3 · model claude-sonnet-4`
    - Task-done: `✓ T01 done · 2.3s`
    - Task-failed: `✗ T01 failed after 3 attempts`
  - The Events tab receives `task_done` and `task_failed` in addition to the existing `task_start`, `attempt_start`, `circuit_state_change`, `cooldown_start/end`, `replan_start/end`, `model_switched`.
  - The Execution screen now writes default placeholders at mount — "Waiting for run to start…" in the Output tab and a friendly idle hint in the footer — so the UI never looks empty before a run begins.
  - Details tab now initializes with a `(waiting)` sentinel instead of blank fields.
  - 2 new tests (`test_mount_shows_default_waiting_messages`, `test_mount_default_details_shows_waiting`) verify the mount-time state. Existing 15 TUI tests continue to pass.

- **TUI Phase 5 — wait-phase screens (build 10066):**
  - New `WaitApp` (`the_architect/tui/screens/wait.py`): reusable Textual screen for long-running agent work. Animated braille spinner (10 FPS), title line, free-form detail block, and a bounded tail of log lines via a `RichLog`.
  - New `TuiWaitSession` + `tui_wait_session()` context helper (`the_architect/tui/session.py`): mirror of the Phase 2 execution-session design. Launches `WaitApp` in a background thread, exposes thread-safe `set_title`, `set_detail`, `append_log` methods, and tears down cleanly on exit. When `enabled=False`, yields a no-op session so non-TTY/CI runs are unchanged.
  - Wired three wait-phase call sites to use the TUI when `--tui` (or `ARCHITECT_TUI=1`) is active:
    1. **Planning** — `run_planner(...)` in `run_planning_mode`
    2. **Retrospective review** — `run_retrospective(...)` per round
    3. **Inline reassessment** — `run_task_reassessment(...)` after completed tasks with downstream impact
  - Each call site keeps the existing `_start_live_spinner(...)` fallback for the plain terminal, so nothing regresses outside TUI mode.
  - 7 new tests (`tests/test_tui_wait.py`) covering initial render, `set_title`, `set_detail`, `append_log`, spinner frame advance, and the disabled-session no-op contract.

- **TUI Phase 4 — inspection screens (build 10065):**
  - `ListApp` (`the_architect/tui/screens/list_screen.py`): read-only task list with prefix / title / status and a done/total summary. `r` refreshes.
  - `StatusApp` (`the_architect/tui/screens/status_screen.py`): run status dashboard — lock state, tasks table, circuit breaker (OPEN/HALF_OPEN), token budget, recent logs. `r` refreshes.
  - `LogsApp` (`the_architect/tui/screens/logs_screen.py`): paneled log viewer with a file picker on the left and a RichLog content pane on the right. Auto-opens a task's log when `--task` is passed, otherwise opens the newest log. JSON event lines are parsed to show agent text output; raw lines pass through.
  - `CircuitApp` (`the_architect/tui/screens/circuit_screen.py`): per-task circuit breaker state with state / no-progress / same-error / recovery / opened-ago.
  - `MonitorApp` (`the_architect/tui/screens/monitor_screen.py`): polls `.architect/monitor_state.json` (1 Hz) and renders run / current-task / cooldown / token / per-task-status sections. No tmux required.
  - `--tui` flag added to `architect list`, `architect status`, `architect logs`, `architect circuit`, `architect monitor`. The flag also honors `ARCHITECT_TUI=1`. Every path falls back to the existing rich output on any TUI error so no command ever becomes unusable.
  - 8 new tests (`tests/test_tui_phase4_screens.py`) covering empty-data paths, populated-data paths, and monitor state file handling.

- **TUI Phase 3 — interactive screens (build 10064):**
  - New `ModeSelectionApp` (`the_architect/tui/screens/mode_selection.py`): Textual version of the pre-run mode screen. Collects `free`, `persistent`, `integrity`, and `token_budget_per_hour`, returns the exact same dict shape as the legacy prompt_toolkit screen. Hides Free Tier when the provider doesn't support it.
  - New `ResumeApp` (`the_architect/tui/screens/resume.py`): Textual resume screen. Renders pending tasks, prefills toggles from the current config, offers Execute/Replan buttons, returns `{free, persistent, integrity, token_budget_per_hour, action}`.
  - New `ConfigApp` (`the_architect/tui/screens/config.py`): read-only Textual viewer for all 20 runtime config fields with a scrollable DataTable and source-path indicator. `q`/`Esc`/`Ctrl+C` quit.
  - `--tui` flag now routes `_prompt_mode_selection`, `_prompt_resume_screen`, and `architect config` through the new screens (falls back to prompt_toolkit/rich on any TUI error). Same behavior is available via `ARCHITECT_TUI=1`.
  - `architect config --tui` launches the scrollable Textual viewer.
  - 13 new tests (`test_tui_mode_selection.py`, `test_tui_resume.py`, `test_tui_config.py`) covering defaults, prefill-from-config, cancel paths, hide-free-tier, and invalid-budget clamping.
  - Init prompts (questionary-based, only run during first-time setup) deliberately deferred — they'll be rebuilt alongside the list/status/logs/circuit/monitor screens in a later phase.

- **TUI Phase 2 — live run integration (build 10063):**
  - New `the_architect/tui/session.py` with `tui_execution_session()` context manager that launches `ArchitectApp` in a background thread, binds a `TextualStreamRenderer`, and tears down cleanly on exit. When disabled, yields a no-op session whose renderer is a `PlainStreamRenderer` so non-TTY/CI paths are unchanged.
  - New `--tui` flag on `architect run` (opt-in during phase 2). When passed on a TTY, task execution is rendered inside the Textual app: provider output streams into the Output tab, runner events (`task_start`, `attempt_start`, `circuit_state_change`, `cooldown_start/end`, `replan_start/end`, `model_switched`) flow into the Events tab, and the Details tab plus the footer update with current task / phase / attempt / model.
  - Wrapped `run_all(...)` inside `_run_tasks_raw` with the TUI session; existing callbacks are preserved and only wrapped when the TUI is actually running. Threaded `use_tui` through `main` and `_run_main`.
  - Removed the unused `ManagedExecutionRenderer` import from the CLI (the runner's compatibility shim stays for tests and external callers).
  - 5 new tests covering the session lifecycle and no-op semantics when disabled.

- **TUI Phase 1 — Textual foundation (build 10062):**
  - Added `textual>=0.80` as a core dependency.
  - New `the_architect/tui/` package with `ArchitectApp`, `ExecutionScreen`, and `TextualStreamRenderer`.
  - Execution screen uses a tabbed layout (Output / Events / Details) with a one-line status footer. Keyboard bindings: `o` / `e` / `d` switch tabs, `q` / `ctrl+c` quit.
  - `TextualStreamRenderer` implements the existing `StreamRenderer` contract so the TUI plugs into the runner without any business-logic changes. When no app is bound, it falls back to the plain streaming renderer.
  - New `architect tui` CLI command launches the TUI app standalone for preview. Phase 2 will wire it into the live run flow once the foundation is proven stable.
  - 10 new tests covering the renderer (fallback, forwarding, error handling) and the app (mount, output tab, footer updates, details merging).

- Two distinct animation primitives with consistent wiring across every waiting moment (build 10047):
  - **Wait spinner** (`_start_wait_spinner`, `_wait_animate`) — lightweight braille dot for fast I/O waits. Wired into loading-model-list (`_prompt_architect_model`), loading-agent-list (`_prompt_exec_agent`), free-tier model fetch in `_run_main`, and project-context setup (structure detection + `ARCHITECT.md` + `provider.ensure_setup`) in `run_planning_mode`.
  - **Agent scanner** (existing `_start_live_spinner`) — bouncing green bar for long LLM agent work. Wired into the planner (`run_planner`), the retrospective reviewer loop, and the per-task reassessment loop (`run_task_reassessment`), alongside the already-wired executor startup.

### Changed

- Interactive UI polish across four focused fixes (build 10046):
  - Dashboard no longer flickers every 2 s — replaced full-buffer clear (`\033[2J\033[H`) with cursor-home + erase-below (`\033[H` + `\033[J`) so only the cells that actually changed are repainted.
  - `_countdown` now accepts a `next_task` label and shows it dimly beside the timer between tasks; `_run_tasks_raw` wires a closure that feeds the upcoming pending task into each pause.
  - A dim 41-char horizontal rule (`_SEPARATOR`) is printed after every task's done/failed status line so the next `══ TNN` header is never buried under provider output.
  - The outdated-provider confirmation in `run_planning_mode` is now a single-keypress `prompt_toolkit` screen (`_prompt_update_action`) — `C` continues, `Q` / Esc / Ctrl+C exits, no Enter required. Headless mode still emits the warning without prompting.

- Replaced the fixed 1.2 s blocking startup spinner with a continuous background bouncing-scanner animation (`_live_spinner` / `_start_live_spinner`) that runs in a daemon thread and stops the moment the provider produces its first line of output.  Wired a new `on_first_output` callback through `run_all` → `run_task` → `run_task_once` → `stream_provider` so the animation closes exactly when real output begins — no dead terminal gap, no animation cut short (build 10045).

<!--
Every completed task appends a bullet here and bumps __build__ in /version.py.
When cutting a release, rename [Unreleased] to the version and add a fresh
empty [Unreleased] above it. Use Keep a Changelog section headings:
Added / Changed / Deprecated / Removed / Fixed / Security.
-->

## [1.2.0] (build 10043) — 2026-05-02

### Added

- Full Gemini CLI (Google) provider support — new `GeminiCliProvider` module with JSONL stream-json output parsing, model resolution via `GEMINI_MODEL` env var / `~/.gemini/settings.json`, and `gemini -p` non-interactive invocation with `--yolo` approval bypass. Select with `--provider gemini-cli` or `provider = "gemini-cli"` in `architect.toml`. Auto-detection order is now OpenCode → Codex CLI → Claude Code → Gemini CLI (build 10039).
- `"gemini-cli"` is now a valid `provider` value in `architect.toml` and `architect config --set provider=gemini-cli` (build 10035).

### Changed

- Added a lightweight post-task architect reassessment loop, richer default `tasks/INSTRUCTIONS.md` execution guidance, and structured `PROGRESS.md` task outcome tracking so downstream tasks can adapt to discoveries made during earlier execution (build 10034).
- Tightened executor outcome reporting to use an explicit structured task-outcome block, narrowed reassessment to explicit downstream-impact signals, expanded regression coverage for the new adaptive loop, and revalidated the full CI/release pipeline for the `1.1.0` release line (build 10034).
- Updated `README.md` and `documentation/` to reflect the current provider-agnostic architecture, including Gemini CLI and four-provider support (build 10040).
- Polished top-level docs for consistent provider-agnostic wording across `README.md`, `more_things.md`, and `CONTRIBUTING.md` (build 10041).

## [1.1.0] (build 10033) — 2026-04-29

### Fixed

- Main console pane now has a small left margin plus right breathing room for streamed output, so text doesn't touch either edge near the dashboard split (build 10027).
- Dashboard side panel now keeps only a small right padding, while using the full left edge as requested (build 10027).
- Interactive prompt-toolkit screens in the main pane now reserve left and right padding, so long goal/input text and menu screens no longer run into the dashboard pane (build 10028).
- Fixed the prompt-toolkit padding container to be horizontal (`VSplit`) rather than vertical, so the main-pane prompt screens now add true left/right padding instead of blank top/bottom space (build 10029).
- Replaced raw `questionary.text(...)` free-text prompts with a custom padded prompt-toolkit text input for goal/model entry, so long typed input no longer reaches the right-side dashboard pane (build 10030).
- Removed the accidental extra vertical spacing above the custom text input prompt; the goal/model input now uses only horizontal side padding in the main pane (build 10031).
- Refined the custom goal/model text prompt to preserve multi-line pasted input, restore the original green heading style, and keep only right-side breathing room in the typed input area (build 10032).
- Restored normal `Enter` submission for the custom goal/model prompt while keeping the green bold prompt styling and right-side breathing room in the input area (build 10033).
- tmux pane borders are now invisible (tmux 3.4+) or very subtle (brightblack fallback) — no more visible lines between panes (build 10024).

### Changed

- Generalised CLI provider selection screen text and comments to work with any number of providers instead of hard-coding "both OpenCode and Claude Code". Model prompt instruction now uses `provider.display_name` dynamically (build 10015).
- Updated docstrings across `cli.py` that said "Defaults to OpenCode" to correctly say "Defaults to auto-detection" (build 10015).
- Fixed pre-existing test failures in `test_claude_code_provider.py`: `test_find_user_config_global_only` now safely cleans up non-empty directories; `test_command_building_basic` correctly asserts on the full binary path (build 10016).

### Added

- Full Codex CLI (OpenAI) provider support — new `CodexCliProvider` module with JSONL output parsing, model resolution via `CODEX_MODEL` env var / `~/.codex/config.toml`, and `codex exec` non-interactive invocation. Select with `--provider codex` or `provider = "codex"` in `architect.toml`. Auto-detection order is now OpenCode → Codex CLI → Claude Code (build 10017).
- Codex CLI (OpenAI) as a third provider in provider detection. `detect_provider("codex")` returns a `CodexCliProvider`; auto-detection order is now OpenCode → Codex CLI → Claude Code. `detect_available_providers()` lists Codex between OpenCode and Claude Code (build 10013).
- `provider = "codex"` is now a valid value in `architect.toml` config. The `provider` field description lists all four options (`auto`, `opencode`, `codex`, `claude-code`) and the updated auto-detection order (build 10014).
- Comprehensive test suite for `CodexCliProvider` in `tests/test_codex_cli_provider.py`: identity, installation, command building, env overrides, JSONL output parsing, model resolution, config discovery, setup, and prompts. Extended `tests/test_provider.py` with Codex detection, 3-provider ordering, and protocol compliance tests (build 10016).

## [1.0.1] (build 10011) — 2026-04-28

### Fixed

- Provider update-required and misconfiguration errors are now surfaced
  immediately with actionable messages instead of silently retrying 3 times
  with a generic "no tasks created" message. OpenCode and Claude Code can
  now be proactively checked for updates before planning starts (build 10010).

- README banner image now uses absolute GitHub URL so it renders correctly
  on PyPI (relative `assets/` path is not resolvable on pypi.org) (build 10004).

- `.gitignore` no longer excludes `the_architect/resources/prompts/architect.md`
  from version control. The previous rule `ARCHITECT.md` was intended to ignore
  the runtime-generated project memory file but matched case-insensitively on
  macOS/Windows filesystems, silently dropping the lowercase resource file
  from the initial commit and breaking 60+ tests on CI (build 10002).
- Circuit-breaker replan tests (`test_replan_resets_circuit_state_on_success`,
  `test_replan_discovers_new_task_files`) now patch `stream_provider` instead
  of the removed `stream_opencode` symbol. The tests passed locally on dev
  machines where `opencode` is installed (because the real call succeeded)
  but failed on CI where it is not (build 10002).
- Retry-command tests accept both legacy wording ("not marked Done") and
  current wording ("not in a terminal state") so the assertions survive the
  terminal-status vocabulary change (build 10002).

### Added

- `more_things.md` — post-style writeup covering design philosophy and
  non-obvious insights not found in the technical documentation (build 10006).

- CI auto-creates a GitHub release for every green build on `main`.
  Same-SemVer builds are marked as pre-release; new-SemVer builds are
  marked as latest and also published to PyPI after reviewer approval
  (build 10005).

- PyPI Trusted Publishing job in CI — tagged releases (`v*`) auto-publish
  to PyPI via OIDC, no API tokens required (build 10001).
- Python 3.13 to the CI test matrix and `pyproject.toml` classifiers
  (build 10001).
- `SECURITY.md` with private vulnerability-disclosure policy (build 10001).
- `CODE_OF_CONDUCT.md` based on the Contributor Covenant 2.1
  (build 10001).
- `.github/dependabot.yml` — weekly grouped updates for pip dependencies
  and GitHub Actions, running through the existing CI pipeline before
  any merge (build 10001).
- `-V` / `--version` flag on the root `architect` command — previously
  only the `architect version` subcommand worked despite the flag being
  documented (build 10001).

### Changed

- Rewrapped 25 long lines in `the_architect/cli.py` to comply with the
  configured 100-character limit; CI now passes `ruff check` cleanly
  (build 10001).
- Reformatted `tests/test_runner.py` to satisfy `ruff format --check`
  (build 10001).

---

## [1.0.0] (build 10000) — 2026-04-27

**Initial public release of The Architect** — an autonomous development
lifecycle layer that wraps OpenCode or Claude Code to turn any coding
agent into a fire-and-forget development partner.

### Providers

- OpenCode and Claude Code supported as AI CLI backends.
- Auto-detection of installed providers with interactive selection on first run.
- Provider preference persisted via `architect.toml` or the
  `ARCHITECT_PROVIDER` environment variable.
- Pass-through for all provider API keys
  (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

### Planning

- Goal decomposition into numbered task files (`T01`, `T02`, …) with a
  shared `INSTRUCTIONS.md`.
- Three scope levels — `simple`, `standard`, `complex` — that shape
  task granularity.
- Context injection from PRDs, specs, or any document via repeatable
  `--context FILE_OR_DIR` or the `ARCHITECT_CONTEXT` env var.
- Goal auto-extraction from context files (scans for `## Goal`,
  `## Objective`, `## Requirements`).
- Project structure auto-detection — repo type, languages, frameworks,
  components, and dependency graph — captured in `ARCHITECT.md` for
  every planning session.
- Previous-run archiving with timestamped directories so no history is lost.
- Planning resilience — 3 attempts with a 30-second pause on transient failures.

### Execution

- Live streaming of provider output to the terminal.
- Multi-signal completion detection combining promise tags,
  `PROGRESS.md` state, exit codes, and output analysis.
- Anti-hallucination guard — a stuck task is never reported complete,
  even if the agent claims success.
- Configurable pause between tasks.
- Authoritative post-attempt status reconciliation — the runner writes
  `Done` or `Failed (N attempts)` into `PROGRESS.md` after every task
  attempt so the next iteration skips resolved tasks. Prevents
  re-execution of tasks the executor completed but forgot to mark.

### Task Lifecycle and Status Vocabulary

- Full terminal-status vocabulary in `PROGRESS.md`: `Pending`, `Done`,
  `Failed`, `Blocked`. `Failed` is written by the runner when all
  retries are exhausted; `Blocked` is reserved for resource-limit
  persistence.
- `reconcile_task_status()`, `task_is_resolved()`, and `task_status()`
  helpers in `the_architect.core.progress` — used by the runner,
  planner, and CLI to honour the status vocabulary consistently.
- `ProgressState.failed_tasks`, `ProgressState.blocked_tasks`, and
  `ProgressState.resolved_tasks` properties for downstream consumers
  (dashboards, summaries) that need the full lifecycle picture.
- Planner only re-plans non-terminal tasks — failed work is never
  silently re-queued as if it were fresh.
- `execution-protocol.md`, `reviewer.md`, and `architect.md` prompts
  document the expanded status vocabulary so agents do not get confused
  when the runner stamps statuses they did not write.

### Retry and Circuit Breaker

- Automatic retry with per-attempt model fallbacks
  (`retry_model_2`, `retry_model_3`).
- Context carry — a summary of the previous attempt is injected into the
  next retry's instruction.
- Circuit breaker with three independent failure detectors:
  - no-progress detection (zero file writes across attempts),
  - same-error fingerprinting (path- and line-number-normalised), and
  - token-decline detection (model output shrinking across attempts).
- Targeted task replan when all fallback models are exhausted.
- Circuit state persisted to `.architect/circuit.json` — survives process restarts.

### Rate Limit Handling

- Cooldown detection for HTTP 429, 529, and provider-specific text patterns.
- Minimum 1-hour cooldown enforced regardless of the suggested retry-after.
- Cooldown waits do not consume retry slots.
- Free-tier model rotation (OpenCode + OpenRouter only) — swaps models
  mid-stream without restarting the task.

### Retrospective Review

- Reviewer agent runs after execution completes.
- Reads the actual code, runs the test suite, and assesses completeness
  and quality.
- Emits `R`-prefixed fix-up tasks when it finds issues.
- Configurable rounds — 1 by default, 2 in persistent mode.

### Persistent Memory

- `ARCHITECT.md` — project intelligence that accumulates decisions,
  constraints, lessons, and best practices across sessions.
- `PROGRESS.md` — operational state between tasks with a strict
  regex-parsed format.
- `SUCCESS.md` — full run summary written after every execution.
- Planning history auto-appended to `ARCHITECT.md` after each session.

### Modes

- Interactive mode with arrow-key screens for configuring runs,
  resuming, and selecting providers.
- `--persistent` — up to 30 retries and 2 retrospective rounds.
- `--free` — free-tier OpenRouter rotation (OpenCode only).
- `--headless` — fully flag- and env-var-driven, suitable for CI.
- `--only` and `--from` for targeted task execution.
- `--standalone MODEL` — bypass provider config entirely.
- `--provider {auto,opencode,claude-code}` — select the AI CLI provider
  from the command line; overrides `architect.toml` and
  `ARCHITECT_PROVIDER`.
- `--project DIR` — operate on a directory other than CWD.
- `--no-monitor` — disable the tmux dashboard.

### CLI Commands

- `architect` — start a run (plan, resume, or all-done guard automatic).
- `architect --plan` — force planning mode.
- `architect list` — show all tasks and their status.
- `architect status` — show current run state.
- `architect retry --task T03` — reset and re-run a specific task.
- `architect skip --task T03` — mark a task done without running it.
- `architect reset` — reset `PROGRESS.md`.
- `architect cancel` — remove a stale lock file.
- `architect init` — initialise a project directory.
- `architect config` / `--set key=value` — view or edit configuration.
- `architect circuit` / `--reset T04` — view or reset circuit-breaker state.
- `architect logs` / `--task T01` — view execution logs.
- `architect monitor` — attach to the live tmux dashboard.
- `architect version`, `architect --version`, `architect -V` — print the
  version string.

### tmux Dashboard

- Auto-launches a split-pane session when tmux is available.
- Live task progress, circuit-breaker state, token usage, and build
  number in a single view.
- Atomic state-file writes so external readers never see a partial update.
- Offers to install tmux interactively if it's missing.

### Token Budget

- Optional hourly token-spend cap per run.
- Rolling 1-hour window with automatic reset.

### Build Tracking

- Global build counter increments with every agent operation and never resets.
- Dual-track versioning — SemVer for releases, build counter for cumulative effort.
- Major-version build-floor alignment (`v1` ≥ 10000, `v2` ≥ 20000, `v3` ≥ 30000).
  Build numbers are always at least 5 digits.

### Technical Baseline

- Python 3.11+ required; uses the built-in `tomllib` (no `tomli` dependency).
- Zero AI SDK dependencies — every model call goes through the provider CLI.
- Strict static checks — `ruff check`, `ruff format --check`, and
  `mypy --strict` all clean.
- PEP 639 compliant packaging — `license = "Apache-2.0"` SPDX expression
  with `LICENSE` and `NOTICE` explicitly attached as `license-files`.
- Defensive `setup_logging(log_dir)` — rejects non-`Path`/`str` inputs
  with a clear `TypeError` instead of silently writing log files with
  mock-derived names.

---

*Build counter starts at 10000 for v1.0.0 and is always at least 5 digits. It never resets.*
*Each release records the exact build number at ship time.*
