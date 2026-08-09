# Live Integration Reliability Design

**Date:** 2026-08-09

## 1. Goal

Make the current dashboard-first OpenClaw CRM dependable when Gmail and Google
Calendar integrations are enabled, without replacing the existing chat relay,
approval system, SQLite database, or OpenClaw skill architecture. Discord should
continue to use the same dedicated CRM agent and safety boundaries as dashboard
chat.

The change should prevent avoidable duplicate external actions, stop permanently
failing jobs after five attempts, keep repeated inbox processing idempotent, and
avoid blocking the async health endpoint. Setup must not place the CRM API token
in a subprocess argument.

## 2. Constraints

- Keep the existing durable hook outbox and at-least-once delivery model.
- Keep the dedicated `openhouse-crm` agent and `crm-db-operations` skill.
- Preserve human approval for CRM writes and post-approval integrations.
- Prefer small, additive migrations and narrow route changes.
- Do not claim exactly-once provider delivery. Composio's current Calendar and
  Gmail actions do not expose a compatible idempotency key, so a process crash
  after provider acceptance but before the local checkpoint can still replay an
  action.
- Dashboard chat reliability is the primary acceptance path. Discord support is
  verified against the same configured agent, not through a separate backend.

## 3. Approaches Considered

### A. Incrementally harden the current outbox, selected

Keep one approved-operation outbox row, add per-step replay checks, cap retries,
and fix the poller and health endpoint in place. This has the smallest migration
and deployment risk while resolving the failures found in review.

### B. Store one outbox row per external provider action

Split lead creation into independent Calendar and Gmail jobs with separate
provider state. This makes partial progress more explicit, but requires a larger
schema and dispatcher rewrite. It is not justified for the current project size.

### C. Disable live integrations until a larger redesign

This removes duplicate-delivery risk but also removes useful Gmail and Calendar
behavior. It does not meet the product goal.

## 4. External Hook Replay Safety

Every outbox delivery already has a stable `idempotency_key`. The dispatcher will
pass a stable step key into the hook, such as
`pending-change:42:calendar` or `pending-change:42:gmail-draft`.

Before making a provider call, the hook will check for local evidence that the
exact step succeeded:

- appointments and reminders first use their stored `gcal_event_id`;
- lead call blocks and Gmail drafts use a successful audit row containing the
  exact stable step key;
- successful Calendar responses continue to persist provider event IDs where
  the local table has a matching field.

A successful provider response and its audit checkpoint will be recorded before
the composite hook reports success. On retry, completed steps are skipped.

Lead creation is sequential. If Calendar creation fails, Gmail draft creation is
not attempted. If Calendar succeeds and Gmail fails, the retry skips Calendar
and retries only Gmail. This prevents the currently observed duplicate draft and
duplicate event paths during ordinary partial failures.

The remaining provider-success/local-checkpoint-crash window will stay documented
as an at-least-once limitation rather than being presented as exactly once.

## 5. Retry Exhaustion and Manual Recovery

`hook_outbox.status` gains the terminal value `exhausted` through the existing
additive table-rebuild migration pattern. Existing rows and timestamps are
preserved.

An attempt is counted when a row is claimed. After the fifth failed attempt:

- the row becomes `exhausted`;
- `next_attempt_at`, claim token, and claim time are cleared;
- one sanitized audit records the terminal state and attempt count;
- the worker no longer selects the row.

`GET /api/integrations/outbox?status=exhausted` will provide a small operator
view of jobs that need attention. A narrow user-only
`POST /api/integrations/outbox/{id}/retry` endpoint will move one `exhausted` row
back to `pending`, clear its error and scheduling fields, and reset its attempt
count. Requests with `X-Actor: agent` are rejected. This preserves a deliberate
human recovery step without requiring direct SQLite edits. The retry endpoint
never replays the action in the request thread; it only requeues the durable
intent and wakes the worker.

Obsolete rows continue to use the existing terminal `cancelled` state and cannot
be manually retried unless the underlying CRM action is proposed again.

## 6. Inbox Poller Idempotency

The inbox reply logger will distinguish a newly inserted event from an already
known provider message. Lead processing runs only for a newly inserted event.
Retries or concurrent polling of the same Gmail message therefore do not rerun
extraction or create another pending proposal.

Source-event proposal deduplication will include a digest of the normalized
proposal payload. An identical proposal for the same event remains suppressed,
including after denial. If newer model output or corrected extraction produces a
meaningfully different proposal for that same event, it can enter review as a new
pending change.

## 7. Async Health Check

`POST /api/health/crm-check` remains async because it waits on OpenClaw. Its
SQLite reads will move into small synchronous helpers executed in FastAPI's
threadpool. No database transaction is held while awaiting OpenClaw, and a busy
SQLite writer cannot block the event loop used by dashboard chat and other async
requests.

The existing nonce and post-request audit evidence remain the source of truth for
CRM verification.

## 8. Route Safety

The demo time-advance endpoint and reminder-completion endpoint will explicitly
reject `X-Actor: agent`. They remain available to the dashboard and authenticated
operators. This closes an accidental write path if a future OpenClaw wrapper or
tool policy exposes those routes more broadly.

No general agent tool expansion is introduced. Dashboard and Discord keep the
same read tools, editable proposal flow, and dedicated-agent restrictions.

## 9. OpenClaw Token Handling

When `OHI_API_TOKEN` is enabled, the setup helper will configure the skill value
as an OpenClaw environment SecretRef by name. It will not pass the token value in
the `openclaw config set` argument vector or persist the plaintext value in the
OpenClaw configuration.

Preflight will verify that the installed OpenClaw `config set` command supports
the SecretRef builder options. If it does not, setup stops with a clear upgrade or
manual-configuration message instead of falling back to plaintext. Documentation
will explain that the OpenClaw gateway process must receive `OHI_API_TOKEN` in its
environment so the reference can resolve.

Token values remain redacted from dry-run output, errors, and tests.

## 10. Documentation and Operator Experience

The contract and local setup guide will describe:

- partial-success replay behavior and the remaining at-least-once limitation;
- the five-attempt exhausted state and the manual retry command;
- the SecretRef requirement when API authentication is enabled;
- how to verify dashboard CRM capability first, then run one Discord read and
  one approval-gated write against the same agent;
- that successful automated tests do not count as real Gmail, Calendar, or
  target-hardware verification.

Instructions will use short commands, expected results, and plain-language
recovery steps suitable for nontechnical operators.

## 11. Testing and Acceptance

Implementation will be test-driven. Focused tests will cover:

- Calendar failure prevents Gmail draft creation;
- Calendar success plus Gmail failure retries only Gmail;
- stored appointment and reminder event IDs prevent provider replay;
- rows fail four times, become exhausted on attempt five, and stay unselected;
- the user retry endpoint requeues exhausted rows and rejects agent calls;
- duplicate inbox messages do not rerun lead processing;
- identical source-event proposals deduplicate while changed payloads can requeue;
- CRM health database reads do not execute on the event-loop thread;
- demo time advance and reminder completion reject agent attribution;
- OpenClaw setup uses only a SecretRef identifier and never includes the token
  value in subprocess arguments or rendered output.

Final verification requires the complete backend test suite, dashboard production
build, setup-helper unit tests, migration tests from the previous schema, and a
review of the changed documentation. Live Gmail, Calendar, Mac mini, and Discord
acceptance remains explicitly unverified until run on configured hardware with
real provider accounts.
