import { readFileSync } from "node:fs";

import { runCrmTool } from "./runner.js";


const operations = Object.freeze(
  JSON.parse(readFileSync(new URL("../operations.json", import.meta.url), "utf8")),
);


export function createPluginDefinition(executeCrm = runCrmTool) {
  return {
    id: "openhouse-crm",
    name: "Open House CRM",
    description: "Provides audited CRM reads and approval-gated CRM writes.",
    register(api) {
      api.registerTool((toolContext) => ({
        name: "openhouse_crm",
        description:
          "Read the local Open House CRM or propose a CRM change for human approval. "
          + "Use the named operation and an object of named arguments. Never invent CRM facts.",
        parameters: {
          type: "object",
          additionalProperties: false,
          required: ["operation"],
          properties: {
            operation: {
              type: "string",
              enum: [...operations],
              description: "A supported Open House CRM operation.",
            },
            arguments: {
              type: "object",
              additionalProperties: true,
              description: "Named arguments for the selected CRM operation.",
            },
          },
        },
        async execute(_callId, params) {
          const details = await executeCrm(params, toolContext);
          return {
            content: [{ type: "text", text: JSON.stringify(details) }],
            details,
          };
        },
      }));
    },
  };
}
