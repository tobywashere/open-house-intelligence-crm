import { crmContract } from "./contract.js";


const DEFAULT_MAX_ENTRIES = 256;
const DEFAULT_MAX_OUTCOMES = 6;
const MAX_CONFIGURED_OUTCOMES = 32;
const DEFAULT_TTL_MS = 300_000;
const MAX_SUMMARY_CHARACTERS = 240;
const MAX_EVIDENCE_IDS = 6;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
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
  outcome_unknown: "CRM mutation outcome is unknown",
});
const mutationOperations = new Set(
  Object.entries(crmContract.operations)
    .filter(([, entry]) => entry.effect === "proposal" || entry.effect === "validated_write")
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
    || !mutationOperations.has(receipt.operation)
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


function validatedWriteOutcome(receipt) {
  if (
    receipt.ok !== true
    || receipt.kind !== "validated_write"
    || crmContract.operations[receipt.operation]?.effect !== "validated_write"
    || !("result" in receipt)
  ) return undefined;
  return {
    evidence: validatedWriteEvidence(receipt.operation, receipt.result),
    operation: receipt.operation,
    status: "applied",
  };
}


function validatedWriteEvidence(operation, result) {
  if (operation === "find_neglected_leads" && Array.isArray(result)) {
    const ids = result.map((entry) => isPlainObject(entry) ? entry.id : undefined);
    if (!ids.every((id) => Number.isSafeInteger(id) && id > 0)) return undefined;
    return {
      kind: "neglected_leads",
      count: ids.length,
      ids: ids.slice(0, MAX_EVIDENCE_IDS),
      hiddenIds: Math.max(0, ids.length - MAX_EVIDENCE_IDS),
    };
  }
  if (
    operation === "post_briefing"
    && isPlainObject(result)
    && typeof result.date === "string"
    && ISO_DATE_RE.test(result.date)
  ) {
    return { kind: "briefing", date: result.date };
  }
  return undefined;
}


function failureOutcome(receipt) {
  if (
    receipt.ok !== false
    || receipt.kind !== "error"
    || !mutationOperations.has(receipt.operation)
    || !isPlainObject(receipt.error)
  ) return undefined;
  const error = Object.hasOwn(SAFE_ERROR_REASONS, receipt.error.code)
    ? SAFE_ERROR_REASONS[receipt.error.code]
    : undefined;
  if (!error) return undefined;
  return {
    operation: receipt.operation,
    status: receipt.error.code === "outcome_unknown" ? "unknown" : "failed",
    error,
  };
}


function outcomeFromReceipt(receipt) {
  if (!isPlainObject(receipt)) return undefined;
  return proposalOutcome(receipt)
    ?? validatedWriteOutcome(receipt)
    ?? failureOutcome(receipt);
}


function sentence(text) {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}


function operationLabel(operation) {
  return operation.replaceAll("_", " ");
}


function renderOutcome(outcome) {
  if (outcome.status === "pending") {
    return `Proposal #${outcome.proposalId} is waiting for your review: ${sentence(outcome.summary)}`;
  }
  const operation = operationLabel(outcome.operation);
  if (outcome.status === "applied") {
    if (outcome.evidence?.kind === "neglected_leads") {
      const { count, hiddenIds, ids } = outcome.evidence;
      const leadNoun = count === 1 ? "lead" : "leads";
      if (count === 0) {
        return `Verified CRM write completed: ${operation} (0 leads flagged).`;
      }
      const idNoun = ids.length === 1 ? "ID" : "IDs";
      const hidden = hiddenIds > 0 ? `; ${hiddenIds} more IDs not shown` : "";
      return `Verified CRM write completed: ${operation} `
        + `(${count} ${leadNoun} flagged; ${idNoun} ${ids.join(", ")}${hidden}).`;
    }
    if (outcome.evidence?.kind === "briefing") {
      return `Verified CRM write completed: ${operation} `
        + `(briefing date ${outcome.evidence.date}).`;
    }
    return `Verified CRM write completed: ${operation}.`;
  }
  if (outcome.status === "unknown") {
    return `The ${operation} CRM change may have reached the backend, but its result could not be verified.`;
  }
  return `The ${operation} CRM change failed before confirmation. `
    + `That attempt did not queue or apply a change. ${sentence(outcome.error)}`;
}


export function createOutcomeGuard({
  maxEntries = DEFAULT_MAX_ENTRIES,
  maxOutcomes = DEFAULT_MAX_OUTCOMES,
  ttlMs = DEFAULT_TTL_MS,
  now = Date.now,
} = {}) {
  if (!Number.isSafeInteger(maxEntries) || maxEntries <= 0) {
    throw new TypeError("maxEntries must be a positive integer");
  }
  if (!Number.isFinite(ttlMs) || ttlMs <= 0) {
    throw new TypeError("ttlMs must be positive");
  }
  if (
    !Number.isSafeInteger(maxOutcomes)
    || maxOutcomes <= 0
    || maxOutcomes > MAX_CONFIGURED_OUTCOMES
  ) {
    throw new TypeError(`maxOutcomes must be an integer from 1 to ${MAX_CONFIGURED_OUTCOMES}`);
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
    const previous = entries.get(key);
    const outcomes = previous?.outcomes ?? [];
    let truncated = previous?.truncated ?? 0;
    if (outcomes.length < maxOutcomes) {
      outcomes.push(outcome);
    } else {
      truncated += 1;
    }
    entries.delete(key);
    while (entries.size >= maxEntries) {
      entries.delete(entries.keys().next().value);
    }
    entries.set(key, {
      outcomes,
      truncated,
      blocked: previous?.blocked === true || outcome.status === "unknown",
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
    const lines = entry.outcomes.map(renderOutcome);
    if (entry.truncated > 0) {
      const noun = entry.truncated === 1 ? "outcome was" : "outcomes were";
      lines.push(
        `${entry.truncated} additional CRM mutation ${noun} not shown because this safety summary is bounded.`,
      );
    }
    if (entry.blocked) {
      lines.push(
        "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.",
      );
    }
    return lines.join("\n");
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

  function mutationBlocked({ runId, agentId, operation } = {}) {
    if (
      !validIdentity(runId)
      || !validIdentity(agentId)
      || !mutationOperations.has(operation)
    ) return false;
    const timestamp = now();
    removeExpired(timestamp);
    return entries.get(scopeKey(runId, agentId))?.blocked === true;
  }

  return Object.freeze({ record, rewrite, clear, mutationBlocked });
}
