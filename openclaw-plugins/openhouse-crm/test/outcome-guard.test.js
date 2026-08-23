import assert from "node:assert/strict";
import test from "node:test";

import { createOutcomeGuard } from "../dist/outcome-guard.js";


const pendingReceipt = {
  ok: true,
  operation: "create_lead",
  kind: "proposal",
  result: {
    pending: true,
    id: 4,
    operation: "create_lead",
    summary: "Create lead Jordan Ellis",
    status: "pending",
  },
};


function failedReceipt(overrides = {}) {
  return {
    ok: false,
    operation: "create_lead",
    kind: "error",
    error: {
      code: "invalid_arguments",
      message: "Unsupported argument: secret_prompt",
      retryable: false,
    },
    ...overrides,
  };
}


test("rewrites a pending proposal deterministically and consumes it once", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-1", agentId: "openhouse-crm", receipt: pendingReceipt });

  assert.equal(
    guard.rewrite({ runId: "run-1", agentId: "openhouse-crm", text: "Done" }),
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
  );
  assert.equal(
    guard.rewrite({ runId: "run-1", agentId: "openhouse-crm", text: "Second payload" }),
    "Second payload",
  );
});


test("rewrites a failed proposal with a fixed safe reason and no raw error text", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-failed", agentId: "openhouse-crm", receipt: failedReceipt() });

  assert.equal(
    guard.rewrite({ runId: "run-failed", agentId: "openhouse-crm", text: "Created it" }),
    "I could not queue that CRM change. Nothing was changed. Invalid CRM arguments.",
  );
});


test("ignores inherited object property names presented as error codes", () => {
  const guard = createOutcomeGuard();
  guard.record({
    runId: "run-unsafe-code",
    agentId: "openhouse-crm",
    receipt: failedReceipt({
      error: { code: "toString", message: "private prompt and token", retryable: false },
    }),
  });

  assert.equal(
    guard.rewrite({ runId: "run-unsafe-code", agentId: "openhouse-crm", text: "Original" }),
    "Original",
  );
});


test("passes through successful reads and failed read operations", () => {
  const guard = createOutcomeGuard();
  guard.record({
    runId: "run-read",
    agentId: "openhouse-crm",
    receipt: { ok: true, operation: "list_leads", kind: "read", result: [] },
  });
  guard.record({
    runId: "run-read",
    agentId: "openhouse-crm",
    receipt: failedReceipt({ operation: "list_leads" }),
  });

  assert.equal(
    guard.rewrite({ runId: "run-read", agentId: "openhouse-crm", text: "No leads" }),
    "No leads",
  );
});


test("does not deliver or consume an outcome for another agent", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-agent", agentId: "openhouse-crm", receipt: pendingReceipt });

  assert.equal(
    guard.rewrite({ runId: "run-agent", agentId: "other-agent", text: "Other reply" }),
    "Other reply",
  );
  assert.equal(
    guard.rewrite({ runId: "run-agent", agentId: "openhouse-crm", text: "CRM reply" }),
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
  );
});


test("ignores records and rewrites without an exact non-empty run ID", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: undefined, agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({ runId: "", agentId: "openhouse-crm", receipt: pendingReceipt });

  assert.equal(
    guard.rewrite({ runId: undefined, agentId: "openhouse-crm", text: "No run" }),
    "No run",
  );
  assert.equal(
    guard.rewrite({ runId: "", agentId: "openhouse-crm", text: "Empty run" }),
    "Empty run",
  );
});


test("isolates exact run IDs without prefix matching or cross-run cleanup", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-1", agentId: "openhouse-crm", receipt: pendingReceipt });

  assert.equal(
    guard.rewrite({ runId: "run-10", agentId: "openhouse-crm", text: "Different run" }),
    "Different run",
  );
  assert.equal(
    guard.rewrite({ runId: "run-1", agentId: "openhouse-crm", text: "Exact run" }),
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.",
  );
});


test("expires outcomes at the configured TTL boundary", () => {
  let clock = 1_000;
  const guard = createOutcomeGuard({ ttlMs: 50, now: () => clock });
  guard.record({ runId: "run-ttl", agentId: "openhouse-crm", receipt: pendingReceipt });
  clock = 1_050;

  assert.equal(
    guard.rewrite({ runId: "run-ttl", agentId: "openhouse-crm", text: "Expired" }),
    "Expired",
  );
});


test("evicts the oldest outcome when the maximum entry count is exceeded", () => {
  let clock = 1_000;
  const guard = createOutcomeGuard({ maxEntries: 2, now: () => clock++ });
  for (const runId of ["run-oldest", "run-middle", "run-newest"]) {
    guard.record({ runId, agentId: "openhouse-crm", receipt: pendingReceipt });
  }

  assert.equal(
    guard.rewrite({ runId: "run-oldest", agentId: "openhouse-crm", text: "Evicted" }),
    "Evicted",
  );
  assert.match(
    guard.rewrite({ runId: "run-middle", agentId: "openhouse-crm", text: "Middle" }),
    /^Proposal #4/,
  );
  assert.match(
    guard.rewrite({ runId: "run-newest", agentId: "openhouse-crm", text: "Newest" }),
    /^Proposal #4/,
  );
});


test("clears one exact run without removing another run", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-clear", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({ runId: "run-keep", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.clear("run-clear");

  assert.equal(
    guard.rewrite({ runId: "run-clear", agentId: "openhouse-crm", text: "Cleared" }),
    "Cleared",
  );
  assert.match(
    guard.rewrite({ runId: "run-keep", agentId: "openhouse-crm", text: "Kept" }),
    /^Proposal #4/,
  );
});


test("clears every in-memory outcome for gateway restart cleanup", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-a", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({ runId: "run-b", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.clear();

  assert.equal(
    guard.rewrite({ runId: "run-a", agentId: "openhouse-crm", text: "A" }),
    "A",
  );
  assert.equal(
    guard.rewrite({ runId: "run-b", agentId: "openhouse-crm", text: "B" }),
    "B",
  );
});
