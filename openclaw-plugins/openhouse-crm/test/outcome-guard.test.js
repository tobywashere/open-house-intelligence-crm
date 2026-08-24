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


test("records a proposal summary at the 240-character storage boundary", () => {
  const guard = createOutcomeGuard();
  const summary = "a".repeat(240);
  guard.record({
    runId: "run-boundary",
    agentId: "openhouse-crm",
    receipt: { ...pendingReceipt, result: { ...pendingReceipt.result, summary } },
  });

  assert.equal(
    guard.rewrite({ runId: "run-boundary", agentId: "openhouse-crm", text: "Done" }),
    `Proposal #4 is waiting for your review: ${"a".repeat(240)}.`,
  );
});


test("truncates an over-limit proposal summary instead of discarding the receipt", () => {
  const guard = createOutcomeGuard();
  const summary = `${"b".repeat(240)}extra private lead text`;
  guard.record({
    runId: "run-over-limit",
    agentId: "openhouse-crm",
    receipt: { ...pendingReceipt, result: { ...pendingReceipt.result, summary } },
  });

  assert.equal(
    guard.rewrite({ runId: "run-over-limit", agentId: "openhouse-crm", text: "Done" }),
    `Proposal #4 is waiting for your review: ${"b".repeat(240)}.`,
  );
});


test("applies Unicode normalization before enforcing the summary storage limit", () => {
  const guard = createOutcomeGuard();
  const summary = `${"c".repeat(239)}\ufb03`;
  guard.record({
    runId: "run-normalized-limit",
    agentId: "openhouse-crm",
    receipt: { ...pendingReceipt, result: { ...pendingReceipt.result, summary } },
  });

  assert.equal(
    guard.rewrite({ runId: "run-normalized-limit", agentId: "openhouse-crm", text: "Done" }),
    `Proposal #4 is waiting for your review: ${"c".repeat(239)}f.`,
  );
});


test("neutralizes Unicode controls, Discord mentions, and markup before rendering", () => {
  const guard = createOutcomeGuard();
  const summary = "\u202e@everyone\n@here <@123> <@!456> <@&789> <#321> "
    + "`code` ||secret|| >quote **bold** _italics_ ~~strike~~ [link](target) # heading";
  guard.record({
    runId: "run-adversarial",
    agentId: "openhouse-crm",
    receipt: { ...pendingReceipt, result: { ...pendingReceipt.result, summary } },
  });

  const output = guard.rewrite({
    runId: "run-adversarial",
    agentId: "openhouse-crm",
    text: "Unsafe model reply",
  });
  const renderedSummary = output.slice(output.indexOf(": ") + 2, -1);
  assert.equal(
    renderedSummary,
    "＠everyone ＠here ‹＠123› ‹＠!456› ‹＠&789› ‹＃321› "
      + "＇code＇ ｜｜secret｜｜ ›quote ∗∗bold∗∗ ＿italics＿ ～～strike～～ "
      + "［link］（target） ＃ heading",
  );
  assert.doesNotMatch(renderedSummary, /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u);
  assert.doesNotMatch(renderedSummary, /@(?:everyone|here)|<@|<#|[`*_~|<>\[\]()#]/u);
});


test("rewrites a failed proposal with a fixed safe reason and no raw error text", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-failed", agentId: "openhouse-crm", receipt: failedReceipt() });

  assert.equal(
    guard.rewrite({ runId: "run-failed", agentId: "openhouse-crm", text: "Created it" }),
    "The create lead CRM change failed before confirmation. "
      + "That attempt did not queue or apply a change. Invalid CRM arguments.",
  );
});


test("never claims nothing changed when a mutation outcome is unknown", () => {
  const guard = createOutcomeGuard();
  guard.record({
    runId: "run-unknown",
    agentId: "openhouse-crm",
    receipt: failedReceipt({
      error: {
        code: "outcome_unknown",
        message: "private transport details",
        retryable: false,
      },
    }),
  });

  const reply = guard.rewrite({
    runId: "run-unknown",
    agentId: "openhouse-crm",
    text: "Nothing happened, retrying now",
  });
  assert.equal(
    reply,
    "The create lead CRM change may have reached the backend, but its result could not be verified.\n"
      + "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.",
  );
  assert.doesNotMatch(reply, /nothing (?:was )?changed/i);
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


test("preserves multiple pending proposals in receipt order", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-many", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({
    runId: "run-many",
    agentId: "openhouse-crm",
    receipt: {
      ...pendingReceipt,
      operation: "book_appointment",
      result: {
        ...pendingReceipt.result,
        id: 9,
        operation: "book_appointment",
        summary: "Book Jordan for August 28 at 3 PM",
      },
    },
  });

  assert.equal(
    guard.rewrite({ runId: "run-many", agentId: "openhouse-crm", text: "Done" }),
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.\n"
      + "Proposal #9 is waiting for your review: Book Jordan for August 28 at 3 PM.",
  );
});


test("tracks every contract-declared proposal operation", () => {
  const guard = createOutcomeGuard({ maxOutcomes: 32 });
  const operations = [
    "create_lead",
    "update_lead",
    "add_note",
    "close_lead",
    "merge_leads",
    "book_appointment",
    "schedule_followup",
    "delete_lead",
  ];
  operations.forEach((operation, index) => {
    guard.record({
      runId: "run-all-proposals",
      agentId: "openhouse-crm",
      receipt: {
        ...pendingReceipt,
        operation,
        result: {
          ...pendingReceipt.result,
          id: index + 1,
          operation,
          summary: `Verified operation ${index + 1}`,
        },
      },
    });
  });

  const output = guard.rewrite({
    runId: "run-all-proposals",
    agentId: "openhouse-crm",
    text: "Done",
  });
  operations.forEach((operation, index) => {
    assert.match(
      output,
      new RegExp(`Proposal #${index + 1}[^\\n]+Verified operation ${index + 1}`, "u"),
      operation,
    );
  });
});


test("a later deterministic failure cannot erase an earlier proposal", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-mixed", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({
    runId: "run-mixed",
    agentId: "openhouse-crm",
    receipt: failedReceipt({ operation: "book_appointment" }),
  });

  const output = guard.rewrite({
    runId: "run-mixed",
    agentId: "openhouse-crm",
    text: "Nothing happened",
  });
  assert.equal(
    output,
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.\n"
      + "The book appointment CRM change failed before confirmation. "
      + "That attempt did not queue or apply a change. Invalid CRM arguments.",
  );
  assert.doesNotMatch(output, /^I could not queue that CRM change/u);
});


test("renders each validated-write success including both contract operations", () => {
  const guard = createOutcomeGuard();
  guard.record({
    runId: "run-applied",
    agentId: "openhouse-crm",
    receipt: {
      ok: true,
      operation: "find_neglected_leads",
      kind: "validated_write",
      result: [{ id: 7 }],
    },
  });
  guard.record({
    runId: "run-applied",
    agentId: "openhouse-crm",
    receipt: {
      ok: true,
      operation: "post_briefing",
      kind: "validated_write",
      result: { date: "2026-08-24" },
    },
  });

  assert.equal(
    guard.rewrite({ runId: "run-applied", agentId: "openhouse-crm", text: "Done" }),
    "Verified CRM write completed: find neglected leads.\n"
      + "Verified CRM write completed: post briefing.",
  );
});


test("an unknown outcome preserves earlier successes and keeps the mutation gate closed", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-unknown-many", agentId: "openhouse-crm", receipt: pendingReceipt });
  guard.record({
    runId: "run-unknown-many",
    agentId: "openhouse-crm",
    receipt: failedReceipt({
      operation: "post_briefing",
      error: {
        code: "outcome_unknown",
        message: "private transport detail",
        retryable: false,
      },
    }),
  });

  assert.equal(
    guard.mutationBlocked({
      runId: "run-unknown-many",
      agentId: "openhouse-crm",
      operation: "schedule_followup",
    }),
    true,
  );
  assert.equal(
    guard.rewrite({
      runId: "run-unknown-many",
      agentId: "openhouse-crm",
      text: "Retried it",
    }),
    "Proposal #4 is waiting for your review: Create lead Jordan Ellis.\n"
      + "The post briefing CRM change may have reached the backend, but its result could not be verified.\n"
      + "Do not retry automatically. Inspect the CRM and Pending approvals before retrying.",
  );
});


test("bounds per-run outcomes, reports truncation, and still gates on a truncated unknown", () => {
  const guard = createOutcomeGuard({ maxOutcomes: 2 });
  for (const id of [4, 5, 6]) {
    guard.record({
      runId: "run-bounded",
      agentId: "openhouse-crm",
      receipt: {
        ...pendingReceipt,
        result: { ...pendingReceipt.result, id, summary: `Create lead ${id}` },
      },
    });
  }
  guard.record({
    runId: "run-bounded",
    agentId: "openhouse-crm",
    receipt: failedReceipt({
      operation: "post_briefing",
      error: { code: "outcome_unknown", message: "private", retryable: false },
    }),
  });

  assert.equal(
    guard.mutationBlocked({
      runId: "run-bounded",
      agentId: "openhouse-crm",
      operation: "create_lead",
    }),
    true,
  );
  const output = guard.rewrite({
    runId: "run-bounded",
    agentId: "openhouse-crm",
    text: "Done",
  });
  assert.match(output, /Proposal #4[^\n]+\nProposal #5/u);
  assert.doesNotMatch(output, /Proposal #6/u);
  assert.match(output, /2 additional CRM mutation outcomes were not shown/u);
  assert.match(output, /Do not retry automatically/u);
});


test("default bounded summary stays within Discord's message limit", () => {
  const guard = createOutcomeGuard();
  for (let id = 1; id <= 8; id += 1) {
    guard.record({
      runId: "run-discord-limit",
      agentId: "openhouse-crm",
      receipt: {
        ...pendingReceipt,
        result: { ...pendingReceipt.result, id, summary: "x".repeat(240) },
      },
    });
  }
  guard.record({
    runId: "run-discord-limit",
    agentId: "openhouse-crm",
    receipt: failedReceipt({
      operation: "post_briefing",
      error: { code: "outcome_unknown", message: "private", retryable: false },
    }),
  });

  const output = guard.rewrite({
    runId: "run-discord-limit",
    agentId: "openhouse-crm",
    text: "Done",
  });
  assert.ok(output.length <= 2_000, `summary was ${output.length} characters`);
  assert.match(output, /additional CRM mutation outcomes were not shown/u);
  assert.match(output, /Do not retry automatically/u);
});


test("keeps ordered outcome collections isolated by exact run and agent", () => {
  const guard = createOutcomeGuard();
  guard.record({ runId: "run-a", agentId: "custom-crm", receipt: pendingReceipt });
  guard.record({
    runId: "run-a",
    agentId: "custom-crm",
    receipt: { ...pendingReceipt, result: { ...pendingReceipt.result, id: 8 } },
  });

  assert.equal(
    guard.rewrite({ runId: "run-a", agentId: "openhouse-crm", text: "Wrong agent" }),
    "Wrong agent",
  );
  assert.equal(
    guard.rewrite({ runId: "run-b", agentId: "custom-crm", text: "Wrong run" }),
    "Wrong run",
  );
  assert.match(
    guard.rewrite({ runId: "run-a", agentId: "custom-crm", text: "Right scope" }),
    /Proposal #4[^\n]+\nProposal #8/u,
  );
});
