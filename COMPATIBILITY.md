# COMPATIBILITY.md — Provider Workarounds

This file tracks active workarounds for known provider regressions.
Each entry documents what broke, what version it broke in, what we did,
and exactly what to revert and re-test when the provider ships a fix.

Entries are never deleted — they are moved to the **Resolved** section
so the history is preserved.

---

## Active Workarounds

*(none)*

---

## Resolved Workarounds

### [OC-1] OpenCode ≥ 1.15: `--agent` flag raises "InstanceRef not provided"

| Field | Value |
|---|---|
| **Provider** | OpenCode |
| **Broke in** | 1.15.0 (Effect-based event system rewrite) |
| **Fixed in** | 1.15.2 |
| **Workaround applied** | Build: 10460 |
| **Workaround reverted** | Build: 10467 |

**Root cause:** Two regressions in OpenCode 1.15.0:

1. Child `opencode run` processes inherited `OPENCODE_PROCESS_ROLE=worker` and
   `OPENCODE_RUN_ID` from the parent session, causing them to attempt to attach
   to a non-existent server and immediately exit with "InstanceRef not provided".

2. The `--agent` CLI flag raised "InstanceRef not provided" on startup regardless
   of whether the named agent existed in config.

**Changes made (build 10460), all reverted at build 10467:**

1. `the_architect/core/runner.py` — stripped OpenCode session env vars from the
   child process environment. Reverted: plain `{**os.environ.copy(), ...}` restored.

2. `the_architect/resources/opencode_template.json` — added `"default_agent": "architect"`.
   Reverted: field removed; `--agent` flag handles agent selection again.

3. `the_architect/core/opencode_provider.py` `build_command` — added
   `_agent_flag_broken()` version check that skipped `--agent` on ≥ 1.15.
   Reverted: `_agent_flag_broken()` removed; `--agent` always passed unconditionally.
