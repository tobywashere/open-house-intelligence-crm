import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createPluginDefinition } from "../dist/definition.js";


const root = new URL("../", import.meta.url);


function registerPlugin(executeCrm = async () => ({
  ok: true,
  operation: "list_leads",
  kind: "read",
  result: [],
})) {
  const registrations = [];
  const hooks = new Map();
  const plugin = createPluginDefinition(executeCrm);
  plugin.register({
    registerTool: (...args) => registrations.push(args),
    on: (name, handler, options) => {
      hooks.set(name, { handler, options });
    },
  });
  return { hooks, plugin, registrations };
}


function pendingReceipt() {
  return {
    ok: true,
    operation: "create_lead",
    kind: "proposal",
    result: {
      pending: true,
      id: 4,
      operation: "create_lead",
      summary: "Create lead Jordan Ellis",
      status: "pending",
    },
  };
}


function toolResult(details) {
  return {
    content: [{ type: "text", text: JSON.stringify(details) }],
    details,
  };
}


test("plugin registers exactly one required CRM tool factory", async () => {
  const registrations = [];
  const receipt = {
    ok: true,
    operation: "list_leads",
    kind: "read",
    result: [{ id: 4, name: "Chris" }],
  };
  const plugin = createPluginDefinition(async () => receipt);
  plugin.register({
    registerTool: (...args) => registrations.push(args),
    on: () => {},
  });

  assert.equal(plugin.id, "openhouse-crm");
  assert.equal(registrations.length, 1);
  const [factory, metadata] = registrations[0];
  assert.equal(typeof factory, "function");
  assert.deepEqual(metadata, { name: "openhouse_crm" });

  const tool = factory({ workspaceDir: "/trusted/workspace" });
  assert.equal(tool.name, "openhouse_crm");
  const contract = JSON.parse(
    await readFile(new URL("../../../skills/crm-db-operations/contract.json", import.meta.url)),
  );
  assert.equal(tool.parameters.oneOf.length, Object.keys(contract.operations).length);
  const createBranch = tool.parameters.oneOf.find(
    (branch) => branch.properties.operation.const === "create_lead",
  );
  assert.equal(createBranch.type, "object");
  assert.equal(createBranch.additionalProperties, false);
  assert.deepEqual(createBranch.required, ["operation", "arguments"]);
  assert.equal(createBranch.properties.arguments.additionalProperties, false);
  assert.equal(createBranch.properties.arguments.properties.source_note, undefined);
  assert.equal(createBranch.properties.arguments.properties.status, undefined);

  const result = await tool.execute("call-id", {
    operation: "list_leads",
    arguments: { sort: "priority" },
  });
  assert.deepEqual(result.details, receipt);
  assert.deepEqual(result.content, [
    { type: "text", text: JSON.stringify(receipt) },
  ]);
});


test("plugin registers the four supported scoped hooks", () => {
  const { hooks } = registerPlugin();

  assert.deepEqual([...hooks.keys()], [
    "before_tool_call",
    "after_tool_call",
    "reply_payload_sending",
    "gateway_stop",
  ]);
  assert.deepEqual(hooks.get("before_tool_call").options, { matcher: ["openhouse_crm"] });
  assert.deepEqual(hooks.get("after_tool_call").options, { matcher: ["openhouse_crm"] });
  assert.equal(hooks.get("reply_payload_sending").options, undefined);
  assert.equal(hooks.get("gateway_stop").options, undefined);
});


test("before-tool hook blocks only internal CRM calls from the exact dashboard marker", () => {
  const { hooks } = registerPlugin();
  const beforeToolCall = hooks.get("before_tool_call").handler;

  assert.deepEqual(
    beforeToolCall(
      { toolName: "openhouse_crm", params: {} },
      { toolName: "openhouse_crm", requester: { channel: "openhouse-dashboard" } },
    ),
    {
      block: true,
      blockReason: "Dashboard CRM calls must use the verified tool invocation path.",
    },
  );
  for (const ctx of [
    { toolName: "openhouse_crm", requester: { channel: "discord" } },
    { toolName: "openhouse_crm", requester: { channel: "slack" } },
    { toolName: "openhouse_crm", requester: { channel: "Openhouse-Dashboard" } },
    { toolName: "openhouse_crm" },
  ]) {
    assert.equal(beforeToolCall({ toolName: "openhouse_crm", params: {} }, ctx), undefined);
  }
  assert.equal(
    beforeToolCall(
      { toolName: "other_tool", params: {} },
      { toolName: "other_tool", requester: { channel: "openhouse-dashboard" } },
    ),
    undefined,
  );
});


test("hooks record only structured proposal outcomes for the exact CRM tool, run, and agent", () => {
  const { hooks } = registerPlugin();
  const afterToolCall = hooks.get("after_tool_call").handler;
  const replyPayloadSending = hooks.get("reply_payload_sending").handler;
  const proposal = pendingReceipt();
  const crmContext = { toolName: "openhouse_crm", runId: "run-record", agentId: "openhouse-crm" };

  afterToolCall(
    { toolName: "other_tool", runId: "run-record", params: {}, result: toolResult(proposal) },
    crmContext,
  );
  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-record",
      params: {},
      result: { content: [{ type: "text", text: JSON.stringify(proposal) }] },
    },
    crmContext,
  );
  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-record",
      params: {},
      result: toolResult({ ok: true, operation: "list_leads", kind: "read", result: [] }),
    },
    crmContext,
  );
  afterToolCall(
    { toolName: "openhouse_crm", runId: "run-record", params: {}, result: toolResult(proposal) },
    { ...crmContext, agentId: "other-agent" },
  );
  afterToolCall(
    { toolName: "openhouse_crm", params: {}, result: toolResult(proposal) },
    { toolName: "openhouse_crm", agentId: "openhouse-crm" },
  );

  const noRecord = replyPayloadSending(
    {
      payload: { text: "Original" },
      kind: "final",
      runId: "run-record",
      usageState: { agentId: "openhouse-crm" },
    },
    { channelId: "discord-channel", runId: "run-record" },
  );
  assert.equal(noRecord, undefined);

  afterToolCall(
    { toolName: "openhouse_crm", runId: "run-record", params: {}, result: toolResult(proposal) },
    crmContext,
  );
  assert.deepEqual(
    replyPayloadSending(
      {
        payload: { text: "Original" },
        kind: "final",
        runId: "run-record",
        usageState: { agentId: "openhouse-crm" },
      },
      { channelId: "discord-channel", runId: "run-record" },
    ),
    { payload: { text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis." } },
  );
});


test("reply hook replaces only text on the latest payload for the matching CRM run", () => {
  const { hooks } = registerPlugin();
  const afterToolCall = hooks.get("after_tool_call").handler;
  const replyPayloadSending = hooks.get("reply_payload_sending").handler;
  afterToolCall(
    { toolName: "openhouse_crm", runId: "run-payload", params: {}, result: toolResult(pendingReceipt()) },
    { toolName: "openhouse_crm", runId: "run-payload", agentId: "openhouse-crm" },
  );
  const media = [{ type: "image", url: "https://example.invalid/photo.jpg" }];
  const metadata = { audit: "keep" };
  const payload = {
    text: "Done",
    media,
    to: "discord-channel-42",
    accountId: "discord-main",
    threadId: "thread-9",
    metadata,
    presentation: { mode: "compact" },
    delivery: { replyToId: "message-8" },
    custom: 17,
  };

  const wrongAgent = replyPayloadSending(
    {
      payload,
      kind: "final",
      runId: "run-payload",
      usageState: { agentId: "other-agent" },
    },
    { channelId: "discord-channel", runId: "run-payload" },
  );
  assert.equal(wrongAgent, undefined);

  const rewritten = replyPayloadSending(
    {
      payload,
      kind: "final",
      runId: "run-payload",
      usageState: { agentId: "openhouse-crm" },
    },
    { channelId: "discord-channel", runId: "run-payload" },
  );
  assert.notStrictEqual(rewritten.payload, payload);
  assert.deepEqual(rewritten, {
    payload: {
      ...payload,
      text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
    },
  });
  assert.strictEqual(rewritten.payload.media, media);
  assert.strictEqual(rewritten.payload.metadata, metadata);
  assert.equal(payload.text, "Done");

  assert.equal(
    replyPayloadSending(
      {
        payload: { ...payload, text: "Second payload" },
        kind: "final",
        runId: "run-payload",
        usageState: { agentId: "openhouse-crm" },
      },
      { channelId: "discord-channel", runId: "run-payload" },
    ),
    undefined,
  );
});


test("gateway stop clears all pending in-memory outcomes", () => {
  const { hooks } = registerPlugin();
  hooks.get("after_tool_call").handler(
    { toolName: "openhouse_crm", runId: "run-stop", params: {}, result: toolResult(pendingReceipt()) },
    { toolName: "openhouse_crm", runId: "run-stop", agentId: "openhouse-crm" },
  );
  hooks.get("gateway_stop").handler({ reason: "restart" }, {});

  assert.equal(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "After restart" },
        kind: "final",
        runId: "run-stop",
        usageState: { agentId: "openhouse-crm" },
      },
      { channelId: "discord-channel", runId: "run-stop" },
    ),
    undefined,
  );
});


test("manifest declares exact tool ownership and no configuration", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("openclaw.plugin.json", root), "utf8"),
  );
  assert.equal(manifest.id, "openhouse-crm");
  assert.deepEqual(manifest.contracts, { tools: ["openhouse_crm"] });
  assert.equal(manifest.toolMetadata, undefined);
  assert.deepEqual(manifest.activation, {
    onStartup: true,
    onCapabilities: ["hook"],
  });
  assert.deepEqual(manifest.configSchema, {
    type: "object",
    additionalProperties: false,
  });
});


test("package ships built ESM with no install-time or runtime dependencies", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("package.json", root), "utf8"),
  );
  assert.equal(packageJson.type, "module");
  assert.deepEqual(packageJson.openclaw.extensions, ["./dist/index.js"]);
  assert.equal(packageJson.dependencies, undefined);
  assert.equal(packageJson.optionalDependencies, undefined);
  assert.equal(packageJson.scripts?.install, undefined);
  assert.equal(packageJson.files.includes("operations.json"), false);
  assert.match(packageJson.peerDependencies.openclaw, /^>=2026\./);
});


test("runtime entry uses the supported focused plugin SDK entrypoint", async () => {
  const source = await readFile(new URL("dist/index.js", root), "utf8");
  assert.match(source, /openclaw\/plugin-sdk\/plugin-entry/);
  assert.match(source, /definePluginEntry/);
  assert.doesNotMatch(source, /typebox|child_process|exec\s*\(/i);
});
