# Native first-run setup and independent acceptance

Status: written specification approved by the user in this conversation.
Approved direction: WSL/Linux first, two isolated gateway profiles, one persistent CRM,
and a fresh-install acceptance run on the other computer. No implementation or
live acceptance is implied by this document.

Base: `71558d5a91383101eb2cd5d34dbac58802d46ec4` (merged PR #8).

## Outcome

A new operator can follow one guide, install the documented prerequisites, set up
OpenHouse, and use local OpenClaw/Ollama to read the CRM and propose a lead for
human approval. Both workflows operate on the same initially empty database.
Restarting preserves records, credentials, proposals, and request recovery.
The setup must not depend on a developer's existing OpenClaw configuration,
virtual environment, copied secrets, synthetic fixture, or undisclosed commands.

The first supported target is Ubuntu 24.04 on WSL2, with Python 3.12 and a
compatible GPU configuration verified during acceptance. Linux portability can
be tested separately; this milestone does not certify every Linux distribution,
macOS, or native Windows. Hardware requirements will be reported from measured
acceptance, not inferred from the previous run or model parameter count.

## Boundaries and existing evidence

Keep the current native receipt protocol, capability authentication, approval
transactions, request IDs, no-retry behavior, and native plugins. The supported
AI scope is unfiltered counts/first 25 directory entries, plus proposing a name
and optional email/phone. Human editing, approval, denial, status, and explicit
close retain their existing semantics.

No new CRM actions, industry customization, remote providers, integrations,
Discord binding, automatic startup services, multi-user accounts, or public
network access belong in this milestone. Existing users retain their legacy
setup; the new setup does not migrate or overwrite it.

PR #8 established 10 successful live reads and three proposal sessions on one
pinned WSL configuration. Its fixture used separate databases and preinstalled
runtimes. It did not establish a fresh installation or live uncertain/late-result
recovery. Keep those captures unchanged and collect new evidence separately.

## Chosen arrangement

One backend serves the built dashboard and owns one SQLite database. Two
foreground OpenClaw gateways each have a private HOME, profile, workspace,
gateway secret, and exactly one agent/tool:

- Read gateway: `native-read` and `openhouse_crm`.
- Proposal gateway: `native-proposals` and `openhouse_propose_lead`.

Both plugins target the same loopback backend. Both gateways use the same
restricted CRM agent credential; its API scope permits reads and proposal
submission. The read plugin/tool policy narrows the read gateway's behavior;
this is not a claim of two distinct backend capability roles or OS isolation.
The human key stays outside gateway HOME/config/environment. Filesystem
permissions protect against other OS users, not malicious same-user processes.

Both gateways use local Ollama without provider fallback. The launcher does not
own or stop an existing Ollama service. Installing and starting the documented
Ollama prerequisite is explicit in the guide. The new OpenHouse profile never
loads ambient repository dotenv files or existing OpenClaw configuration.

Two gateways preserve the arrangement exercised in acceptance. Sharing one
gateway with two agents could reduce process overhead later, but would add a new
policy/configuration combination to this installation milestone.

## Operator commands

Provide one entry point, provisionally `python3 scripts/native_local.py`, with
`setup`, `start`, and `doctor` subcommands. The guide uses the same entry point
throughout and distinguishes prerequisite installation from OpenHouse setup.

### setup

1. Check OS, Python/Node/OpenClaw versions, local Ollama version/model digest,
   required executables, and selected ports. No inference is needed for preflight.
2. Install OpenHouse dependencies into a dedicated environment using a committed
   target-specific Python lock and the existing npm lock. Use `npm ci`. Build a
   fresh dashboard; a failed build is fatal, never a reason to serve a stale one.
3. Prepare private application state under a named user-owned directory outside
   the checkout (default `~/.local/share/openhouse/native`). Generate independent
   human, agent, and two gateway secrets with private file permissions.
4. Create the two profile configurations and an empty application database using
   normal schema initialization. Do not run demo seed scripts or copy an old DB.
5. Validate both generated gateway configs through the selected OpenClaw binary
   under the sanitized environment. Record the exact runtime paths/versions,
   source revision, model digest, and dependency-lock identities in a nonsecret
   manifest. Store no secret values in that manifest.
6. Print the next start command and the private human-key file location, not the
   key. The operator opens that file locally and enters it into runtime unlock.

Use a private staging directory and publish the state only after successful
preparation. Mark incomplete preparation explicitly. Repeating setup against a
completed matching installation verifies it and gives start instructions without
changing keys, records, or configuration. A mismatch or partial installation
requires a clear repair instruction; no automatic reset/delete or force flag.
Reject unsafe/symlinked state targets and refuse to adopt unrelated directories.
Concurrent setup/start on the same state must be prevented by an OS-held lock,
not a stale PID file or a check-then-create race.

The checkout remains the application source. Setup records its resolved path;
start refuses a missing/moved source or an unprepared source/lock revision and
explains how to prepare the matching revision. Arbitrary in-place upgrades and
DB rollback are outside this first-install milestone.

### start

Verify the manifest, build identity, credentials, local runtime/model, and three
configured ports before starting children. Start one backend and both gateways
with explicit environment allowlists. Never inherit cloud provider credentials,
ambient human credentials, proxy variables, or arbitrary OpenClaw overrides.

Bind all listeners to loopback. Prebind the backend socket and reserve gateway
ports before launch; never terminate an existing listener. Readiness must include
an authenticated check against each owned gateway, not only a successful TCP
connection. Report readiness as readiness, not proof of model/tool success.

Run in the foreground until Ctrl-C/SIGTERM. Unlike the acceptance helper, this is
not limited to an 1800-second session. If a required child fails, stop remaining
owned children with bounded termination and return failure. Cleanup targets only
children created by this invocation. Data and credentials remain on disk.
Prevent concurrent starts and bound startup/shutdown. Do not install a daemon.

Restart uses the same DB and secrets. Existing backend recovery changes unbound
running requests to unknown; startup never resubmits them. Disconnection must
not trigger a client-generated replacement request.

### doctor

By default, perform read-only configuration/runtime/authentication/connectivity
checks without model inference, DB migrations, reseeding, repair, or key rotation.
Output separates installed, configured, reachable, and live-verified states.
Return a nonzero status and an actionable diagnostic for missing dependencies,
version/digest mismatch, invalid config, occupied ports, stopped services, and
missing builds. A stopped installation is reported as stopped, not corrupted.
Never print credentials, request text, lead data, or raw gateway logs.

An explicit `--live-read` runs exactly one existing verified native read and
reports only safe outcome/count/timing metadata. It makes no proposals or writes
to CRM records. A successful read does not certify proposal inference. Readiness
and live evidence are timestamped and tied to runtime/source identity; historical
success is not represented as current service health.

## Supported runtime and reproducibility

Use the recorded OpenClaw `2026.8.1-beta.3 (5831b80)`, Ollama `0.32.15`, and
`qwen3.5:9b` Q4_K_M digest
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`
as the initial candidate matrix. Preserve context 16384, maxTokens 2048,
thinking off, localModelLean false, and no model fallbacks. The prior Node runtime
was 24.15.0; its installability and compatibility must be checked explicitly.

Before publishing runnable installation commands, verify exact runtime artifacts
are obtainable from upstream and that the selected Node/Python versions are
supported by those artifacts. Use pinned OpenClaw installation in a private
prefix, not a change to the operator's global CLI. If a candidate cannot be
obtained or has an incompatible prerequisite, report the blocker and evaluate a
replacement explicitly; never silently substitute latest or claim the older
acceptance transfers to it.

The guide must include prerequisites, GPU checks, model acquisition and digest
verification, fresh checkout, locked dependency installation, setup, startup,
unlock, first read/proposal, shutdown/restart, and recovery. Installation may
need internet; operation after dependencies/model are present must work without
cloud credentials or outbound provider access. Starting does not fetch updates.

## Honest first-run interface

The existing General chat defaults to canned mock replies and the global badge
reports legacy-agent health. Those must not make a new native installation look
like a demo or imply that native readiness equals a verified model result.

Introduce an explicit native-only installation setting, defaulting off for
existing installations. Publish only a nonsecret mode indicator through the
existing runtime/authentication bootstrap. In this mode, show the two supported
native chat modes and an accurate native-setup label. Reject legacy General chat
submission before dispatch/persistence, even if called directly. Do not silently
route it to either native agent or return canned CRM facts.

Legacy/demo behavior outside this setting remains unchanged. Optional AI and
integration features not configured by native setup are visibly unavailable or
retain explicit existing fallback labels; the new guide does not advertise them
as supported. No broad dashboard redesign or expansion of native tool scope.

## Validation before handoff

Automated checks cover configuration generation and exact per-profile tool
allowlists, shared DB targeting, private permissions, credential/environment
separation, repeat setup/data preservation, partial setup and lock contention,
port conflicts without killing their owner, child exit/cleanup, stale build or
runtime detection, and redacted diagnostics. Use disposable state and synthetic
records; never test against the operator's actual installation.

Use protocol-faithful local stubs for process orchestration failure cases and
label them as simulated. Exercise the native-only UI/backend boundary and retain
all existing auth/read/proposal browser suites, plugin suites, and backend tests.
Run fresh locked dependencies/build on the supported Linux target; a reused Mac
environment is not evidence of a clean Linux installation.

## Independent acceptance on the other computer

Deliver a commit-addressed runbook and evidence template. The operator uses a
fresh WSL distro or disposable Linux environment with a fresh OS HOME, checkout,
Python environment, Node dependencies, OpenClaw install/config, application state,
and Ollama/model installation. Do not reuse the earlier developer runtime or
fixtures and call it fresh. GPU host drivers may be shared but are recorded.
The existing WSL distro, original gateway, CRM files, and evidence stay intact.

Record exact commands, source/runtime versions, hardware, dependency provenance,
model digest, durations, and failures. Capture no credentials/private records.
Acceptance must establish:

1. The written guide alone reaches a running, initially empty CRM.
2. A verified native read reports zero leads. A real proposal produces zero leads
   before approval; editing/approval creates exactly one lead with edited values.
3. Read mode in the same installation sees that lead. Unsupported scopes/writes
   are rejected. Denial and duplicate approval do not create another lead.
4. Restart preserves records, secrets and pending/terminal request status without
   new inference. Repeating setup does not modify existing data or credentials.
5. Explicit fault injection in this disposable installation interrupts a real
   dispatched proposal before submission. The request becomes uncertain, survives
   restart, can be closed, and cannot be revived. Preserve all failed attempts.
   Hold/release the tool HTTP submission through an acceptance-only loopback relay
   if needed to prove a real late tool call is rejected after closure. The relay
   is declared test infrastructure, is never enabled in normal startup, and only
   forwards to the fixture CRM. Do not substitute a fabricated model response.
6. Missing model/config, occupied ports and a failed child produce actionable
   failures without touching another service or duplicating inference.
7. After installation, reads and proposals work with external network access
   disabled while local loopback remains available. Record how this was enforced.
8. All owned test services stop, final DB counts match recorded decisions, and
   original-machine preservation checks pass.

If an interruption occurs after a proposal was already stored, classify it as
that outcome; it does not establish the unknown/late-call case. Do not repeatedly
submit user requests until a desired outcome appears or omit failures. Every
additional run gets a new recorded case, not a hidden retry.

## Completion and deferred work

Implementation is ready for independent acceptance only after automated tests,
locked build, and review pass. It is a verified first-run release candidate only
after the independent clean installation and fault cases pass. A handoff or local
simulation alone does not complete that claim.

Publish sanitized evidence with limits and installation corrections. The next
PR may be prepared for review; merging, releasing, and changing the other
computer are separate lifecycle actions. No external message is sent by this
specification. Migration of existing real DBs, automatic upgrades/services,
additional platforms/models and broader CRM tools remain future work.

## Upstream references

These explain supported installation mechanisms; the implementation must verify
the chosen release rather than apply current-default commands blindly.

- OpenClaw installation: https://docs.openclaw.ai/install
- OpenClaw foreground gateway: https://docs.openclaw.ai/cli/gateway
- Ollama Linux/manual/versioned installation: https://docs.ollama.com/linux
