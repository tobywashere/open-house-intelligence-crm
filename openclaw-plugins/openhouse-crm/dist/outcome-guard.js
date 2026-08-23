import { crmContract } from "./contract.js";


const DEFAULT_MAX_ENTRIES = 256;
const DEFAULT_TTL_MS = 300_000;
const MAX_SUMMARY_CHARACTERS = 240;
const DISCORD_SAFE_CHARACTERS = new Map([
  ["@", "＠"],
  ["#", "＃"],
  ["<", "‹"],
  [">", "›"],
  ["`", "＇"],
  ["*", "∗"],
  ["_", "＿"],
  ["~", "～"],
  ["|", "｜"],
  ["[", "［"],
  ["]", "］"],
  ["(", "（"],
  [")", "）"],
  ["\\", "＼"],
]);
const DISCORD_CONTROL_SYNTAX = /[@#<>`*_~|[\]()\\]/gu;
const SAFE_ERROR_REASONS = Object.freeze({
  invalid_arguments: "Invalid CRM arguments",
  not_found: "CRM record was not found",
  ambiguous_match: "CRM record match is ambiguous",
  schedule_conflict: "Requested schedule conflicts with an existing appointment",
  backend_unavailable: "CRM backend is unavailable",
  timeout: "CRM operation timed out",
  result_too_large: "CRM operation returned too much data",
  operation_failed: "CRM operation failed",
});
const proposalOperations = new Set(
  Object.entries(crmContract.operations)
    .filter(([, entry]) => entry.effect === "proposal")
    .map(([operation]) => operation),
);


function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}


function validIdentity(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 512;
}


function scopeKey(runId, agentId) {
  return JSON.stringify([runId, agentId]);
}


function cleanSummary(value) {
  if (typeof value !== "string") return undefined;
  const normalized = value
    .normalize("NFKC")
    .replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]+/gu, " ")
    .replace(DISCORD_CONTROL_SYNTAX, (character) => DISCORD_SAFE_CHARACTERS.get(character))
    .replace(/\s+/gu, " ")
    .trim();
  if (!normalized) return undefined;
  return Array.from(normalized)
    .slice(0, MAX_SUMMARY_CHARACTERS)
    .join("")
    .trimEnd();
}


function proposalOutcome(receipt) {
  const result = receipt.result;
  if (
    receipt.ok !== true
    || receipt.kind !== "proposal"
    || !proposalOperations.has(receipt.operation)
    || !isPlainObject(result)
    || result.pending !== true
    || result.operation !== receipt.operation
    || result.status !== "pending"
    || !Number.isSafeInteger(result.id)
    || result.id <= 0
  ) return undefined;
  const summary = cleanSummary(result.summary);
  if (!summary) return undefined;
  return {
    proposalId: result.id,
    operation: receipt.operation,
    summary,
    status: "pending",
  };
}


function failureOutcome(receipt) {
  if (
    receipt.ok !== false
    || receipt.kind !== "error"
    || !proposalOperations.has(receipt.operation)
    || !isPlainObject(receipt.error)
  ) return undefined;
  const error = Object.hasOwn(SAFE_ERROR_REASONS, receipt.error.code)
    ? SAFE_ERROR_REASONS[receipt.error.code]
    : undefined;
  if (!error) return undefined;
  return {
    operation: receipt.operation,
    status: "failed",
    error,
  };
}


function outcomeFromReceipt(receipt) {
  if (!isPlainObject(receipt)) return undefined;
  return proposalOutcome(receipt) ?? failureOutcome(receipt);
}


function sentence(text) {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}


export function createOutcomeGuard({
  maxEntries = DEFAULT_MAX_ENTRIES,
  ttlMs = DEFAULT_TTL_MS,
  now = Date.now,
} = {}) {
  if (!Number.isSafeInteger(maxEntries) || maxEntries <= 0) {
    throw new TypeError("maxEntries must be a positive integer");
  }
  if (!Number.isFinite(ttlMs) || ttlMs <= 0) {
    throw new TypeError("ttlMs must be positive");
  }
  if (typeof now !== "function") throw new TypeError("now must be a function");

  const entries = new Map();

  function removeExpired(timestamp) {
    for (const [key, entry] of entries) {
      if (entry.expiry <= timestamp) entries.delete(key);
    }
  }

  function record({ runId, agentId, receipt } = {}) {
    if (!validIdentity(runId) || !validIdentity(agentId)) return;
    const outcome = outcomeFromReceipt(receipt);
    if (!outcome) return;
    const timestamp = now();
    removeExpired(timestamp);
    const key = scopeKey(runId, agentId);
    entries.delete(key);
    while (entries.size >= maxEntries) {
      entries.delete(entries.keys().next().value);
    }
    entries.set(key, {
      ...outcome,
      runId,
      agentId,
      expiry: timestamp + ttlMs,
    });
  }

  function rewrite({ runId, agentId, text } = {}) {
    if (!validIdentity(runId) || !validIdentity(agentId) || typeof text !== "string") return text;
    const timestamp = now();
    removeExpired(timestamp);
    const key = scopeKey(runId, agentId);
    const entry = entries.get(key);
    if (!entry) return text;
    entries.delete(key);
    if (entry.status === "pending") {
      return `Proposal #${entry.proposalId} is waiting for your review: ${sentence(entry.summary)}`;
    }
    return `I could not queue that CRM change. Nothing was changed. ${sentence(entry.error)}`;
  }

  function clear(runId) {
    if (runId === undefined) {
      entries.clear();
      return;
    }
    if (!validIdentity(runId)) return;
    for (const [key, entry] of entries) {
      if (entry.runId === runId) entries.delete(key);
    }
  }

  return Object.freeze({ record, rewrite, clear });
}
