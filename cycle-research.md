# Cycle Research — Token & Cost Tracking in Agentic Coding Tools

> Research findings collected before feature development cycles.
> Each cycle appends a new entry. Do not modify previous entries.

---

## Cycle 2026-05-14 — Cross-Run Token & Cost Ledger

**Feature:** Persistent cross-run token usage and cost estimation ledger (`architect token-report`)
**Researcher:** T01 execution agent
**Timestamp:** 2026-05-14

### Research Sources Consulted

| Source | URL | Date Accessed |
|--------|-----|---------------|
| Codex CLI GitHub Issues (token/cost) | `github.com/openai/codex/issues?q=is:issue+token+cost+tracking` | 2026-05-14 |
| Claude Code GitHub Issues (cost/token) | `github.com/anthropics/claude-code/issues?q=is:issue+cost+token+usage+tracking` | 2026-05-14 |
| OpenCode GitHub Issues (token/cost) | `github.com/opencode-ai/opencode/issues?q=is:issue+token+cost+usage` | 2026-05-14 |
| GitHub Topics: llm-cost-tracking | `github.com/topics/llm-cost-tracking` | 2026-05-14 |
| GitHub Topics: agent-token-tracking | `github.com/topics/agent-token-tracking` | 2026-05-14 |

---

### Concrete Findings

#### 1. Codex CLI — Token Waste and Cost Blindness

**Issue #14593** — "Burning tokens very fast" (Open, Mar 2026)
- Business subscription user reports burning ~20% of weekly tokens in 2 hours
- Labels: `bug`, `rate-limits`
- Signal: Users have NO visibility into what consumed the tokens — no per-task or per-session breakdown
- **[Justifies token ledger]** — A persistent ledger would let users see WHICH Architect runs consumed tokens, not just that tokens vanished

**Issue #13733** — "Background process polling wastes tokens" (Open, Mar 2026)
- Each background process poll triggers a full API round-trip with complete conversation history
- A 60-second `cargo build` generates ~12 polling turns, each re-transmitting 200-300+ history items
- Labels: `bug`, `rate-limits`, `session`, `tool-calls`
- Signal: Token waste is structural — users need visibility into per-session cost to understand waste patterns
- **[Justifies token ledger]** — Post-session cost recording would surface which sessions had excessive polling costs

**Issue #13568** — "Usage dropping too quickly" (Closed, Mar 2026)
- User reports usage depletion faster than expected
- Labels: `bug`, `rate-limits`
- Signal: Repeated pattern of users unable to explain rapid token consumption

**Issue #13186** — "Possible Codex usage metering anomaly on Plus" (Closed, Mar 2026)
- Very small tasks consuming large portions of 5h weekly quota
- Labels: `bug`, `CLI`, `rate-limits`
- Signal: Users cannot attribute quota consumption to specific tasks or sessions

#### 2. Claude Code — Usage Limit Frustration Without Visibility

**Issue #16157** — "[BUG] Instantly hitting usage limits with Max subscription" (Open, Jan 2026, Pinned)
- Max subscription user hit usage limits after 2 hours of continuous usage
- Previously never hit limits in 3 months of use
- Labels: `area:api`, `area:cost`, `bug`, `oncall`, `platform:macos`
- Pinned by Anthropic staff (ThariqS) — confirmed as a widespread issue
- Signal: Users on paid plans have NO per-session cost breakdown — they just see a percentage bar on claude.ai
- **[Justifies token ledger]** — Per-run cost tracking would let users understand spending patterns across sessions

**Issue #9094** — "[Meta] Unexpected change in Claude usage limits as of 2025-09-29" (Closed, 30+ linked reports)
- 30+ individual reports of usage limits dropping from 40-50 hours/week to 6-8 hours/week
- Anthropic confirmed: "people are using more tokens with Opus 4.5 as it runs longer and does more work"
- Labels: `area:cost`, `bug`, `has repro`, `oncall`
- Signal: Model changes dramatically affect token consumption — users need historical cost tracking to compare runs
- **[Justifies token ledger]** — Cross-run comparison is impossible without persistent cost records

**Issue #38335** — "Claude Max plan session limits exhausted abnormally fast" (Open, Mar 2026)
- CLI usage depleting limits faster than web usage
- Signal: Different usage patterns (CLI vs web) affect cost — per-session tracking helps diagnose

**Issue #29579** — "API Error: Rate limit reached despite Claude Max subscription and only 16% usage" (Open, Feb 2026)
- Rate limits triggered even with low overall usage
- Labels: `area:api`, `area:auth`, `bug`, `has repro`, `platform:vscode`, `platform:windows`
- Signal: Usage tracking granularity is insufficient — users see aggregate % but not per-model or per-session breakdown

#### 3. OpenCode — No Token Tracking Issues Found

- **opencode-ai/opencode** is archived (Sep 2025) — no active issues
- The active OpenCode project does not appear to have a public GitHub issue tracker for token/cost concerns
- Signal: OpenCode users may face the same blind spots but lack a venue to report them

#### 4. Adjacent Tools — Emerging Cost Tracking Ecosystem

**costclaw-telemetry** (Aperturesurvivor/costclaw-telemetry, 15 stars, Mar 2026)
- TypeScript plugin for OpenClaw that provides real-time LLM cost tracking + dashboard
- Uses SQLite for persistence
- Tags: `cost-dashboard`, `llm-monitoring`, `llm-costs`, `ai-cost-optimization`
- Signal: There is demand for agent-specific cost dashboards — users want to see where money goes

**llm-cost-attribution-recipes** (vivian254338489, May 2026)
- Offline cost attribution for OpenAI-compatible gateway logs by user, feature, route, model, retry, tool call
- Tags: `finops`, `token-usage`, `ai-gateway`, `cost-attribution`
- Signal: Enterprise users need cost attribution by multiple dimensions

**openai-compatible-cost-guardrail-kit** (vivian254338489, May 2026)
- Offline-first cost tracking and budget guardrail CLI
- Tags: `usage-monitoring`, `budget-guardrails`
- Signal: Budget enforcement is a related but separate concern from cost tracking

**llm-cost-dashboard** (abject-milkingmachine273, May 2026)
- Terminal dashboard for real-time LLM token cost monitoring with per-request tracking, budget control, alerting
- Tags: `sqlite`, `tui`, `cost-dashboard`, `cost-tracking`
- Signal: Terminal-based cost dashboards are a viable UX pattern — matches The Architect's TUI approach

#### 5. General Patterns Across All Tools

**Common pain points:**
- Users cannot see WHICH sessions or tasks consumed their tokens
- Users cannot compare costs across runs or models
- Users have no historical record of spending — once a session ends, the data is gone
- Subscription plans provide aggregate usage bars but no granular breakdown
- Token waste from polling, retries, and context bloat is invisible to end users

**What existing tools offer (and don't offer):**
- Codex CLI: Shows aggregate weekly quota % — no per-session or per-task breakdown
- Claude Code: Shows aggregate weekly usage on claude.ai — no CLI-side per-session tracking
- OpenCode: Shows per-request token counts in JSON output — but no persistence or aggregation
- Third-party tools: Emerging ecosystem of cost dashboards (costclaw, llm-cost-dashboard) but none integrated into an orchestration layer like The Architect

---

### Signals Already Implemented in The Architect

| Signal | Status | Location |
|--------|--------|----------|
| Per-task token tracking | [implemented] | `runner.py` — `TokenUsage` model captures input/output/cache tokens per task |
| Per-session token tracking | [implemented] | `monitor_state.py` — `MonitorStateWriter` persists session-level token data |
| Token budget per hour | [implemented] | `ArchitectConfig.token_budget_per_hour` — enforces hourly spending caps |
| Provider-agnostic token parsing | [implemented] | Provider modules parse structured output (JSON/JSONL) for token counts |

### Signals Justifying the Token Ledger Feature

| Signal | Gap in Current Implementation | How Ledger Fills It |
|--------|-------------------------------|---------------------|
| No cross-run persistence | Token data exists per-task and per-session but is NOT persisted across Architect runs | Ledger writes `.architect/token_ledger.json` after each run — survives restarts |
| No cost estimation | Token counts exist but have no dollar-value context | Ledger includes `estimate_cost()` with built-in pricing table |
| No historical query capability | No way to ask "how much did I spend last month?" | `architect token-report` CLI command queries ledger with date filters |
| No per-model cost breakdown | Token counts are aggregated per-task without model attribution | Ledger stores `ModelTokenRecord` per model per run |
| No spending trend visibility | Users cannot compare costs across runs | Ledger accumulates records — `token-report` shows cumulative and per-run views |

---

### Key Takeaways for T02-T05 Implementation

1. **Pricing table must be built-in** — No external pricing API. Use a Python dict with per-1M-token rates for major models. Document as approximate.
2. **Atomic write pattern** — Follow the same temp-file-then-rename pattern used by `monitor_state.json` to prevent corruption.
3. **Unknown models return 0.0** — Graceful degradation for models not in the pricing table.
4. **Per-model granularity is critical** — Users need to see which models cost the most, not just aggregate tokens.
5. **Config toggle required** — Some users may want to disable ledger writes (privacy, disk space). Default: enabled.
6. **No rotation/pruning planned** — Simple append-only JSON array. Long-running projects may accumulate large files. Acceptable for v1.

---
