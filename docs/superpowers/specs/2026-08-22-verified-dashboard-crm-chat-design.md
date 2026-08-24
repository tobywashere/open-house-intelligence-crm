# Verified Dashboard CRM Chat Design

Date: 2026-08-22

Status: Approved direction, pending written-spec review

## 1. Purpose

Make dashboard chat reliably perform natural-language CRM reads, reviewed
writes, and bookings on supported local hardware. Keep Discord on the same CRM
tool and approval path. A model must not be able to report that a CRM action
succeeded when the tool failed or no pending proposal exists.

This design follows the successful installation and policy work already in PR
#7. It does not redesign the dashboard, replace OpenClaw, change the database,
or bypass the existing audit and approval system.

## 2. Confirmed State

Live WSL2 testing at commit `f71506f` proved that:

- setup completes repeatedly;
- the dedicated `openhouse-crm` agent and native plugin load correctly;
- the global `coding` profile remains unchanged;
- the dedicated agent exposes only `openhouse_crm` and `exec`;
- exact JSON passed to `openhouse_crm` reaches the CRM backend;
- writes enter Pending approvals and are not applied automatically;
- missing briefing data remains missing instead of being fabricated.

The remaining failures are at the model-facing boundary:

1. The capability prompt can receive a plausible `CHECKED` reply without a CRM
   tool call. The backend correctly rejects this because no matching audit row
   exists.
2. `openhouse_crm` validates the operation name but declares `arguments` as an
   unrestricted object. The local model therefore invented fields such as
   `source_note` and `status` for `create_lead`.
3. The plugin converts every wrapper failure into the same generic error. The
   model cannot distinguish invalid arguments, an ambiguous lead, a missing
   lead, a schedule conflict, or an unavailable backend.
4. A full lead list can exceed the useful compact-tool response budget. The
   model then loses the exact count or part of the list.
5. Dashboard chat receives only OpenClaw's final prose. It cannot currently
   prove which tool result produced that prose, so it cannot stop a false
   success claim after a failed write.

These are one architectural problem, not five unrelated prompt bugs: the
application lets the model both choose a loosely typed action and certify its
own result.

## 3. Chosen Approach

Use a small verified orchestration loop for dashboard chat while retaining the
native `openhouse_crm` plugin as the only CRM execution path.

Dashboard chat will give OpenClaw two request-scoped client functions:

- `openhouse_crm_request`, which accepts the strict CRM operation contract;
- `finish_crm_response`, which submits a final response classification and the
  tool-call evidence it relies on.

Every orchestration round requires one of these client functions. The backend
will reject `finish_crm_response` until the turn has sufficient successful CRM
evidence or a truthful clarification or failure outcome. When OpenClaw returns
`openhouse_crm_request`, the backend invokes the installed native
`openhouse_crm` tool through OpenClaw's policy-controlled `POST /tools/invoke`
endpoint. The result then returns to the model as a normal tool result.

The backend, not the model, renders final mutation status and critical CRM
facts from the verified tool receipts. The model remains responsible for
understanding natural language, choosing operations, asking for missing
information, and drafting narrative content where narrative is appropriate.

Official OpenClaw contracts used by this design:

- Chat Completions client tools, required tool choice, tool calls, and tool
  follow-up messages:
  <https://docs.openclaw.ai/gateway/openai-http-api>
- direct, policy-controlled tool invocation:
  <https://docs.openclaw.ai/gateway/tools-invoke-http-api>

## 4. Why This Approach

### 4.1 Prompt and schema changes alone are insufficient

Strict schemas would stop the exact invalid arguments found in testing, but a
model could still skip a tool, stop after an error, or describe a failed write
as successful. Prompt-only correctness has already failed repeatedly on the
supported local model.

### 4.2 A custom deterministic language parser is unnecessary

Replacing the agent with a hand-built intent parser would be a larger product
change and would duplicate the language understanding OpenClaw already
provides. The application only needs to control execution and verify outcomes.

### 4.3 The native plugin remains the single execution path

Dashboard chat will not import router internals, write SQLite directly, or add
a second CRM client. Both dashboard and Discord continue through the plugin,
the fixed Python dispatcher, the REST API, agent attribution, the audit log,
and Pending approvals.

### 4.4 Required client functions create a closed loop

OpenClaw cannot end a dashboard CRM turn with unstructured prose alone. It must
request a CRM operation or explicitly finish with evidence. The backend can
therefore reject unsupported arguments and false success states before the
reply is saved or displayed.

## 5. Canonical Operation Contract

Replace the current name-only operation catalog with one source-controlled
contract that defines, for each operation:

- operation name;
- read, narrative, or reviewed-write classification;
- required arguments;
- optional arguments and defaults;
- field types, enums, bounds, and formats;
- whether additional properties are forbidden;
- stable result presentation metadata.

The plugin schema and dashboard client-function schema are generated from the
same contract. Tests require exact parity with the Python dispatcher's public
operations. An operation cannot be added to one surface without updating the
others.

Every operation rejects unknown arguments before executing the wrapper. This
specifically prevents `create_lead` from accepting `source_note`, `status`, or
any other unsupported field. Update operations receive an explicit allowlist
of writable fields instead of an open `**fields` model-facing schema.

The existing REST contract remains authoritative. This contract is the strict
model-facing projection of it.

## 6. Structured Execution Receipts

The plugin returns one of two bounded envelopes to the model and caller:

```json
{
  "ok": true,
  "operation": "create_lead",
  "kind": "proposal",
  "result": {
    "pending": true,
    "id": 4,
    "status": "pending",
    "summary": "Create lead Jordan Ellis"
  }
}
```

```json
{
  "ok": false,
  "operation": "create_lead",
  "kind": "error",
  "error": {
    "code": "invalid_arguments",
    "message": "Unsupported argument: source_note",
    "retryable": false
  }
}
```

The runner may expose only bounded, sanitized errors from a fixed set:

- `invalid_arguments`;
- `not_found`;
- `ambiguous_match`;
- `schedule_conflict`;
- `backend_unavailable`;
- `timeout`;
- `result_too_large`;
- `operation_failed`.

Absolute paths, tokens, environment values, raw stack traces, raw stderr, and
unbounded backend bodies remain hidden.

## 7. Compact Lead Directory

Add a read operation dedicated to natural-language listing and counting. It
returns:

```json
{
  "total": 15,
  "offset": 0,
  "limit": 25,
  "leads": [
    {
      "id": 4,
      "name": "Jordan Ellis",
      "status": "new",
      "score": 72,
      "area": "Kirkland",
      "intent": "buy",
      "is_neglected": 0
    }
  ]
}
```

The total is computed before pagination and remains exact. Full profiles stay
behind `get_lead_context`. The existing `list_leads` operation remains
available for compatibility, but skill guidance uses the compact directory for
ordinary list, count, and prioritization questions.

## 8. Dashboard Chat Data Flow

1. The dashboard sends the existing `{message, session_id}` request.
2. The backend persists the user turn as it does today.
3. The OpenClaw driver starts a bounded orchestration loop with the dedicated
   agent, the two client functions, and required client-tool selection.
4. `openhouse_crm_request` arguments are validated against the canonical
   operation contract.
5. The backend calls `POST /tools/invoke` with `tool=openhouse_crm`, the
   dedicated agent ID, a turn-scoped session key, and a stable idempotency key.
6. OpenClaw applies the real agent policy, invokes the plugin, and returns the
   structured receipt.
7. The receipt is appended as a client tool result. The model may request
   another CRM operation for multi-step work such as resolving a lead, checking
   availability, and proposing a booking.
8. `finish_crm_response` supplies an outcome classification and the call IDs it
   relies on.
9. The backend verifies that classification against the collected receipts,
   renders authoritative facts or mutation status, persists the final reply,
   and returns it to the dashboard.

The accepted finish classifications are exact:

| Classification | Required evidence |
|---|---|
| `answered` | At least one successful read or narrative receipt |
| `queued` | One successful proposal receipt with a real pending ID |
| `needs_clarification` | No success claim; a bounded question for missing or ambiguous input |
| `failed` | No success claim; at least one structured error or an exhausted orchestration limit |

`queued` is rejected when its pending ID and operation do not match a collected
receipt. `answered` cannot be used to describe a mutation as submitted or
applied. A proposal receipt takes precedence over model wording.

The loop has a small fixed maximum number of CRM calls and model rounds. Hitting
the limit returns a truthful failure that says no unverified action was
reported. It never falls back to accepting arbitrary success prose.

The dashboard request uses a distinct synthetic channel marker. The plugin
blocks internal model-initiated `openhouse_crm` execution for that marker, so a
dashboard turn cannot execute the same mutation once internally and again
through the verified client loop. The backend's later `/tools/invoke` request
does not carry that marker and remains policy-controlled and callable.

## 9. Response Rules

### 9.1 Reviewed writes

If a receipt contains a real pending proposal, the final response is rendered
from that receipt and names its pending ID and review status. The model may add
a short clarification but cannot replace the status.

If every attempted write failed, the final response states that nothing was
queued or changed and includes only the sanitized reason. It cannot say
"created," "booked," "saved," "submitted," or "pending."

If the model never attempted a required write, it may ask a clarification or
admit failure. It cannot classify the outcome as queued.

### 9.2 Reads

Critical facts such as counts, lead IDs, names, statuses, appointment times,
availability, and dashboard metrics are rendered from receipts. Common read
operations use small deterministic natural-language formatters.

Narrative is allowed for drafts, explanations, and knowledge summaries, but it
must be based on the supplied receipt and cannot introduce new CRM records or
claim a mutation.

### 9.3 Briefings

The existing truthful-briefing boundary remains unchanged. Missing summaries
remain 404, canonical CRM schedule facts remain server-derived, and no fallback
generates fabricated market information.

## 10. Discord Behavior

Discord remains bound to the same dedicated `openhouse-crm` agent and native
plugin. It receives:

- the same strict operation schemas;
- the compact lead-directory operation;
- the same structured success and error receipts;
- updated skill guidance that distinguishes pending proposals from applied
  writes.

The plugin records its own write receipt for the active run. On channel
delivery, a bounded plugin hook replaces contradictory mutation prose with the
authoritative pending or failure receipt. Read narration remains model-written
from the strict tool result.

This hook is scoped to the dedicated CRM agent, the `openhouse_crm` tool, and
the exact run ID. It does not inspect or rewrite unrelated agents, plugins, or
messages. Setup must capability-check the required hook surface. If the
installed OpenClaw cannot provide it, Discord write verification is reported as
unsupported rather than silently described as safe.

## 11. Capability and Health Checks

The CRM doctor no longer asks the model to invoke a tool or reply `CHECKED`.
It calls OpenClaw's `POST /tools/invoke` directly with:

- `tool: openhouse_crm`;
- `agentId: openhouse-crm` or the configured dedicated agent;
- `operation: generate_dashboard_insights`;
- a unique `probe_nonce`;
- a stable turn-specific idempotency key.

`crm_verified` still requires the matching new backend audit row. A 200 response
without the nonce audit, an old audit row, plugin inventory, or chat text is
insufficient.

The ordinary live-chat check remains separate. The report therefore proves:

1. the chat endpoint can complete a turn;
2. the installed plugin can execute under the dedicated agent's policy;
3. the CRM backend recorded that exact read.

Capability detection, not a guessed version range, remains the compatibility
rule. An unsupported Chat Completions client-tool contract or plugin hook stops
the affected feature with an actionable message.

## 12. Security and Data Integrity

- CRM execution still goes through OpenClaw's effective agent policy.
- The model cannot choose an executable, shell command, URL, token, path,
  timeout, environment variable, or working directory.
- The dashboard uses the already configured loopback Gateway credential and
  never exposes it to the browser.
- `/tools/invoke` is used only from the backend to the local Gateway.
- Every reviewed write still carries agent attribution and enters Pending
  approvals.
- No write is reported as pending unless its receipt contains a real pending
  ID returned by the backend.
- The backend never automatically retries a mutating `/tools/invoke` request.
  A stable idempotency key keeps the call identity traceable, while the existing
  pending-change validation remains the final mutation boundary.
- Tool results and errors are size-bounded and sanitized.
- The orchestration loop has fixed call, round, and time limits.
- User and retrieved knowledge content remain untrusted data, not system
  instructions.

## 13. Open-Source and Hardware Compatibility

Supported deployment targets remain:

- Apple-silicon Mac mini with at least 16 GB memory;
- Linux x86_64 or ARM64 with at least 16 GB memory;
- Windows 11 through WSL2 on a host with at least 16 GB memory.

Native Windows remains unsupported. A WSL guest that exposes slightly less
than 16 GiB continues to receive a warning, not a false hard failure, when the
configured local model works.

The implementation adds no cloud service, paid dependency, database service,
compiler requirement, or platform-specific application path. Setup and the
doctor remain safe to rerun. Beginner documentation continues to lead with one
setup command, one serve command, and one compatibility command.

## 14. Verification

### 14.1 Automated tests

Tests must prove behavior rather than source wording:

- every catalog operation has an exact schema and Python dispatcher target;
- unsupported fields fail before child execution;
- safe HTTP and validation failures produce the correct structured error;
- secrets, paths, stderr, and oversized data cannot appear in errors;
- compact lead listing reports an exact total and stable pagination;
- dashboard turns cannot finish without verified evidence;
- failed writes cannot produce a queued response;
- pending writes render the exact pending ID and remain unapplied;
- dashboard synthetic-channel turns cannot execute the internal plugin path;
- multi-step booking can read, check availability, and queue one proposal;
- the doctor calls `/tools/invoke` and still requires the matching nonce audit;
- Discord write-receipt correction is scoped by agent and run;
- chat persistence stores only the verified final response;
- existing approval, audit, briefing, voice, setup, and rollback behavior does
  not regress.

Run the complete backend suite, plugin suite, installer suite, and dashboard
production build.

### 14.2 One-command live acceptance

Add an explicit acceptance command for supported hardware. Its default mode is
read-only. A separate `--allow-test-write` flag authorizes disposable reviewed
create-lead and booking proposals. It never approves either proposal.

Setup is an explicit prerequisite, not a hidden acceptance-runner mutation. A
separate evidence helper performs two explicit setup runs and records strict,
machine-verifiable setup evidence tied to the tested revision. The acceptance
runner validates that evidence and reports whether both runs succeeded and were
idempotent by comparing complete canonical structured installed-state snapshots
after each run. The helper requires a clean, unchanged exact revision before and
after both runs. Acceptance recomputes each snapshot digest and strictly checks
the tracked HEAD files, content digests, executable modes, shipped and installed
skills, plugin registration/configuration and runtime inventory, agent policy,
bindings, executable approvals, and relevant gateway references. Modified,
missing, or ignored extra files in any material source tree are a required
failure, as are partial, failed, malformed, mismatched-state, dirty-worktree,
changed-HEAD, or wrong-revision evidence. Sanitized setup logs are manual
diagnostics only and cannot make `Setup twice` pass.
Shared JSON includes no raw logs, local paths, home data, or secrets.

The sanitized report records:

- revision and dependency versions;
- two setup runs, revision match, and idempotence evidence;
- chat endpoint result;
- direct audited CRM capability result;
- ordinary natural-language lead count with the API count beside it;
- ordinary natural-language pending write with its pending ID;
- proof that the disposable lead was never applied;
- denial and cleanup of the disposable proposal;
- an ordinary natural-language booking proposal for an existing lead, with a
  unique future time and address marker;
- the exact booking pending ID and `book_appointment` operation;
- proof that no appointment was applied before or after denial and that no
  acceptance-owned booking proposal remains Pending;
- truthful missing-briefing behavior;
- Discord binding status, labeled only as a manual-hardware prerequisite;
- cleanup results and any remaining warnings.

The command exits nonzero when a required capability fails. It must not ask a
tester to edit OpenClaw configuration or application source between checks.
Read-only mode does not attempt proposals. Write mode first establishes strict
Pending and appointment baselines, owns proposals only by exact unique payload,
never approves, and fails closed when ownership or cleanup cannot be proved.

Discord delivery remains a manual hardware test because binding inspection is
not authoritative delivery evidence. Binding alone is not proof. When Discord
is in scope, a bound tester must verify that Discord lists the real CRM lead
count and that a disposable write appears in dashboard Pending approvals while
remaining unapplied. Merge waits for the manual Discord evidence when Discord
is in scope.

## 15. Scope Boundaries

Included:

- strict CRM operation schemas;
- structured receipts and safe errors;
- compact lead listing;
- verified dashboard orchestration;
- deterministic capability invocation;
- Discord mutation-receipt protection;
- automated and supported-hardware acceptance coverage;
- beginner documentation updates for the new checks.

Not included:

- dashboard visual redesign;
- database schema changes;
- replacing OpenClaw or the local model;
- native Windows support;
- configuring a Discord account for the operator;
- choosing or installing an audio transcription provider;
- lowering the documented 16 GB hardware baseline;
- unrelated CRM feature work.

## 16. Completion Criteria

The change is complete only when a clean supported-hardware run and its
sanitized evidence prove all of the following without manual repair:

1. two explicit setup runs succeed at the tested revision and the acceptance
   report validates their complete canonical installed-state snapshots as
   identical on a clean, unchanged exact revision whose tracked HEAD files,
   content digests, and executable modes match at every checkpoint, with no
   ignored extra files in material setup trees;
2. the doctor records an audited direct CRM call;
3. dashboard chat reports the exact real lead count;
4. dashboard chat queues a natural-language disposable write and names the
   real pending proposal;
5. a deliberately invalid write reports that nothing changed;
6. the disposable proposal is not applied and can be denied;
7. the automated runner uses an existing lead and an ordinary
   natural-language booking proposal to queue exactly one real
   `book_appointment` pending ID, proves that no appointment is applied, denies
   exactly the acceptance-owned proposal, and proves it remains unapplied with
   no acceptance-owned proposal left Pending;
8. missing briefing sources produce no fabricated content;
9. Discord, when bound and in scope, has manual hardware evidence that it lists
   the real CRM lead count and routes a disposable reviewed write into dashboard
   Pending approvals without falsely reporting a failed write as pending;
10. all automated suites and the dashboard build pass.

Automated tests and a binding/configuration check cannot satisfy item 9. No
tracked file contains fabricated passing hardware output.
