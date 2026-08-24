import { buildToolParameters, crmContract } from "./contract.js";
import { createOutcomeGuard } from "./outcome-guard.js";
import { runCrmTool } from "./runner.js";


const toolParameters = buildToolParameters(crmContract);
const TOOL_NAME = "openhouse_crm";
const DEFAULT_CRM_AGENT_ID = "openhouse-crm";
const AGENT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const DASHBOARD_CHANNEL = "openhouse-dashboard";
const INTERNAL_ANALYSIS_CHANNEL = "openhouse-analysis";
const SETUP_CAPABILITY_CHANNEL = "openhouse-setup-capability";
const SETUP_AGENT_GUARD_CHANNEL = "openhouse-setup-agent-guard";
const SETUP_AGENT_GUARD_OPERATION = "__openhouse_agent_guard_probe__";
const PRODUCTION_PROBE_OPERATION = "__openhouse_behavior_probe_status__";
const PRODUCTION_PROBE_QUERY_PREFIX = "__openhouse_behavior_probe__:";
const SETUP_MARKER_TOOL = "openhouse_setup_marker_probe";
const SETUP_NONCE_PATTERN = /^[a-f0-9]{32}$/;
const TOOL_BLOCKED_CHANNELS = new Set([
  DASHBOARD_CHANNEL,
  INTERNAL_ANALYSIS_CHANNEL,
]);


function exactRunId(event, context) {
  const eventRunId = event?.runId;
  const contextRunId = context?.runId;
  if (
    typeof eventRunId === "string"
    && typeof contextRunId === "string"
    && eventRunId !== contextRunId
  ) return undefined;
  return typeof eventRunId === "string" ? eventRunId : contextRunId;
}


function structuredReceipt(result) {
  if (result === null || typeof result !== "object" || Array.isArray(result)) return undefined;
  const details = result.details;
  return details !== null && typeof details === "object" && !Array.isArray(details)
    ? details
    : undefined;
}


function configuredAgentId(pluginConfig) {
  if (pluginConfig === undefined || pluginConfig === null) return DEFAULT_CRM_AGENT_ID;
  if (
    typeof pluginConfig !== "object"
    || Array.isArray(pluginConfig)
    || (
      pluginConfig.agentId !== undefined
      && (
        typeof pluginConfig.agentId !== "string"
        || !AGENT_ID_PATTERN.test(pluginConfig.agentId)
      )
    )
  ) {
    throw new TypeError("Configured CRM agent ID is invalid");
  }
  return pluginConfig.agentId ?? DEFAULT_CRM_AGENT_ID;
}


function configuredSetupProbe(pluginConfig) {
  const setupProbe = pluginConfig?.setupProbe;
  if (setupProbe === undefined) return undefined;
  if (
    setupProbe === null
    || typeof setupProbe !== "object"
    || Array.isArray(setupProbe)
    || Object.keys(setupProbe).length !== 2
    || typeof setupProbe.agentId !== "string"
    || !AGENT_ID_PATTERN.test(setupProbe.agentId)
    || typeof setupProbe.nonce !== "string"
    || !SETUP_NONCE_PATTERN.test(setupProbe.nonce)
  ) {
    throw new TypeError("Configured setup probe is invalid");
  }
  return setupProbe;
}


function setupProbeParameters(nonce) {
  return {
    type: "object",
    additionalProperties: false,
    required: ["action", "channel", "nonce"],
    properties: {
      action: { type: "string", enum: ["attempt", "status"] },
      channel: {
        type: "string",
        enum: [DASHBOARD_CHANNEL, INTERNAL_ANALYSIS_CHANNEL],
      },
      nonce: { const: nonce },
    },
  };
}


function validSetupProbeParams(params, nonce) {
  return (
    params !== null
    && typeof params === "object"
    && !Array.isArray(params)
    && Object.keys(params).length === 3
    && (params.action === "attempt" || params.action === "status")
    && TOOL_BLOCKED_CHANNELS.has(params.channel)
    && params.nonce === nonce
  );
}


export function createPluginDefinition(executeCrm = runCrmTool) {
  const outcomeGuard = createOutcomeGuard();
  return {
    id: "openhouse-crm",
    name: "Open House CRM",
    description: "Provides audited CRM reads and approval-gated CRM writes.",
    register(api) {
      const crmAgentId = configuredAgentId(api.pluginConfig);
      const setupProbe = configuredSetupProbe(api.pluginConfig);
      const setupProbeState = new Map();
      const productionProbeState = new Set();
      api.on(
        "before_tool_call",
        (event, context) => {
          if (setupProbe && event.toolName === SETUP_MARKER_TOOL) {
            const params = event.params;
            const channel = TOOL_BLOCKED_CHANNELS.has(params?.channel)
              ? params.channel
              : undefined;
            const contextSupported = (
              context?.agentId === setupProbe.agentId
              && context.requester !== null
              && typeof context.requester === "object"
              && !Array.isArray(context.requester)
              && typeof context.requester.channel === "string"
            );
            if (
              validSetupProbeParams(params, setupProbe.nonce)
              && params.action === "status"
              && contextSupported
              && context.requester.channel === SETUP_CAPABILITY_CHANNEL
            ) return;
            if (
              !validSetupProbeParams(params, setupProbe.nonce)
              || params.action !== "attempt"
              || !contextSupported
            ) {
              for (const target of channel ? [channel] : TOOL_BLOCKED_CHANNELS) {
                setupProbeState.set(target, "hook_context_unsupported");
              }
              return {
                block: true,
                blockReason: "Setup marker probe hook context is unsupported.",
              };
            }
            if (context.requester.channel === params.channel) {
              setupProbeState.set(params.channel, "tool_blocked");
              return {
                block: true,
                blockReason: "Internal analysis and dashboard turns must not execute native tools.",
              };
            }
            setupProbeState.set(params.channel, "marker_missing");
          }
          if (
            event.toolName === TOOL_NAME
            && context.agentId === crmAgentId
            && context.requester?.channel === SETUP_CAPABILITY_CHANNEL
            && event.params?.operation === PRODUCTION_PROBE_OPERATION
          ) {
            const nonce = event.params?.arguments?.nonce;
            const channel = event.params?.arguments?.channel;
            const key = `${channel}:${nonce}`;
            const protectedChannel = (
              typeof nonce === "string"
              && SETUP_NONCE_PATTERN.test(nonce)
              && TOOL_BLOCKED_CHANNELS.has(channel)
              && productionProbeState.has(key)
            );
            return {
              block: true,
              blockReason: protectedChannel
                ? `Production CRM channel ${channel} nonce ${nonce} is protected.`
                : "Fresh production CRM channel protection was not proven.",
            };
          }
          if (
            event.toolName === TOOL_NAME
            && context.agentId === crmAgentId
            && TOOL_BLOCKED_CHANNELS.has(context.requester?.channel)
            && event.params?.operation === "search_leads"
          ) {
            const query = event.params?.arguments?.query;
            const nonce = typeof query === "string"
              && query.startsWith(PRODUCTION_PROBE_QUERY_PREFIX)
              ? query.slice(PRODUCTION_PROBE_QUERY_PREFIX.length)
              : undefined;
            if (typeof nonce === "string" && SETUP_NONCE_PATTERN.test(nonce)) {
              productionProbeState.add(`${context.requester.channel}:${nonce}`);
            }
          }
          if (
            context.requester?.channel === SETUP_AGENT_GUARD_CHANNEL
            && context.agentId === crmAgentId
            && event.toolName === TOOL_NAME
            && event.params?.operation === SETUP_AGENT_GUARD_OPERATION
          ) {
            return {
              block: true,
              blockReason: `Configured CRM agent ${crmAgentId} is protected.`,
            };
          }
          if (
            TOOL_BLOCKED_CHANNELS.has(context.requester?.channel)
          ) {
            return {
              block: true,
              blockReason: "Internal analysis and dashboard turns must not execute native tools.",
            };
          }
          const runId = exactRunId(event, context);
          if (
            event.toolName === TOOL_NAME
            && context.agentId === crmAgentId
            && context.requester?.channel === "discord"
            && outcomeGuard.mutationBlocked({
              runId,
              agentId: context.agentId,
              operation: event.params?.operation,
            })
          ) {
            return {
              block: true,
              blockReason: "An earlier CRM mutation outcome is unknown. "
                + "Inspect the CRM and Pending approvals before retrying; "
                + "later CRM mutations are blocked for this run.",
            };
          }
        },
      );

      api.on(
        "after_tool_call",
        (event, context) => {
          if (
            event.toolName !== TOOL_NAME
            || context.agentId !== crmAgentId
            || context.requester?.channel !== "discord"
          ) return;
          const runId = exactRunId(event, context);
          const receipt = structuredReceipt(event.result);
          if (!runId || !receipt) return;
          outcomeGuard.record({ runId, agentId: context.agentId, receipt });
        },
        { matcher: [TOOL_NAME] },
      );

      api.on("reply_payload_sending", (event, context) => {
        const runId = exactRunId(event, context);
        if (
          !runId
          || (context.channel !== "discord" && context.messageProvider !== "discord")
          || typeof event.payload?.text !== "string"
        ) return;
        const text = outcomeGuard.rewrite({
          runId,
          agentId: crmAgentId,
          text: event.payload.text,
        });
        if (text === event.payload.text) return;
        return { payload: { ...event.payload, text } };
      });

      api.on("gateway_stop", () => {
        outcomeGuard.clear();
        setupProbeState.clear();
        productionProbeState.clear();
      });

      api.registerTool(
        (toolContext) => ({
          name: TOOL_NAME,
          description:
            "Read the local Open House CRM or propose a CRM change for human approval. "
            + "Use the named operation and an object of named arguments. Never invent CRM facts.",
          parameters: toolParameters,
          async execute(_callId, params) {
            const details = await executeCrm(params, toolContext);
            return {
              content: [{ type: "text", text: JSON.stringify(details) }],
              details,
            };
          },
        }),
        { name: TOOL_NAME },
      );
      if (setupProbe) {
        api.registerTool(
          () => ({
            name: SETUP_MARKER_TOOL,
            description: "Setup-only marker propagation probe with no CRM or exec access.",
            parameters: setupProbeParameters(setupProbe.nonce),
            async execute(_callId, params) {
              if (!validSetupProbeParams(params, setupProbe.nonce)) {
                throw new TypeError("Invalid setup marker probe arguments");
              }
              if (params.action === "attempt") {
                setupProbeState.set(params.channel, "sentinel_executed");
              }
              const details = {
                schema_version: 1,
                channel: params.channel,
                nonce: setupProbe.nonce,
                status: setupProbeState.get(params.channel) ?? "tool_not_attempted",
              };
              return {
                content: [{ type: "text", text: JSON.stringify(details) }],
                details,
              };
            },
          }),
          { name: SETUP_MARKER_TOOL },
        );
      }
    },
  };
}
