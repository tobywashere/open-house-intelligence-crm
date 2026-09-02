# Deterministic OpenClaw Setup Verification Design

Date: 2026-08-30

Status: Approved direction, pending written-spec review

## 1. Purpose

Make OpenClaw setup prove the dashboard and analysis channel restrictions
without depending on a local model choosing a tool. Preserve fail-closed
security, exact rollback, and supported OpenClaw cleanup while allowing setup
to finish on machines whose model can load the CRM schemas but does not
reliably follow forced tool-selection instructions.

This is a focused correction to the setup verification added in PR #7. It
does not redesign dashboard chat, replace OpenClaw, weaken human review, or
declare a machine compatible before live CRM acceptance passes.

## 2. Confirmed Problem

OpenClaw beta.3 now accepts the plugin, exposes the expected runtime hooks,
propagates the dashboard channel marker, and can automatically receive the
required conversation-hook permission. The remaining setup probe asks the
configured model to call a client-defined sentinel tool. On the tested WSL
machine, `qwen3.5:9b` returns normal text instead of making that tool call.
Setup therefore reports `tool_not_attempted`, rolls back an otherwise valid
security configuration, and never reaches live acceptance.

The model's behavior is a real compatibility concern, but it is not evidence
that the plugin's channel restriction is unsafe. The current probe combines
two separate questions:

1. Does OpenClaw propagate the protected channel and run the plugin hook that
   blocks native tools?
2. Will this model choose and correctly use the CRM tools in natural-language
   conversation?

Setup must answer the first question deterministically. Live acceptance must
answer the second with real audited CRM evidence.

The repeated WSL runs also exposed cleanup warnings. OpenClaw sometimes
removes the temporary diagnostic agent from configuration but reports a
session-store purge failure and leaves an empty agent directory. The installer
currently verifies roster removal but does not fully interpret the structured
agent-deletion result or retry OpenClaw's supported cleanup path.

## 3. Goals

- Prove protected dashboard and analysis channels reach the plugin hook.
- Prove a native tool attempt under each protected channel is blocked before
  execution.
- Keep malformed, missing, or contradictory hook evidence fatal to setup.
- Stop using probabilistic model tool selection as an installer safety gate.
- Keep full production schemas in setup so OpenClaw/provider schema rejection
  remains detectable.
- Report model noncompliance honestly as a compatibility warning.
- Require live, audited CRM behavior before claiming the installation works.
- Use OpenClaw's supported deletion and retry behavior for diagnostic cleanup.
- Preserve exact rollback and idempotence on macOS, Linux, and WSL.

## 4. Non-goals

- Do not bypass OpenClaw policy or call the CRM database directly.
- Do not weaken the dedicated agent's restrictive tool allowlist.
- Do not make reviewed writes apply without user approval.
- Do not claim Qwen, Granite, or any other model works merely because setup
  completes.
- Do not delete unknown OpenClaw directories with raw filesystem commands.
- Do not broaden this change into dashboard UI, database, or Discord redesign.

## 5. Chosen Architecture

Separate deterministic installation safety from behavioral compatibility.

### 5.1 Deterministic channel proof

For each protected channel, `openhouse-dashboard` and
`openhouse-analysis`, setup creates a bounded diagnostic run with:

- the temporary diagnostic agent;
- an isolated session key;
- the exact protected channel;
- a cryptographically random run nonce;
- a short expiration time.

Setup sends one ordinary chat request with no client tools. The plugin's
`before_agent_reply` hook matches the exact diagnostic body, agent, session,
channel, user trigger, and nonce, records that the protected conversation path
was reached, then returns a fixed setup-only reply before the model provider is
called. The record is bounded by count and time and is not exposed to ordinary
agents.

If the prompt hook does not run, receives the wrong channel, cannot correlate
the diagnostic identity, or produces malformed state, setup fails closed.

### 5.2 Deterministic native-sentinel block proof

After prompt-channel evidence exists, setup directly invokes a setup-only
native sentinel through OpenClaw's policy-controlled `/tools/invoke` API. The
invocation carries the same diagnostic agent, session, protected channel, and
nonce. This is not a model-selected client tool.

The plugin's `before_tool_call` hook must:

1. match the invocation to an unexpired prompt record;
2. verify the exact agent, session, channel, and nonce;
3. record `tool_blocked` for that run;
4. block the sentinel before its handler executes.

The sentinel handler records `sentinel_executed` if execution ever reaches
it. Setup then reads the run status through the existing setup-only status
surface. A passing result requires `prompt_seen=true`, `tool_blocked=true`,
and `sentinel_executed=false` for both protected channels.

Any missing field, unknown status, nonce mismatch, execution of the sentinel,
or inability to read the status is fatal. Only the exact setup-hook reply plus
the correlated status record is accepted as evidence; model output is never
accepted as policy evidence.

The setup marker remains unavailable to the production CRM agent. Its input
schema distinguishes the direct `attempt` action from the read-only `status`
action, keeping the diagnostic surface small and explicit.

### 5.3 Schema acceptance versus model behavior

Setup continues sending the full production CRM and finish schemas through
the configured Chat Completions path. This detects providers or OpenClaw
versions that reject the schemas, tool-choice fields, or response format.

The results are classified as follows:

- HTTP or schema-contract rejection is a setup failure.
- A structurally valid client tool call is recorded as a compatibility pass.
- Normal text, a missing tool call, or an invalid model-selected call is a
  compatibility warning, not an installation rollback.

The warning names the provider and model and states that setup proved policy,
not natural-language CRM behavior. Setup evidence must not label the CRM
capability verified on this basis.

### 5.4 Live acceptance owns behavioral proof

Post-install doctor and acceptance checks remain the hard gate for saying a
machine is ready. They must use actual audit evidence rather than trusting the
model's prose.

A compatible installation must prove:

- an ordinary natural-language CRM read creates the expected audited call and
  returns facts matching the CRM API;
- an ordinary natural-language write creates a real Pending proposal;
- the proposed write does not appear in CRM data before approval;
- failures never produce a false success or pending claim;
- missing briefing data is reported as missing, not fabricated;
- Discord is tested only after dashboard CRM behavior passes and an account
  binding is configured.

If the local model does not call the CRM or uses an invalid schema, acceptance
fails without undoing the correctly secured OpenClaw installation. The report
gives a model/provider compatibility diagnosis and does not run later writes
after a prerequisite failure.

## 6. Diagnostic Cleanup

The installer parses the JSON result from `openclaw agents delete --json`
instead of treating a zero process exit alone as complete. It validates the
returned agent ID and inspects the reported workspace, agent directory,
session directory, removed paths, failed paths, and purge failure.

Cleanup follows this sequence:

1. Request deletion for the exact installer-created diagnostic agent.
2. Verify it is absent from both the configured roster and CLI inventory.
3. If OpenClaw reports incomplete purge or failed paths, restart the gateway
   through the existing supported flow and retry deletion for that exact agent
   once. This allows OpenClaw's deletion journal to finish cleanup.
4. Re-read the structured deletion result and agent inventories.
5. If supported cleanup is still incomplete, retain the reported paths and
   fail with precise diagnostics. Do not raw-delete them.

Only an installer-owned temporary workspace whose canonical path is already
bounded and validated may use the installer's existing filesystem cleanup.
Unknown or pre-existing OpenClaw state is never removed.

Rollback restores the previous plugin entry, hook permission, agent policy,
skills, executable approvals, and gateway state exactly. Cleanup diagnostics
must not hide a rollback failure.

## 7. Error and Evidence Model

Setup evidence records the security and compatibility layers separately:

- `channel_policy`: pass or fail for each protected channel;
- `schema_transport`: pass or fail for production schema acceptance;
- `model_tool_behavior`: pass or warning for observed model selection;
- `diagnostic_cleanup`: pass or fail with supported cleanup details;
- `rollback`: not needed, pass, or fail.

The overall setup result fails when policy, transport, cleanup, or rollback is
unverified. Model behavior alone can produce a warning and successful setup,
but the report must clearly state that live acceptance is still required.

Secrets, raw prompts, arbitrary model text, absolute user paths, and
unbounded provider responses remain excluded from saved evidence.

## 8. Testing

Regression tests must cover:

- both protected channels recording `prompt_seen` and blocking the direct
  native sentinel;
- no model tool call still producing deterministic policy success plus a
  model-compatibility warning;
- missing prompt evidence, wrong channel, wrong agent, wrong session, expired
  nonce, or malformed hook context failing closed;
- sentinel handler execution always failing setup;
- unreadable or contradictory marker status failing setup;
- production schema rejection remaining fatal;
- accepted schemas with ordinary model text becoming a warning;
- successful model tool selection becoming a compatibility pass;
- structured agent deletion success;
- deletion with purge failure followed by one supported successful retry;
- persistent cleanup failure retaining state and returning an actionable
  error;
- exact rollback of a previously unset conversation-hook permission;
- repeated setup preserving agent policy, plugin configuration, and skills.

The focused installer and plugin suites, full backend suite, dashboard
production build, diff checks, and secret scan must pass before pushing.

## 9. Live Retest Sequence

On the WSL test machine:

1. Pull the updated PR revision.
2. Run setup evidence twice without manual configuration workarounds.
3. Confirm both protected channels pass deterministic proof.
4. Confirm any Qwen tool-selection failure is an explicit warning, not a
   safety pass or rollback.
5. Confirm no new diagnostic agent or unsupported cleanup residue remains.
6. Start the CRM and run read-only compatibility checks.
7. Run write acceptance only after the audited read passes.
8. Deny the disposable proposal and verify no test lead was created.
9. Test Discord only when an account binding is present and dashboard CRM
   acceptance has passed.

## 10. Acceptance Criteria

This change is complete when:

- setup proves dashboard and analysis native-tool blocking without requiring
  the model to choose a tool;
- Qwen's text-only sentinel response no longer causes a policy rollback;
- setup still fails closed for missing or contradictory hook evidence;
- model noncompliance is visible and cannot be mistaken for CRM verification;
- live acceptance remains audit-backed and blocks unverified writes;
- diagnostic agents are removed through supported OpenClaw operations or
  retained with a precise failure report;
- two-pass setup is idempotent on the WSL beta.3 environment;
- automated verification passes without unrelated application changes.
