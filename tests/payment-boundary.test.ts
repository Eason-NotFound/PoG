import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PAYMENT_BOUNDARY_VERSION,
  PAYMENT_PHASE_LABELS,
  atomicToHkdCents,
  comparePaymentRetry,
  formatHkdCents,
  hkdCentsToAtomic,
  parseHkdDisplay,
  paymentExecutionAvailability,
  previewMockConversion,
  validatePaymentIntent,
  validateReleaseReference,
  type AuthorizedChainProofReference,
  type PaymentContext,
  type PaymentIntent,
  type PaymentScope,
} from "../src/lib/payment-boundary";

const bytes32 = (digit: string) => `0x${digit.repeat(64)}`;
const address = (digit: string) => `0x${digit.repeat(40)}`;
const uuid = (digit: string) =>
  `${digit.repeat(8)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(12)}`;
function scope(): PaymentScope {
  return {
    chainId: "31337",
    runId: "a".repeat(32),
    instanceId: bytes32("a"),
    registryAddress: address("1"),
    escrowAddress: address("2"),
    tokenAddress: address("3"),
    namespaceId: uuid("1"),
    projectApiId: uuid("2"),
    procurementApiId: uuid("3"),
    projectChainId: bytes32("b"),
    procurementChainId: bytes32("c"),
    foundationAddress: address("4"),
    fixedSupplierAddress: address("5"),
    invoiceAmountAtomic: "72000000",
  };
}
function proof(): AuthorizedChainProofReference {
  const s = scope();
  return {
    ...s,
    operationId: uuid("4"),
    transactionHash: bytes32("d"),
    blockNumber: "30",
    blockHash: bytes32("e"),
    operationStatus: "confirmed",
    receiptStatus: 1,
    canonical: true,
    logIndex: "2",
    event: {
      name: "FundsReleasedToFoundation",
      emitter: s.escrowAddress,
      projectId: s.projectChainId,
      procurementId: s.procurementChainId,
      foundation: s.foundationAddress,
      invoiceAmount: "72000000",
      unusedReservation: "8000000",
    },
  };
}
function context(): PaymentContext {
  return {
    ...scope(),
    phase: "foundation_released",
    procurementState: 9,
    releaseProof: proof(),
    redemptionOperationId: null,
    supplierPaymentOperationId: null,
  };
}
function intent(): PaymentIntent {
  return {
    ...scope(),
    version: PAYMENT_BOUNDARY_VERSION,
    action: "redeem_to_hkd",
    principalId: uuid("5"),
    idempotencyKey: "redeem-invoice-72",
    amountAtomic: "72000000",
    supplierAddress: address("5"),
    releaseOperationId: uuid("4"),
    redemptionOperationId: null,
    supplierPaymentOperationId: null,
  };
}
function invalid(i: PaymentIntent, c: PaymentContext, expected: string) {
  const result = validatePaymentIntent(i, c);
  assert.equal(result.validationScope, "structural_only");
  assert.equal(result.valid, false);
  assert.ok(result.issues.includes(expected), JSON.stringify(result.issues));
}

test("HKD cents use exact 10000-atomic conversion and two-decimal display", () => {
  assert.equal(parseHkdDisplay("72"), "7200");
  assert.equal(parseHkdDisplay("72.0"), "7200");
  assert.equal(parseHkdDisplay("72.01"), "7201");
  assert.equal(hkdCentsToAtomic("7201"), "72010000");
  assert.equal(atomicToHkdCents("72010000"), "7201");
  assert.equal(formatHkdCents("7201"), "72.01");
  assert.equal(formatHkdCents("1"), "0.01");
  assert.equal(hkdCentsToAtomic("0"), "0");
});
test("large safe monetary values do not use JavaScript Number", () => {
  const cents = "900719925474099312345";
  assert.equal(atomicToHkdCents(hkdCentsToAtomic(cents)), cents);
});
for (const bad of [
  "72.001",
  "-72",
  "+72",
  " 72",
  "72 ",
  "1e2",
  "072",
  ".50",
  "1.",
  "NaN",
  "Infinity",
  "",
  72,
]) {
  test(`HKD input rejects noncanonical display ${JSON.stringify(bad)}`, () => {
    assert.throws(() => parseHkdDisplay(bad as string));
  });
}
for (const bad of ["01", "-1", "+1", "1.0", "1e6", "", 72]) {
  test(`atomic/cents transport rejects invalid integer ${JSON.stringify(bad)}`, () => {
    assert.throws(() => hkdCentsToAtomic(bad as string));
    assert.throws(() => atomicToHkdCents(bad as string));
  });
}
test("sub-cent redemption is explicitly rejected rather than rounded", () => {
  assert.throws(() => atomicToHkdCents("72000001"), /sub_cent_amount/);
  assert.throws(
    () =>
      previewMockConversion({ direction: "mock_to_hkd", amount: "72000001" }),
    /sub_cent_amount/,
  );
});
test("uint256 overflow is rejected before conversion", () => {
  const max = (1n << 256n) - 1n;
  assert.throws(() => atomicToHkdCents((max + 1n).toString()));
  assert.throws(() => hkdCentsToAtomic((max / 10000n + 1n).toString()));
});
test("1:1 mock quote is not funding, mint, transfer or payment", () => {
  const result = previewMockConversion({
    direction: "hkd_to_mock",
    amount: "100.00",
  });
  assert.equal(result.mode, "simulation");
  assert.equal(result.amountAtomic, "100000000");
  assert.equal(result.hkdCents, "10000");
  assert.equal(result.executed, false);
  assert.equal(result.effect, "quote_only_no_mint_transfer_lock_or_payment");
  assert.equal("operationId" in result, false);
  assert.equal("phase" in result, false);
  assert.equal("transactionHash" in result, false);
  assert.equal(
    previewMockConversion({ direction: "mock_to_hkd", amount: "72000000" })
      .hkdDisplay,
    "72.00",
  );
});
test("quote rejects zero and unknown conversion directions", () => {
  assert.throws(() =>
    previewMockConversion({ direction: "hkd_to_mock", amount: "0.00" }),
  );
  assert.throws(() =>
    previewMockConversion({
      direction: "other" as "hkd_to_mock",
      amount: "72.00",
    }),
  );
});
test("all seven UI statuses distinguish custody, release, redemption, supplier and settlement", () => {
  assert.equal(Object.keys(PAYMENT_PHASE_LABELS).length, 7);
  assert.match(PAYMENT_PHASE_LABELS.receipt_confirmed, /仍冻结/);
  assert.match(PAYMENT_PHASE_LABELS.foundation_released, /供应商尚未收款/);
  assert.match(PAYMENT_PHASE_LABELS.redeemed_hkd, /供应商尚未收款/);
  assert.match(PAYMENT_PHASE_LABELS.supplier_paid, /尚未完成/);
  assert.match(PAYMENT_PHASE_LABELS.settled, /不代表真实/);
});
test("a structurally valid release reference never confers execution permission", () => {
  assert.deepEqual(validateReleaseReference(proof(), scope()), {
    validationScope: "structural_only",
    valid: true,
    issues: [],
  });
  assert.deepEqual(validatePaymentIntent(intent(), context()), {
    validationScope: "structural_only",
    valid: true,
    issues: [],
  });
  assert.equal(paymentExecutionAvailability().available, false);
  assert.equal(paymentExecutionAvailability().executionDisabled, true);
});
test("receipt confirmation cannot redeem or pay a supplier", () => {
  const c = {
    ...context(),
    phase: "receipt_confirmed" as const,
    procurementState: 6,
    releaseProof: null,
  };
  invalid(intent(), c, "foundation_not_released");
  invalid(
    { ...intent(), action: "pay_supplier" },
    c,
    "supplier_payment_wrong_phase",
  );
});
test("UI phase or transaction hash alone cannot stand in for release", () => {
  invalid(
    intent(),
    { ...context(), releaseProof: null },
    "release_proof_missing",
  );
});
for (const [name, patch] of [
  ["queued", { operationStatus: "queued" }],
  ["failed", { operationStatus: "failed", receiptStatus: 0 }],
  ["unknown outcome", { operationStatus: "requires_attention" }],
  ["noncanonical", { canonical: false }],
  ["boolean string", { canonical: "true" }],
  ["status string", { receiptStatus: "1" }],
  ["invalidated instance", { operationStatus: "invalidated_instance" }],
] as const) {
  test(`release reference rejects ${name}`, () => {
    invalid(
      intent(),
      {
        ...context(),
        releaseProof: { ...proof(), ...patch } as AuthorizedChainProofReference,
      },
      "release_not_confirmed_canonical",
    );
  });
}
for (const [field, value] of [
  ["chainId", "1"],
  ["runId", "b".repeat(32)],
  ["instanceId", bytes32("f")],
  ["registryAddress", address("6")],
  ["escrowAddress", address("7")],
  ["tokenAddress", address("8")],
  ["namespaceId", uuid("6")],
  ["projectApiId", uuid("7")],
  ["procurementApiId", uuid("8")],
  ["projectChainId", bytes32("1")],
  ["procurementChainId", bytes32("2")],
  ["foundationAddress", address("9")],
  ["fixedSupplierAddress", address("a")],
  ["invoiceAmountAtomic", "73000000"],
] as const) {
  test(`release reference is bound to current ${field}`, () => {
    invalid(
      intent(),
      { ...context(), releaseProof: { ...proof(), [field]: value } },
      `proof_scope_mismatch:${field}`,
    );
  });
}
for (const [field, value, issue] of [
  ["emitter", address("1"), "wrong_event_emitter"],
  ["projectId", bytes32("1"), "wrong_event_project"],
  ["procurementId", bytes32("1"), "wrong_event_procurement"],
  ["foundation", address("1"), "wrong_event_foundation"],
  ["invoiceAmount", "73000000", "wrong_event_invoice_amount"],
] as const) {
  test(`release event rejects mismatched ${field}`, () => {
    const p = proof();
    invalid(
      intent(),
      {
        ...context(),
        releaseProof: { ...p, event: { ...p.event, [field]: value } },
      },
      issue,
    );
  });
}
test("wrong event name cannot prove release", () => {
  const p = proof();
  p.event.name = "MockPaymentConfirmed" as "FundsReleasedToFoundation";
  invalid(intent(), { ...context(), releaseProof: p }, "wrong_release_event");
});
test("malformed proof identifiers and zero addresses are rejected", () => {
  assert.equal(
    validateReleaseReference({ ...proof(), transactionHash: "0x123" }, scope())
      .valid,
    false,
  );
  assert.equal(
    validateReleaseReference({ ...proof(), blockHash: bytes32("0") }, scope())
      .valid,
    false,
  );
  assert.equal(
    validateReleaseReference({ ...proof(), blockNumber: "01" }, scope()).valid,
    false,
  );
  assert.equal(
    validateReleaseReference({ ...proof(), operationId: "not-a-uuid" }, scope())
      .valid,
    false,
  );
  assert.equal(
    validateReleaseReference(
      { ...proof(), tokenAddress: address("0") },
      scope(),
    ).valid,
    false,
  );
});
test("invoice ceiling, full-only payment and sub-cent amounts are distinct rejections", () => {
  invalid(
    { ...intent(), amountAtomic: "73000000" },
    context(),
    "amount_exceeds_invoice",
  );
  invalid(
    { ...intent(), amountAtomic: "71000000" },
    context(),
    "partial_payment_unsupported",
  );
  invalid(
    { ...intent(), amountAtomic: "72000001" },
    context(),
    "invalid_or_sub_cent_amount",
  );
  invalid(
    { ...intent(), amountAtomic: "0" },
    context(),
    "invalid_or_sub_cent_amount",
  );
});
test("wrong fixed supplier or release dependency cannot redirect payment", () => {
  invalid(
    { ...intent(), supplierAddress: address("6") },
    context(),
    "wrong_fixed_supplier",
  );
  invalid(
    { ...intent(), releaseOperationId: uuid("6") },
    context(),
    "wrong_release_operation",
  );
});
test("intent rejects stale namespace and mismatched procurement even with a current proof", () => {
  invalid(
    { ...intent(), namespaceId: uuid("8") },
    context(),
    "intent_scope_mismatch:namespaceId",
  );
  invalid(
    { ...intent(), procurementChainId: bytes32("f") },
    context(),
    "intent_scope_mismatch:procurementChainId",
  );
});
test("payment intent version, principal and idempotency key are guarded", () => {
  invalid(
    { ...intent(), version: "future" as typeof PAYMENT_BOUNDARY_VERSION },
    context(),
    "unsupported_payment_version",
  );
  invalid(
    { ...intent(), principalId: "foundation" },
    context(),
    "invalid_principal_id",
  );
  invalid(
    { ...intent(), idempotencyKey: "short" },
    context(),
    "invalid_idempotency_key",
  );
});
test("supplier payment requires confirmed redemption and cannot repeat a started payment", () => {
  const c = {
    ...context(),
    phase: "redeemed_hkd" as const,
    redemptionOperationId: uuid("6"),
  };
  const i = {
    ...intent(),
    action: "pay_supplier" as const,
    redemptionOperationId: uuid("6"),
  };
  assert.equal(validatePaymentIntent(i, c).valid, true);
  invalid(
    { ...i, redemptionOperationId: null },
    c,
    "confirmed_redemption_dependency_missing",
  );
  invalid(
    i,
    { ...c, supplierPaymentOperationId: uuid("7") },
    "supplier_payment_already_started",
  );
  assert.equal(paymentExecutionAvailability().available, false);
});
test("settlement recording requires separate redeemed and supplier-paid references", () => {
  const c = {
    ...context(),
    phase: "supplier_paid" as const,
    redemptionOperationId: uuid("6"),
    supplierPaymentOperationId: uuid("7"),
  };
  const i = {
    ...intent(),
    action: "record_settlement" as const,
    redemptionOperationId: uuid("6"),
    supplierPaymentOperationId: uuid("7"),
  };
  assert.equal(validatePaymentIntent(i, c).valid, true);
  invalid(
    { ...i, supplierPaymentOperationId: null },
    c,
    "confirmed_supplier_payment_dependency_missing",
  );
  invalid(i, { ...c, phase: "foundation_released" }, "settlement_wrong_phase");
});
test("redemption replay is compared before current-phase guards, never re-executed here", () => {
  const i = intent();
  assert.equal(comparePaymentRetry(null, i), "new");
  assert.equal(comparePaymentRetry(i, { ...i }), "replay");
  invalid(
    i,
    { ...context(), redemptionOperationId: uuid("6") },
    "redemption_already_started_or_invalid_dependency",
  );
  assert.equal(
    paymentExecutionAvailability().code,
    "payment_adapter_unavailable",
  );
});
for (const [field, value] of [
  ["amountAtomic", "71000000"],
  ["action", "pay_supplier"],
  ["principalId", uuid("6")],
  ["projectApiId", uuid("6")],
  ["procurementApiId", uuid("6")],
  ["runId", "b".repeat(32)],
  ["instanceId", bytes32("f")],
  ["supplierAddress", address("6")],
  ["invoiceAmountAtomic", "73000000"],
  ["releaseOperationId", uuid("6")],
  ["redemptionOperationId", uuid("6")],
  ["supplierPaymentOperationId", uuid("6")],
  ["idempotencyKey", "another-unique-key"],
] as const) {
  test(`same-key material comparison detects altered ${field}`, () => {
    assert.equal(
      comparePaymentRetry(intent(), {
        ...intent(),
        [field]: value,
      } as PaymentIntent),
      "conflict",
    );
  });
}
test("address casing changes no bytes and is not an idempotency material conflict", () => {
  const i = { ...intent(), supplierAddress: address("a") };
  assert.equal(
    comparePaymentRetry(i, {
      ...i,
      supplierAddress: i.supplierAddress.toUpperCase().replace("0X", "0x"),
    }),
    "replay",
  );
});
test("boundary returns no private evidence, bank details, wallet key or execution success", () => {
  const result = paymentExecutionAvailability();
  assert.equal("signature" in result, false);
  assert.equal("privateKey" in result, false);
  assert.equal("bankAccount" in result, false);
  assert.equal("executed" in result, false);
  assert.equal("operationId" in result, false);
});
