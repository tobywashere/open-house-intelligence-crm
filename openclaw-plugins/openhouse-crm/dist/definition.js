import { buildToolParameters, crmContract } from "./contract.js";
import { createOutcomeGuard } from "./outcome-guard.js";
import { runCrmTool } from "./runner.js";


const toolParameters = buildToolParameters(crmContract);
const TOOL_NAME = "openhouse_crm";
const DEFAULT_CRM_AGENT_ID = "openhouse-crm";
const AGENT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const DASHBOARD_CHANNEL = "openhouse-dashboard";
const INTERNAL_ANALYSIS_CHANNEL = "openhouse-analysis";
const SETUP_AGENT_GUARD_CHANNEL = "openhouse-setup-agent-guard";
const SETUP_AGENT_GUARD_OPERATION = "__openhouse_agent_guard_probe__";
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


export function createPluginDefinition(executeCrm = runCrmTool) {
  const outcomeGuard = createOutcomeGuard();
  return {
    id: "openhouse-crm",
    name: "Open House CRM",
    description: "Provides audited CRM reads and approval-gated CRM writes.",
    register(api) {
      const crmAgentId = configuredAgentId(api.pluginConfig);
      api.on(
        "before_tool_call",
        (event, context) => {
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
        const agentId = event.usageState?.agentId;
        if (
          !runId
          || agentId !== crmAgentId
          || (context.channel !== "discord" && context.messageProvider !== "discord")
          || typeof event.payload?.text !== "string"
        ) return;
        const text = outcomeGuard.rewrite({ runId, agentId, text: event.payload.text });
        if (text === event.payload.text) return;
        return { payload: { ...event.payload, text } };
      });

      api.on("gateway_stop", () => {
        outcomeGuard.clear();
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
    },
  };
}
