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


test("returns a structured read and invokes only the fixed wrapper without a shell", async () => {
  let invocation;
  const child = async (file, args, options) => {
    invocation = { file, args, options };
    return { stdout: JSON.stringify({ ok: true, result: [{ id: 4, name: "Chris" }] }), stderr: "" };
  };

  const result = await runCrmTool(
    { operation: "list_leads", arguments: { sort: "priority" } },
    context,
    child,
  );

  assert.deepEqual(result, [{ id: 4, name: "Chris" }]);
  assert.equal(
    invocation.file,
    "/trusted/openhouse-workspace/skills/crm-db-operations/cli.py",
  );
  assert.deepEqual(invocation.args, [
    "list_leads",
    "--args",
    JSON.stringify({ sort: "priority" }),
  ]);
  assert.equal(invocation.options.shell, false);
  assert.equal(invocation.options.timeout, TOOL_TIMEOUT_MS);
  assert.equal(invocation.options.maxBuffer, MAX_OUTPUT_BYTES);
  assert.equal(invocation.options.encoding, "utf8");
  assert.equal("cwd" in invocation.options, false);
  assert.equal("env" in invocation.options, false);
});


test("returns the backend pending proposal without applying or rewriting it", async () => {
  const pending = {
    pending: true,
    id: 12,
    operation: "create_lead",
    summary: "Create lead Chris",
    status: "pending",
  };
  assert.deepEqual(
    await runCrmTool(
      { operation: "create_lead", arguments: { raw_text: "Chris" } },
      context,
      successfulChild(pending),
    ),
    pending,
  );
});


test("rejects unknown operations before starting a child", async () => {
  let called = false;
  await assert.rejects(
    runCrmTool({ operation: "run_anything", arguments: {} }, context, async () => {
      called = true;
    }),
    /not supported/,
  );
  assert.equal(called, false);
});


for (const [input, message] of [
  [null, "input"],
  [{}, "operation"],
  [{ operation: "list_leads", arguments: [] }, "arguments"],
  [{ operation: "list_leads", arguments: null }, "arguments"],
  [{ operation: "list_leads", arguments: { nested: undefined } }, "JSON-compatible"],
]) {
  test(`rejects malformed input containing ${message}`, async () => {
    await assert.rejects(runCrmTool(input, context, successfulChild([])), new RegExp(message));
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


test("rejects oversized arguments before starting a child", async () => {
  let called = false;
  await assert.rejects(
    runCrmTool(
      { operation: "search_knowledge", arguments: { query: "x".repeat(MAX_ARGUMENT_BYTES) } },
      context,
      async () => {
        called = true;
      },
    ),
    /too large/,
  );
  assert.equal(called, false);
});


for (const workspaceDir of [undefined, "", "."]) {
  test(`rejects an unusable trusted workspace ${String(workspaceDir)}`, async () => {
    await assert.rejects(
      runCrmTool({ operation: "list_leads" }, { workspaceDir }, successfulChild([])),
      /workspace is unavailable/,
    );
  });
}


test("sanitizes timeouts", async () => {
  const timeout = Object.assign(new Error("secret /home/person/token"), { killed: true });
  await assert.rejects(
    runCrmTool({ operation: "list_leads" }, context, async () => { throw timeout; }),
    /^Error: CRM operation timed out$/,
  );
});


test("sanitizes child failures", async () => {
  const failure = Object.assign(new Error("token=secret /home/person/cli.py"), {
    code: 2,
    stderr: '{"ok":false,"error":"private backend detail"}',
  });
  await assert.rejects(
    runCrmTool({ operation: "list_leads" }, context, async () => { throw failure; }),
    /^Error: CRM operation failed$/,
  );
});


test("rejects invalid wrapper JSON without returning raw output", async () => {
  await assert.rejects(
    runCrmTool(
      { operation: "list_leads" },
      context,
      async () => ({ stdout: "secret non-json output", stderr: "" }),
    ),
    /^Error: CRM operation returned an invalid response$/,
  );
});


test("rejects wrapper-declared errors without returning their private detail", async () => {
  await assert.rejects(
    runCrmTool(
      { operation: "list_leads" },
      context,
      async () => ({ stdout: '{"ok":false,"error":"token and private path"}', stderr: "" }),
    ),
    /^Error: CRM operation failed$/,
  );
});


test("rejects oversized returned output", async () => {
  const error = Object.assign(new Error("stdout maxBuffer length exceeded"), {
    code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
  });
  await assert.rejects(
    runCrmTool({ operation: "list_leads" }, context, async () => { throw error; }),
    /^Error: CRM operation returned too much data$/,
  );
});
