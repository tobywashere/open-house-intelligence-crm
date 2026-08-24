import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_ARGUMENT_BYTES,
  MAX_OUTPUT_BYTES,
  TOOL_TIMEOUT_MS,
  runCrmTool,
} from "../dist/runner.js";


const context = { workspaceDir: "/trusted/openhouse-workspace" };


function successfulChild(result) {
  return async (file, args, options) => ({
    file,
    args,
    options,
    stdout: JSON.stringify({ ok: true, result }),
    stderr: "",
  });
}


function childFailure(error) {
  return async () => { throw error; };
}


test("returns a structured read and invokes only the fixed wrapper without a shell", async () => {
  let invocation;
  const child = async (file, args, options) => {
    invocation = { file, args, options };
    return { stdout: JSON.stringify({ ok: true, result: [{ id: 4, name: "Chris" }] }), stderr: "" };
  };

  assert.deepEqual(
    await runCrmTool(
      { operation: "list_leads", arguments: { sort: "priority" } }, context, child,
    ),
    { ok: true, operation: "list_leads", kind: "read", result: [{ id: 4, name: "Chris" }] },
  );
  assert.equal(invocation.file, "/trusted/openhouse-workspace/skills/crm-db-operations/cli.py");
  assert.deepEqual(invocation.args, ["list_leads", "--args", JSON.stringify({ sort: "priority" })]);
  assert.equal(invocation.options.shell, false);
  assert.equal(invocation.options.timeout, TOOL_TIMEOUT_MS);
  assert.equal(invocation.options.maxBuffer, MAX_OUTPUT_BYTES);
  assert.equal(invocation.options.encoding, "utf8");
  assert.equal("cwd" in invocation.options, false);
  assert.equal("env" in invocation.options, false);
});


test("labels pending writes as proposals", async () => {
  const pending = {
    pending: true, id: 12, operation: "create_lead", summary: "Create lead Chris", status: "pending",
  };
  assert.deepEqual(
    await runCrmTool(
      { operation: "create_lead", arguments: { raw_text: "Chris" } }, context, successfulChild(pending),
    ),
    { ok: true, operation: "create_lead", kind: "proposal", result: pending },
  );
});


test("returns an invalid-argument receipt for unknown operations before starting a child", async () => {
  let called = false;
  assert.deepEqual(
    await runCrmTool({ operation: "run_anything", arguments: {} }, context, async () => { called = true; }),
    {
      ok: false,
      operation: "run_anything",
      kind: "error",
      error: { code: "invalid_arguments", message: "CRM operation is not supported", retryable: false },
    },
  );
  assert.equal(called, false);
});


test("does not echo an unsafe operation name into an error receipt", async () => {
  assert.deepEqual(
    await runCrmTool({ operation: "/private/token", arguments: {} }, context, successfulChild([])),
    {
      ok: false,
      operation: "unknown",
      kind: "error",
      error: { code: "invalid_arguments", message: "CRM operation is not supported", retryable: false },
    },
  );
});


for (const [input, message] of [
  [null, "input"],
  [{}, "operation"],
  [{ operation: "list_leads", arguments: [] }, "arguments"],
  [{ operation: "list_leads", arguments: null }, "arguments"],
  [{ operation: "list_leads", arguments: { nested: undefined } }, "JSON-compatible"],
]) {
  test(`returns a safe invalid-argument receipt for malformed input containing ${message}`, async () => {
    const receipt = await runCrmTool(input, context, successfulChild([]));
    assert.equal(receipt.ok, false);
    assert.equal(receipt.kind, "error");
    assert.equal(receipt.error.code, "invalid_arguments");
    assert.equal(receipt.error.retryable, false);
    assert.match(receipt.error.message, new RegExp(message));
  });
}


test("defaults omitted arguments to an empty object", async () => {
  let args;
  await runCrmTool({ operation: "list_appointments" }, context, async (_file, childArgs) => {
    args = childArgs;
    return { stdout: '{"ok":true,"result":[]}', stderr: "" };
  });
  assert.equal(args[2], "{}");
});


test("returns invalid arguments for oversized arguments before starting a child", async () => {
  let called = false;
  const receipt = await runCrmTool(
    { operation: "search_knowledge", arguments: { query: "x".repeat(MAX_ARGUMENT_BYTES) } },
    context,
    async () => { called = true; },
  );
  assert.equal(receipt.error.code, "invalid_arguments");
  assert.equal(called, false);
});


for (const workspaceDir of [undefined, "", "."]) {
  test(`returns a safe error for an unusable trusted workspace ${String(workspaceDir)}`, async () => {
    const receipt = await runCrmTool({ operation: "list_leads" }, { workspaceDir }, successfulChild([]));
    assert.deepEqual(receipt, {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "operation_failed", message: "CRM operation failed", retryable: false },
    });
  });
}


test("returns a safe timeout receipt", async () => {
  const timeout = Object.assign(new Error("secret /home/person/token"), {
    killed: true,
    signal: "SIGTERM",
  });
  assert.deepEqual(
    await runCrmTool({ operation: "list_leads" }, context, childFailure(timeout)),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "timeout", message: "CRM operation timed out", retryable: true },
    },
  );
});


for (const [label, failure] of [
  ["timeout", Object.assign(new Error("private timeout"), { killed: true, signal: "SIGTERM" })],
  ["process kill", Object.assign(new Error("private crash"), { signal: "SIGKILL" })],
  ["max buffer kill", Object.assign(new Error("private output"), { code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" })],
]) {
  test(`marks a dispatched mutation ${label} as outcome unknown`, async () => {
    assert.deepEqual(
      await runCrmTool(
        { operation: "create_lead", arguments: { name: "Jordan" } },
        context,
        childFailure(failure),
      ),
      {
        ok: false,
        operation: "create_lead",
        kind: "error",
        error: {
          code: "outcome_unknown",
          message: "CRM mutation outcome is unknown",
          retryable: false,
        },
      },
    );
  });
}


test("keeps a mutation child spawn failure deterministic before dispatch", async () => {
  const failure = Object.assign(new Error("private missing executable path"), {
    code: "ENOENT",
  });

  const receipt = await runCrmTool(
    { operation: "create_lead", arguments: { name: "Jordan" } },
    context,
    childFailure(failure),
  );

  assert.deepEqual(receipt.error, {
    code: "operation_failed",
    message: "CRM operation failed",
    retryable: false,
  });
});


test("preserves a definite HTTP rejection classified by the Python source", async () => {
  const failure = Object.assign(new Error("private HTTP 403 rejection"), {
    code: 2,
    stderr: '{"ok":false,"error":{"code":"operation_failed","message":"CRM operation failed","retryable":false}}',
  });

  const receipt = await runCrmTool(
    { operation: "create_lead", arguments: { name: "Jordan" } },
    context,
    childFailure(failure),
  );

  assert.deepEqual(receipt.error, {
    code: "operation_failed",
    message: "CRM operation failed",
    retryable: false,
  });
});


test("preserves an unknown mutation outcome classified by the Python source", async () => {
  const failure = Object.assign(new Error("private child failure"), {
    code: 2,
    stderr: '{"ok":false,"error":{"code":"outcome_unknown","message":"CRM mutation outcome is unknown","retryable":false}}',
  });

  const receipt = await runCrmTool(
    { operation: "book_appointment", arguments: {
      lead_id: 4,
      start_ts: "2026-08-24T17:00:00",
      end_ts: "2026-08-24T17:30:00",
    } },
    context,
    childFailure(failure),
  );

  assert.equal(receipt.error.code, "outcome_unknown");
  assert.equal(receipt.error.retryable, false);
});


test("malformed mutation output is unknown because the backend may already have acted", async () => {
  const receipt = await runCrmTool(
    { operation: "create_lead", arguments: { name: "Jordan" } },
    context,
    async () => ({ stdout: "truncated after dispatch", stderr: "" }),
  );

  assert.deepEqual(receipt.error, {
    code: "outcome_unknown",
    message: "CRM mutation outcome is unknown",
    retryable: false,
  });
});


for (const [name, operation, error, expected] of [
  [
    "404", "get_lead_context",
    { code: 2, stderr: '{"ok":false,"error":{"code":"not_found","message":"CRM record was not found","retryable":false}}' },
    { code: "not_found", message: "CRM record was not found", retryable: false },
  ],
  [
    "409", "book_appointment",
    { code: 2, stderr: '{"ok":false,"error":{"code":"schedule_conflict","message":"Requested schedule conflicts with an existing appointment","retryable":false}}' },
    { code: "schedule_conflict", message: "Requested schedule conflicts with an existing appointment", retryable: false },
  ],
  [
    "backend unavailability", "list_leads",
    { code: 2, stderr: '{"ok":false,"error":{"code":"backend_unavailable","message":"CRM backend is unavailable","retryable":true}}' },
    { code: "backend_unavailable", message: "CRM backend is unavailable", retryable: true },
  ],
  [
    "invalid arguments", "create_lead",
    { code: 2, stderr: '{"ok":false,"error":{"code":"invalid_arguments","message":"Unsupported argument: source_note","retryable":false}}' },
    { code: "invalid_arguments", message: "Unsupported argument: source_note", retryable: false },
  ],
]) {
  test(`returns the structured CLI ${name} receipt without exposing child errors`, async () => {
    const failure = Object.assign(new Error("token=secret /home/person/cli.py"), error);
    assert.deepEqual(
      await runCrmTool({ operation }, context, childFailure(failure)),
      { ok: false, operation, kind: "error", error: expected },
    );
  });
}


test("returns a safe failure receipt for invalid wrapper JSON", async () => {
  assert.deepEqual(
    await runCrmTool(
      { operation: "list_leads" }, context,
      async () => ({ stdout: "secret non-json output", stderr: "" }),
    ),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "operation_failed", message: "CRM operation failed", retryable: false },
    },
  );
});


test("returns a safe failure receipt for an unknown child failure", async () => {
  const failure = Object.assign(new Error("token=secret /home/person/cli.py"), {
    code: 2, stderr: "unparseable private stderr",
  });
  assert.deepEqual(
    await runCrmTool({ operation: "list_leads" }, context, childFailure(failure)),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "operation_failed", message: "CRM operation failed", retryable: false },
    },
  );
});


test("does not classify a signal-only child failure as a timeout", async () => {
  const failure = Object.assign(new Error("child crashed"), { signal: "SIGABRT" });
  assert.deepEqual(
    await runCrmTool({ operation: "list_leads" }, context, childFailure(failure)),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "operation_failed", message: "CRM operation failed", retryable: false },
    },
  );
});


test("does not classify a message-only maxBuffer child failure as oversized output", async () => {
  const failure = new Error("maxBuffer label appeared in a child failure");
  assert.deepEqual(
    await runCrmTool({ operation: "list_leads" }, context, childFailure(failure)),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "operation_failed", message: "CRM operation failed", retryable: false },
    },
  );
});


test("returns a safe result-too-large receipt", async () => {
  const error = Object.assign(new Error("stdout maxBuffer length exceeded"), {
    code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
  });
  assert.deepEqual(
    await runCrmTool({ operation: "list_leads" }, context, childFailure(error)),
    {
      ok: false,
      operation: "list_leads",
      kind: "error",
      error: { code: "result_too_large", message: "CRM operation returned too much data", retryable: false },
    },
  );
});
