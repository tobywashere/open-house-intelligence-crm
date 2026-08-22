# Cross-Platform Open-Source Readiness Design

**Date:** 2026-08-20

## Purpose

Make the existing local-first CRM setup reliable on appropriately sized personal
hardware without redesigning the application. The primary target remains a
16 GB or larger Apple-silicon Mac mini. Native Linux and Windows through WSL2
are supported alternatives when the machine can run the selected local model.

This change starts from a real Windows/WSL2 test of PR #7. That test proved the
GPU, local model, OpenClaw gateway, application fallback, setup rollback, backend
suite, and dashboard build. It also exposed a setup validation error that the
mock OpenClaw CLI did not reproduce.

## Supported Platform Boundary

The repository will describe these support levels precisely:

| Platform | Support level | Notes |
|---|---|---|
| Apple-silicon macOS | Primary native target | 16 GB unified memory is the minimum supported baseline. |
| Linux on x86_64 or ARM64 | Supported native target | Hardware acceleration depends on the model runtime and vendor drivers. |
| Windows 11 with WSL2 | Supported target | Run the repository, Python, Node.js, OpenClaw, and Ollama inside WSL2. GPU access depends on the Windows, WSL, and model-runtime installation. |
| Native Windows PowerShell | Not supported in this change | The shipped launchers are Bash scripts and have not been validated as native Windows processes. |

Sixteen gigabytes is a product baseline, not a promise that every model will
fit or perform well. Documentation will tell users to select a quantized model
that fits their memory and to verify it in OpenClaw before configuring the CRM.
No particular GPU, model provider, or computer brand is required by application
code.

## Goals

- Fix the confirmed real-OpenClaw false rejection without weakening execution
  restrictions.
- Test against representative JSON contracts from both OpenClaw
  `v2026.7.1-2` and `v2026.8.1-beta.2`.
- Preserve safe reruns, partial-install repair, rollback, SecretRef handling,
  agent isolation, and review-before-apply CRM writes.
- Explain Mac, Linux, and Windows/WSL2 setup in language a nontechnical tester
  can follow.
- Produce one sanitized compatibility report that a tester can send back
  instead of discovering setup problems through repeated messages.
- Keep dashboard chat as the primary supported interface and Discord as an
  optional second acceptance test.

## Non-goals

- Do not redesign dashboard chat, the OpenClaw transport, or CRM skills.
- Do not add native PowerShell launchers.
- Do not containerize the model runtime, OpenClaw gateway, or entire product.
- Do not install GPU drivers, WSL2, OpenClaw, Ollama, or a model automatically.
- Do not pin users to a prerelease OpenClaw version.
- Do not claim that automated tests prove a particular model or hardware works.
- Do not broaden the dedicated agent's tool or executable allowlists.
- Do not expose the OpenClaw gateway or CRM API beyond loopback by default.

## Confirmed Failure and Correct Contract

PR #7 writes and reads back the dedicated agent policy as:

```json
{
  "allow": ["exec"],
  "deny": ["web_fetch", "web_search", "browser", "read", "write", "edit", "apply_patch", "canvas", "nodes", "cron"],
  "exec": {"mode": "allowlist", "host": "gateway"}
}
```

It then calls `openclaw sandbox explain --agent openhouse-crm --json` and
recursively searches that payload for `host: gateway` and an exec-policy
`mode: allowlist`. Official source for both the tested stable and beta releases
shows that `sandbox explain` reports sandbox state, sandbox tool policy, and
elevated policy. It does not report `tools.exec.host` or the exec approval mode.

The existing fake CLI returned an invented shape containing:

```json
{"mode": "off", "exec": {"host": "gateway", "mode": "allowlist"}}
```

That allowed tests to pass while the real CLI was correctly rejected for not
returning fields outside the command's contract.

## Validation Design

Each OpenClaw surface will validate only the state it owns:

1. `openclaw config get <agent-prefix>.tools --json` must exactly prove that
   `exec` is the only allowed tool, the general tools are denied, and exec uses
   `host: gateway` with `mode: allowlist`.
2. `openclaw config validate --json` must accept the complete configuration.
3. `openclaw skills check --agent <id> --json` must report
   `crm-db-operations` as eligible.
4. `openclaw sandbox explain --agent <id> --json` must identify the requested
   agent, report `sandbox.mode` as `off`, and report
   `sandbox.sessionIsSandboxed` as `false` when that field is present. It must
   not be used to infer exec-host or approval policy.
5. `openclaw approvals get --gateway --json` must continue to prove the
   dedicated agent's effective host request, allowlist mode, allowlist security,
   noninteractive ask mode, safe fallback, and exact executable patterns.

Malformed, missing, contradictory, or wrong-agent results remain fatal. The
fix removes a check from the wrong evidence source; it does not remove the same
security guarantee from the overall setup.

## Test Contract

The fake CLI will return a realistic nested `sandbox explain` payload without
exec-host fields. Two named fixtures will document the observed stable and beta
shapes. Tests will prove:

- both release-shaped payloads pass when the authoritative tools and gateway
  approval surfaces are safe;
- a wrong `agentId` fails;
- missing or non-object `sandbox` fails;
- sandbox mode other than `off` fails;
- `sessionIsSandboxed: true` fails;
- omitting `sessionIsSandboxed` remains compatible with an older CLI when
  `mode: off` is explicit;
- unsafe authoritative exec host or mode still fails before success;
- unsafe effective gateway policy still fails before success;
- fresh creation, a second idempotent run, partial repair, and rollback retain
  their existing coverage.

Tests will use field-addressed validation rather than recursive key searches so
an unrelated `mode` or `host` value cannot satisfy a security check.

## Shareable Compatibility Report

`scripts/doctor.py` will gain a shareable report mode instead of adding a
second diagnostic framework. The report remains read-only and will include:

- product commit identifier when Git is available;
- operating system, WSL detection, and CPU architecture;
- total memory when the operating system exposes it safely;
- Python, Node.js, npm, OpenClaw, and optional Ollama versions;
- whether the application API is reachable;
- the existing endpoint, live chat, and audited CRM capability statuses;
- clear warnings when the machine is below the 16 GB baseline or a component
  is unavailable.

The shareable form will not include usernames, home-directory paths, environment
values, tokens, full OpenClaw configuration, CRM rows, chat content, or model
responses. A JSON option will make the report easy to attach without losing
the normal beginner-readable terminal output.

The live report command assumes the application is already running. Setup
failure output and the report together provide one complete test bundle.

## Documentation Design

The README will lead with the product and the two modes, then give a compact
support table. It will distinguish application requirements from model-runtime
requirements and link to one platform guide per supported route.

`docs/MAC-MINI-SETUP.md` remains the primary beginner guide. It will state that
16 GB supports the CRM plus a modest quantized model, not every local model.

A new `docs/WINDOWS-WSL-SETUP.md` will explain:

1. Install and enter WSL2 using current vendor instructions.
2. Keep the repository and local runtime inside the Linux filesystem.
3. Verify Git, Python, Node.js, OpenClaw, Ollama, and the model before CRM setup.
4. Run the same repository setup commands used on Linux.
5. Keep OpenClaw and the CRM bound to loopback.
6. Run setup, start the server, generate the compatibility report, and send the
   report plus setup output to maintainers.
7. Treat WSL user-service startup, Ollama restart, and shell `PATH` persistence
   as host operations rather than application failures.

`docs/LOCAL-AI.md` will become the shared technical reference and acceptance
record. It will not tell users to loosen global OpenClaw execution policy when
setup reports a compatibility problem.

## Security and Trust Boundary

The gateway and CRM continue binding to loopback by default. Network exposure
requires explicit operator configuration and CRM API authentication.

The dedicated agent continues using sandbox mode `off` because the two shipped
Python entrypoints need host access to the local CRM. This is acceptable only
with the existing exec-only tool restriction and exact executable allowlist.
The compatibility fix must preserve authoritative and effective checks for both.

OpenClaw may warn that a small local model is unsuitable for unsandboxed,
untrusted content. Dashboard use on a trusted local machine is the first
acceptance target. Discord remains optional and must not be described as ready
until its read and review-before-write flow has been tested on the same
restricted dedicated agent.

## Implementation Order

1. Add failing contract tests using real stable and beta sandbox-explain shapes.
2. Replace recursive sandbox-policy inference with field-addressed sandbox
   validation.
3. Run the focused setup suite and preserve all setup safety tests.
4. Add failing tests for sanitized cross-platform report data.
5. Implement the shareable report in `scripts/doctor.py` using only the Python
   standard library.
6. Update the README and local-AI documentation and add the WSL2 guide.
7. Run the complete backend suite and dashboard production build.
8. Review the diff for weakened policy checks, secret exposure, platform
   overclaims, and unrelated changes.
9. Provide one ordered Windows/WSL2 test script covering setup, rerun, live
   dashboard CRM read, reviewed write, voice intake, and optional Discord.

## Acceptance Criteria

Local automated acceptance requires:

- focused OpenClaw setup tests pass;
- doctor/report tests pass on simulated macOS, Linux, and WSL2 inputs;
- all backend tests pass;
- dashboard production build passes;
- no application architecture or dependency changes are introduced;
- no secret or user-specific path appears in the shareable report fixtures.

Real-machine acceptance requires recording, without manually changing OpenClaw
policy between steps:

1. OS, architecture, memory, OpenClaw version, model, and report timestamp.
2. First setup completes and creates the dedicated agent.
3. A second setup completes without changing effective policy.
4. `doctor.py --live-agent --live-crm` reports `crm_verified`.
5. Dashboard chat lists real CRM data.
6. Dashboard chat proposes a disposable write and does not apply it before
   approval.
7. Voice intake reaches editable review without creating a lead first.
8. Daily briefing shows only stored CRM facts and source-backed market items.
9. Optional Discord reads through the same agent and places a disposable write
   in dashboard Pending approvals.

The Windows/WSL2 result will establish that platform as live-tested. The Mac
mini remains documented as the primary target until its acceptance record is
also completed on physical hardware.

## References

- [OpenClaw stable sandbox explain source](https://github.com/openclaw/openclaw/blob/v2026.7.1-2/src/commands/sandbox-explain.ts)
- [OpenClaw beta sandbox explain source](https://github.com/openclaw/openclaw/blob/v2026.8.1-beta.2/src/commands/sandbox-explain.ts)
- [OpenClaw beta agent tool schema](https://github.com/openclaw/openclaw/blob/v2026.8.1-beta.2/src/config/zod-schema.agent-runtime.ts)
- [Microsoft WSL installation](https://learn.microsoft.com/windows/wsl/install)
