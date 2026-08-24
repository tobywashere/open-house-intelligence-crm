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
}), pluginConfig) {
  const registrations = [];
  const hooks = new Map();
  const plugin = createPluginDefinition(executeCrm);
  plugin.register({
    pluginConfig,
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


function unknownMutationReceipt() {
  return {
    ok: false,
    operation: "create_lead",
    kind: "error",
    error: {
      code: "outcome_unknown",
      message: "CRM mutation outcome is unknown",
      retryable: false,
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
  assert.equal(hooks.get("before_tool_call").options, undefined);
  assert.deepEqual(hooks.get("after_tool_call").options, { matcher: ["openhouse_crm"] });
  assert.equal(hooks.get("reply_payload_sending").options, undefined);
  assert.equal(hooks.get("gateway_stop").options, undefined);
});


test("before-tool hook blocks every native tool on analysis and verified dashboard channels", () => {
  const { hooks } = registerPlugin();
  const beforeToolCall = hooks.get("before_tool_call").handler;

  for (const channel of ["openhouse-dashboard", "openhouse-analysis"]) {
    for (const toolName of ["openhouse_crm", "exec", "session_status", "web_search"]) {
      const blocked = beforeToolCall(
        { toolName, params: {} },
        { toolName, requester: { channel } },
      );
      assert.equal(blocked.block, true);
      assert.match(blocked.blockReason, /must not execute native tools/i);
    }
  }
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
    ).block,
    true,
  );
});


test("setup probe registers one config-gated marker tool without changing normal inventory", () => {
  const normal = registerPlugin();
  assert.deepEqual(normal.registrations.map(([, metadata]) => metadata.name), [
    "openhouse_crm",
  ]);

  const diagnostic = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: {
      agentId: "openhouse-setup-probe-a1b2c3d4",
      nonce: "0123456789abcdef0123456789abcdef",
    },
  });
  assert.deepEqual(diagnostic.registrations.map(([, metadata]) => metadata.name), [
    "openhouse_crm",
    "openhouse_setup_marker_probe",
  ]);
});


for (const channel of ["openhouse-dashboard", "openhouse-analysis"]) {
  test(`setup probe records and proves a blocked ${channel} native-tool attempt`, async () => {
    const nonce = "0123456789abcdef0123456789abcdef";
    const agentId = "openhouse-setup-probe-a1b2c3d4";
    const { hooks, registrations } = registerPlugin(undefined, {
      agentId: "portable-crm",
      setupProbe: { agentId, nonce },
    });
    const beforeToolCall = hooks.get("before_tool_call").handler;
    const blocked = beforeToolCall(
      {
        toolName: "openhouse_setup_marker_probe",
        params: { action: "attempt", channel, nonce },
      },
      { agentId, requester: { channel } },
    );
    assert.equal(blocked.block, true);

    assert.equal(
      beforeToolCall(
        {
          toolName: "openhouse_setup_marker_probe",
          params: { action: "status", channel, nonce },
        },
        {
          agentId,
          requester: { channel: "openhouse-setup-capability" },
        },
      ),
      undefined,
    );

    const [factory] = registrations.find(
      ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
    );
    const result = await factory({}).execute("status-call", {
      action: "status",
      channel,
      nonce,
    });
    assert.deepEqual(result.details, {
      schema_version: 1,
      channel,
      nonce,
      status: "tool_blocked",
    });
  });
}


test("setup probe distinguishes a missing marker that allowed sentinel execution", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const { hooks, registrations } = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  });
  const [factory] = registrations.find(
    ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
  );
  const tool = factory({});
  assert.equal(
    hooks.get("before_tool_call").handler(
      {
        toolName: "openhouse_setup_marker_probe",
        params: { action: "attempt", channel: "openhouse-dashboard", nonce },
      },
      { agentId, requester: { channel: "generic-chat" } },
    ),
    undefined,
  );
  await tool.execute("attempt-call", {
    action: "attempt",
    channel: "openhouse-dashboard",
    nonce,
  });

  const result = await tool.execute("status-call", {
    action: "status",
    channel: "openhouse-dashboard",
    nonce,
  });
  assert.equal(result.details.status, "sentinel_executed");
});


test("setup probe distinguishes no tool attempt from malformed hook context", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const { hooks, registrations } = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  });
  const [factory] = registrations.find(
    ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
  );
  const tool = factory({});

  const untouched = await tool.execute("status-call", {
    action: "status",
    channel: "openhouse-dashboard",
    nonce,
  });
  assert.equal(untouched.details.status, "tool_not_attempted");

  const blocked = hooks.get("before_tool_call").handler(
    {
      toolName: "openhouse_setup_marker_probe",
      params: { action: "attempt", channel: "openhouse-analysis", nonce },
    },
    { agentId },
  );
  assert.equal(blocked.block, true);
  const malformed = await tool.execute("status-call", {
    action: "status",
    channel: "openhouse-analysis",
    nonce,
  });
  assert.equal(malformed.details.status, "hook_context_unsupported");
});


test("Discord blocks a later mutation in the same run after an unknown outcome", () => {
  const { hooks } = registerPlugin();
  const beforeToolCall = hooks.get("before_tool_call").handler;
  hooks.get("after_tool_call").handler(
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-gate",
      params: { operation: "create_lead", arguments: { name: "Jordan" } },
      result: toolResult(unknownMutationReceipt()),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-gate",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
  );

  const blocked = beforeToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-gate",
      params: { operation: "book_appointment", arguments: {} },
    },
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-gate",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
  );

  assert.equal(blocked.block, true);
  assert.match(blocked.blockReason, /earlier CRM mutation outcome is unknown/i);
  assert.match(blocked.blockReason, /CRM and Pending approvals/i);
  assert.deepEqual(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "Retried it" },
        kind: "final",
        runId: "run-unknown-gate",
        usageState: { agentId: "openhouse-crm" },
      },
      { channel: "discord", channelId: "discord-channel", runId: "run-unknown-gate" },
    ),
    {
      payload: {
        text: "The create lead CRM change may have reached the backend, but its result could not be verified.\n"
          + "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.",
      },
    },
  );
});


test("Discord still allows reads after an unknown mutation and preserves the unknown output", () => {
  const { hooks } = registerPlugin();
  const afterToolCall = hooks.get("after_tool_call").handler;
  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-read",
      params: { operation: "create_lead", arguments: { name: "Jordan" } },
      result: toolResult(unknownMutationReceipt()),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-read",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
  );

  assert.equal(
    hooks.get("before_tool_call").handler(
      {
        toolName: "openhouse_crm",
        runId: "run-unknown-read",
        params: { operation: "list_leads", arguments: {} },
      },
      {
        toolName: "openhouse_crm",
        runId: "run-unknown-read",
        agentId: "openhouse-crm",
        requester: { channel: "discord" },
      },
    ),
    undefined,
  );
  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-read",
      params: { operation: "list_leads", arguments: {} },
      result: toolResult({ ok: true, operation: "list_leads", kind: "read", result: [] }),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-unknown-read",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
  );

  assert.deepEqual(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "No leads" },
        kind: "final",
        runId: "run-unknown-read",
        usageState: { agentId: "openhouse-crm" },
      },
      { channel: "discord", channelId: "discord-channel", runId: "run-unknown-read" },
    ),
    {
      payload: {
        text: "The create lead CRM change may have reached the backend, but its result could not be verified.\n"
          + "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.",
      },
    },
  );
});


test("hooks record only structured proposal outcomes for the exact CRM tool, run, and agent", () => {
  const { hooks } = registerPlugin();
  const afterToolCall = hooks.get("after_tool_call").handler;
  const replyPayloadSending = hooks.get("reply_payload_sending").handler;
  const proposal = pendingReceipt();
  const crmContext = {
    toolName: "openhouse_crm",
    runId: "run-record",
    agentId: "openhouse-crm",
    requester: { channel: "discord" },
  };

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
    { channel: "discord", channelId: "discord-channel", runId: "run-record" },
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
      { channel: "discord", channelId: "discord-channel", runId: "run-record" },
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
    {
      toolName: "openhouse_crm",
      runId: "run-payload",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
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

  const contradictoryAgent = replyPayloadSending(
    {
      payload,
      kind: "final",
      runId: "run-payload",
      usageState: { agentId: "other-agent" },
    },
    { channel: "discord", channelId: "discord-channel", runId: "run-payload" },
  );
  assert.deepEqual(contradictoryAgent, {
    payload: {
      ...payload,
      text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
    },
  });

  const rewritten = replyPayloadSending(
    {
      payload,
      kind: "final",
      runId: "run-payload",
      usageState: { agentId: "openhouse-crm" },
    },
    { channel: "discord", channelId: "discord-channel", runId: "run-payload" },
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

  assert.deepEqual(
    replyPayloadSending(
      {
        payload: { ...payload, text: "Second payload" },
        kind: "final",
        runId: "run-payload",
        usageState: { agentId: "openhouse-crm" },
      },
      { channel: "discord", channelId: "discord-channel", runId: "run-payload" },
    ),
    {
      payload: {
        ...payload,
        text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
      },
    },
  );
});


test("reply hook uses configured agent state when usage metadata is omitted", () => {
  const { hooks } = registerPlugin(undefined, { agentId: "custom-crm" });
  hooks.get("after_tool_call").handler(
    {
      toolName: "openhouse_crm",
      runId: "run-optional-usage",
      params: {},
      result: toolResult(pendingReceipt()),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-optional-usage",
      agentId: "custom-crm",
      requester: { channel: "discord" },
    },
  );

  assert.deepEqual(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "Unverified model success" },
        kind: "final",
        runId: "run-optional-usage",
      },
      { channel: "discord", runId: "run-optional-usage" },
    ),
    {
      payload: {
        text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
      },
    },
  );
});


test("unknown Discord state survives every payload and remains blocked until cleanup", () => {
  const { hooks } = registerPlugin();
  const beforeToolCall = hooks.get("before_tool_call").handler;
  hooks.get("after_tool_call").handler(
    {
      toolName: "openhouse_crm",
      runId: "run-sticky-unknown",
      params: { operation: "create_lead", arguments: {} },
      result: toolResult(unknownMutationReceipt()),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-sticky-unknown",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
  );
  const safeText = "The create lead CRM change may have reached the backend, "
    + "but its result could not be verified.\n"
    + "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.";

  for (const usageState of [undefined, { agentId: "different-agent" }, { agentId: "openhouse-crm" }]) {
    const event = {
      payload: { text: "Created successfully" },
      kind: "final",
      runId: "run-sticky-unknown",
    };
    if (usageState !== undefined) event.usageState = usageState;
    assert.deepEqual(
      hooks.get("reply_payload_sending").handler(
        event,
        { channel: "discord", runId: "run-sticky-unknown" },
      ),
      { payload: { text: safeText } },
    );
    assert.equal(
      beforeToolCall(
        {
          toolName: "openhouse_crm",
          runId: "run-sticky-unknown",
          params: { operation: "book_appointment", arguments: {} },
        },
        {
          toolName: "openhouse_crm",
          runId: "run-sticky-unknown",
          agentId: "openhouse-crm",
          requester: { channel: "discord" },
        },
      )?.block,
      true,
    );
  }

  hooks.get("gateway_stop").handler({ reason: "restart" }, {});
  assert.equal(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "After cleanup" },
        kind: "final",
        runId: "run-sticky-unknown",
      },
      { channel: "discord", runId: "run-sticky-unknown" },
    ),
    undefined,
  );
  assert.equal(
    beforeToolCall(
      {
        toolName: "openhouse_crm",
        runId: "run-sticky-unknown",
        params: { operation: "book_appointment", arguments: {} },
      },
      {
        toolName: "openhouse_crm",
        runId: "run-sticky-unknown",
        agentId: "openhouse-crm",
        requester: { channel: "discord" },
      },
    ),
    undefined,
  );
});


test("gateway stop clears all pending in-memory outcomes", () => {
  const { hooks } = registerPlugin();
  hooks.get("after_tool_call").handler(
    { toolName: "openhouse_crm", runId: "run-stop", params: {}, result: toolResult(pendingReceipt()) },
    {
      toolName: "openhouse_crm",
      runId: "run-stop",
      agentId: "openhouse-crm",
      requester: { channel: "discord" },
    },
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
      { channel: "discord", channelId: "discord-channel", runId: "run-stop" },
    ),
    undefined,
  );
});


test("manifest declares exact tool ownership and validated agent configuration", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("openclaw.plugin.json", root), "utf8"),
  );
  assert.equal(manifest.id, "openhouse-crm");
  assert.deepEqual(manifest.contracts, {
    tools: ["openhouse_crm", "openhouse_setup_marker_probe"],
  });
  assert.deepEqual(manifest.toolMetadata, {
    openhouse_setup_marker_probe: {
      optional: true,
    },
  });
  assert.deepEqual(manifest.activation, {
    onStartup: true,
    onCapabilities: ["hook"],
  });
  assert.deepEqual(manifest.configSchema, {
    type: "object",
    additionalProperties: false,
    properties: {
      agentId: {
        type: "string",
        pattern: "^[a-z0-9][a-z0-9_-]{0,63}$",
      },
      setupProbe: {
        type: "object",
        additionalProperties: false,
        required: ["agentId", "nonce"],
        properties: {
          agentId: {
            type: "string",
            pattern: "^[a-z0-9][a-z0-9_-]{0,63}$",
          },
          nonce: {
            type: "string",
            pattern: "^[a-f0-9]{32}$",
          },
        },
      },
    },
  });
});


test("configured CRM agent is guarded while the default and other agents are ignored", () => {
  const { hooks } = registerPlugin(undefined, { agentId: "custom-crm" });
  const afterToolCall = hooks.get("after_tool_call").handler;
  const discordContext = {
    toolName: "openhouse_crm",
    runId: "run-custom",
    agentId: "custom-crm",
    requester: { channel: "discord" },
  };
  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-custom",
      params: { operation: "create_lead", arguments: {} },
      result: toolResult(pendingReceipt()),
    },
    { ...discordContext, agentId: "openhouse-crm" },
  );
  assert.equal(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "Default agent" },
        kind: "final",
        runId: "run-custom",
        usageState: { agentId: "openhouse-crm" },
      },
      { runId: "run-custom", channel: "discord" },
    ),
    undefined,
  );

  afterToolCall(
    {
      toolName: "openhouse_crm",
      runId: "run-custom",
      params: { operation: "create_lead", arguments: {} },
      result: toolResult(pendingReceipt()),
    },
    discordContext,
  );
  assert.deepEqual(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "Custom agent" },
        kind: "final",
        runId: "run-custom",
        usageState: { agentId: "custom-crm" },
      },
      { runId: "run-custom", channel: "discord" },
    ),
    {
      payload: {
        text: "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
      },
    },
  );
});


test("setup can safely prove the configured custom agent guard without executing CRM", () => {
  const { hooks } = registerPlugin(undefined, { agentId: "custom-crm" });
  const beforeToolCall = hooks.get("before_tool_call").handler;
  const event = {
    toolName: "openhouse_crm",
    params: { operation: "__openhouse_agent_guard_probe__", arguments: {} },
  };
  const requester = { channel: "openhouse-setup-agent-guard" };

  assert.deepEqual(
    beforeToolCall(event, { agentId: "custom-crm", requester }),
    {
      block: true,
      blockReason: "Configured CRM agent custom-crm is protected.",
    },
  );
  assert.equal(
    beforeToolCall(event, { agentId: "openhouse-crm", requester }),
    undefined,
  );
});


test("non-Discord tool outcomes cannot enter Discord mutation state", () => {
  const { hooks } = registerPlugin();
  hooks.get("after_tool_call").handler(
    {
      toolName: "openhouse_crm",
      runId: "run-channel-scope",
      params: { operation: "create_lead", arguments: {} },
      result: toolResult(pendingReceipt()),
    },
    {
      toolName: "openhouse_crm",
      runId: "run-channel-scope",
      agentId: "openhouse-crm",
      requester: { channel: "slack" },
    },
  );

  assert.equal(
    hooks.get("reply_payload_sending").handler(
      {
        payload: { text: "Discord reply" },
        kind: "final",
        runId: "run-channel-scope",
        usageState: { agentId: "openhouse-crm" },
      },
      { runId: "run-channel-scope", channel: "discord" },
    ),
    undefined,
  );
});


test("invalid configured agent IDs fail plugin registration instead of falling back", () => {
  assert.throws(
    () => registerPlugin(undefined, { agentId: "Custom CRM" }),
    /configured CRM agent ID/i,
  );
  assert.throws(
    () => registerPlugin(undefined, { agentId: 123 }),
    /configured CRM agent ID/i,
  );
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
  assert.equal(packageJson.peerDependencies.openclaw, ">=2026.8.1-beta.2");
  assert.deepEqual(packageJson.openclaw.compat, {
    pluginApi: ">=2026.8.1-beta.2",
    minGatewayVersion: "2026.8.1-beta.2",
  });
  assert.deepEqual(packageJson.openclaw.build, {
    openclawVersion: "2026.8.1-beta.2",
    pluginSdkVersion: "2026.8.1-beta.2",
  });
});


test("runtime entry uses the supported focused plugin SDK entrypoint", async () => {
  const source = await readFile(new URL("dist/index.js", root), "utf8");
  assert.match(source, /openclaw\/plugin-sdk\/plugin-entry/);
  assert.match(source, /definePluginEntry/);
  assert.doesNotMatch(source, /typebox|child_process|exec\s*\(/i);
});
