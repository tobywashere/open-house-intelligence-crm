import { readFileSync } from "node:fs";


const EFFECTS = new Set(["read", "proposal", "narrative", "validated_write"]);
const OPERATION_NAME = /^[a-z][a-z0-9_]*$/;


function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}


function assertContract(contract) {
  if (!isPlainObject(contract) || contract.version !== 1 || !isPlainObject(contract.operations)) {
    throw new Error("invalid CRM operation contract");
  }
  const entries = Object.entries(contract.operations);
  if (!entries.length) throw new Error("invalid CRM operation contract");
  for (const [operation, entry] of entries) {
    if (
      !OPERATION_NAME.test(operation)
      || !isPlainObject(entry)
      || Object.keys(entry).length !== 3
      || typeof entry.description !== "string"
      || !entry.description
      || !EFFECTS.has(entry.effect)
      || !isPlainObject(entry.arguments)
      || entry.arguments.type !== "object"
      || entry.arguments.additionalProperties !== false
    ) {
      throw new Error("invalid CRM operation contract");
    }
  }
  return contract;
}


export const crmContract = assertContract(
  JSON.parse(readFileSync(new URL("../../../skills/crm-db-operations/contract.json", import.meta.url), "utf8")),
);


export function buildToolParameters(contract) {
  assertContract(contract);
  return {
    oneOf: Object.entries(contract.operations).map(([operation, entry]) => ({
      type: "object",
      additionalProperties: false,
      required: ["operation", "arguments"],
      properties: {
        operation: { const: operation, description: entry.description },
        arguments: entry.arguments,
      },
    })),
  };
}
