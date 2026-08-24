import { buildToolParameters, crmContract } from "./contract.js";
import { createOutcomeGuard } from "./outcome-guard.js";
import { runCrmTool } from "./runner.js";


const toolParameters = buildToolParameters(crmContract);
const TOOL_NAME = "openhouse_crm";
const CRM_AGENT_ID = "openhouse-crm";
const DASHBOARD_CHANNEL = "openhouse-dashboard";
const INTERNAL_ANALYSIS_CHANNEL = "openhouse-analysis";
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


export function createPluginDefinition(executeCrm = runCrmTool) {
  const outcomeGuard = createOutcomeGuard();
  return {
    id: "openhouse-crm",
    name: "Open House CRM",
    description: "Provides audited CRM reads and approval-gated CRM writes.",
    register(api) {
      api.on(
        "before_tool_call",
        (event, context) => {
          if (
            TOOL_BLOCKED_CHANNELS.has(context.requester?.channel)
          ) {
            return {
              block: true,
              blockReason: "Internal analysis and dashboard turns must not execute native tools.",
            };
          }
        },
      );

      api.on(
        "after_tool_call",
        (event, context) => {
          if (event.toolName !== TOOL_NAME || context.agentId !== CRM_AGENT_ID) return;
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
          || agentId !== CRM_AGENT_ID
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
