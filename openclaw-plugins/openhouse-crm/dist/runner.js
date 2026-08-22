import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { isAbsolute, join, normalize } from "node:path";
import { readFileSync } from "node:fs";


export const TOOL_TIMEOUT_MS = 20_000;
export const MAX_ARGUMENT_BYTES = 32 * 1024;
export const MAX_OUTPUT_BYTES = 256 * 1024;

const defaultRunChild = promisify(execFile);
const operations = JSON.parse(
  readFileSync(new URL("../operations.json", import.meta.url), "utf8"),
);
const operationSet = new Set(operations);


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


function mapChildError(error) {
  if (
    error?.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER"
    || String(error?.message || "").includes("maxBuffer")
  ) {
    return new Error("CRM operation returned too much data");
  }
  if (error?.killed || error?.code === "ETIMEDOUT" || error?.signal) {
    return new Error("CRM operation timed out");
  }
  return new Error("CRM operation failed");
}


export async function runCrmTool(input, toolContext, runChild = defaultRunChild) {
  if (!isPlainObject(input)) {
    throw new Error("CRM tool input must be an object");
  }
  if (typeof input.operation !== "string" || !input.operation) {
    throw new Error("CRM operation is required");
  }
  if (!operationSet.has(input.operation)) {
    throw new Error("CRM operation is not supported");
  }

  const argsObject = input.arguments === undefined ? {} : input.arguments;
  if (!isPlainObject(argsObject)) {
    throw new Error("CRM arguments must be an object");
  }
  assertJsonCompatible(argsObject);
  const serializedArguments = JSON.stringify(argsObject);
  if (Buffer.byteLength(serializedArguments, "utf8") > MAX_ARGUMENT_BYTES) {
    throw new Error("CRM arguments are too large");
  }

  const file = wrapperPath(toolContext?.workspaceDir);
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
    throw mapChildError(error);
  }

  const stdout = completed?.stdout;
  if (typeof stdout !== "string") {
    throw new Error("CRM operation returned an invalid response");
  }
  if (Buffer.byteLength(stdout, "utf8") > MAX_OUTPUT_BYTES) {
    throw new Error("CRM operation returned too much data");
  }

  let payload;
  try {
    payload = JSON.parse(stdout);
  } catch {
    throw new Error("CRM operation returned an invalid response");
  }
  if (!isPlainObject(payload) || payload.ok !== true || !("result" in payload)) {
    throw new Error("CRM operation failed");
  }
  return payload.result;
}
