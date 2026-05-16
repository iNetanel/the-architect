# COMPATIBILITY.md — Provider Workarounds

This file tracks active workarounds for known provider regressions.
Each entry documents what broke, what version it broke in, what we did,
and exactly what to revert and re-test when the provider ships a fix.

Entries are never deleted — they are moved to the **Resolved** section
so the history is preserved.

---

## Active Workarounds

### [OC-1] OpenCode ≥ 1.15: `--agent` flag raises "InstanceRef not provided"

| Field | Value |
|---|---|
| **Provider** | OpenCode |
| **Broke in** | 1.15.0 (Effect-based event system rewrite) |
| **Still broken in** | 1.15.1 |
| **Upstream cause** | `--agent` flag attempts to look up an agent instance via Effect's dependency injection before the config is fully wired; crashes regardless of whether the named agent exists in config |
| **Workaround applied** | Build: 10459 |

**Changes made:**

1. `the_architect/core/runner.py` — strip `OPENCODE_PROCESS_ROLE`, `OPENCODE_RUN_ID`,
   `OPENCODE_PID`, `OPENCODE`, `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR` from the
   subprocess environment before spawning any child OpenCode process.
   Prevents a child process from inheriting the parent session's worker role
   and trying to attach to a non-existent server instance (also a 1.15 regression).

2. `the_architect/resources/opencode_template.json` — added `"default_agent": "architect"`.
   Since `--agent` cannot be passed on the CLI, the planning agent is selected
   via `default_agent` in the injected `architect.json` config instead.

3. `the_architect/core/opencode_provider.py` `build_command` — added
   `_agent_flag_broken()` version check. On ≥ 1.15 the flag is skipped and a
   warning is logged. On < 1.15 `--agent` is still passed as before.

**How to check if OpenCode fixed it:**

```bash
# From any git project directory, with NO Architect-related env vars set:
opencode run --format json --dangerously-skip-permissions \
  --agent <any-agent-name> -- "hello"
# Expected when fixed: exits 0, produces JSON output
# Still broken: "InstanceRef not provided", exits non-zero
```

**What to revert when fixed:**

1. `the_architect/core/runner.py`: restore the original env block:
   ```python
   env = {
       **os.environ.copy(),
       "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "900000",
   }
   ```

2. `the_architect/resources/opencode_template.json`: remove `"default_agent": "architect"`.

3. `the_architect/core/opencode_provider.py` `build_command`: restore:
   ```python
   if agent_override:
       cmd.extend(["--agent", agent_override])
   ```

4. Update the two test assertions in `tests/test_provider.py` and
   `tests/test_opencode_provider.py` that were changed to assert `--agent` is
   **not** in the command — flip them back to assert it **is**.

5. Move this entry to the **Resolved** section below with the fixed version noted.

---

## Resolved Workarounds

*(none yet)*
