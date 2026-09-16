# Native OpenClaw CRM Tool Design

Date: 2026-08-21

Status: Approved direction, recorded after supported-hardware verification of
PR #7 at commit `1d12c18`

## 1. Purpose

Make natural-language CRM reads and approval-gated writes reliable in dashboard
chat and Discord without rewriting the dashboard, replacing the backend, or
granting OpenClaw general shell access.

PR #7 successfully made OpenClaw setup repeatable and security-verifiable. A
live Windows WSL2 test with OpenClaw `2026.8.1-beta.2`, Ollama `0.32.15`, and
`qwen3.5:9b` proved that the remaining Markdown-skill-to-`exec` hop is not a
reliable model-facing integration.

## 2. Confirmed Failure

The supported setup now creates and validates the dedicated `openhouse-crm`
agent, installs the four shipped skills, and applies the intended policy. The
CRM backend and fixed Python wrapper also work when invoked directly.

The live agent still fails in two ways:

1. The model treats the `crm-db-operations` skill name as a dynamic tool ID and
   calls it through `tool_call`. OpenClaw rejects it because skills are prompt
   instructions, not registered tools.
2. When explicitly told to use `exec`, the model constructs a command that
   OpenClaw cannot reduce to the installed execution plan. Strict allowlist mode
   correctly rejects it with `execution-plan-miss`.

This proves the failure is between model selection and command construction.
It is not caused by the CRM API, audit layer, approval queue, dedicated-agent
routing, executable allowlist installation, or gateway availability.

The prior design explicitly deferred a native OpenClaw plugin unless the
dedicated skill approach failed on compatible hardware. That condition is now
met.

## 3. Chosen Approach

Ship a small, source-controlled OpenClaw tool plugin that registers one real
model-visible tool named `openhouse_crm`.

The tool accepts:

```json
{
  "operation": "list_leads",
  "arguments": {"sort": "priority"}
}
```

The operation is limited to the existing fixed catalog in
`skills/crm-db-operations/cli.py`. The plugin runs that fixed wrapper with an
argument vector and `shell: false`; neither the model nor user content can
choose an executable, interpreter, shell operator, or environment variable.
The wrapper remains the single CRM HTTP client and continues to mark agent
requests for auditing.

The plugin returns the wrapper's structured JSON result to OpenClaw. Errors are
bounded and sanitized before they return to the model. Tokens, local environment
values, stack traces, and unrestricted command output are never returned.

## 4. Why This Approach

### 4.1 Prompt-only changes are rejected

More forceful Markdown may improve one probe but still leaves correctness up to
small-model interpretation and shell command generation. It does not satisfy
the reliability requirement.

### 4.2 A dashboard-owned function loop is deferred

OpenClaw's Chat Completions endpoint supports caller-provided function tools,
but a backend-owned loop would solve dashboard chat only. Discord would retain
the failing skill path or require a second implementation.

### 4.3 A native tool is shared by dashboard and Discord

Both channels already route to the same dedicated OpenClaw agent. Registering
one actual tool fixes the shared agent boundary and keeps channel behavior
consistent. It is a focused OpenClaw integration, not an application rewrite.

## 5. Plugin Package

Create `openclaw-plugins/openhouse-crm/` containing:

- `openclaw.plugin.json`: declares plugin identity, compatibility, configuration,
  and ownership of `openhouse_crm`;
- `package.json`: local ESM package metadata with no install-time network
  dependency;
- `index.js`: registers the tool and invokes the fixed wrapper;
- `test/`: Node tests for operation validation, shell-free invocation, bounded
  errors, and result parsing.

The repository ships runnable JavaScript rather than requiring TypeScript
compilation on the operator's machine. OpenClaw and Node remain required, but
setup must not download a plugin build toolchain.

The OpenClaw tool factory receives the active agent workspace and derives the
wrapper path as `skills/crm-db-operations/cli.py` inside that trusted workspace.
The plugin has no operator configuration and no runtime package dependencies.
The backend URL and optional API token remain in the existing OpenClaw skill
environment and are inherited by the fixed child process. The tool input cannot
override the workspace, executable path, or environment.

## 6. Agent Policy

The dedicated agent's model-visible allowlist becomes:

```json
{
  "allow": ["openhouse_crm", "exec"],
  "exec": {"mode": "allowlist", "host": "gateway"}
}
```

`exec` remains only because the already verified deterministic daily-brief
runner still uses it. The CRM wrapper is removed from the executable approval
allowlist. The only remaining executable entry is the daily-brief runner.

General web, browser, filesystem, messaging, and unrelated runtime tools remain
denied. The plugin tool cannot execute an arbitrary command and exposes no raw
HTTP URL, headers, file path, or environment input to the model.

The skill allowlist remains the four shipped skills. The
`crm-db-operations/SKILL.md` file becomes usage guidance for the real tool and
must explicitly state:

- `crm-db-operations` is a skill name, never a callable tool ID;
- call `openhouse_crm` with `operation` and `arguments`;
- never use `exec` for CRM operations;
- writes remain proposals awaiting dashboard approval.

## 7. Setup and Upgrade Behavior

`scripts/setup_openclaw.py` remains idempotent and gains a plugin phase:

1. Verify the installed OpenClaw exposes the required plugin install, list, and
   inspection surfaces before mutation.
2. Link or refresh only the repository-owned `openhouse-crm` plugin through the
   supported local-plugin CLI, with no dependency download.
3. Verify that the runtime tool resolves its wrapper only from the dedicated
   agent workspace and existing CRM skill environment.
4. Add `openhouse_crm` to the dedicated agent's exact allowed-tool set.
5. Remove the CRM wrapper from that agent's gateway executable allowlist while
   preserving the daily-brief entry.
6. Validate the plugin manifest, plugin activation, registered tool discovery,
   authoritative agent tool policy, and final gateway approval document.
7. Restart the gateway only after all staged configuration validates.

An existing plugin with the same ID but an unrelated installation path or
manifest is a conflict. Setup must stop with an explicit recovery message rather
than overwrite it.

Dry-run output describes plugin installation and policy changes without writing
files or configuration. Rollback restores the prior CRM-owned plugin and agent
configuration if a later setup step fails.

## 8. Runtime and Health Contract

Dashboard chat continues to call `/v1/chat/completions` with
`model=openclaw/openhouse-crm`. No dashboard API or chat persistence contract
changes.

The CRM capability probe requests the exact `openhouse_crm` tool and the
read-only `generate_dashboard_insights` operation with a unique nonce. Health
reports `crm_verified` only when the backend observes the matching new audit
row. Plausible assistant text, a successful generic completion, and plugin
discovery alone remain insufficient.

For normal CRM chat:

1. The model selects `openhouse_crm`.
2. The plugin validates the operation and arguments.
3. The plugin runs the fixed wrapper without a shell.
4. The wrapper calls the existing CRM API with agent attribution.
5. Reads return real CRM data.
6. Writes return the existing pending proposal object.
7. The model tells the user the change is awaiting review.

## 9. Security and Data Integrity

- The operation name must match the fixed catalog.
- Arguments must be a JSON object with bounded serialized size.
- Child execution must use an argument array with `shell: false`.
- The executable path comes only from validated plugin configuration.
- The model cannot set the API URL, token, process environment, working
  directory, timeout, or executable.
- Execution has a short fixed timeout and bounded stdout/stderr capture.
- Redirect protection and API-token handling remain in the existing Python
  client.
- The backend remains authoritative for audit records and human approval.
- No write can bypass pending approvals through the plugin.
- No error includes secrets, local absolute paths, or stack traces.

## 10. Compatibility

The supported targets remain:

- Apple-silicon Mac mini with 16 GB or more;
- Linux x86_64 or ARM64 with 16 GB or more;
- Windows 11 through WSL2 with 16 GB or more available on the host.

Native Windows PowerShell remains unsupported. The plugin uses Node's
cross-platform process API and the existing executable Python wrapper, so macOS,
Linux, and WSL2 use the same code path.

Setup remains capability-based. It must validate the installed OpenClaw plugin
surfaces rather than infer compatibility only from a version string.

## 11. Verification

Automated acceptance requires:

- plugin tests for one read, one approval-gated write result, unknown operation,
  malformed arguments, timeout, oversized output, invalid JSON, and child
  failure;
- setup tests for new install, rerun, upgrade, conflict, dry run, rollback,
  secret redaction, exact model-visible tools, and exact remaining executable
  allowlist;
- backend tests proving the health probe requests the registered tool and still
  requires a matching audit nonce;
- the complete backend suite;
- the dashboard production build.

Live supported-hardware acceptance uses one evidence bundle and must prove:

1. setup succeeds twice without manual OpenClaw edits;
2. dashboard chat lists real CRM leads;
3. a disposable write appears only in Pending approvals and can be denied;
4. the capability report records an audited CRM read;
5. the daily briefing remains truthful when source data is absent;
6. Discord can perform the same safe CRM read through `openhouse-crm`;
7. no CRM operation produces `Unknown tool id` or `execution-plan-miss`.

## 12. Separate Follow-ups

The WSL usable-memory warning and local audio transcription provider are
independent of CRM tool routing. They will be handled after this tool path is
implemented and verified, so they do not obscure the dashboard-chat acceptance
result.
