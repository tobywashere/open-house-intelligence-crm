# Deterministic OpenClaw Setup Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenClaw setup prove dashboard and analysis tool blocking deterministically, report model tool-selection failures as compatibility warnings, and clean up diagnostic agents through OpenClaw's supported retry path.

**Architecture:** The plugin returns a correlated receipt when an isolated diagnostic prompt reaches each protected channel, then returns a second correlated receipt when it blocks a direct native sentinel carrying the same agent, session, channel, and nonce. The installer treats those direct hook responses and schema transport as security gates, records model tool behavior separately, and delegates real CRM capability to audit-backed live acceptance. Diagnostic deletion parses OpenClaw's structured result and retries the exact deletion once after a gateway restart when OpenClaw reports incomplete cleanup.

> Implementation update, September 2, 2026: live beta validation showed that
> Chat Completions and `/tools/invoke` can use separate plugin runtime instances.
> The completed implementation therefore uses independently correlated prompt
> and block receipts and no longer stores or reads setup status across requests.
> OpenClaw's direct tool route intentionally omits message-requester identity,
> so the temporary sentinel correlates its configured agent, session, nonce,
> and channel argument while the separate prompt receipt proves the
> host-derived channel.
> Sections 5.1 and 5.2 of the matching design document are authoritative.

**Tech Stack:** Python 3 standard library, pytest, OpenClaw CLI and loopback Gateway APIs, Node.js ES modules, `node:test`, JSON schema, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-30-deterministic-openclaw-setup-verification-design.md`

## Global Constraints

- Do not redesign dashboard chat, replace OpenClaw, change the CRM database, or weaken Pending approval review.
- The dedicated CRM agent keeps its existing restrictive tool policy.
- Setup must fail closed for missing or contradictory channel-hook evidence, rejected production schemas, incomplete supported cleanup, or failed rollback.
- Model text without a valid tool call is a compatibility warning, never proof that CRM works.
- Live doctor and acceptance remain required before the installation is described as CRM-compatible.
- Do not raw-delete unknown OpenClaw state directories.
- Preserve exact rollback and idempotence on macOS, Linux, and WSL.
- Do not add a runtime dependency.

---

### Task 1: Make the plugin's setup sentinel deterministic

**Files:**
- Modify: `openclaw-plugins/openhouse-crm/dist/definition.js:18-270`
- Modify: `openclaw-plugins/openhouse-crm/dist/definition.js:432-466`
- Test: `openclaw-plugins/openhouse-crm/test/plugin.test.js:362-625`

**Interfaces:**
- Consumes: plugin config `{ setupProbe: { agentId: string, nonce: string } }`; OpenClaw hook contexts containing `agentId`, `sessionKey`, `runId`, and protected `channel` or `requester.channel`.
- Produces: one registered native tool named `openhouse_setup_marker_probe` with actions `attempt` and `status`; status details `{ schema_version: 2, channel, nonce, prompt_seen, tool_blocked, sentinel_executed }`.

- [ ] **Step 1: Replace model-selected attempt tests with a direct native-attempt test**

Update the two channel tests in `plugin.test.js` so `before_prompt_build` records the exact session, then `before_tool_call` receives the registered native tool with `action: "attempt"` and the same protected requester channel:

```js
for (const channel of ["openhouse-dashboard", "openhouse-analysis"]) {
  test(`setup probe deterministically blocks a native ${channel} attempt`, async () => {
    const nonce = "0123456789abcdef0123456789abcdef";
    const agentId = "openhouse-setup-probe-a1b2c3d4";
    const sessionKey = `agent:${agentId}:dashboard:openhouse-setup-test`;
    const { hooks, registrations } = registerPlugin(undefined, {
      agentId: "portable-crm",
      setupProbe: { agentId, nonce },
    });

    hooks.get("before_prompt_build").handler({}, {
      agentId,
      runId: `prompt-${channel}`,
      sessionKey,
      channel,
    });
    const blocked = hooks.get("before_tool_call").handler(
      {
        toolName: "openhouse_setup_marker_probe",
        params: { action: "attempt", channel, nonce },
      },
      { agentId, sessionKey, requester: { channel } },
    );

    assert.equal(blocked.block, true);
    const [factory] = registrations.find(
      ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
    );
    const status = await factory({}).execute("status-call", {
      action: "status", channel, nonce,
    });
    assert.deepEqual(status.details, {
      schema_version: 2,
      channel,
      nonce,
      prompt_seen: true,
      tool_blocked: true,
      sentinel_executed: false,
    });
  });
}
```

- [ ] **Step 2: Add fail-closed correlation and execution tests**

Add table-driven cases for a wrong agent, wrong session, wrong channel, missing `requester`, and an expired or missing prompt record. Each must return `block: true` and report a status that cannot satisfy all three passing booleans. Add a handler-execution test that calls the registered tool directly and verifies `sentinel_executed: true`:

```js
test("setup sentinel execution is permanently visible as a failed proof", async () => {
  const { registrations } = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  });
  const [factory] = registrations.find(
    ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
  );
  const tool = factory({});
  await tool.execute("unexpected-execution", {
    action: "attempt",
    channel: "openhouse-dashboard",
    nonce,
  });
  const status = await tool.execute("status", {
    action: "status",
    channel: "openhouse-dashboard",
    nonce,
  });
  assert.equal(status.details.sentinel_executed, true);
});
```

Keep the existing bounded-state and `gateway_stop` tests, but make them assert that stale or cleared session correlations cannot become passing evidence.

- [ ] **Step 3: Run the focused plugin tests and verify the new tests fail**

Run:

```bash
cd openclaw-plugins/openhouse-crm
npm test
```

Expected: FAIL because the schema only accepts `status`, prompt observations are keyed by `runId`, and the handler does not record execution.

- [ ] **Step 4: Implement session-bound probe records**

In `definition.js`, remove `SETUP_MARKER_ATTEMPT_TOOL` and replace the separate status string and run-channel maps with one bounded map keyed by channel:

```js
const setupProbeState = new Map();
const SETUP_PROBE_TTL_MS = 2 * 60 * 1000;

const emptySetupProbeRecord = (channel) => ({
  agentId: setupProbe.agentId,
  channel,
  nonce: setupProbe.nonce,
  sessionKey: undefined,
  promptSeen: false,
  toolBlocked: false,
  sentinelExecuted: false,
  invalid: false,
  observedAt: undefined,
});
```

Extend `setupProbeParameters` to accept exact actions:

```js
action: { type: "string", enum: ["attempt", "status"] },
```

In `before_prompt_build`, require the configured diagnostic agent, protected channel, nonempty `sessionKey`, and nonempty `runId`; then store the exact session-bound prompt record with `observedAt=Date.now()`. Preserve the existing maximum of 64 records and clear all records on `gateway_stop`.

In `before_tool_call`:

- allow `status` only through `openhouse-setup-capability` or the existing bounded direct-status context;
- for `attempt`, require the configured diagnostic agent, exact session key, exact protected `requester.channel`, exact nonce, a matching prompt record, and an observation no older than `SETUP_PROBE_TTL_MS`;
- set `toolBlocked=true` only for an exact match;
- set `invalid=true` for any mismatch or malformed context;
- always return `block: true` for an attempt.

In the registered tool handler, set `sentinelExecuted=true` before returning an attempt result. The status action returns schema version 2 and all three booleans. Invalid evidence can never be overwritten by a later attempt.

- [ ] **Step 5: Run the plugin suite**

Run:

```bash
cd openclaw-plugins/openhouse-crm
npm test
```

Expected: all plugin tests pass, including wrong-session, wrong-channel, handler-executed, bounded-state, and gateway-stop cases.

- [ ] **Step 6: Commit the deterministic plugin proof**

```bash
git add openclaw-plugins/openhouse-crm/dist/definition.js openclaw-plugins/openhouse-crm/test/plugin.test.js
git commit -m "Make OpenClaw channel probe deterministic"
```

---

### Task 2: Separate installer safety from model compatibility

**Files:**
- Modify: `scripts/setup_openclaw.py:130-310`
- Modify: `scripts/setup_openclaw.py:430-625`
- Modify: `scripts/setup_openclaw.py:4670-4705`
- Modify: `scripts/setup_openclaw.py:5647-6030`
- Modify: `scripts/setup_openclaw.py:6620-6650`
- Modify: `scripts/setup_openclaw.py:7120-7290`
- Test: `backend/tests/test_setup_openclaw.py:230-430`
- Test: `backend/tests/test_setup_openclaw.py:2540-2610`
- Test: `backend/tests/test_setup_openclaw.py:2740-3045`

**Interfaces:**
- Consumes: Task 1's native `attempt` and `status` actions; existing full production client schemas; OpenClaw Chat Completions and `/tools/invoke` responses.
- Produces: `_classify_client_tool_completion(result: CommandResult, nonce: str) -> str`; deterministic `_verify_channel_marker(cli: OpenClawCLI, agent_id: str, nonce: str, channel: str, session_key: str | None = None) -> None`; structured runtime verification containing security checks and `model_tool_behavior`.

- [ ] **Step 1: Rewrite FakeCLI expectations around two deterministic calls**

Change `FakeCLI.probe_channel_marker_attempt` to simulate a native `/tools/invoke` result instead of an HTTP client-tool response, and add `probe_channel_prompt` to represent the tool-free Chat Completions request. Track prompt and attempt calls separately:

```python
def probe_channel_prompt(self, *, agent_id, nonce, channel, session_key=None):
    self.channel_prompt_calls.append({
        "agent_id": agent_id,
        "nonce": nonce,
        "channel": channel,
        "session_key": session_key,
    })
    return CommandResult(200, json.dumps({
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "observed"},
        }],
    }), "")

def probe_channel_marker_attempt(self, *, agent_id, nonce, channel, session_key=None):
    self.channel_probe_attempt_calls.append({
        "agent_id": agent_id,
        "nonce": nonce,
        "channel": channel,
        "session_key": session_key,
    })
    return CommandResult(403, "", "Setup sentinel blocked by channel policy")
```

Update the main successful setup test to require, in order for both channels: tool-free prompt, native attempt, status read.

- [ ] **Step 2: Add regression tests for model warnings and deterministic failures**

Add tests proving:

```python
def test_text_only_model_is_warning_after_deterministic_channel_proof(tmp_path):
    cli = FakeCLI(client_tool_probe=CommandResult(
        200,
        json.dumps({"choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "CHECKED"},
        }]}),
        "",
    ))
    result = configure_openclaw(make_options(tmp_path), cli=cli)
    assert result.ok, result.render()
    assert "model did not produce a valid CRM client-tool call" in result.render()
    assert result.runtime_verification["setup_checks"]["model_tool_behavior"] == "warning_no_tool_call"
```

Also test these fatal cases:

- prompt request is not HTTP 200;
- prompt completion envelope is malformed;
- native attempt is not blocked;
- status has `prompt_seen=false`;
- status has `tool_blocked=false`;
- status has `sentinel_executed=true`;
- status has the wrong session-correlated nonce or schema version;
- full production schemas receive HTTP 400 or a malformed OpenClaw envelope.

Add one warning case for `finish_reason="tool_calls"` with an invalid tool name or arguments. Add one passing case for the exact `finish_crm_response` tool call.

- [ ] **Step 3: Run focused installer tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q -k "channel_marker or channel_probe or client_tool or model_tool_behavior"
```

Expected: FAIL because setup still asks the model to select the sentinel and rejects text-only client-tool completions.

- [ ] **Step 4: Replace the model-selected marker request with prompt plus native attempt**

In `OpenClawCLI`:

```python
def probe_channel_prompt(
    self,
    *,
    agent_id: str,
    nonce: str,
    channel: str,
    session_key: str,
) -> CommandResult:
    payload = {
        "model": f"openclaw/{agent_id}",
        "user": f"setup-channel-marker:{channel}:{nonce}",
        "messages": [{"role": "user", "content": f"Setup channel probe {nonce}"}],
        "tools": [],
        "tool_choice": "none",
        "max_completion_tokens": 32,
    }
    return self._post_gateway_json(
        chat_path, payload, channel=channel, session_key=session_key,
    )

def probe_channel_marker_attempt(
    self,
    *,
    agent_id: str,
    nonce: str,
    channel: str,
    session_key: str,
) -> CommandResult:
    payload = {
        "tool": SETUP_MARKER_TOOL,
        "args": {"action": "attempt", "channel": channel, "nonce": nonce},
        "agentId": agent_id,
        "sessionKey": session_key,
        "idempotencyKey": f"setup-marker-attempt:{nonce}:{channel}",
    }
    return self._post_gateway_json("/tools/invoke", payload, channel=channel)
```

Require the prompt request to return a structurally valid single text completion. Require the attempt request to be blocked with the expected bounded status. Do not parse or trust its model content.

Update `_verify_channel_marker` to accept only schema version 2 with exact channel and nonce plus:

```python
details == {
    "schema_version": 2,
    "channel": channel,
    "nonce": nonce,
    "prompt_seen": True,
    "tool_blocked": True,
    "sentinel_executed": False,
}
```

Every other value raises `SetupConflict` with the specific failed invariant.

- [ ] **Step 5: Classify client-tool behavior without weakening schema transport**

Rename `_verify_client_tool_completion` to:

```python
def _classify_client_tool_completion(result: CommandResult, nonce: str) -> str:
    """Return verified, warning_no_tool_call, or warning_invalid_tool_call."""
```

The decoder still fails for invalid JSON, missing `choices`, multiple choices, a non-object message, or an unknown finish shape. A valid `finish_reason="stop"` with string content returns `warning_no_tool_call`. A structurally valid tool-call envelope with the wrong function or arguments returns `warning_invalid_tool_call`. The exact `finish_crm_response` call returns `verified`.

Keep `_request_client_tool_capability` fatal for non-200 responses, including HTTP 400 schema rejection. Return the classification from `_verify_setup_probe_behavior` and append one truthful message when it is not `verified`:

```text
Compatibility warning: the configured model accepted the production schemas but did not produce a valid CRM client-tool call. Setup proved channel policy only. Run doctor and live acceptance before using CRM chat.
```

- [ ] **Step 6: Put structured setup checks in runtime evidence**

Extend both `_inventory_runtime_verification` and `_behavioral_runtime_verification` with an exact `setup_checks` object:

```python
{
    "channel_policy": [DASHBOARD_CHANNEL, INTERNAL_ANALYSIS_CHANNEL],
    "schema_transport": "accepted",
    "model_tool_behavior": model_tool_behavior,
    "diagnostic_cleanup": "verified",
}
```

Update `_validate_runtime_verification` to accept only `verified`, `warning_no_tool_call`, or `warning_invalid_tool_call` for the model field and exact passing values for the security fields. Update installed-state tests so two-pass evidence retains the distinction without calling it `crm_verified`.

When runtime hook inventory is unavailable, keep the existing production behavioral fallback for its configured-agent guard and tool-free analysis checks, but feed it the already classified client-tool result. Text-only or invalid tool selection remains a warning; failure to prove the native production channel guard remains fatal.

- [ ] **Step 7: Update dry-run and success wording**

Replace wording that says setup proves channel markers "through the production Chat Completions path" with wording that says it proves protected channel propagation and native-tool blocking through an isolated session. State that production-schema transport is verified separately and that model behavior is reported, not trusted.

- [ ] **Step 8: Run the focused setup suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q
```

Expected: all setup tests pass. Existing rollback, hook permission, path parsing, strict schema rejection, and installed-state validation tests remain green.

- [ ] **Step 9: Commit installer separation and evidence**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "Separate OpenClaw policy proof from model behavior"
```

---

### Task 3: Parse and retry supported diagnostic-agent cleanup

**Files:**
- Modify: `scripts/setup_openclaw.py:218-245`
- Modify: `scripts/setup_openclaw.py:5350-5405`
- Modify: `scripts/setup_openclaw.py:7200-7245`
- Modify: `scripts/setup_openclaw.py:7288-7345`
- Test: `backend/tests/test_setup_openclaw.py:445-495`
- Test: `backend/tests/test_setup_openclaw.py:3030-3210`

**Interfaces:**
- Consumes: OpenClaw `agents delete --force --json` output with `agentId`, `workspace`, `agentDir`, `sessionsDir`, `removed`, `failed`, optional `purgeFailed`, and optional `transport`.
- Produces: `AgentDeletionReport`; `_delete_agent_and_verify(cli: OpenClawCLI, agent_id: str, *, expected_workspace: Path) -> AgentDeletionReport`; one supported retry after gateway restart for incomplete OpenClaw cleanup.

- [ ] **Step 1: Make FakeCLI return the real deletion envelope**

When FakeCLI processes `agents delete`, return:

```python
CommandResult(0, json.dumps({
    "agentId": agent_id,
    "workspace": workspace,
    "agentDir": f"/safe/openclaw/agents/{agent_id}",
    "sessionsDir": f"/safe/openclaw/agents/{agent_id}/sessions",
    "removed": [],
    "failed": [],
    "removedBindings": 0,
    "removedAllow": 0,
}), "")
```

Keep these values synthetic. Tests must never depend on a real home directory.

- [ ] **Step 2: Add structured cleanup regression tests**

Add tests for:

- malformed deletion JSON;
- wrong `agentId`;
- wrong workspace;
- non-list `removed` or `failed`;
- failed entries not shaped as `{path: str, reason: str}`;
- `purgeFailed=true` followed by one successful retry after gateway restart;
- a nonempty `failed` list followed by one successful retry;
- persistent purge or path failure returning incomplete and retaining diagnostic state;
- successful deletion requiring roster and CLI absence after the structured result;
- no raw filesystem removal of an OpenClaw-reported agent or sessions directory.

The retry test must assert the call order:

```python
first_delete_index < retry_restart_index < second_delete_index < final_inventory_index
```

- [ ] **Step 3: Run cleanup tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q -k "diagnostic_agent or agent_cleanup or purge"
```

Expected: FAIL because `_delete_agent_and_verify` ignores deletion JSON and cannot retry a deletion journal after the agent leaves the roster.

- [ ] **Step 4: Add an exact deletion-result parser**

Add:

```python
@dataclass(frozen=True)
class AgentDeletionReport:
    complete: bool
    retry_restart_performed: bool
    retained_paths: tuple[str, ...]
```

Implement a private parser that:

- requires an object and the exact requested `agentId`;
- requires the canonical `workspace` to match `expected_workspace`;
- requires nonempty string `agentDir` and `sessionsDir`;
- accepts `removed` as a list of strings;
- accepts `failed` as a list of exact `{path, reason}` string objects;
- accepts absent or boolean `purgeFailed`, where only `true` is incomplete;
- accepts optional `transport` only when it is a nonempty string;
- rejects shared-workspace retention for the installer-owned unique diagnostic workspace;
- returns reported failed paths only after redacting them from normal success output.

Do not unlink, trash, or recursively delete any reported OpenClaw state path.

- [ ] **Step 5: Retry the supported deletion journal once**

Refactor `_delete_agent_and_verify` to:

1. verify ownership before the first delete;
2. run `openclaw agents delete AGENT --force --json`;
3. parse its structured output;
4. verify roster and CLI absence;
5. if `failed` is nonempty or `purgeFailed=true`, restart the gateway once and run the same delete command again even though the agent is no longer configured;
6. parse the retry output and reverify absence;
7. return `complete=false` with retained paths if OpenClaw still reports incomplete cleanup.

Use OpenClaw's deletion journal for the second call. Do not invent a manual directory cleanup fallback.

Update success and rollback callers to use `report.complete`. If the retry already restarted the gateway, keep the existing final restart because it verifies the installed production state after temporary plugin configuration is removed.

- [ ] **Step 6: Run the full setup suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q
```

Expected: all setup and cleanup tests pass, and persistent cleanup failure remains fail closed with recoverable diagnostic state.

- [ ] **Step 7: Commit supported cleanup handling**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "Verify OpenClaw diagnostic agent cleanup"
```

---

### Task 4: Document the result and run repository-wide verification

**Files:**
- Modify: `docs/LOCAL-AI.md:267-305`
- Modify: `docs/WINDOWS-WSL-SETUP.md`
- Test: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Consumes: structured setup checks and compatibility warning from Tasks 2 and 3.
- Produces: nontechnical setup guidance that distinguishes secure installation, model compatibility, and live CRM readiness.

- [ ] **Step 1: Add documentation assertions before editing prose**

Extend the existing documentation checks in `test_setup_openclaw.py` to require these concepts in both local and WSL guidance:

```python
assert "setup proves the OpenClaw policy" in local_ai
assert "does not prove that your model can use the CRM" in local_ai
assert "Compatibility warning" in local_ai
assert "Do not delete OpenClaw agent folders manually" in wsl_guide
assert "python3 -I scripts/acceptance_openclaw.py" in wsl_guide
```

- [ ] **Step 2: Run the documentation tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q -k "document or local_ai or wsl"
```

Expected: FAIL because the current docs describe model-dependent markers as part of setup proof and do not explain the warning.

- [ ] **Step 3: Update nontechnical setup guidance**

In `docs/LOCAL-AI.md`, explain in plain language:

- setup proves the OpenClaw plugin, restrictions, schemas, and cleanup;
- setup does not prove that the selected model understands ordinary CRM requests;
- a compatibility warning means setup can continue safely, but dashboard CRM use must wait for doctor and acceptance;
- no write acceptance runs until the audited read passes.

In `docs/WINDOWS-WSL-SETUP.md`, add the same distinction to the tester sequence and state:

```text
Do not delete OpenClaw agent folders manually. If diagnostic cleanup fails, keep the report and send it to the maintainer. The installer will retry OpenClaw's supported cleanup once and otherwise leave the recoverable state untouched.
```

Keep the instructions usable for nontechnical operators and preserve the 16 GB memory baseline.

- [ ] **Step 4: Run the focused documentation tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -p no:cacheprovider -q -k "document or local_ai or wsl"
```

Expected: PASS.

- [ ] **Step 5: Run all automated verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
cd openclaw-plugins/openhouse-crm && npm test
cd ../../dashboard && npm run build
git diff --check
git status --short
```

Expected: backend, plugin, and dashboard verification pass; `git diff --check` is silent; only intended documentation changes remain uncommitted.

- [ ] **Step 6: Commit documentation**

```bash
git add docs/LOCAL-AI.md docs/WINDOWS-WSL-SETUP.md backend/tests/test_setup_openclaw.py
git commit -m "Document OpenClaw setup compatibility warnings"
```

- [ ] **Step 7: Perform final review and push for WSL retest**

Review the full branch diff from `4ec880c`, confirm no unrelated app changes, and rerun `git diff --check`. Push the branch only after all verification is green. The WSL tester then runs setup evidence twice and proceeds to read-only doctor and acceptance. Write acceptance remains blocked until the audited read passes.
