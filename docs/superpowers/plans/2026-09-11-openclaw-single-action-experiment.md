# OpenClaw Single-Action Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Test removal of the model finish protocol while retaining OpenClaw and all existing CRM protections.

**Architecture:** A separate single-action module reuses the existing contract,
gateway and verified renderer. A read-only CLI compares it to the unchanged
production loop. Temporary-database tests verify proposal lifecycle behavior.

**Tech Stack:** Existing Python/FastAPI/SQLite/httpx/pytest and OpenClaw; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-11-openclaw-single-action-experiment.md`

## Global Constraints

- Base revision `f015e4e`; work only in `codex/openclaw-single-action`.
- Production driver, API, plugin, installer and authentication remain unchanged.
- No new dependency; no model retry; uncertain writes never replay.
- CLI performs read-only CRM operations, regardless of model instructions.
- Default diagnostics contain no raw CRM or model content.
- No live runtime exists on this Mac shell. Live WSL findings remain unverified.

## Task 1: Single-action candidate

**Files:** Create `backend/app/agent/single_action.py` and `backend/tests/test_single_action.py`.

**Interface:**
```python
async def run_single_action(gateway, message: str, session_id: str, agent_id: str,
                            *, allowed_operations=None, deadline_seconds=120.0
                            ) -> SingleActionResult: ...
# Result fields: reply: str, status: str, operation: str | None,
# receipt: crm_chat.CrmCallReceipt | None, model_calls: int, tool_calls: int.
```

- [x] Write failing behavior tests for verified directory rendering after exactly
  one completion; no finish tool; narrow allowed operations; invalid and multiple
  calls without dispatch; incorrect receipt; timed-out write reported unknown.
- [x] Run `../../.venv/bin/python -m pytest backend/tests/test_single_action.py -q`.
- [x] Implement the bounded candidate with existing contract validation and
  receipt helpers. Render successful receipts directly; never surface model prose.
- [x] Repeat candidate tests and existing `backend/tests/test_crm_chat.py`.
- [x] Review candidate safety and record actual results.

## Task 2: Proposal lifecycle through the candidate

**Files:** Create `backend/tests/test_single_action_lifecycle.py`.

**Consumes:** `run_single_action` and `SingleActionResult` above.
**Produces:** Regression evidence for actual CRM changes in a disposable SQLite DB.

- [x] Test using a gateway boundary double for model selection and a real
  TestClient/CRM dispatcher path for invocation. Never connect to provider accounts.
- [x] Verify a create proposal changes no lead until approved, edited approval is
  persisted, a repeat approval is refused, and a fresh app lifespan reads it back.
- [x] Verify note and follow-up proposals and denial leave business rows correct.
- [x] Verify booking approval respects a slot filled after proposal creation.
- [x] Run the lifecycle tests and record the boundary still requiring real hardware.

## Task 3: Read-only comparison and handoff

**Files:** Create `scripts/compare_crm_chat.py`, `backend/tests/test_compare_crm_chat.py`,
and `docs/experiments/openclaw-single-action.md`.

**Consumes:** Both chat runners, `OpenClawGateway`, and API directory count.
**Produces:** A JSON summary with case/path/status, counts, timings, call counts;
optional owner-only raw-reply capture explicitly labelled private.

- [x] Write failing tests showing a model-selected write never reaches the wrapped
  gateway, invalid/remote URLs fail before network, raw replies stay out of default
  reports, and exclusive private files cannot overwrite an existing path.
- [x] Implement explicit `--live-read-only` opt-in and bounded localhost requests.
  Use current exported environment settings for tokens, never command arguments.
- [x] Compare the original lead-directory prompt plus two paraphrases. A successful
  count comparison is not a broad quality benchmark; mark skipped/unavailable paths.
- [x] Document Bash environment loading and the exact WSL command; explain that
  native plugin configuration must match the target database and remain unchanged.
- [x] Run all backend tests, native-plugin tests, and whitespace checks. Get an
  independent review, fix actionable findings, then record results and live limits.

## Execution rulings

- The approved design is the conversational proposal. The spec records its bounded
  first experiment; no second approval is needed to build this reversible code.
- A native-agent shortcut would require weakening a current channel guard. Keep
  that guard and isolate the finish dependency first; disclose this smaller scope.
- No browser claim is made by API lifecycle tests. No new UI or deployment is added.
