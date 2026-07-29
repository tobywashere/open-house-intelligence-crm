# Mac mini, Voice Intake, and Trustworthy Briefing Design

**Date:** 2026-07-28  
**Status:** Approved  
**Decision:** Use a verified-hybrid architecture: CRM facts are derived from the
database, while AI output is restricted to clearly identified advice and draft
language.

## 1. Context

OpenHouse Intelligence already has a working FastAPI/SQLite backend, dashboard,
OpenClaw relay, CRM operation skill, deterministic analytics, appointment
booking, and optional Gmail/Google Calendar integrations. A repository
assessment found several gaps between the product claims and the behavior:

- the product was documented primarily around the original Dell GB10 even
  though the application should also run on an Apple-silicon Mac mini;
- voice-note intake was advertised but not implemented;
- the dashboard could invent meetings when no briefing existed;
- arbitrary agent-authored briefing payloads were stored and displayed without
  structural or referential validation;
- a reachable OpenClaw gateway was reported as connected even when its
  `/v1/chat/completions` endpoint was disabled, producing a chat failure for a
  first-time operator;
- documentation described “chat that acts” as parked even though
  natural-language CRM reads, writes, reminders, and booking are the actual
  supported agent contract;
- launcher environment loading, bind-address guidance, gateway health,
  integration retries, integration status labels, mock behavior, outcome
  tracking, privacy wording, and beginner onboarding had correctness gaps.

The implementation must close these gaps without requiring cloud inference or
silently writing uncertain voice transcriptions into the CRM.

## 2. Goals

1. Support an Apple-silicon Mac mini with 16 GB RAM as the documented minimum
   local-AI host.
2. Finish browser voice-note intake with local transcription, review, duplicate
   detection, and explicit confirmation.
3. Never invent appointments, people, or CRM facts in the morning briefing.
4. Treat natural-language CRM reads, writes, follow-ups, and booking as the
   product's supported agent capability and verify the contract in tests.
5. Fix the high-priority runtime and documentation issues found during the
   assessment.
6. Make the README understandable and useful to a nontechnical operator.

## 3. Non-goals

- Supporting Intel or 8 GB Mac minis.
- Realtime streaming speech recognition or a voice-call interface.
- A cloud transcription fallback.
- Automatically saving a lead solely because a transcription completed.
- A general claim-provenance ledger or web crawler.
- Replacing OpenClaw as the agent harness.
- Guaranteeing performance for every model that can technically start on
  16 GB; the documentation will name a tested size class and explain the memory
  tradeoff.

## 4. Trust Model

Displayed information belongs to one of four classes:

| Class | Examples | Display rule |
| --- | --- | --- |
| CRM fact | lead name, budget, appointment time | Read from SQLite or an explicitly configured integration; never accepted from generated prose as canonical |
| Deterministic calculation | days since contact, pipeline count | Computed from CRM records by application code |
| AI suggestion | preparation idea, recommended next step, email draft | Clearly labeled as a suggestion or draft and never presented as a recorded fact |
| External research | market-news item | Requires a source name and valid HTTP(S) URL; generated takeaway is labeled as an AI summary |

An absent source produces an honest empty state. The application will not
substitute plausible-looking content.

## 5. Supported Mac mini Architecture

The supported minimum is:

- Apple-silicon Mac mini;
- 16 GB unified memory;
- current supported macOS;
- OpenClaw and a local tool-capable model;
- local batch transcription configured through OpenClaw's
  `infer audio transcribe` interface.

The application remains model-server agnostic. The Mac mini guide will document
a conservative small-model configuration suitable for 16 GB and explain that
larger models, long contexts, and simultaneous model/transcription workloads
need more memory.

The production launcher will:

1. load a repository-root `.env` before applying defaults;
2. default to loopback-only binding;
3. document the explicit `HOST` value required for LAN or Tailscale access;
4. expose one application port for the dashboard and API;
5. fail with actionable errors when required executables or built assets are
   missing.

A read-only readiness command will report:

- Python, Node, and dashboard-build availability;
- database writability;
- configured bind address;
- OpenClaw gateway reachability/authentication state;
- whether the configured Chat Completions endpoint is enabled;
- transcription configuration;
- optional integration configuration;
- the distinction between “configured” and “last verified by a successful
  operation.”

The readiness command's explicit live-agent check may send one harmless test
prompt. Routine dashboard health polling must not repeatedly invoke the model.

## 6. Voice-note Intake

### 6.1 User flow

The dashboard gains a voice-intake route reachable from primary navigation:

1. record through `MediaRecorder` or choose an existing audio file;
2. preview, replace, or delete the recording;
3. submit it for local transcription;
4. review and edit the transcript;
5. extract structured CRM fields from the reviewed text;
6. review possible duplicate leads;
7. explicitly choose create, update an existing lead, or cancel;
8. show the resulting lead and audit entry.

The recorder must handle denied microphone permission, unsupported browsers,
empty recordings, oversized files, timeouts, and unavailable transcription
without losing the user's recording or typed transcript.

### 6.2 Backend boundary

The backend accepts an audio data URL plus a declared filename/content type.
It validates an allowlist of audio MIME types, decoded size, and a 20 MB maximum
before writing a uniquely named temporary file. The temporary file is removed
after the request, including on failure.

Transcription uses an adapter interface. The production adapter executes
OpenClaw's argument-vector CLI without a shell:

```text
openclaw infer audio transcribe --file <temporary-path> --json
```

An optional configured local model may be passed through the supported
`provider/model` selector. There is no application-level cloud fallback. The
mock adapter is deterministic only in automated tests; the shipped UI never
pretends that an untranscribed recording has real text.

The response contains:

```json
{
  "transcript": "Met Taylor Brooks...",
  "draft": {
    "name": "Taylor Brooks",
    "phone": null,
    "email": null,
    "intent": "buy",
    "area": "Bellevue",
    "budget": 900000,
    "timeline": "this fall",
    "preferences": ["three bedrooms"]
  },
  "duplicates": [],
  "warnings": []
}
```

All extracted fields use existing lead validation rules. A transcription or
extraction request performs no CRM mutation. Creation/update uses the existing
lead endpoints only after explicit user confirmation.

### 6.3 Audit and privacy

The audit stream records transcription success/failure metadata and the final
confirmed CRM action, but not raw audio. Raw audio is not retained by default.
Documentation states that local transcription remains local only when the
operator configures a local OpenClaw audio provider.

## 7. Trustworthy Briefing

### 7.1 Factual envelope

The briefing displayed by the dashboard is assembled from current CRM data:

- today's schedule comes only from real appointment rows;
- factual meeting fields are rehydrated from the referenced lead and
  appointment;
- greeting counts are computed from those rows;
- pipeline headlines and follow-up urgency come from deterministic application
  calculations;
- names, areas, scores, times, and contact history are never trusted from
  generated prose.

Meeting schedule records gain an appointment identifier. Legacy stored payloads
without valid lead/appointment references are sanitized on read rather than
displayed.

### 7.2 Agent contribution

The agent may contribute:

- preparation suggestions;
- a recommended conversational approach;
- a draft next action.

These fields are stored only after schema validation and only for a lead or
appointment that exists. The UI labels them “AI suggestion” or “Draft.” The
agent cannot override the canonical name, appointment time, score, or other CRM
facts.

### 7.3 Empty and failure states

If no appointment exists, the UI says that no appointments are scheduled. It
does not promote high-scoring leads into fake meetings. If no agent advice has
been generated, the factual briefing still renders and the advice area says
that no suggestion is available.

If an API request fails, the UI shows a retryable error. It does not convert a
network/authentication/server error into mock content.

### 7.4 Daily summary and research

Client-side generated summary fallbacks are removed. Stored report payloads use
typed schemas. Market items require a source name and HTTP(S) URL; the takeaway
is labeled as an AI-generated summary. A missing or invalid summary produces an
honest empty state.

Refresh reports success only after a newer persisted summary is observed. A
request timeout or agent failure remains visible and does not claim that a
fresh briefing is ready.

## 8. Natural-language CRM Contract

The supported live-agent contract is:

- find and summarize leads;
- create and update leads;
- record notes and activities;
- schedule follow-ups;
- detect and review duplicates;
- draft follow-up messages;
- check availability and book appointments;
- mark follow-ups sent;
- close opportunities with an explicit outcome.

The OpenClaw skill documentation and README will use this concrete wording
instead of “chat that acts.” Contract tests will exercise the skill's REST
mapping against a disposable backend and verify that create, update, reminder,
and conflict-checked booking operations change the expected records and audit
entries.

Mock mode will remain explicitly a product tour. Its recognized prompts and
extraction examples must match the dashboard copy, including punctuation such
as `follow-up`, and it must not silently create an “Unknown lead” when a name is
present. Enabling the real OpenClaw endpoint fixes the live-agent path but does
not excuse the separate deterministic mock extraction bug.

## 9. Team Integration Boundary

An upstream documentation change (`3b153f1`) now explains how to enable
OpenClaw's disabled-by-default Chat Completions endpoint and documents the
business-card flow. This work preserves and simplifies that guidance rather
than replacing it.

Bobo is concurrently repairing the `daily-command-center` skill. This work
does not independently rewrite that skill. It owns the receiving boundary:

- typed and referentially validated briefing POST/GET behavior;
- canonical CRM-derived facts;
- honest dashboard empty/error states;
- compatibility updates needed after Bobo's change lands.

Before modifying the daily-brief skill or its output contract, pull the latest
upstream state and adapt rather than duplicating the concurrent implementation.

## 10. Runtime Reliability Repairs

### 10.1 Environment and networking

- `scripts/dev.sh` and `scripts/serve.sh` load `.env` consistently.
- `.env.example`, README, Local AI, GB10, and Mac mini instructions describe
  the actual behavior.
- Loopback remains the secure default; LAN/Tailscale binding is explicit.

### 10.2 OpenClaw status

Gateway health distinguishes:

- unreachable;
- reachable but unauthorized;
- reachable but the configured Chat Completions endpoint is disabled;
- endpoint enabled but not yet verified by a successful completion;
- last chat completion succeeded;
- last chat completion failed.

HTTP 401, 403, and 404 never count as a working chat connection. A lightweight
health probe reports process/endpoint reachability, while the application
records the result of actual chat attempts for the “last verified” state.
Malformed model responses and timeouts return actionable errors rather than
unhandled 500 responses.

### 10.3 Gmail and Google Calendar

Read-only operations may use bounded transient retries. Non-idempotent send and
create operations are not automatically replayed unless the external API
supports and receives an idempotency key.

The UI/API distinguish:

- integration disabled;
- configured;
- last operation succeeded;
- last operation failed.

Possessing a key or CLI session alone is not labeled “Google live.”

### 10.4 Opportunity outcomes

Closing a lead requires an explicit `won` or `lost` outcome, with an optional
reason. Existing `closed` records remain readable. Funnel conversion counts won
opportunities rather than treating every closed lead as a successful
conversion.

## 11. Documentation Design

The README is rewritten as an operator-first guide:

1. what the product does, in plain language;
2. choose Demo mode or Local AI mode;
3. Mac mini prerequisites and copy/paste setup;
4. first-use walkthrough:
   - add a lead by typing;
   - add one by voice;
   - review a duplicate;
   - draft a follow-up;
   - book an appointment;
5. what is local and what optional integrations transmit;
6. backup and update instructions;
7. common problems and their fixes;
8. advanced/developer links.

The quickstart will not contain placeholder clone URLs. Feature status is
truthful: working, requires local AI, optional integration, or planned. The
README, `.env.example`, setup guides, contract, historical status notes, and
test count will not contradict one another.

## 12. Test Strategy

### Backend

- audio MIME, size, malformed data URL, temp-file cleanup, CLI argument safety,
  timeout, malformed JSON, and successful transcription;
- extraction validation and zero-write preparation;
- briefing schema validation and canonical fact rehydration;
- rejection/sanitization of unknown leads and appointments;
- summary source URL validation;
- OpenClaw endpoint-disabled, 2xx/401/403/404/405/timeout, successful-chat,
  and malformed-response behavior;
- no automatic retry for non-idempotent integration writes;
- explicit won/lost outcomes and funnel behavior;
- skill action contract against a temporary database.

### Dashboard

- record/upload/review/confirm voice flow;
- denied microphone and unavailable transcription states;
- briefing with appointments, without appointments, and with invalid stored
  agent data;
- no mock fallback after 404 or network failure;
- accurate integration and gateway status labels;
- won/lost close flow;
- production TypeScript build.

### End-to-end

- text creates or updates a lead through the supported action contract;
- reviewed voice transcript creates the intended lead;
- appointment booking respects conflict checks;
- missing briefing never produces fabricated meetings;
- a fake OpenClaw gateway proves auth/error handling deterministically.

Hardware verification is a separate checklist because this development machine
cannot prove Mac mini performance. The repository will provide commands and
expected results for an operator to run on the target Mac mini, including model
and transcription checks.

## 13. Migration and Compatibility

- Database additions are additive and applied by the existing migration path.
- Existing closed leads receive a null/unknown outcome until explicitly
  classified; they are excluded from won conversion totals.
- Existing report rows remain stored but are sanitized through the new response
  schema.
- Existing GB10 deployments continue to use `scripts/serve.sh`.
- New environment variables have safe defaults and are documented.

## 14. Acceptance Criteria

The work is complete when:

1. no code path fabricates meetings or silently substitutes mock briefing
   content;
2. recorded/uploaded audio can be locally transcribed, reviewed, and explicitly
   saved on the supported hardware path;
3. natural-language create/update/reminder/booking contracts pass against a
   disposable backend;
4. the readiness check detects a disabled OpenClaw Chat Completions endpoint,
   and OpenClaw/integration status labels reflect verified state;
5. non-idempotent integration actions cannot be duplicated by the generic
   retry loop;
6. won/lost outcomes drive accurate funnel conversion;
7. `.env`, bind-address, Mac mini, privacy, and feature-status documentation
   match runtime behavior;
8. backend tests, dashboard checks, and production build pass;
9. the README can be followed by a nontechnical Mac mini operator without
   requiring the historical project documents.
