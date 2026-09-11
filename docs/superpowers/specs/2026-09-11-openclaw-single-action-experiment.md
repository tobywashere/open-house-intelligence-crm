# OpenClaw single-action experiment

The user approved an isolated comparison that keeps OpenClaw and local inference,
preserves approvals, and measures a simpler request path before any migration.
Base revision: `f015e4e`. This is an experiment, not a new supported release.

## Decision

Existing dashboard and analysis channels prohibit native model tool execution.
Do not remove those guards or change installation policy to make a prototype work.
First isolate one avoidable dependency: the model-selected finish call. The
candidate makes one model request through OpenClaw, validates exactly one proposed
operation, invokes the existing native CRM tool through the trusted gateway API,
and renders the validated receipt in application code. No model retry or second
completion is allowed. This does not yet test a fully native agent-owned loop.

Keep the production driver, API, plugin, and installer unchanged. Candidate code
lives in a separate module used by an experiment CLI and tests only. Reuse the
existing operation contract, receipt validation, and deterministic rendering.

## Candidate scope

Allow `list_lead_directory`, `get_lead_context`, `check_availability`,
`create_lead`, `add_note`, `schedule_followup`, and `book_appointment` only.
An optional per-run operation allowlist can narrow this set, never expand it.
The CLI narrows both paths to `list_lead_directory` and `get_lead_context` and
rejects every other operation before gateway dispatch. It never performs writes,
approvals, setup, model installation, or configuration changes.

Malformed, multiple, absent, or unsupported model tool calls stop without executing
any CRM call. Do not display model prose as a CRM fact. Invalid arguments ask the
operator to provide explicit fields; resolving ambiguous people across several
model turns is outside this candidate. A backend error remains an error.
Uncertain mutations are not retried or described as definitely unexecuted.
All model and tool work shares one bounded deadline.

Return a typed `SingleActionResult` with `reply`, `status`, `operation`, `receipt`,
`model_calls`, and `tool_calls`. Status is `answered`, `pending`, `failed`,
`needs_clarification`, or `unknown`. The structured receipt remains the source of
truth; no parser of model prose establishes success.

## Verification

1. Verify baseline backend and native-plugin tests.
2. Test candidate selection, validation, receipt handling, timeout/unknown outcomes,
   and refusal without external models. Script only the unavailable model boundary.
3. Exercise candidate proposals through real HTTP handlers and SQLite in temporary
   databases: create, note, follow-up, booking, approve, repeated approve, restart,
   and booking conflict. Disable external integrations. These are backend lifecycle
   tests, not browser or live-model evidence.
4. Provide a read-only comparison CLI with three ordinary lead-directory prompts,
   separate sessions, per-path timings and call counts, and API count comparison.
   Its default output excludes CRM content, model prose, arguments, and secrets.
   An explicitly requested private capture saves bounded exact replies to a new
   owner-only file; it is not a sanitized shareable report.
5. A missing runtime produces a clear failed/unavailable result, not simulated
   live success. The original WSL reply and live comparison remain pending until
   collected on configured hardware. Do not claim a winner from scripted tests.

## Exit and next decision

Produce tested experiment code and exact operator instructions. Do not merge,
push, change production routing, or upgrade OpenClaw. A successful local test run
is permission to try the experiment on the WSL host, not evidence to replace the
current architecture. Follow-up hardware work must verify natural-language writes
against an isolated configured database, UI behavior, and restore from backup.
