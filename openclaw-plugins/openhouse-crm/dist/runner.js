import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { isAbsolute, join, normalize } from "node:path";

import { crmContract } from "./contract.js";


export const TOOL_TIMEOUT_MS = 20_000;
export const MAX_ARGUMENT_BYTES = 32 * 1024;
export const MAX_OUTPUT_BYTES = 256 * 1024;

const defaultRunChild = promisify(execFile);
const operations = crmContract.operations;
const operationSet = new Set(Object.keys(operations));
const SAFE_CODES = new Set([
  "invalid_arguments",
  "not_found",
  "ambiguous_match",
  "schedule_conflict",
  "backend_unavailable",
  "timeout",
  "result_too_large",
  "operation_failed",
  "outcome_unknown",
]);
const SAFE_MESSAGES = Object.freeze({
  invalid_arguments: "Invalid CRM arguments",
  not_found: "CRM record was not found",
  ambiguous_match: "CRM record match is ambiguous",
  schedule_conflict: "Requested schedule conflicts with an existing appointment",
  backend_unavailable: "CRM backend is unavailable",
  timeout: "CRM operation timed out",
  result_too_large: "CRM operation returned too much data",
  operation_failed: "CRM operation failed",
  outcome_unknown: "CRM mutation outcome is unknown",
});
const SAFE_OPERATION_NAME = /^[a-z][a-z0-9_]{0,127}$/;
const mutatingOperations = new Set(
  Object.entries(operations)
    .filter(([, entry]) => entry.effect === "proposal" || entry.effect === "validated_write")
    .map(([operation]) => operation),
);
const PRE_DISPATCH_CHILD_ERRORS = new Set(["EACCES", "ENOENT", "ENOTDIR"]);


function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}


function assertJsonCompatible(value, seen = new Set()) {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return;
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new Error("CRM arguments must be JSON-compatible");
    seen.add(value);
    for (const item of value) assertJsonCompatible(item, seen);
    seen.delete(value);
    return;
  }
  if (isPlainObject(value)) {
    if (seen.has(value)) throw new Error("CRM arguments must be JSON-compatible");
    seen.add(value);
    for (const item of Object.values(value)) assertJsonCompatible(item, seen);
    seen.delete(value);
    return;
  }
  throw new Error("CRM arguments must be JSON-compatible");
}


function wrapperPath(workspaceDir) {
  if (
    typeof workspaceDir !== "string"
    || !workspaceDir.trim()
    || !isAbsolute(workspaceDir)
  ) {
    throw new Error("CRM workspace is unavailable");
  }
  return normalize(
    join(workspaceDir, "skills", "crm-db-operations", "cli.py"),
  );
}


function receiptOperation(input) {
  return isPlainObject(input) && typeof input.operation === "string" && SAFE_OPERATION_NAME.test(input.operation)
    ? input.operation
    : "unknown";
}


function errorReceipt(operation, code, message = SAFE_MESSAGES[code]) {
  return {
    ok: false,
    operation,
    kind: "error",
    error: {
      code,
      message,
      retryable: code === "backend_unavailable" || code === "timeout",
    },
  };
}


function unknownMutationReceipt(operation) {
  return errorReceipt(operation, "outcome_unknown");
}


function safeInvalidMessage(error) {
  const message = error instanceof Error ? error.message : "Invalid CRM arguments";
  return message.length <= 256 ? message : SAFE_MESSAGES.invalid_arguments;
}


function safeCliMessage(code, message) {
  if (typeof message !== "string" || message.length > 256) return SAFE_MESSAGES[code];
  if (code !== "invalid_arguments") return message === SAFE_MESSAGES[code] ? message : SAFE_MESSAGES[code];
  if (/^(Unsupported|Missing|Invalid) argument: [a-z][a-z0-9_]*$/.test(message)) return message;
  if (/^Invalid CRM arguments: [a-z][a-z0-9_]*$/.test(message)) return message;
  return SAFE_MESSAGES.invalid_arguments;
}


function parseCliError(operation, stderr) {
  if (typeof stderr !== "string" || Buffer.byteLength(stderr, "utf8") > 2048) return undefined;
  try {
    const payload = JSON.parse(stderr);
    if (
      !isPlainObject(payload)
      || payload.ok !== false
      || !isPlainObject(payload.error)
      || Object.keys(payload.error).length !== 3
      || !SAFE_CODES.has(payload.error.code)
      || typeof payload.error.message !== "string"
      || typeof payload.error.retryable !== "boolean"
    ) return undefined;
    const code = payload.error.code;
    const receipt = errorReceipt(operation, code, safeCliMessage(code, payload.error.message));
    if (receipt.error.retryable !== payload.error.retryable) return undefined;
    return receipt;
  } catch {
    return undefined;
  }
}


function mapChildError(operation, error) {
  const cliReceipt = parseCliError(operation, error?.stderr);
  if (cliReceipt) return cliReceipt;
  if (PRE_DISPATCH_CHILD_ERRORS.has(error?.code)) {
    return errorReceipt(operation, "operation_failed");
  }
  if (mutatingOperations.has(operation)) {
    return unknownMutationReceipt(operation);
  }
  if (error?.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") {
    return errorReceipt(operation, "result_too_large");
  }
  if (
    error?.code === "ETIMEDOUT"
    || (error?.killed === true && error?.signal === "SIGTERM")
  ) {
    return errorReceipt(operation, "timeout");
  }
  return errorReceipt(operation, "operation_failed");
}


export async function runCrmTool(input, toolContext, runChild = defaultRunChild) {
  const operation = receiptOperation(input);
  let argsObject;
  let serializedArguments;
  try {
    if (!isPlainObject(input)) throw new Error("CRM tool input must be an object");
    if (typeof input.operation !== "string" || !input.operation) throw new Error("CRM operation is required");
    if (!operationSet.has(input.operation)) throw new Error("CRM operation is not supported");
    argsObject = input.arguments === undefined ? {} : input.arguments;
    if (!isPlainObject(argsObject)) throw new Error("CRM arguments must be an object");
    assertJsonCompatible(argsObject);
    serializedArguments = JSON.stringify(argsObject);
    if (Buffer.byteLength(serializedArguments, "utf8") > MAX_ARGUMENT_BYTES) {
      throw new Error("CRM arguments are too large");
    }
  } catch (error) {
    return errorReceipt(operation, "invalid_arguments", safeInvalidMessage(error));
  }

  let file;
  try {
    file = wrapperPath(toolContext?.workspaceDir);
  } catch {
    return errorReceipt(operation, "operation_failed");
  }
  let completed;
  try {
    completed = await runChild(
      file,
      [input.operation, "--args", serializedArguments],
      {
        encoding: "utf8",
        maxBuffer: MAX_OUTPUT_BYTES,
        shell: false,
        timeout: TOOL_TIMEOUT_MS,
      },
    );
  } catch (error) {
    return mapChildError(operation, error);
  }

  const stdout = completed?.stdout;
  if (typeof stdout !== "string") {
    return mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "operation_failed");
  }
  if (Buffer.byteLength(stdout, "utf8") > MAX_OUTPUT_BYTES) {
    return mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "result_too_large");
  }

  let payload;
  try {
    payload = JSON.parse(stdout);
  } catch {
    return mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "operation_failed");
  }
  if (!isPlainObject(payload) || payload.ok !== true || !("result" in payload)) {
    return mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "operation_failed");
  }
  try {
    assertJsonCompatible(payload.result);
  } catch {
    return mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "operation_failed");
  }
  const receipt = {
    ok: true,
    operation,
    kind: payload.result?.pending === true ? "proposal" : operations[operation].effect,
    result: payload.result,
  };
  return Buffer.byteLength(JSON.stringify(receipt), "utf8") <= MAX_OUTPUT_BYTES
    ? receipt
    : mutatingOperations.has(operation)
      ? unknownMutationReceipt(operation)
      : errorReceipt(operation, "result_too_large");
}
