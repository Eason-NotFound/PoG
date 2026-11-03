/**
 * Payment integration v0.1: read-only DTO and structural checks, never execution.
 * These checks do NOT authenticate an API response, verify an on-chain receipt,
 * prove a signature or authorize money. The trusted backend must do all of those.
 * In particular, a browser-provided canonical boolean/tx hash is not a release.
 */
export const PAYMENT_BOUNDARY_VERSION = "pog-payment-handoff-v0.1" as const;
export const MOCK_HKD_DECIMALS = 6;
const UINT256_MAX = (1n << 256n) - 1n;
const ATOMIC_PER_CENT = 10_000n;
const DECIMAL_INTEGER = /^(0|[1-9][0-9]*)$/;
const HEX32 = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export type PaymentPhase =
  | "funding_minted"
  | "donated_locked"
  | "receipt_confirmed"
  | "foundation_released"
  | "redeemed_hkd"
  | "supplier_paid"
  | "settled";

export const PAYMENT_PHASE_LABELS: Readonly<Record<PaymentPhase, string>> = {
  funding_minted: "模拟兑换铸币已确认；尚未捐入项目",
  donated_locked: "捐款已入项目 Escrow 并冻结",
  receipt_confirmed: "Recipient 已确认收货；资金仍冻结",
  foundation_released:
    "最终人工审批后的链上 Foundation 放款已确认；供应商尚未收款",
  redeemed_hkd: "平台模拟 HKD 兑回已对账；供应商尚未收款",
  supplier_paid: "模拟供应商付款已对账；链上结算确认尚未完成",
  settled: "链上人工 mock payment 确认已完成；不代表真实银行付款",
};

function uint(value: unknown, label: string, positive = false): bigint {
  if (
    typeof value !== "string" ||
    value.length > 78 ||
    !DECIMAL_INTEGER.test(value)
  )
    throw new Error(`${label}: canonical decimal integer string required`);
  const parsed = BigInt(value);
  if (parsed > UINT256_MAX || (positive && parsed === 0n))
    throw new Error(`${label}: outside uint256/positive range`);
  return parsed;
}

/** Monetary transport is strings. Number, exponent, sign and rounding are rejected. */
export function hkdCentsToAtomic(cents: string): string {
  const amount = uint(cents, "hkdCents") * ATOMIC_PER_CENT;
  if (amount > UINT256_MAX)
    throw new Error("amountAtomic: outside uint256 range");
  return amount.toString();
}

export function atomicToHkdCents(amountAtomic: string): string {
  const amount = uint(amountAtomic, "amountAtomic");
  if (amount % ATOMIC_PER_CENT !== 0n)
    throw new Error(
      "sub_cent_amount: no silent rounding or unsupported partial redemption",
    );
  return (amount / ATOMIC_PER_CENT).toString();
}

export function parseHkdDisplay(display: string): string {
  if (
    typeof display !== "string" ||
    display.length > 80 ||
    !/^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$/.test(display)
  )
    throw new Error(
      "HKD display: unsigned decimal with at most two decimal places required",
    );
  const [whole, fraction = ""] = display.split(".");
  const cents = (
    BigInt(whole) * 100n +
    BigInt(fraction.padEnd(2, "0"))
  ).toString();
  hkdCentsToAtomic(cents); // Also enforce the chain amount's uint256 bound.
  return cents;
}

export function formatHkdCents(cents: string): string {
  const amount = uint(cents, "hkdCents");
  return `${amount / 100n}.${(amount % 100n).toString().padStart(2, "0")}`;
}

export function previewMockConversion(input: {
  direction: "hkd_to_mock" | "mock_to_hkd";
  amount: string;
}) {
  if (!["hkd_to_mock", "mock_to_hkd"].includes(input.direction))
    throw new Error("unsupported conversion direction");
  const hkdCents =
    input.direction === "hkd_to_mock"
      ? parseHkdDisplay(input.amount)
      : atomicToHkdCents(input.amount);
  uint(hkdCents, "hkdCents", true);
  return {
    version: PAYMENT_BOUNDARY_VERSION,
    mode: "simulation" as const,
    direction: input.direction,
    rate: "1 HKD = 1 mHKD" as const,
    feeHkdCents: "0" as const,
    hkdCents,
    hkdDisplay: formatHkdCents(hkdCents),
    amountAtomic: hkdCentsToAtomic(hkdCents),
    executed: false as const,
    effect: "quote_only_no_mint_transfer_lock_or_payment" as const,
  };
}

export type DeploymentBinding = {
  chainId: string;
  runId: string;
  instanceId: string;
  registryAddress: string;
  escrowAddress: string;
  tokenAddress: string;
};

export type PaymentScope = DeploymentBinding & {
  namespaceId: string;
  projectApiId: string;
  procurementApiId: string;
  projectChainId: string;
  procurementChainId: string;
  foundationAddress: string;
  fixedSupplierAddress: string;
  invoiceAmountAtomic: string;
};

/**
 * Reference supplied by an authenticated, independently verifying API, not a UI.
 * The name describes its intended producer, not a capability minted by this TS.
 * Event fields below match the existing V2 event (no invented vendor/asset fields).
 */
export type AuthorizedChainProofReference = PaymentScope & {
  operationId: string;
  transactionHash: string;
  blockNumber: string;
  blockHash: string;
  operationStatus:
    | "confirmed"
    | "queued"
    | "submitted"
    | "failed"
    | "requires_attention"
    | "invalidated_instance";
  receiptStatus: 0 | 1;
  canonical: boolean;
  logIndex: string;
  event: {
    name: "FundsReleasedToFoundation";
    emitter: string;
    procurementId: string;
    projectId: string;
    foundation: string;
    invoiceAmount: string;
    unusedReservation: string;
  };
};

/** All fields come from the server's current namespace and confirmed read model. */
export type PaymentContext = PaymentScope & {
  phase: PaymentPhase;
  procurementState: number;
  releaseProof: AuthorizedChainProofReference | null;
  redemptionOperationId: string | null;
  supplierPaymentOperationId: string | null;
};

/** Internal server-derived plan, NOT the body accepted from a browser. */
export type PaymentIntent = PaymentScope & {
  version: typeof PAYMENT_BOUNDARY_VERSION;
  action: "redeem_to_hkd" | "pay_supplier" | "record_settlement";
  principalId: string;
  idempotencyKey: string;
  amountAtomic: string;
  supplierAddress: string;
  releaseOperationId: string;
  redemptionOperationId: string | null;
  supplierPaymentOperationId: string | null;
};

const SCOPE_KEYS = [
  "chainId",
  "runId",
  "instanceId",
  "registryAddress",
  "escrowAddress",
  "tokenAddress",
  "namespaceId",
  "projectApiId",
  "procurementApiId",
  "projectChainId",
  "procurementChainId",
  "foundationAddress",
  "fixedSupplierAddress",
  "invoiceAmountAtomic",
] as const;
const ADDRESS_KEYS = [
  "registryAddress",
  "escrowAddress",
  "tokenAddress",
  "foundationAddress",
  "fixedSupplierAddress",
] as const;
const UUID_KEYS = ["namespaceId", "projectApiId", "procurementApiId"] as const;

function same(a: unknown, b: unknown): boolean {
  return (
    typeof a === "string" &&
    typeof b === "string" &&
    a.toLowerCase() === b.toLowerCase()
  );
}

function scopeIssues(scope: PaymentScope): string[] {
  const issues: string[] = [];
  if (scope.chainId !== "31337") issues.push("unsupported_chain");
  if (typeof scope.runId !== "string" || !/^[0-9a-f]{32}$/.test(scope.runId))
    issues.push("invalid_run_id");
  for (const key of [
    "instanceId",
    "projectChainId",
    "procurementChainId",
  ] as const)
    if (!HEX32.test(scope[key]) || /^0x0+$/.test(scope[key]))
      issues.push(`invalid_${key}`);
  for (const key of ADDRESS_KEYS)
    if (!ADDRESS.test(scope[key]) || /^0x0+$/.test(scope[key]))
      issues.push(`invalid_${key}`);
  for (const key of UUID_KEYS)
    if (!UUID.test(scope[key])) issues.push(`invalid_${key}`);
  try {
    uint(scope.invoiceAmountAtomic, "invoiceAmountAtomic", true);
  } catch {
    issues.push("invalid_invoice_amount");
  }
  if (same(scope.foundationAddress, scope.fixedSupplierAddress))
    issues.push("foundation_supplier_must_differ");
  return issues;
}

/** Pure consistency check only. A true result MUST NOT enable execution. */
export function validateReleaseReference(
  proof: AuthorizedChainProofReference | null,
  current: PaymentScope,
) {
  const issues = scopeIssues(current);
  if (!proof) issues.push("release_proof_missing");
  else {
    issues.push(...scopeIssues(proof));
    for (const key of SCOPE_KEYS)
      if (!same(proof[key], current[key]))
        issues.push(`proof_scope_mismatch:${key}`);
    if (!UUID.test(proof.operationId))
      issues.push("invalid_release_operation_id");
    if (
      !HEX32.test(proof.transactionHash) ||
      /^0x0+$/.test(proof.transactionHash)
    )
      issues.push("invalid_transaction_hash");
    if (!HEX32.test(proof.blockHash) || /^0x0+$/.test(proof.blockHash))
      issues.push("invalid_block_hash");
    if (
      proof.operationStatus !== "confirmed" ||
      proof.receiptStatus !== 1 ||
      proof.canonical !== true
    )
      issues.push("release_not_confirmed_canonical");
    try {
      uint(proof.blockNumber, "blockNumber");
      uint(proof.logIndex, "logIndex");
    } catch {
      issues.push("invalid_receipt_position");
    }
    if (!proof.event || proof.event.name !== "FundsReleasedToFoundation")
      issues.push("wrong_release_event");
    else {
      if (!same(proof.event.emitter, current.escrowAddress))
        issues.push("wrong_event_emitter");
      if (!same(proof.event.projectId, current.projectChainId))
        issues.push("wrong_event_project");
      if (!same(proof.event.procurementId, current.procurementChainId))
        issues.push("wrong_event_procurement");
      if (!same(proof.event.foundation, current.foundationAddress))
        issues.push("wrong_event_foundation");
      if (proof.event.invoiceAmount !== current.invoiceAmountAtomic)
        issues.push("wrong_event_invoice_amount");
      try {
        uint(proof.event.invoiceAmount, "invoiceAmount", true);
        uint(proof.event.unusedReservation, "unusedReservation");
      } catch {
        issues.push("invalid_event_amount");
      }
    }
  }
  return {
    validationScope: "structural_only" as const,
    valid: issues.length === 0,
    issues,
  };
}

/** DTO guard for a future backend handoff; current executor remains unavailable. */
export function validatePaymentIntent(
  intent: PaymentIntent,
  context: PaymentContext,
) {
  const issues = [
    ...scopeIssues(intent),
    ...validateReleaseReference(context.releaseProof, context).issues,
  ];
  if (intent.version !== PAYMENT_BOUNDARY_VERSION)
    issues.push("unsupported_payment_version");
  for (const key of SCOPE_KEYS)
    if (!same(intent[key], context[key]))
      issues.push(`intent_scope_mismatch:${key}`);
  if (
    typeof intent.idempotencyKey !== "string" ||
    !/^[A-Za-z0-9_-]{8,100}$/.test(intent.idempotencyKey)
  )
    issues.push("invalid_idempotency_key");
  if (!UUID.test(intent.principalId)) issues.push("invalid_principal_id");
  if (!same(intent.supplierAddress, context.fixedSupplierAddress))
    issues.push("wrong_fixed_supplier");
  if (
    !context.releaseProof ||
    intent.releaseOperationId !== context.releaseProof.operationId
  )
    issues.push("wrong_release_operation");
  try {
    const amount = uint(intent.amountAtomic, "amountAtomic", true);
    const invoice = uint(
      context.invoiceAmountAtomic,
      "invoiceAmountAtomic",
      true,
    );
    if (amount > invoice) issues.push("amount_exceeds_invoice");
    else if (amount !== invoice) issues.push("partial_payment_unsupported");
    atomicToHkdCents(intent.amountAtomic);
  } catch {
    issues.push("invalid_or_sub_cent_amount");
  }
  // Final AI/human consent is checked by the Escrow before state 9 can exist.
  // A receipt-only state 6, UI approval or HTTP 202 is never a release.
  if (![9, 10, 11, 12].includes(context.procurementState))
    issues.push("foundation_not_released");
  if (intent.action === "redeem_to_hkd") {
    if (
      context.phase !== "foundation_released" ||
      context.procurementState !== 9
    )
      issues.push("redemption_wrong_phase");
    if (
      context.redemptionOperationId !== null ||
      intent.redemptionOperationId !== null ||
      intent.supplierPaymentOperationId !== null
    )
      issues.push("redemption_already_started_or_invalid_dependency");
  } else if (intent.action === "pay_supplier") {
    if (context.phase !== "redeemed_hkd" || context.procurementState !== 9)
      issues.push("supplier_payment_wrong_phase");
    if (
      !context.redemptionOperationId ||
      !UUID.test(context.redemptionOperationId) ||
      intent.redemptionOperationId !== context.redemptionOperationId
    )
      issues.push("confirmed_redemption_dependency_missing");
    if (
      context.supplierPaymentOperationId !== null ||
      intent.supplierPaymentOperationId !== null
    )
      issues.push("supplier_payment_already_started");
  } else if (intent.action === "record_settlement") {
    if (context.phase !== "supplier_paid" || context.procurementState !== 9)
      issues.push("settlement_wrong_phase");
    if (
      !context.redemptionOperationId ||
      !UUID.test(context.redemptionOperationId) ||
      intent.redemptionOperationId !== context.redemptionOperationId
    )
      issues.push("confirmed_redemption_dependency_missing");
    if (
      !context.supplierPaymentOperationId ||
      !UUID.test(context.supplierPaymentOperationId) ||
      intent.supplierPaymentOperationId !== context.supplierPaymentOperationId
    )
      issues.push("confirmed_supplier_payment_dependency_missing");
  } else issues.push("unsupported_payment_action");
  return {
    validationScope: "structural_only" as const,
    valid: issues.length === 0,
    issues: [...new Set(issues)],
  };
}

/**
 * No persistence or execution. The backend must compare a saved, transactionally
 * claimed operation before state checks, so same-key retries can replay history.
 */
export function comparePaymentRetry(
  previous: PaymentIntent | null,
  next: PaymentIntent,
): "new" | "replay" | "conflict" {
  if (!previous) return "new";
  const keys = [
    ...SCOPE_KEYS,
    "version",
    "action",
    "principalId",
    "idempotencyKey",
    "amountAtomic",
    "supplierAddress",
    "releaseOperationId",
    "redemptionOperationId",
    "supplierPaymentOperationId",
  ] as const;
  return keys.every((key) => {
    if (
      ADDRESS_KEYS.includes(key as (typeof ADDRESS_KEYS)[number]) ||
      key === "supplierAddress"
    )
      return same(previous[key], next[key]);
    return previous[key] === next[key];
  })
    ? "replay"
    : "conflict";
}

/** This milestone has no payment execution adapter. Always fail closed. */
export function paymentExecutionAvailability() {
  return {
    available: false as const,
    code: "payment_adapter_unavailable" as const,
    executionDisabled: true as const,
    message: "模拟报价可查看；未接入已验证的兑换／付款后端，不执行资金操作",
  };
}
