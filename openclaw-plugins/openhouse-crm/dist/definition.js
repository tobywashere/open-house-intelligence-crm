import { buildToolParameters, crmContract } from "./contract.js";
import { runCrmTool } from "./runner.js";


const toolParameters = buildToolParameters(crmContract);


export function createPluginDefinition(executeCrm = runCrmTool) {
  return {
    id: "openhouse-crm",
    name: "Open House CRM",
    description: "Provides audited CRM reads and approval-gated CRM writes.",
    register(api) {
      api.registerTool(
        (toolContext) => ({
          name: "openhouse_crm",
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
        { name: "openhouse_crm" },
      );
    },
  };
}
