# OpenClaw Minimal Reliability and Open Source Readiness Design

Date: 2026-08-05

Status: Approved for implementation planning

## 1. Purpose

Make dashboard chat and Discord CRM actions work reliably with OpenClaw while
keeping the existing application architecture. The project should be practical
for a nontechnical user to install on a 16 GB Mac mini or a Linux machine, and
the product must not present generic chat connectivity as proof that CRM actions
work.

This design intentionally avoids a backend chat rewrite, a native OpenClaw
plugin, and a new agent framework. It standardizes and verifies the existing
OpenClaw skill approach first.

## 2. Confirmed Problem

The current backend sends valid requests to OpenClaw's Chat Completions
endpoint, but it targets the default agent and assumes that agent has already
loaded `crm-db-operations` and can execute its Python client.

The following conditions can therefore produce a healthy-looking but generic
assistant:

- the skill is installed under the wrong name or in the wrong location;
- the selected agent has a nonempty allowlist that excludes the skill;
- the selected agent does not have the `exec` capability needed by the skill;
- the OpenClaw service does not inherit `CRM_API_URL` or the API token;
- an existing OpenClaw session holds an older skill snapshot;
- the dashboard is talking to `main` while Discord uses another agent;
- the endpoint answers ordinary prompts but cannot complete a CRM tool call.

The existing readiness check asks for `READY` while explicitly prohibiting tool
use. It proves chat transport only. The `verified` label does not prove CRM
capability.

The newly merged pending-change queue provides a useful human-review foundation,
but currently covers only create, update, close, delete, and merge lead actions.

## 3. Goals

1. Make one supported setup path configure a dedicated OpenClaw CRM agent.
2. Make dashboard chat and Discord use that same agent configuration.
3. Prove that the agent executed a real, read-only CRM operation before showing
   CRM capability as verified.
4. Preserve human review for agent-proposed changes and extend it to the missing
   user-facing writes required by natural-language CRM use.
5. Prevent briefings from filling missing CRM information with generated claims.
6. Make degraded extraction or drafting visible instead of silently presenting
   fallback output as normal agent output.
7. Provide beginner-oriented installation and recovery documentation.
8. Verify the supported workflow on a 16 GB Mac mini and Linux without requiring
   GB10-specific hardware.

## 4. Non-goals

- Replacing `/v1/chat/completions` with a new chat protocol.
- Supplying a second backend-owned function-calling loop.
- Building or publishing a native OpenClaw plugin in this change.
- Replacing SQLite or restructuring the CRM data model.
- Reworking the dashboard layout.
- Automatically enabling cloud providers or external integrations.
- Silently granting broad host command access to an existing OpenClaw agent.

If the verified dedicated-agent approach still fails on supported OpenClaw
versions and hardware, a native tool plugin or backend tool loop can be proposed
as a separate, evidence-driven project.

## 5. Chosen Architecture

### 5.1 Dedicated CRM agent

Add one application setting, `AGENT_ID`, for the OpenClaw agent used by the CRM.
The supported agent identifier will be `openhouse-crm`. The skill remains named
`crm-db-operations`; documentation and diagnostics must clearly distinguish the
agent identifier from the skill name. The backend will target
`openclaw/<AGENT_ID>` when the setting is present and retain the current
`openclaw` model value only as a documented compatibility fallback.

The dedicated agent isolates CRM skill and command permissions from a user's
general-purpose `main` agent. Dashboard chat and the documented Discord binding
will both use this agent.

### 5.2 Idempotent setup helper

Provide one setup helper for macOS and Linux. It will use the installed OpenClaw
CLI rather than editing undocumented configuration files directly.

The helper will:

1. detect the OpenClaw version and reject unsupported versions with a clear
   upgrade or compatibility message;
2. copy the required skills with their canonical directory names;
3. create the dedicated agent when absent or update only the CRM-owned parts of
   an existing dedicated agent;
4. include the required skills in an explicit allowlist when one is used;
5. configure the execution capability required by the current skill;
6. configure the backend URL and optional API token for the OpenClaw service;
7. validate configuration through the OpenClaw CLI;
8. restart or reload the Gateway only when required;
9. run the capability checks described below;
10. print the exact recovery action when a step cannot be completed safely.

The helper must support a dry-run mode and must not overwrite unrelated agents,
providers, channels, models, or credentials. Enabling execution must be explicit
in its output. The dedicated agent should use the narrowest OpenClaw sandbox and
tool policy that still permits the repository's CRM skill to run.

### 5.3 Skill paths and sessions

All skill references will use canonical installed names and location-independent
paths supported by OpenClaw, such as `{baseDir}`, rather than hardcoded
`~/.openclaw/skills` imports or repository-relative commands.

Capability verification will always use a new session identifier. Starting a
new dashboard conversation already creates a new identifier, but clearing local
chat history does not clear the corresponding OpenClaw session. The UI and docs
will distinguish clearing displayed history from starting a genuinely new agent
session.

## 6. Capability and Health Contract

Health will distinguish these layers:

1. Gateway reachable
2. Chat endpoint enabled and authorized
3. Ordinary completion successful
4. Dedicated CRM agent selected
5. Required skill eligible
6. Required execution capability available
7. CRM backend reachable from the OpenClaw process
8. Read-only CRM capability verified

The existing live check remains useful for chat transport, but it will no longer
cause the UI to claim that CRM capability is verified.

The CRM capability probe will use a fresh agent session and request the existing
deterministic `generate_dashboard_insights` read. The metrics route will audit
this read only when it carries the skill client's `X-Actor: agent` marker, so
normal dashboard polling does not create probe evidence. The doctor will record
the audit position before the request and verify that a new matching agent audit
entry appears afterward. This proves real skill execution without a business-data
mutation or optional calendar/network call. Merely seeing plausible metrics in
model text is not sufficient.

The status UI should use plain labels such as:

- `OpenClaw connected`
- `Chat verified, CRM not verified`
- `CRM agent verified`
- `CRM agent degraded`

Diagnostic details must remain local and must not display tokens or sensitive
configuration values.

## 7. Natural-language CRM Writes and Approval

The existing pending-change queue will remain the single operator-review surface
for agent-proposed CRM changes.

The following agent actions require editable approval before application:

- create a lead;
- update a lead;
- add a note or activity record;
- book an appointment;
- schedule a reminder;
- close a lead;
- merge leads;
- delete a lead.

Approving an appointment or reminder is the point at which any enabled external
calendar hook may run. Denying it must leave both local and external state
unchanged.

Safe reads, availability checks, duplicate searches, and draft generation do not
require approval. Deterministic scoring may remain immediate when it changes only
derived score fields. If its current processing path can change operator-entered
lead fields, that field-changing portion must either be separated or queued.

Agent attribution must be preserved through the shared backend route. Manual
dashboard edits continue to apply through their current direct path.

## 8. Truthful Extraction, Drafting, and Briefings

### 8.1 Fallback visibility

The current deterministic fallback behavior may remain for resilience, but it
must be labeled. Health and the relevant review screen will indicate when local
fallback extraction or drafting was used. A fallback result remains editable and
must not be committed without the existing operator confirmation step.

No fallback may invent a CRM record, appointment, action completion, or source.

### 8.2 Daily briefings

Daily briefing data must come from CRM tool results and stored source material.
The briefing flow will:

- use corrected location-independent skill paths;
- distinguish no records from agent failure;
- reject or omit unsupported claims;
- display `unavailable` or equivalent language when source data is missing;
- avoid sample, placeholder, or mock briefing content in real OpenClaw mode;
- preserve source identifiers needed to trace suggestions back to CRM records.

Tests will cover an empty CRM, partial records, an unavailable agent, malformed
agent output, and unsupported claims. The empty state must remain useful without
inventing priorities, events, or follow-ups.

## 9. Documentation and Open Source Readiness

The root README will prioritize a short, nontechnical path:

1. supported hardware and software;
2. install OpenClaw;
3. copy `.env.example`;
4. run the CRM OpenClaw setup helper;
5. start the application;
6. run the doctor;
7. open the dashboard;
8. complete one safe verification workflow.

The README will state that 16 GB memory is the supported minimum for a Mac mini,
while actual local-model speed depends on the selected model and provider.

Advanced OpenClaw, GB10, integration, and troubleshooting details will remain in
dedicated documents linked from the README. All setup documents will use the
same skill names, agent name, environment variables, port defaults, and status
language.

The project will document:

- the supported OpenClaw version range;
- what the setup helper changes;
- the security implications of agent execution permission;
- how to undo or re-run setup;
- how to bind the dedicated agent to Discord;
- how to start a fresh session after a skill update;
- how to identify fallback mode;
- which features require optional internet integrations;
- how to report a diagnostic bundle without exposing secrets.

## 10. Error Handling

- Missing or unsupported OpenClaw: stop setup and provide the supported action.
- Existing conflicting CRM agent: show the conflict and require an explicit
  repair option rather than overwriting it.
- Missing skill or ineligible skill: identify the exact name and location.
- Missing execution capability: report it separately from skill discovery.
- Gateway cannot reach the CRM backend: show the backend URL without credentials.
- Capability call returns generic text without an audit entry: report CRM as not
  verified.
- Chat works but CRM does not: keep generic chat available, but label it clearly
  and disable claims that CRM actions succeeded.
- Agent-proposed write cannot be queued: return a visible failure and do not
  apply the write directly as a fallback.
- Briefing generation fails: retain the last valid dated briefing when present,
  otherwise show an honest empty state.

## 11. Testing Strategy

### 11.1 Automated tests

- Agent model selection with and without `AGENT_ID`.
- Setup helper dry-run, repeat run, partial configuration, conflicting agent,
  unsupported version, and secret redaction.
- Skill path resolution from managed and agent-specific installations.
- Fresh-session capability probe and audit proof.
- Negative capability cases for generic replies, missing `exec`, missing skill,
  bad token, and unreachable backend.
- Separate chat transport and CRM capability statuses in API and UI.
- Approval, editing, denial, and replay for notes, appointments, and reminders.
- No external hook before approval.
- Scoring does not bypass approval for operator-entered field changes.
- Fallback status propagation for extraction and drafting.
- Briefing grounding and honest empty states.
- Documentation command smoke checks where practical.

### 11.2 Hardware acceptance

Run the documented clean-install path on:

- a 16 GB Mac mini using a supported local model configuration;
- a supported Linux machine;
- the original GB10 configuration when available.

The acceptance workflow will verify:

1. doctor reports CRM capability, not only chat transport;
2. dashboard chat lists real CRM leads without inventing records;
3. Discord lists the same records through the dedicated agent;
4. a natural-language lead write appears as an editable pending approval;
5. a note, reminder, and booking each require approval;
6. denial leaves local and integration state unchanged;
7. voice-note intake produces an editable draft and requires confirmation;
8. an empty daily briefing contains no fabricated content;
9. restarting OpenClaw and the CRM preserves a working configuration;
10. a new session picks up an updated skill.

Hardware-specific claims may only be marked verified after this checklist is run
on that hardware. Until then, documentation will label the checklist as
operator-run validation.

## 12. Implementation Boundaries and Order

Implementation planning should divide the work into small, independently tested
changes:

1. agent targeting and truthful status vocabulary;
2. setup helper and canonical skill paths;
3. real read-only capability verification;
4. missing approval coverage for core CRM writes;
5. fallback and briefing truthfulness;
6. beginner README and synchronized setup documents;
7. automated regression suite and hardware acceptance checklist.

Each change should preserve existing APIs where practical. Database migrations
must be additive. Existing mock mode remains available for development, but the
UI and documentation must never present it as real CRM agent verification.

## 13. Success Criteria

The work is complete when a new user can follow the README on supported hardware,
run one setup helper, receive a successful CRM capability check, and use dashboard
chat for grounded reads and reviewable writes. Discord must use the same
dedicated CRM agent and pass the same read-only capability expectation.

No green status may mean more than the exact layer it verified. No agent-proposed
business write may claim success before required operator approval. No daily
briefing may substitute generated facts for missing CRM data.
