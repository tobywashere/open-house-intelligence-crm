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


function recordSetupChannelPrompt(hooks, { agentId, nonce, runId, sessionKey, channel }) {
  return hooks.get("before_agent_reply").handler(
    { cleanedBody: `Setup channel probe ${nonce}` },
    { agentId, runId, sessionKey, channel, trigger: "user" },
  );
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


test("plugin registers the five supported scoped hooks", () => {
  const { hooks } = registerPlugin();

  assert.deepEqual([...hooks.keys()], [
    "before_agent_reply",
    "before_tool_call",
    "after_tool_call",
    "reply_payload_sending",
    "gateway_stop",
  ]);
  assert.deepEqual(hooks.get("before_agent_reply").options, {
    eligibleTriggers: ["user"],
  });
  assert.equal(hooks.get("before_tool_call").options, undefined);
  assert.deepEqual(hooks.get("after_tool_call").options, { matcher: ["openhouse_crm"] });
  assert.equal(hooks.get("reply_payload_sending").options, undefined);
  assert.equal(hooks.get("gateway_stop").options, undefined);
});


test("setup channel proof is acknowledged before the model provider runs", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const channel = "openhouse-dashboard";
  const sessionKey = `agent:${agentId}:dashboard:openhouse-setup-test`;
  const { hooks } = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  });

  const beforeAgentReply = hooks.get("before_agent_reply");
  assert.ok(beforeAgentReply);
  assert.deepEqual(beforeAgentReply.options, { eligibleTriggers: ["user"] });
  const reply = await beforeAgentReply.handler(
    {
      cleanedBody:
        `[OpenClaw channel context]\nSetup channel probe ${nonce}\n[/OpenClaw channel context]`,
    },
    {
      agentId,
      runId: "setup-channel-run",
      sessionKey,
      channel,
      trigger: "user",
    },
  );
  assert.deepEqual(reply, {
    handled: true,
    reply: {
      text: `OpenHouse setup channel ${channel} nonce ${nonce} session ${sessionKey} verified.`,
    },
    reason: "openhouse_setup_channel_marker",
  });
});


test("setup channel proof never claims ordinary or uncorrelated turns", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const exactEvent = { cleanedBody: `Setup channel probe ${nonce}` };
  const exactContext = {
    agentId,
    runId: "setup-channel-run",
    sessionKey: `agent:${agentId}:dashboard:openhouse-setup-test`,
    channel: "openhouse-dashboard",
    trigger: "user",
  };
  const cases = [
    ["wrong body", { cleanedBody: "List my CRM leads" }, exactContext],
    ["wrong agent", exactEvent, { ...exactContext, agentId: "openhouse-crm" }],
    ["missing run", exactEvent, { ...exactContext, runId: undefined }],
    ["missing session", exactEvent, { ...exactContext, sessionKey: undefined }],
    ["wrong channel", exactEvent, { ...exactContext, channel: "discord" }],
    ["wrong trigger", exactEvent, { ...exactContext, trigger: "heartbeat" }],
  ];

  for (const [name, event, context] of cases) {
    const { hooks } = registerPlugin(undefined, {
      agentId: "portable-crm",
      setupProbe: { agentId, nonce },
    });
    assert.equal(
      await hooks.get("before_agent_reply").handler(event, context),
      undefined,
      name,
    );
  }

  const normal = registerPlugin();
  assert.equal(
    await normal.hooks.get("before_agent_reply").handler(exactEvent, exactContext),
    undefined,
  );
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


test("production channel proof requires a canonical blocked read and is single-use", () => {
  let executions = 0;
  const { hooks } = registerPlugin(async () => {
    executions += 1;
    return { ok: true, operation: "generate_dashboard_insights", kind: "read", result: {} };
  }, { agentId: "portable-crm" });
  const beforeToolCall = hooks.get("before_tool_call").handler;
  const nonce = "0123456789abcdef0123456789abcdef";
  const statusEvent = {
    toolName: "openhouse_crm",
    params: {
      operation: "__openhouse_behavior_probe_status__",
      arguments: { channel: "openhouse-dashboard", nonce },
    },
  };
  const statusContext = {
    agentId: "portable-crm",
    requester: { channel: "openhouse-setup-capability" },
  };

  assert.match(
    beforeToolCall(statusEvent, statusContext).blockReason,
    /not proven/i,
  );
  const blocked = beforeToolCall(
    {
      toolName: "openhouse_crm",
      params: {
        operation: "generate_dashboard_insights",
        arguments: { probe_nonce: nonce },
      },
    },
    {
      agentId: "portable-crm",
      requester: { channel: "openhouse-dashboard" },
    },
  );
  assert.equal(blocked.block, true);
  assert.equal(executions, 0);
  assert.equal(
    beforeToolCall(statusEvent, { agentId: "portable-crm" }).blockReason,
    `Production CRM channel openhouse-dashboard nonce ${nonce} is protected.`,
  );
  assert.match(
    beforeToolCall(statusEvent, statusContext).blockReason,
    /not proven/i,
  );
  assert.equal(
    beforeToolCall(statusEvent, {
      ...statusContext,
      agentId: "another-agent",
    }),
    undefined,
  );

  hooks.get("gateway_stop").handler();
  assert.match(
    beforeToolCall(statusEvent, statusContext).blockReason,
    /not proven/i,
  );
});


test("production channel proof fails if the canonical probe executor ran", () => {
  const { hooks } = registerPlugin(undefined, { agentId: "portable-crm" });
  const nonce = "fedcba9876543210fedcba9876543210";
  const event = {
    toolName: "openhouse_crm",
    params: {
      operation: "generate_dashboard_insights",
      arguments: { probe_nonce: nonce },
    },
  };
  const context = {
    agentId: "portable-crm",
    requester: { channel: "openhouse-analysis" },
  };
  hooks.get("before_tool_call").handler(event, context);
  hooks.get("after_tool_call").handler(
    { ...event, result: { details: { ok: true } } },
    context,
  );

  const status = hooks.get("before_tool_call").handler(
    {
      toolName: "openhouse_crm",
      params: {
        operation: "__openhouse_behavior_probe_status__",
        arguments: { channel: "openhouse-analysis", nonce },
      },
    },
    {
      agentId: "portable-crm",
      requester: { channel: "openhouse-setup-capability" },
    },
  );
  assert.match(status.blockReason, /not proven/i);
});


test("production channel proof fails as soon as the registered executor begins", async () => {
  let finishExecution;
  const executionStarted = new Promise((resolve) => {
    finishExecution = resolve;
  });
  const { hooks, registrations } = registerPlugin(
    () => executionStarted.then(() => ({
      ok: true,
      operation: "generate_dashboard_insights",
      kind: "read",
      result: {},
    })),
    { agentId: "portable-crm" },
  );
  const nonce = "abcdef0123456789abcdef0123456789";
  const params = {
    operation: "generate_dashboard_insights",
    arguments: { probe_nonce: nonce },
  };
  const toolContext = {
    agentId: "portable-crm",
    requester: { channel: "openhouse-dashboard" },
  };
  hooks.get("before_tool_call").handler(
    { toolName: "openhouse_crm", params },
    toolContext,
  );

  const tool = registrations[0][0]({ workspaceDir: "/trusted/workspace" });
  const execution = tool.execute("call-id", params);
  const status = hooks.get("before_tool_call").handler(
    {
      toolName: "openhouse_crm",
      params: {
        operation: "__openhouse_behavior_probe_status__",
        arguments: { channel: "openhouse-dashboard", nonce },
      },
    },
    {
      agentId: "portable-crm",
      requester: { channel: "openhouse-setup-capability" },
    },
  );
  assert.match(status.blockReason, /not proven/i);

  finishExecution();
  await execution;
});


test("production channel proof retains only a bounded set of fresh records", () => {
  const { hooks } = registerPlugin(undefined, { agentId: "portable-crm" });
  const beforeToolCall = hooks.get("before_tool_call").handler;
  const nonceFor = (index) => index.toString(16).padStart(32, "0");
  for (let index = 0; index < 65; index += 1) {
    beforeToolCall(
      {
        toolName: "openhouse_crm",
        params: {
          operation: "generate_dashboard_insights",
          arguments: { probe_nonce: nonceFor(index) },
        },
      },
      {
        agentId: "portable-crm",
        requester: { channel: "openhouse-dashboard" },
      },
    );
  }
  const status = (nonce) => beforeToolCall(
    {
      toolName: "openhouse_crm",
      params: {
        operation: "__openhouse_behavior_probe_status__",
        arguments: { channel: "openhouse-dashboard", nonce },
      },
    },
    {
      agentId: "portable-crm",
      requester: { channel: "openhouse-setup-capability" },
    },
  ).blockReason;

  assert.match(status(nonceFor(0)), /not proven/i);
  assert.match(status(nonceFor(64)), /is protected/i);
});


test("production probe implementation contains no noncanonical search_leads literal", async () => {
  const definition = await readFile(new URL("../dist/definition.js", import.meta.url), "utf8");
  assert.equal(definition.includes("search_leads"), false);
  assert.match(definition, /generate_dashboard_insights/);
  assert.match(definition, /probe_nonce/);
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
  test(`setup probe deterministically blocks a native ${channel} attempt`, () => {
    const nonce = "0123456789abcdef0123456789abcdef";
    const agentId = "openhouse-setup-probe-a1b2c3d4";
    const sessionKey = `agent:${agentId}:dashboard:openhouse-setup-test`;
    const { hooks } = registerPlugin(undefined, {
      agentId: "portable-crm",
      setupProbe: { agentId, nonce },
    });
    const blocked = hooks.get("before_tool_call").handler(
      {
        toolName: "openhouse_setup_marker_probe",
        params: { action: "attempt", channel, nonce, session_key: sessionKey },
      },
      { agentId, sessionKey, requester: { channel } },
    );
    assert.deepEqual(blocked, {
      block: true,
      blockReason:
        `OpenHouse setup sentinel ${channel} nonce ${nonce} session ${sessionKey} is blocked.`,
    });
  });
}


test("setup channel proof survives separate OpenClaw runtime instances", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const channel = "openhouse-dashboard";
  const sessionKey = `agent:${agentId}:dashboard:openhouse-setup-test`;
  const pluginConfig = {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  };

  const promptRuntime = registerPlugin(undefined, pluginConfig);
  const reply = await recordSetupChannelPrompt(promptRuntime.hooks, {
    agentId,
    nonce,
    runId: "prompt-runtime-run",
    sessionKey,
    channel,
  });
  assert.deepEqual(reply, {
    handled: true,
    reply: {
      text: `OpenHouse setup channel ${channel} nonce ${nonce} session ${sessionKey} verified.`,
    },
    reason: "openhouse_setup_channel_marker",
  });

  // OpenClaw may serve the direct tool request through another plugin/runtime
  // instance. Verification must not depend on an in-memory record from the
  // earlier chat request.
  const toolRuntime = registerPlugin(undefined, pluginConfig);
  const blocked = toolRuntime.hooks.get("before_tool_call").handler(
    {
      toolName: "openhouse_setup_marker_probe",
      params: { action: "attempt", channel, nonce, session_key: sessionKey },
    },
    { agentId, sessionKey, requester: { channel } },
  );
  assert.deepEqual(blocked, {
    block: true,
    blockReason:
      `OpenHouse setup sentinel ${channel} nonce ${nonce} session ${sessionKey} is blocked.`,
  });
});


test("setup probe fails closed for uncorrelated native attempts", () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const cases = [
    {
      name: "wrong agent",
      channel: "openhouse-dashboard",
      context: (sessionKey) => ({
        agentId: "other-agent",
        sessionKey,
        requester: { channel: "openhouse-dashboard" },
      }),
    },
    {
      name: "wrong session",
      channel: "openhouse-dashboard",
      context: () => ({
        agentId,
        sessionKey: "agent:other:dashboard:setup",
        requester: { channel: "openhouse-dashboard" },
      }),
    },
    {
      name: "wrong channel",
      channel: "openhouse-analysis",
      context: (sessionKey) => ({
        agentId,
        sessionKey,
        requester: { channel: "openhouse-dashboard" },
      }),
    },
    {
      name: "missing requester",
      channel: "openhouse-dashboard",
      context: (sessionKey) => ({ agentId, sessionKey }),
    },
  ];

  for (const scenario of cases) {
    const { hooks } = registerPlugin(undefined, {
      agentId: "portable-crm",
      setupProbe: { agentId, nonce },
    });
    const sessionKey = `agent:${agentId}:dashboard:${scenario.name}`;
    const blocked = hooks.get("before_tool_call").handler(
      {
        toolName: "openhouse_setup_marker_probe",
        params: {
          action: "attempt",
          channel: scenario.channel,
          nonce,
          session_key: sessionKey,
        },
      },
      scenario.context(sessionKey),
    );
    assert.deepEqual(blocked, {
      block: true,
      blockReason: "Setup marker probe hook context is unsupported.",
    });
  }
});


test("setup marker reports execution if the before-tool hook is bypassed", async () => {
  const nonce = "0123456789abcdef0123456789abcdef";
  const agentId = "openhouse-setup-probe-a1b2c3d4";
  const channel = "openhouse-dashboard";
  const sessionKey = `agent:${agentId}:dashboard:unexpected-execution`;
  const { registrations } = registerPlugin(undefined, {
    agentId: "portable-crm",
    setupProbe: { agentId, nonce },
  });
  const [factory] = registrations.find(
    ([, metadata]) => metadata.name === "openhouse_setup_marker_probe",
  );
  const result = await factory({}).execute("unexpected-execution", {
    action: "attempt",
    channel,
    nonce,
    session_key: sessionKey,
  });
  assert.deepEqual(result.details, {
    schema_version: 3,
    channel,
    nonce,
    session_key: sessionKey,
    sentinel_executed: true,
  });
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
  assert.deepEqual(
    beforeToolCall(event, { agentId: "custom-crm" }),
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
