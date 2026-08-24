# Verified Dashboard CRM Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard chat perform verified natural-language CRM reads, reviewed writes, and bookings without accepting fabricated facts or false success claims, while keeping Discord on the same strict native CRM tool.

**Architecture:** Define one canonical operation contract in the installed CRM skill. The native OpenClaw plugin validates calls and returns bounded receipts. Dashboard chat uses required request-scoped client tools to collect operations, executes each through OpenClaw's policy-controlled `/tools/invoke`, and renders critical facts and mutation status from verified receipts. Discord uses the same plugin plus run-scoped delivery correction for mutation receipts.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, httpx, SQLite, pytest, Node.js 22+, ESM JavaScript, Node test runner, OpenClaw Chat Completions and Tools Invoke APIs, React/Vite verification.

**Spec:** `docs/superpowers/specs/2026-08-22-verified-dashboard-crm-chat-design.md`

## Global Constraints

- Keep the dashboard request and response contract `{message, session_id} -> {reply, session_id}` unchanged.
- Keep the existing SQLite schema, CRM REST API, audit log, and Pending approvals authoritative.
- Execute dashboard and Discord CRM operations through the shipped `openhouse_crm` plugin and fixed Python dispatcher.
- Never let model input select a shell command, executable, URL, path, environment variable, token, timeout, or working directory.
- Never report a write as queued without a real pending proposal ID returned by the CRM backend.
- Never automatically retry a mutating `/tools/invoke` request.
- Keep missing briefing and market data missing. Do not generate fabricated fallback content.
- Preserve the dedicated agent's exact final allowlist of `openhouse_crm` and `exec`; `exec` remains only for the deterministic daily brief.
- Preserve the machine-wide OpenClaw tool profile and unrelated agents, plugins, skills, bindings, and approvals.
- Keep setup capability-based, idempotent, rollback-safe, dry-run safe, and secret-redacted.
- Add no cloud dependency, paid service, database service, runtime compiler, or platform-specific application branch.
- Support Apple-silicon macOS and Linux on ARM64 or x86_64, plus Windows 11 through WSL2. Native Windows remains unsupported.
- Keep 16 GB as the documented memory baseline; a WSL guest exposing 15.3 GiB remains a warning rather than a hard failure.
- Write each behavior change only after its focused regression test fails for the intended reason.
- Do not claim supported-hardware success from mocks. Final acceptance requires the external live-machine report.

---

## File Structure

### Canonical contract and CRM skill

- `skills/crm-db-operations/contract.json`: sole source-controlled model-facing operation schemas and classifications.
- `skills/crm-db-operations/contract.py`: load and validate the contract for Python consumers.
- `skills/crm-db-operations/cli.py`: validate named arguments, dispatch fixed functions, and emit sanitized structured errors.
- `skills/crm-db-operations/tools.py`: add the compact lead-directory read.
- `skills/crm-db-operations/SKILL.md`: teach the model to prefer compact reads and interpret receipts.

### OpenClaw plugin

- `openclaw-plugins/openhouse-crm/dist/contract.js`: load the canonical contract and build the discriminated tool schema.
- `openclaw-plugins/openhouse-crm/dist/runner.js`: execute the fixed wrapper and return structured receipts.
- `openclaw-plugins/openhouse-crm/dist/outcome-guard.js`: hold bounded run-scoped mutation receipts and correct Discord delivery text.
- `openclaw-plugins/openhouse-crm/dist/definition.js`: register the tool and scoped hooks.
- `openclaw-plugins/openhouse-crm/dist/index.js`: keep the supported plugin entrypoint.

### Backend orchestration

- `backend/app/agent/openclaw_gateway.py`: one HTTP boundary for Chat Completions and direct tool invocation.
- `backend/app/agent/crm_chat.py`: verified client-tool loop, receipt validation, and deterministic response rendering.
- `backend/app/agent/openclaw.py`: route ordinary chat and health checks through those focused units.
- `backend/app/routers/misc.py`: retain nonce-audit verification around the deterministic capability call.
- `backend/app/routers/chat.py`: persist only the verified final reply, with its public API unchanged.

### Setup, acceptance, and documentation

- `scripts/setup_openclaw.py`: install the canonical contract, verify plugin hooks and request-scoped tool capability, and retain rollback behavior.
- `scripts/doctor.py`: report chat completion and deterministic audited CRM capability separately.
- `scripts/acceptance_openclaw.py`: produce one sanitized supported-hardware acceptance bundle.
- `README.md`, `docs/LOCAL-AI.md`, `docs/MAC-MINI-SETUP.md`, `docs/WINDOWS-WSL-SETUP.md`, `docs/GB10-SETUP.md`, and `docs/CONTRACT.md`: explain the verified flow and one-command retest.

---

### Task 1: Canonical Strict CRM Operation Contract

**Files:**
- Create: `skills/crm-db-operations/contract.json`
- Create: `skills/crm-db-operations/contract.py`
- Modify: `skills/crm-db-operations/cli.py`
- Modify: `skills/crm-db-operations/tools.py`
- Delete: `skills/crm-db-operations/operations.json`
- Modify: `backend/tests/test_crm_operation_catalog.py`
- Modify: `backend/tests/test_skill_tools.py`

**Interfaces:**
- Produces: `load_contract() -> dict`, `operation_names() -> tuple[str, ...]`, and `validate_arguments(operation: str, arguments: dict) -> dict`.
- Produces: `tools.list_lead_directory(sort="priority", status=None, neglected=None, offset=0, limit=25) -> dict`.
- Contract entry shape: `{description, effect, arguments}` where `effect` is `read`, `proposal`, `narrative`, or `validated_write` and `arguments` is a strict JSON Schema object.
- Consumers: Tasks 2, 4, 5, and 6.

The contract must declare these exact model-facing argument sets:

| Operation | Required | Optional and constraints | Effect |
|---|---|---|---|
| `create_lead` | none, but schema requires at least one of `raw_text` or `name` | `source` enum `form,text,note,referral,email`; `phone,email,area,timeline,intent`; integer `budget` | `proposal` |
| `update_lead` | `lead_id` and at least one writable field | `name,phone,email,status,budget,area,timeline,intent,score,score_reason,is_neglected,persona,relationship_summary`; status excludes `closed`; score 0-100; is_neglected 0-1 | `proposal` |
| `add_note` | `lead_id,content` | nonblank `content` | `proposal` |
| `close_lead` | `lead_id,outcome` | outcome enum `won,lost`; optional `reason` | `proposal` |
| `find_duplicate_leads` | `lead_id` | none | `read` |
| `merge_leads` | `primary_id,duplicate_id` | IDs must differ at Python validation | `proposal` |
| `get_lead_context` | `lead_id` | none | `read` |
| `list_leads` | none | sort enum `priority,recent`; status enum `new,contacted,meeting_booked,closed`; neglected 0-1 | `read` |
| `list_lead_directory` | none | same filters as `list_leads`; offset >= 0; limit 1-50 | `read` |
| `score_lead` | `lead_id` | none | `narrative` |
| `draft_followup` | `lead_id` | none | `narrative` |
| `check_availability` | `date` | date pattern `YYYY-MM-DD` | `read` |
| `list_appointments` | none | none | `read` |
| `book_appointment` | `lead_id,start_ts,end_ts` | optional `location`; timestamps are strings and backend-normalized | `proposal` |
| `schedule_followup` | `lead_id,due_ts` | optional `note` | `proposal` |
| `find_neglected_leads` | none | none | `validated_write` |
| `generate_dashboard_insights` | none | optional `probe_nonce`, max 128 characters | `read` |
| `post_briefing` | `payload` | strict payload containing date, optional generated_at, and strict meeting_briefs objects with lead_id, prepare array, and optional recommendation | `validated_write` |
| `get_research_settings` | none | none | `read` |
| `get_insights` | `date` | date pattern `YYYY-MM-DD` | `read` |
| `get_summary` | `date` | date pattern `YYYY-MM-DD` | `read` |
| `delete_lead` | `lead_id` | optional `reason` | `proposal` |
| `search_knowledge` | `query` | nonblank query; integer k 1-10 | `read` |

`post_summary` remains intentionally absent because publication stays confined to the deterministic daily-brief runner.

- [ ] **Step 1: Replace catalog tests with strict contract tests**

Add tests that load `contract.json`, assert the exact operation set above, require `additionalProperties: false` for every operation argument object, require a known effect, and prove every operation resolves to a callable in `cli.OPERATIONS`.

```python
def test_contract_is_strict_and_drives_dispatch():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["version"] == 1
    assert set(contract["operations"]) == EXPECTED_OPERATIONS
    for name, entry in contract["operations"].items():
        assert entry["effect"] in {"read", "proposal", "narrative", "validated_write"}
        assert entry["arguments"]["type"] == "object"
        assert entry["arguments"]["additionalProperties"] is False
        assert callable(_load_cli().OPERATIONS[name])
```

Add focused mutation tests proving `source_note` and `status` are rejected for `create_lead`, an empty `update_lead` is rejected, `status="closed"` is rejected for update, and valid booking arguments survive unchanged.

```python
def test_create_lead_rejects_model_invented_arguments():
    with pytest.raises(ValueError, match="Unsupported argument: source_note"):
        validate_arguments("create_lead", {"name": "Jordan", "source_note": "open house"})
```

- [ ] **Step 2: Add compact directory behavior tests**

Mock `_request` with 30 literal lead rows. Assert exact total before pagination, stable selected fields, offset and limit behavior, filter forwarding, and no large fields such as `relationship_summary`, `preferences`, or `missing_fields` in directory entries.

```python
def test_lead_directory_returns_exact_total_and_compact_page(monkeypatch):
    rows = [{"id": i, "name": f"Lead {i}", "status": "new", "score": i,
             "area": "Kirkland", "intent": "buy", "is_neglected": 0,
             "relationship_summary": "x" * 5000} for i in range(30)]
    monkeypatch.setattr(tools, "_request", lambda *args, **kwargs: rows)
    result = tools.list_lead_directory(offset=5, limit=10)
    assert result["total"] == 30
    assert [row["id"] for row in result["leads"]] == list(range(5, 15))
    assert "relationship_summary" not in result["leads"][0]
```

- [ ] **Step 3: Run focused tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_crm_operation_catalog.py tests/test_skill_tools.py -q
```

Expected: failures because `contract.json`, validation helpers, and `list_lead_directory` do not exist.

- [ ] **Step 4: Implement the contract loader and validator**

Use only the Python standard library. Load relative to the skill directory, reject malformed contract data at import time, and implement the JSON Schema subset used here: required properties, unknown properties, primitive types, enum, const, minimum, maximum, minLength, maxLength, pattern, anyOf, arrays, and nested object properties. Error messages must be stable and name only the operation or argument, never a local path.

```python
def validate_arguments(operation: str, arguments: dict) -> dict:
    entry = CONTRACT["operations"].get(operation)
    if entry is None:
        raise ValueError(f"Unknown CRM operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("CRM arguments must be an object")
    _validate_object(entry["arguments"], arguments, path="")
    return dict(arguments)
```

Make `cli.OPERATIONS` derive only from `operation_names()` and validate before calling a function.

```python
def dispatch(operation: str, arguments: dict):
    function = OPERATIONS.get(operation)
    if function is None:
        raise ValueError(f"Unknown CRM operation: {operation}")
    return function(**validate_arguments(operation, arguments))
```

- [ ] **Step 5: Implement the compact directory**

Call the existing `/leads` request once, compute `total = len(rows)`, slice after filtering, and project exactly `id,name,status,score,area,timeline,intent,is_neglected,last_activity_at` when those keys exist.

```python
def list_lead_directory(sort="priority", status=None, neglected=None,
                        offset=0, limit=25) -> dict:
    rows = list_leads(sort=sort, status=status, neglected=neglected)
    keys = ("id", "name", "status", "score", "area", "timeline", "intent",
            "is_neglected", "last_activity_at")
    page = [{key: row.get(key) for key in keys} for row in rows[offset:offset + limit]]
    return {"total": len(rows), "offset": offset, "limit": limit, "leads": page}
```

- [ ] **Step 6: Run focused tests and confirm green**

Run:

```bash
cd backend
pytest tests/test_crm_operation_catalog.py tests/test_skill_tools.py -q
```

- [ ] **Step 7: Commit**

```bash
git add skills/crm-db-operations backend/tests/test_crm_operation_catalog.py backend/tests/test_skill_tools.py
git commit -m "feat: define strict CRM operation contract"
```

---

### Task 2: Structured Plugin Receipts and Safe Errors

**Files:**
- Create: `openclaw-plugins/openhouse-crm/dist/contract.js`
- Modify: `openclaw-plugins/openhouse-crm/dist/definition.js`
- Modify: `openclaw-plugins/openhouse-crm/dist/runner.js`
- Modify: `openclaw-plugins/openhouse-crm/package.json`
- Delete: `openclaw-plugins/openhouse-crm/operations.json`
- Modify: `openclaw-plugins/openhouse-crm/test/plugin.test.js`
- Modify: `openclaw-plugins/openhouse-crm/test/runner.test.js`
- Modify: `skills/crm-db-operations/cli.py`
- Modify: `backend/tests/test_crm_operation_catalog.py`

**Interfaces:**
- Consumes: Task 1 `contract.json` and Python `validate_arguments`.
- Produces: `buildToolParameters(contract) -> JSONSchema` with one strict branch per operation.
- Produces: `runCrmTool(input, toolContext, runChild?) -> Promise<CrmReceipt>`.
- `CrmReceipt` is either `{ok:true, operation, kind, result}` or `{ok:false, operation, kind:"error", error:{code,message,retryable}}`.
- Consumers: Tasks 3, 4, and 5.

- [ ] **Step 1: Write failing schema and receipt tests**

Require one top-level `oneOf` branch per operation. Each branch must hold `operation: {const: name}` and that operation's strict `arguments` schema. Assert unknown top-level fields remain forbidden.

```javascript
const createBranch = tool.parameters.oneOf.find(
  (branch) => branch.properties.operation.const === "create_lead",
);
assert.equal(createBranch.properties.arguments.additionalProperties, false);
assert.equal(createBranch.properties.arguments.properties.source_note, undefined);
assert.equal(createBranch.properties.arguments.properties.status, undefined);
```

Change runner tests to expect explicit receipts for valid reads, pending writes, invalid arguments, a 404, a 409, backend unavailability, timeout, oversized output, invalid JSON, and unknown child failure. Keep assertions proving `shell:false`, fixed wrapper path, fixed timeout, bounded buffer, and no environment override.

```javascript
assert.deepEqual(await runCrmTool(validCreate, context, successfulChild(pending)), {
  ok: true,
  operation: "create_lead",
  kind: "proposal",
  result: pending,
});
```

- [ ] **Step 2: Run plugin and CLI tests and confirm red**

Run:

```bash
npm --prefix openclaw-plugins/openhouse-crm test
cd backend
pytest tests/test_crm_operation_catalog.py -q
```

Expected: existing plugin still exposes an open argument object and throws generic errors.

- [ ] **Step 3: Emit sanitized CLI errors**

Map exceptions to these exact safe codes:

```python
def _safe_error(exc: Exception) -> dict:
    if isinstance(exc, tools.CRMError):
        code = {0: "backend_unavailable", 404: "not_found",
                409: "schedule_conflict", 400: "invalid_arguments",
                422: "invalid_arguments"}.get(exc.status, "operation_failed")
        message = _bounded_crm_message(code, exc.message)
        return {"code": code, "message": message,
                "retryable": code in {"backend_unavailable", "timeout"}}
    if isinstance(exc, (TypeError, ValueError)):
        return {"code": "invalid_arguments",
                "message": _bounded_argument_message(exc), "retryable": False}
    return {"code": "operation_failed", "message": "CRM operation failed",
            "retryable": False}
```

Continue returning CLI exit code 2 for errors, but print only `{"ok":false,"error":...}`. The Node runner may parse this known JSON from the rejected child's stderr. It must ignore unparseable stderr.

- [ ] **Step 4: Load the contract and build the discriminated schema**

Read the canonical file from `../../../skills/crm-db-operations/contract.json` relative to the plugin's built JavaScript. Validate its version and operation entries before registration.

```javascript
export function buildToolParameters(contract) {
  return {
    oneOf: Object.entries(contract.operations).map(([operation, entry]) => ({
      type: "object",
      additionalProperties: false,
      required: ["operation", "arguments"],
      properties: {
        operation: { const: operation, description: entry.description },
        arguments: entry.arguments,
      },
    })),
  };
}
```

- [ ] **Step 5: Return bounded receipts from the runner**

Successful child output becomes a receipt. A pending result always forces `kind:"proposal"`; otherwise use the contract effect. Parse only known structured CLI errors. Convert process timeouts, max-buffer errors, and malformed output into the fixed error envelope. Keep receipt JSON under the existing output bound.

```javascript
function successReceipt(operation, effect, result) {
  return {
    ok: true,
    operation,
    kind: result?.pending === true ? "proposal" : effect,
    result,
  };
}
```

`definition.js` must put the full receipt JSON in both `content[0].text` and `details`, because OpenClaw strips `details` from model replay.

- [ ] **Step 6: Update package contents and parity tests**

Remove both obsolete `operations.json` references. Assert the plugin reads the single canonical contract and `package.json` still has no install script or runtime dependency.

- [ ] **Step 7: Run focused tests and confirm green**

Run:

```bash
npm --prefix openclaw-plugins/openhouse-crm test
cd backend
pytest tests/test_crm_operation_catalog.py -q
```

- [ ] **Step 8: Commit**

```bash
git add openclaw-plugins/openhouse-crm skills/crm-db-operations/cli.py backend/tests/test_crm_operation_catalog.py
git commit -m "feat: return strict CRM tool receipts"
```

---

### Task 3: Deterministic Gateway Invocation and CRM Doctor

**Files:**
- Create: `backend/app/agent/openclaw_gateway.py`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/app/agent/base.py`
- Modify: `backend/app/routers/misc.py`
- Modify: `backend/tests/test_openclaw.py`
- Modify: `backend/tests/test_doctor.py`

**Interfaces:**
- Consumes: Task 2 receipt envelope.
- Produces: `OpenClawGateway.chat_completion(payload: dict, *, channel: str | None = None) -> dict`.
- Produces: `OpenClawGateway.invoke_tool(name: str, args: dict, *, agent_id: str, session_key: str, idempotency_key: str) -> dict`.
- Changes: `AgentDriver.request_crm_capability(...) -> dict` returns the direct receipt.
- Consumers: Task 4.

- [ ] **Step 1: Write failing Gateway boundary tests**

Use a fake `httpx.AsyncClient` that records URL, headers, and JSON. Assert chat uses the configured Chat Completions path and direct invoke uses `/tools/invoke` with the exact fields below.

```python
assert fake.last_post_json == {
    "tool": "openhouse_crm",
    "args": {"operation": "generate_dashboard_insights",
             "arguments": {"probe_nonce": nonce}},
    "agentId": "openhouse-crm",
    "sessionKey": f"crm-check-{nonce}",
    "idempotencyKey": f"crm-check-{nonce}",
}
```

Cover 200 success, malformed 200, 400 tool input, 401/403 auth, 404 policy/tool absence, 429 auth throttling, timeout, and sanitized 500. No error detail may contain the Gateway token or response body beyond a bounded fixed message.

- [ ] **Step 2: Change health tests to reject model prompting**

Assert `request_crm_capability` calls `invoke_tool` and never calls Chat Completions. Preserve tests requiring a new matching nonce audit, rejecting old or unrelated audits, moving DB reads off the event loop, and keeping a newer chat failure degraded.

```python
async def test_capability_uses_direct_tool_invoke(monkeypatch):
    gateway = FakeGateway(receipt=VALID_METRICS_RECEIPT)
    driver = OpenClawDriver(gateway=gateway)
    await driver.request_crm_capability("crm-check-abc", "abc")
    assert gateway.chat_calls == []
    assert gateway.invoke_calls[0]["name"] == "openhouse_crm"
```

- [ ] **Step 3: Run focused tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_openclaw.py tests/test_doctor.py -q
```

Expected: no Gateway boundary exists and the capability path still asks the model to reply `CHECKED`.

- [ ] **Step 4: Implement `OpenClawGateway`**

Move Gateway URL, token header, timeout handling, response parsing, and safe error mapping into the new focused file. Never log request headers or raw bodies. Add `x-openclaw-message-channel` only when explicitly requested.

```python
class OpenClawGateway:
    async def invoke_tool(self, name, args, *, agent_id, session_key,
                          idempotency_key):
        payload = {"tool": name, "args": args, "agentId": agent_id,
                   "sessionKey": session_key,
                   "idempotencyKey": idempotency_key}
        return await self._post_json("/tools/invoke", payload)
```

- [ ] **Step 5: Replace the model capability request**

Call `openhouse_crm` directly with the existing read-only metrics operation and nonce. Require `{ok:true,result}` from the Gateway and preserve the router's independent audit check. Remove the `CHECKED`, `tool_search`, and `tool_call` capability prompt.

- [ ] **Step 6: Run focused tests and confirm green**

Run:

```bash
cd backend
pytest tests/test_openclaw.py tests/test_doctor.py -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent backend/app/routers/misc.py backend/tests/test_openclaw.py backend/tests/test_doctor.py
git commit -m "fix: verify CRM capability without model routing"
```

---

### Task 4: Verified Dashboard CRM Tool Loop

**Files:**
- Create: `backend/app/agent/crm_chat.py`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/app/routers/chat.py`
- Create: `backend/tests/test_crm_chat.py`
- Modify: `backend/tests/test_openclaw.py`

**Interfaces:**
- Consumes: Task 1 contract and Task 3 `OpenClawGateway`.
- Produces: `run_verified_crm_chat(gateway, message, session_id, agent_id) -> str`.
- Produces: `CrmCallReceipt(call_id, operation, ok, kind, result, error)` dataclass.
- Produces: `validate_finish(params, receipts) -> FinishDecision` and `render_verified_reply(decision, receipts) -> str`.
- Changes: `OpenClawDriver.chat()` delegates CRM dashboard turns to the verified loop.

The client tools have these exact names and roles:

```python
CRM_REQUEST_TOOL = "openhouse_crm_request"
FINISH_TOOL = "finish_crm_response"
DASHBOARD_CHANNEL = "openhouse-dashboard"
MAX_MODEL_ROUNDS = 8
MAX_CRM_CALLS = 6
```

`finish_crm_response` parameters are strict:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["classification", "message", "evidence_call_ids"],
  "properties": {
    "classification": {
      "type": "string",
      "enum": ["answered", "queued", "needs_clarification", "failed"]
    },
    "message": {"type": "string", "maxLength": 4000},
    "evidence_call_ids": {
      "type": "array",
      "items": {"type": "string"},
      "uniqueItems": true,
      "maxItems": 6
    },
    "pending_id": {"type": "integer", "minimum": 1}
  }
}
```

- [ ] **Step 1: Write failing orchestration tests**

Use a scripted fake Gateway with literal Chat Completions responses. Cover:

1. lead directory call followed by `answered`, with exact total 15 in output;
2. create lead followed by `queued`, with exact pending ID and no claim that it is applied;
3. invalid create arguments followed by `failed`, saying nothing changed;
4. a false `queued` finish without a pending receipt, which is returned to the model as a tool error and cannot become the final response;
5. a mismatched pending ID, which is rejected;
6. read, availability, and booking calls across three rounds, producing exactly one pending booking receipt;
7. multiple client calls in one model response, which execute none and return a bounded correction;
8. malformed function arguments, unknown function name, empty call list, repeated failure, model timeout, round limit, and call limit;
9. no automatic retry after an ambiguous tool-invoke transport failure;
10. the exact `openhouse-dashboard` channel header on model rounds;
11. only the verified final reply persisted by `/api/chat`.

```python
def test_failed_write_cannot_claim_pending(scripted_gateway):
    scripted_gateway.chat_responses = [
        tool_call("call-1", CRM_REQUEST_TOOL, {
            "operation": "create_lead",
            "arguments": {"name": "Jordan", "source_note": "open house"},
        }),
        finish_call("failed", "I created it", ["call-1"]),
    ]
    reply = asyncio.run(run_verified_crm_chat(
        scripted_gateway, "Add Jordan", "dashboard", "openhouse-crm"))
    assert "nothing was queued" in reply.lower()
    assert "created" not in reply.lower()
    assert scripted_gateway.invoke_calls == []
```

- [ ] **Step 2: Write failing deterministic renderer tests**

Use literal receipts and assert exact natural-language facts for:

- compact directory total and names;
- dashboard metric counts including `avg_response_minutes:null`;
- availability slots;
- appointment lead names and times;
- lead context identity and stored fields;
- pending proposal ID, summary, and review status;
- sanitized error code and message.

Narrative operations may use the model message, but append no mutation claim unless a proposal receipt exists.

- [ ] **Step 3: Run focused tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_crm_chat.py tests/test_openclaw.py -q
```

Expected: `crm_chat.py` and verified orchestration do not exist.

- [ ] **Step 4: Implement strict client-tool definitions**

Convert each operation contract entry into the request tool's discriminated schema. Define the finish schema literally. Send both tools on every model round with `tool_choice:"required"` and a short system instruction that one function call is required per round.

```python
payload = {
    "model": openclaw_model(agent_id),
    "user": session_id,
    "messages": messages,
    "tools": [crm_request_tool(contract), finish_tool()],
    "tool_choice": "required",
}
```

- [ ] **Step 5: Implement one-call-per-round execution**

Require exactly one structured client call. Validate JSON before execution. For CRM requests, call `/tools/invoke` once with a deterministic call-scoped idempotency key and append the exact receipt as a `role:"tool"` message. Do not retry a transport failure because the backend cannot prove whether the mutation reached the tool.

```python
idempotency_key = f"ohi:{session_id}:{tool_call_id}"
receipt = await gateway.invoke_tool(
    "openhouse_crm", params, agent_id=agent_id,
    session_key=f"dashboard:{session_id}",
    idempotency_key=idempotency_key,
)
```

- [ ] **Step 6: Implement finish validation**

Enforce the evidence table from the spec. `queued` requires one matching successful proposal and exact pending ID. `answered` requires successful read or narrative evidence. `failed` cannot contain success wording and is replaced with a deterministic failure renderer. `needs_clarification` is capped at one question and cannot contain a success claim.

- [ ] **Step 7: Implement deterministic response renderers**

Ignore model-provided critical facts for common reads and all mutation status. Render proposal and failure responses entirely from receipts. For compact lead directories, state the exact total and render only the current page. For narrative-safe operations, retain the bounded model message after stripping unsupported mutation-success language.

- [ ] **Step 8: Connect `OpenClawDriver.chat` and persistence**

Keep `MockDriver` unchanged. For OpenClaw, call the verified loop. Preserve the current readable unavailable response on transport failure. `chat.py` continues persisting the user turn first and stores only the final verified reply.

- [ ] **Step 9: Run focused tests and confirm green**

Run:

```bash
cd backend
pytest tests/test_crm_chat.py tests/test_openclaw.py -q
```

- [ ] **Step 10: Commit**

```bash
git add backend/app/agent/crm_chat.py backend/app/agent/openclaw.py backend/app/routers/chat.py backend/tests/test_crm_chat.py backend/tests/test_openclaw.py
git commit -m "feat: verify dashboard CRM chat outcomes"
```

---

### Task 5: Discord Mutation Outcome Guard

**Files:**
- Create: `openclaw-plugins/openhouse-crm/dist/outcome-guard.js`
- Modify: `openclaw-plugins/openhouse-crm/dist/definition.js`
- Create: `openclaw-plugins/openhouse-crm/test/outcome-guard.test.js`
- Modify: `openclaw-plugins/openhouse-crm/test/plugin.test.js`
- Modify: `openclaw-plugins/openhouse-crm/openclaw.plugin.json`
- Modify: `skills/crm-db-operations/SKILL.md`

**Interfaces:**
- Consumes: Task 2 structured receipts.
- Produces: `createOutcomeGuard({maxEntries=256, ttlMs=300000, now=Date.now})`.
- Produces methods `record({runId, agentId, receipt})`, `rewrite({runId, agentId, text}) -> string`, and `clear(runId)`.
- Plugin hooks: `before_tool_call`, `after_tool_call`, `reply_payload_sending`, and `gateway_stop`.

- [ ] **Step 1: Write failing outcome-guard unit tests**

Cover pending proposal rewrite, failed mutation rewrite, read-only pass-through, unrelated agent pass-through, absent run ID pass-through, exact run isolation, TTL expiry, maximum-entry eviction, one-time cleanup after delivery, and restart cleanup.

```javascript
guard.record({ runId: "run-1", agentId: "openhouse-crm", receipt: pendingReceipt });
assert.equal(
  guard.rewrite({ runId: "run-1", agentId: "openhouse-crm", text: "Done" }),
  "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
);
```

- [ ] **Step 2: Write failing plugin hook tests**

Use a fake plugin API that records hooks. Assert:

- dashboard synthetic-channel internal `openhouse_crm` calls are blocked before execution;
- Discord and other ordinary channel calls are not blocked;
- only `openhouse_crm` mutation receipts are recorded;
- `reply_payload_sending` rewrites only matching CRM-agent run text;
- the hook never changes media, destination, account, thread, or unrelated payload properties;
- `gateway_stop` clears bounded in-memory state.

- [ ] **Step 3: Run plugin tests and confirm red**

Run:

```bash
npm --prefix openclaw-plugins/openhouse-crm test
```

Expected: outcome guard and hooks do not exist.

- [ ] **Step 4: Implement the bounded run-scoped guard**

Store only proposal ID, operation, summary, status, sanitized error, agent ID, run ID, and expiry. Never store user prompts, full lead data, tokens, tool context, or raw model replies. A proposal receipt renders a fixed pending sentence. A failed proposal attempt renders `I could not queue that CRM change. Nothing was changed. <safe reason>`.

- [ ] **Step 5: Register scoped hooks**

In `before_tool_call`, block only when the tool name is `openhouse_crm` and the requester channel is `openhouse-dashboard`. This marker is created only by the backend's verified loop and prevents duplicate internal execution. The later direct `/tools/invoke` call carries no dashboard marker.

In `after_tool_call`, parse only the plugin's structured receipt and record only proposal or failed-proposal outcomes keyed by host-provided run ID. In `reply_payload_sending`, preserve the latest payload object and replace only its text field when a matching guard entry exists.

- [ ] **Step 6: Update skill guidance**

Prefer `list_lead_directory` for ordinary list/count questions. Include one valid call example for create, update, note, availability, booking, and follow-up. State that an `ok:false` receipt means nothing was queued, and an `ok:true,kind:"proposal"` receipt means review is still required.

- [ ] **Step 7: Run plugin tests and confirm green**

Run:

```bash
npm --prefix openclaw-plugins/openhouse-crm test
```

- [ ] **Step 8: Commit**

```bash
git add openclaw-plugins/openhouse-crm skills/crm-db-operations/SKILL.md
git commit -m "fix: guard Discord CRM mutation receipts"
```

---

### Task 6: Capability-Based Setup for Contract and Hooks

**Files:**
- Modify: `scripts/setup_openclaw.py`
- Modify: `backend/tests/test_setup_openclaw.py`
- Modify: `backend/tests/test_launchers.py`

**Interfaces:**
- Consumes: Tasks 1, 2, and 5 plugin contract and hooks.
- Produces: setup verification proving canonical contract copy, exact tool schema registration, required hook registration, dashboard synthetic-channel block, and existing exact agent policy.

- [ ] **Step 1: Extend the fake OpenClaw CLI runtime inventory**

Model plugin runtime inspection with exact tool name and hook names. Add fake Chat Completions help or a harmless request-scoped tool contract probe if the installed CLI exposes no authoritative help field. Keep all preflight calls non-mutating.

- [ ] **Step 2: Write failing fresh-install, rerun, upgrade, and rollback tests**

Require:

- canonical `contract.json` lands in the dedicated agent skill workspace;
- stale `operations.json` files are removed from the installed CRM skill;
- plugin runtime exposes exactly `openhouse_crm` and the required scoped hooks;
- exact `profile:"full"`, allowlist, deny list, exec policy, and daily-brief approval remain unchanged;
- a plugin hook or client-tool incompatibility fails before reporting success;
- every mutation after snapshot restores prior agent policy, plugin state, approvals, and contract files on later failure;
- dry run describes actions without touching files or config;
- logs redact Gateway and CRM tokens.

```python
def test_setup_rejects_runtime_missing_outcome_hooks(tmp_path):
    cli = FakeCLI(plugin_runtime={"toolNames": ["openhouse_crm"], "hooks": []})
    result = run_setup(cli, tmp_path)
    assert result.returncode != 0
    assert "required CRM outcome hooks" in result.render()
    assert cli.config == cli.initial_config
```

- [ ] **Step 3: Run installer tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
```

- [ ] **Step 4: Install and verify the canonical contract**

Include `contract.json` in the CRM skill tree copy. Validate its digest before and after copy. Treat a missing, malformed, or mismatched contract as a setup conflict before Gateway restart.

- [ ] **Step 5: Verify runtime hooks and client-tool contract capability**

Use documented runtime inspection fields when present. If the installed OpenClaw cannot authoritatively report hook registration, run a bounded loopback diagnostic that proves the dashboard channel marker blocks internal tool execution without performing a CRM write. Require the Chat Completions endpoint to accept request-scoped function tools and `tool_choice:"required"`; return an actionable unsupported-installation message on 400.

- [ ] **Step 6: Preserve rollback and exact policy validation**

Add new owned files and plugin fields to the existing snapshot/rollback transaction. Do not weaken any current fail-closed checks or broaden global settings.

- [ ] **Step 7: Run installer tests and confirm green**

Run:

```bash
cd backend
pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
```

- [ ] **Step 8: Commit**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py backend/tests/test_launchers.py
git commit -m "fix: verify CRM chat orchestration capabilities"
```

---

### Task 7: One-Command Supported-Hardware Acceptance

**Files:**
- Create: `scripts/acceptance_openclaw.py`
- Create: `scripts/capture_setup_evidence.py`
- Create: `backend/tests/test_acceptance_openclaw.py`
- Modify: `scripts/doctor.py`
- Modify: `backend/tests/test_doctor.py`

**Interfaces:**
- Produces: `python3 scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json` for two explicit revision-tied setup runs.
- Produces: `python3 scripts/acceptance_openclaw.py --json --setup-evidence openhouse-setup-evidence.json` for read-only checks.
- Produces: `python3 scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json` for disposable reviewed create-lead and booking tests.
- Output schema: `{schema_version:1, revision, checks:[{level,name,detail,evidence}], cleanup:[...], warnings:[...]}` with secrets and local home paths removed.

- [ ] **Step 1: Write failing acceptance-runner tests**

Use a fake HTTP boundary and temporary report path. Cover:

- revision and dependency capture;
- exact API count versus natural-language directory count;
- direct `crm_verified` capability;
- invalid-write message proving no proposal was created;
- explicit flag required before a disposable write;
- proposal created, absent from Leads, denied, and still absent;
- missing summary 404 and briefing with no fabricated market fields;
- chat session cleanup;
- cleanup continuing after an intermediate failure;
- sanitized URL, token, exception, and filesystem output;
- nonzero exit on any required failure;
- Discord marked `SKIP` when unbound rather than `PASS`.
- two strict setup results and matching complete canonical installed-state
  snapshots at one clean, unchanged exact revision, with missing, partial,
  failed, mismatched, or malformed evidence rejected;
- natural-language booking against an existing lead, exact Pending ownership,
  appointment nonapplication, denial, and post-cleanup absence;
- bound Discord delivery retained as a manual hardware check, never automated
  PASS from a binding.

```python
def test_write_acceptance_requires_explicit_flag(fake_api):
    result = run_acceptance(fake_api, allow_test_write=False)
    assert fake_api.pending_posts == []
    assert check(result, "Reviewed write").level == "SKIP"
```

- [ ] **Step 2: Run focused tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_acceptance_openclaw.py tests/test_doctor.py -q
```

- [ ] **Step 3: Implement the read-only acceptance path**

Call the running application's health, agent-check, crm-check, leads, chat, summary, and briefing APIs. Use a unique acceptance session ID. Parse the deterministic lead-directory reply and compare its exact count with the API count. Record only bounded evidence.

- [ ] **Step 4: Implement the explicit disposable-write path**

Generate a unique clearly marked test name. Ask dashboard chat to create it, require a real pending ID in the verified reply, confirm the lead does not exist, deny that exact pending proposal, confirm absence again, and delete the acceptance chat session. Then use one suitable existing lead and a unique future time/address marker for an ordinary natural-language booking. Require the exact new `book_appointment` proposal, prove no appointment was applied, deny only the acceptance-owned proposal, and prove it remains unapplied with no owned Pending proposal left. Never approve either proposal.

Use `try/finally` cleanup. If proposal creation succeeded but a later assertion failed, deny it during cleanup. Report a cleanup failure separately and exit nonzero.

- [ ] **Step 5: Keep doctor read-only and direct**

Update help text so `--live-crm` says it directly invokes one audited read. Do not move disposable writes into `doctor.py`.

- [ ] **Step 6: Run focused tests and confirm green**

Run:

```bash
cd backend
pytest tests/test_acceptance_openclaw.py tests/test_doctor.py -q
```

- [ ] **Step 7: Commit**

```bash
git add scripts/acceptance_openclaw.py scripts/capture_setup_evidence.py scripts/doctor.py backend/tests/test_acceptance_openclaw.py backend/tests/test_doctor.py
git commit -m "test: add one-command OpenClaw acceptance"
```

---

### Task 8: Beginner Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/LOCAL-AI.md`
- Modify: `docs/MAC-MINI-SETUP.md`
- Modify: `docs/WINDOWS-WSL-SETUP.md`
- Modify: `docs/GB10-SETUP.md`
- Modify: `docs/CONTRACT.md`
- Modify: `backend/tests/test_launchers.py`
- Modify: `backend/tests/test_daily_brief_skill.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: one beginner path for setup and read-only verification, plus an explicit optional reviewed-write acceptance command.

- [ ] **Step 1: Write failing documentation behavior checks**

Extend existing launcher/documentation tests to require:

- `python3 scripts/setup_openclaw.py` as the only normal OpenClaw setup command;
- `bash scripts/serve.sh` as the serve command;
- `python3 scripts/doctor.py --live-agent --live-crm` as the read-only readiness command;
- `python3 scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json` as the explicit write-enabled acceptance command;
- plain-language explanation that writes wait for review;
- no instructions to edit `agents.list`, global tool profiles, exec policy, or plugin files manually;
- Mac mini 16 GB minimum and WSL/Linux compatibility language;
- Discord documented as optional and tested only after dashboard acceptance;
- voice transcription provider documented as a separate optional prerequisite;
- missing briefing data described as unavailable, never synthesized.

- [ ] **Step 2: Run documentation tests and confirm red**

Run:

```bash
cd backend
pytest tests/test_launchers.py tests/test_daily_brief_skill.py -q
```

- [ ] **Step 3: Rewrite the beginner-facing path**

Keep the README quick start short. Put detailed recovery and evidence interpretation in `docs/LOCAL-AI.md`. Give macOS and WSL copy-paste commands with the same order. Explain these status labels in ordinary language:

- `chat_verified`: OpenClaw answered, but CRM access has not been proven;
- `crm_verified`: the native CRM tool completed and the backend recorded the matching audit;
- `degraded`: CRM was previously verified but the latest chat completion failed;
- `failed`: a required live check did not complete.

- [ ] **Step 4: Update the frozen contract deliberately**

Document the strict model-facing operation contract, compact directory result, structured receipts, direct health invocation, and verified dashboard orchestration. Do not change any existing REST response shape or database table description.

- [ ] **Step 5: Run all focused suites**

Run:

```bash
cd backend
pytest tests/test_crm_operation_catalog.py tests/test_skill_tools.py tests/test_openclaw.py tests/test_crm_chat.py tests/test_setup_openclaw.py tests/test_acceptance_openclaw.py tests/test_doctor.py tests/test_launchers.py tests/test_daily_brief_skill.py -q
cd ..
npm --prefix openclaw-plugins/openhouse-crm test
npm --prefix dashboard run build
```

- [ ] **Step 6: Run the complete backend suite**

Run:

```bash
cd backend
pytest -q
```

Expected: all tests pass with only the repository's already documented deprecation warnings.

- [ ] **Step 7: Inspect the final diff and security invariants**

Run:

```bash
git diff --check
git status --short
git diff --stat origin/main...HEAD
rg -n "source_note|CHECKED|operations.json" backend scripts skills openclaw-plugins docs README.md
```

Expected: no whitespace errors, no capability prompt that trusts `CHECKED`, no obsolete operation catalogs, and `source_note` only in regression tests or explanatory diagnosis.

- [ ] **Step 8: Commit documentation and final verification updates**

```bash
git add README.md docs backend/tests/test_launchers.py backend/tests/test_daily_brief_skill.py
git commit -m "docs: explain verified local CRM chat"
```

- [ ] **Step 9: Push PR #7 and request live-machine acceptance**

```bash
git push origin codex/openclaw-setup-compat
```

Ask the external tester to pull the exact pushed revision and use the evidence
helper to run setup twice:

```bash
python3 scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json
```

Then start the app and run:

```bash
python3 scripts/acceptance_openclaw.py --json --allow-test-write \
  --setup-evidence openhouse-setup-evidence.json \
  | tee openhouse-acceptance.json
```

Do not merge until the report proves the completion criteria in the design or
identifies a specific capability mismatch that the automated suites could not
simulate.
