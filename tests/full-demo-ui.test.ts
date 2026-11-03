import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FULL_DEMO_INTERFACE,
  capabilityState,
  currentFunding,
  earlyActionStage,
  foundationDefaults,
  fullDemoAmount,
  fullInvoiceAtomic,
  parseFullDemoContext,
  paymentActionFacts,
  quoteForCurrentScope,
  remainingFundingAtomic,
  roleHome,
  type FullDemoContext,
  type DemoExchange,
} from "../src/lib/full-demo-ui";
import { mayPrepareSigning } from "../src/lib/a2-workbench";
import type { A2Project, A2Procurement } from "../src/lib/a2-workbench";
import type { DemoWorkspace } from "../src/lib/full-demo-ui";

const ID = "12345678-9abc-def0-1234-56789abcdef0";
const binding = {
  namespaceId: ID,
  runId: "r",
  instanceId: "i",
  chainId: "31337",
};
const context: FullDemoContext = {
  interfaceId: FULL_DEMO_INTERFACE,
  mode: "simulation",
  binding,
  capabilities: {
    "release.execute": { implemented: true, enabled: true, reasonCode: null },
  },
  defaults: {
    recipientUserId: ID,
    humanApproverUserId: ID,
    supplierWallet: `0x${"a".repeat(40)}`,
  },
};
test("payment buttons require current reconciled facts and never start an existing resource again", () => {
  const resource = (kind: "redemption" | "supplier_payment") => ({
    id: ID,
    operationId: ID,
    kind,
    status: "reconciled",
    reconciled: true,
    projectId: ID,
    procurementId: ID,
  });
  const workspace: DemoWorkspace = {
    procurementId: ID,
    binding,
    documentVersions: [],
    allowedActions: {},
    release: { confirmed: true },
    redemption: null,
    supplierPayment: null,
  };
  const enabled = (actionId: string, current: DemoWorkspace | null) =>
    paymentActionFacts(actionId, context, current, ID, ID).enabled;
  const redeem = "foundation.redemption.convert";
  const supplier = "supplier.payment.execute";
  const evidence = "settlement.evidence.record";
  assert.equal(enabled(redeem, workspace), true);
  for (const confirmed of [false, "true", undefined])
    assert.equal(
      enabled(redeem, { ...workspace, release: { confirmed } }),
      false,
    );
  for (const redemption of [
    undefined,
    {},
    resource("redemption"),
    { ...resource("redemption"), status: "queued", reconciled: false },
    { ...resource("redemption"), status: "failed", reconciled: false },
  ])
    assert.equal(enabled(redeem, { ...workspace, redemption }), false);
  const redeemed = { ...workspace, redemption: resource("redemption") };
  assert.equal(enabled(supplier, workspace), false);
  assert.equal(enabled(evidence, workspace), false);
  assert.equal(enabled(supplier, redeemed), true);
  assert.equal(enabled(evidence, redeemed), false);
  for (const value of [
    undefined,
    {},
    { ...resource("redemption"), status: "queued", reconciled: false },
    { ...resource("redemption"), reconciled: false },
    { ...resource("redemption"), projectId: "other" },
    { ...resource("redemption"), procurementId: "other" },
    { ...resource("redemption"), kind: "supplier_payment" },
  ]) {
    assert.equal(enabled(supplier, { ...workspace, redemption: value }), false);
    assert.equal(
      enabled(evidence, {
        ...redeemed,
        redemption: value,
        supplierPayment: resource("supplier_payment"),
      }),
      false,
    );
  }
  for (const supplierPayment of [
    undefined,
    {},
    resource("supplier_payment"),
    { ...resource("supplier_payment"), status: "queued", reconciled: false },
    { ...resource("supplier_payment"), status: "failed", reconciled: false },
  ])
    assert.equal(enabled(supplier, { ...redeemed, supplierPayment }), false);
  const paid = { ...redeemed, supplierPayment: resource("supplier_payment") };
  assert.equal(enabled(evidence, paid), true);
  for (const supplierPayment of [
    null,
    undefined,
    { ...resource("supplier_payment"), status: "queued", reconciled: false },
    {
      ...resource("supplier_payment"),
      status: "reconciled",
      reconciled: false,
    },
    { ...resource("supplier_payment"), projectId: "other" },
    { ...resource("supplier_payment"), procurementId: "other" },
  ])
    assert.equal(enabled(evidence, { ...paid, supplierPayment }), false);
  for (const actionId of [redeem, supplier, evidence]) {
    const good =
      actionId === redeem ? workspace : actionId === supplier ? redeemed : paid;
    assert.equal(enabled(actionId, null), false);
    assert.equal(enabled(actionId, { ...good, procurementId: "other" }), false);
    assert.equal(
      paymentActionFacts(actionId, null, good, ID, ID).enabled,
      false,
    );
    for (const key of ["namespaceId", "runId", "instanceId", "chainId"])
      assert.equal(
        enabled(actionId, { ...good, binding: { ...binding, [key]: "stale" } }),
        false,
      );
  }
  // Quoting is a pure query. These checks neither block it nor grant permission.
  assert.equal(
    paymentActionFacts("mock.exchange.quote", null, null, "", "").enabled,
    true,
  );
  assert.equal(
    capabilityState(context, supplier, workspace.allowedActions).enabled,
    false,
  );
});
test("early immutable actions require server capability plus matching confirmed stage, never queued projection", () => {
  const project: A2Project = {
    id: ID,
    businessId: ID,
    title: "fixture",
    publicSummary: "fixture",
    chainState: { status: "active", verified: true },
  };
  const procurement: A2Procurement = {
    id: ID,
    projectId: ID,
    businessId: ID,
    title: "fixture",
    vendorWallet: `0x${"a".repeat(40)}`,
    budgetCapAtomic: "80000000",
    chainState: { status: "reserved", verified: true },
  };
  const workspace: DemoWorkspace = {
    procurementId: ID,
    chainState: "reserved",
    documentVersions: [],
    allowedActions: {},
  };
  for (const [actionId, wanted] of [
    ["procurement.chain.create", "off_chain_draft"],
    ["procurement.po.record", "created"],
    ["procurement.invoice.record", "reserved"],
    ["procurement.reserve.execute", "reserve_approval_pending"],
  ]) {
    const current = {
      ...procurement,
      chainState: { status: wanted, verified: wanted !== "off_chain_draft" },
    };
    const currentWorkspace = { ...workspace, chainState: wanted };
    assert.equal(
      earlyActionStage(actionId, project, current, currentWorkspace).enabled,
      true,
    );
    for (const status of [
      "create_queued",
      "po_queued",
      "reserve_queued",
      "invoice_queued",
      "off_chain_draft",
    ])
      if (status !== wanted)
        assert.equal(
          earlyActionStage(
            actionId,
            project,
            { ...current, chainState: { status, verified: false } },
            currentWorkspace,
          ).enabled,
          false,
        );
    assert.equal(
      earlyActionStage(actionId, project, current, {
        ...currentWorkspace,
        chainState: "stale",
      }).enabled,
      false,
    );
    assert.equal(
      earlyActionStage(actionId, project, current, {
        ...currentWorkspace,
        procurementId: "other",
      }).enabled,
      false,
    );
    assert.equal(
      earlyActionStage(actionId, project, current, null).enabled,
      false,
    );
    assert.equal(
      earlyActionStage(
        actionId,
        { ...project, chainState: { status: "active", verified: false } },
        current,
        currentWorkspace,
      ).enabled,
      false,
    );
  }
  assert.equal(
    earlyActionStage(
      "project.chain.create",
      {
        ...project,
        chainState: { status: "off_chain_draft", verified: false },
      },
      null,
      null,
    ).enabled,
    true,
  );
  assert.equal(
    earlyActionStage(
      "project.chain.create",
      { ...project, chainState: { status: "create_queued", verified: false } },
      null,
      null,
    ).enabled,
    false,
  );
  assert.equal(
    earlyActionStage("procurement.draft.create", project, null, null).enabled,
    true,
  );
  assert.equal(
    earlyActionStage(
      "procurement.draft.create",
      { ...project, chainState: { status: "closing", verified: true } },
      null,
      null,
    ).enabled,
    false,
  );
  assert.equal(
    capabilityState(null, "procurement.invoice.record").enabled,
    false,
  );
});
test("context is bound to the exact reviewed interface, namespace and simulation", () => {
  assert.equal(
    parseFullDemoContext(context, { runId: "r", instanceId: "i" }),
    context,
  );
  for (const value of [
    null,
    {},
    { ...context, mode: "production" },
    { ...context, interfaceId: "guess" },
    {
      ...context,
      binding: { ...binding, runId: undefined, instanceId: undefined },
    },
    { ...context, binding: { ...binding, chainId: 31337 } },
    { ...context, binding: { ...binding, runId: "stale" } },
  ])
    assert.equal(
      parseFullDemoContext(value, { runId: "r", instanceId: "i" }),
      null,
    );
});
test("missing, false, unimplemented or scoped-disabled capability never enables an action", () => {
  assert.equal(capabilityState(context, "release.execute").enabled, true);
  assert.equal(capabilityState(null, "release.execute").enabled, false);
  assert.equal(capabilityState(context, "unknown").enabled, false);
  assert.equal(
    capabilityState(context, "release.execute", null).enabled,
    false,
  );
  assert.equal(capabilityState(context, "release.execute", {}).enabled, false);
  assert.equal(
    capabilityState(context, "release.execute", {
      "release.execute": {
        implemented: true,
        enabled: false,
        reasonCode: "ai_service_unavailable",
      },
    }).reason,
    "ai_service_unavailable",
  );
  assert.equal(
    capabilityState(
      {
        ...context,
        capabilities: {
          "release.execute": {
            implemented: false,
            enabled: true,
            reasonCode: null,
          },
        },
      },
      "release.execute",
    ).enabled,
    false,
  );
});
test("Foundation defaults require controlled actual UUIDs and nonzero fixed supplier", () => {
  assert.deepEqual(foundationDefaults(context), context.defaults);
  assert.equal(foundationDefaults({ ...context, defaults: null }), null);
  assert.equal(
    foundationDefaults({
      ...context,
      defaults: { ...context.defaults!, supplierWallet: `0x${"0".repeat(40)}` },
    }),
    null,
  );
  assert.equal(
    foundationDefaults({
      ...context,
      defaults: { ...context.defaults!, recipientUserId: "human" },
    }),
    null,
  );
});
test("full-demo amounts and immutable invoices have exact cents, with no sub-cent release trap", () => {
  assert.deepEqual(fullDemoAmount("72.01"), {
    hkdCents: "7201",
    amountAtomic: "72010000",
  });
  assert.equal(
    fullInvoiceAtomic("9007199254740993.12"),
    "9007199254740993120000",
  );
  for (const input of ["0", "72.001", "-1", "1e3", "01", " 1", "1."])
    assert.throws(() => fullInvoiceAtomic(input));
});
test("quote is pure, exact and current; stale, executed or rounded quote is rejected", () => {
  const quote = {
    interfaceId: FULL_DEMO_INTERFACE,
    mode: "simulation",
    binding,
    executed: false,
    quote: {
      direction: "hkd_to_mock",
      hkdCents: "7200",
      amountAtomic: "72000000",
      rate: "1 HKD = 1 mHKD",
      feeHkdCents: "0",
      eligible: true,
      reasonCode: null,
    },
  };
  assert.equal(quoteForCurrentScope(quote, context), true);
  for (const value of [
    { ...quote, executed: true },
    { ...quote, binding: { ...binding, instanceId: "old" } },
    { ...quote, quote: { ...quote.quote, amountAtomic: "72000001" } },
    { ...quote, quote: { ...quote.quote, feeHkdCents: "1" } },
  ])
    assert.equal(quoteForCurrentScope(value, context), false);
});
test("funding selection uses original actor list, exact project and operation UUID; no faucet source", () => {
  const item: DemoExchange = {
    id: ID,
    operationId: ID,
    kind: "funding",
    status: "queued",
    reconciled: false,
    projectId: ID,
    procurementId: null,
    hkdCents: "10000",
    amountAtomic: "100000000",
    allocatedAtomic: "0",
    sourceOperationId: null,
    evidenceId: null,
    journalIds: [],
    chainProof: null,
  };
  assert.deepEqual(currentFunding([item], ID), [item]);
  assert.equal(remainingFundingAtomic(item), "100000000");
  assert.equal(
    remainingFundingAtomic({ ...item, allocatedAtomic: "100000000" }),
    "0",
  );
  assert.equal(
    remainingFundingAtomic({ ...item, allocatedAtomic: "10000" }),
    "99990000",
  );
  assert.equal(
    remainingFundingAtomic({ ...item, allocatedAtomic: "invalid" }),
    "0",
  );
  assert.deepEqual(
    currentFunding(
      [
        { ...item, kind: "redemption" },
        { ...item, projectId: "other" },
        { ...item, operationId: "faucet" },
        { ...item, procurementId: ID },
      ],
      ID,
    ),
    [],
  );
});
test("separate role homes and signature kinds never grant AI or human identity to Foundation", () => {
  assert.equal(roleHome("human_approver"), "/admin/overview");
  assert.equal(roleHome("foundation"), "/foundation/overview");
  assert.equal(roleHome("service_ai"), null);
  for (const kind of ["release", "settlement"] as const) {
    assert.equal(mayPrepareSigning("human_approver", kind), true);
    assert.equal(mayPrepareSigning("foundation", kind), false);
    assert.equal(mayPrepareSigning("recipient", kind), false);
  }
  assert.equal(mayPrepareSigning("human_approver", "ai_final"), false);
  assert.equal(mayPrepareSigning("service_ai", "ai_final"), true);
});
