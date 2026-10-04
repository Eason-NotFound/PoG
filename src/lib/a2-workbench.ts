/** Browser-side presentation only. Authority, nonces and hashes stay in A2. */
export type A2User = {
  id: string;
  username: string;
  role: string;
  walletAddress: string;
};

export type A2Project = {
  id: string;
  businessId: string;
  title: string;
  publicSummary: string;
  chainState: { status: string; verified: boolean };
};

export type A2Procurement = {
  id: string;
  projectId: string;
  businessId: string;
  title: string;
  vendorWallet: string;
  budgetCapAtomic: string;
  chainState: { status: string; verified: boolean };
};

export type A2Operation = {
  operationId: string;
  status: string;
  operationKind: string;
  chainVerified: boolean;
  errorCode?: string;
  errorMessage?: string;
  steps?: {
    stepIndex: number;
    kind: string;
    status: string;
    expectedEvent?: string;
    transaction?: {
      status: string;
      transactionHash?: string;
      receiptStatus?: number;
      blockNumber?: number;
      blockHash?: string;
      canonical: boolean;
    };
  }[];
};

export type A2Document = {
  id: string;
  versionId: string;
  procurementId: string;
  category: string;
  originalFilename: string;
  keccak256: string;
  sha256: string;
  referenced: boolean;
};

export type A2SigningRequest = {
  id: string;
  procurementId: string;
  kind:
    "ai_pre" | "ai_final" | "reserve" | "receipt" | "release" | "settlement";
  status: string;
  signer: string;
  nonce: string;
  deadline: string;
  digest: string;
  policyEpoch: number;
  synthetic: boolean;
  typedData: Record<string, unknown> | null;
};

export type A2Ledger = {
  projectId: string;
  businessId: string;
  asset: string;
  depositsAtomic: string;
  reservedAtomic: string;
  releasedAtomic: string;
  returnedAtomic: string;
  refundedAtomic: string;
  refundPoolAtomic: string;
  currentCallerDonorCreditAtomic: string | null;
  chainVerified: boolean;
};

const UINT256_MAX = (1n << 256n) - 1n;
const ATOMIC_RE = /^(0|[1-9][0-9]*)$/;

export function atomicValue(value: string): bigint {
  if (!ATOMIC_RE.test(value))
    throw new Error("Invalid canonical atomic amount");
  const atomic = BigInt(value);
  if (atomic > UINT256_MAX) throw new Error("Amount exceeds uint256");
  return atomic;
}

/** No parseFloat / Number: six decimals are preserved exactly. */
export function decimalToAtomic(value: string): string {
  if (!/^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$/.test(value)) {
    throw new Error(
      "Enter a positive amount with at most six decimal places / 金額最多六位小數",
    );
  }
  const [whole, fraction = ""] = value.split(".");
  const atomic = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0"));
  if (atomic === 0n || atomic > UINT256_MAX) {
    throw new Error(
      "Amount must be positive and within uint256 / 金額須大於零",
    );
  }
  return atomic.toString();
}

export function formatAtomic(value: string): string {
  const atomic = atomicValue(value);
  const fraction = (atomic % 1000000n)
    .toString()
    .padStart(6, "0")
    .replace(/0+$/, "");
  return `${atomic / 1000000n}${fraction ? `.${fraction}` : ""}`;
}

export function freeLockedAtomic(ledger: A2Ledger): string {
  if (!ledger.chainVerified) throw new Error("Ledger is not chain verified");
  const free =
    atomicValue(ledger.depositsAtomic) +
    atomicValue(ledger.returnedAtomic) -
    atomicValue(ledger.releasedAtomic) -
    atomicValue(ledger.reservedAtomic) -
    atomicValue(ledger.refundedAtomic);
  if (free < 0n || free > UINT256_MAX) throw new Error("Inconsistent ledger");
  return free.toString();
}

export function isChainConfirmed(operation: A2Operation): boolean {
  return (
    operation.status === "confirmed" &&
    operation.chainVerified === true &&
    Boolean(operation.steps?.length) &&
    operation.steps!.every(
      (step) =>
        step.status === "confirmed" &&
        step.transaction?.receiptStatus === 1 &&
        step.transaction.canonical === true &&
        /^0x[0-9a-fA-F]{64}$/.test(step.transaction.transactionHash ?? "") &&
        /^0x[0-9a-fA-F]{64}$/.test(step.transaction.blockHash ?? "") &&
        Number.isSafeInteger(step.transaction.blockNumber) &&
        (step.transaction.blockNumber ?? -1) >= 0,
    )
  );
}

export function isPendingOperation(operation: A2Operation): boolean {
  return [
    "queued",
    "prepared",
    "sending",
    "processing",
    "broadcast",
    "submitted",
    "confirming",
    "pending",
  ].includes(operation.status);
}

export function deploymentIdentity(
  config: Record<string, unknown>,
): string | null {
  if (typeof config.runId !== "string" || typeof config.instanceId !== "string")
    return null;
  return `${config.runId}:${config.instanceId}`;
}

export type A2AsyncContext = {
  identity: string | null;
  deployment: string | null;
};

export type A2AsyncScope = Readonly<A2AsyncContext & { generation: number }>;

export class StaleA2Response extends Error {
  constructor() {
    super(
      "Discarded response from an obsolete identity or deployment generation",
    );
    this.name = "StaleA2Response";
  }
}

/** One gate shared by reads, writes, polling, errors and finalizers. */
export class A2RequestGeneration {
  private generation = 0;

  invalidate(): void {
    this.generation += 1;
  }

  capture(context: A2AsyncContext): A2AsyncScope {
    return Object.freeze({ ...context, generation: this.generation });
  }

  current(scope: A2AsyncScope, context: A2AsyncContext): boolean {
    return (
      scope.generation === this.generation &&
      scope.identity === context.identity &&
      scope.deployment === context.deployment
    );
  }

  assertCurrent(scope: A2AsyncScope, context: A2AsyncContext): void {
    if (!this.current(scope, context)) throw new StaleA2Response();
  }
}

/** Check rejection as well as resolution; stale 401 must not log out a new user. */
export async function guardA2Response<T>(
  generation: A2RequestGeneration,
  scope: A2AsyncScope,
  context: () => A2AsyncContext,
  work: () => Promise<T>,
): Promise<T> {
  generation.assertCurrent(scope, context());
  try {
    const result = await work();
    generation.assertCurrent(scope, context());
    return result;
  } catch (problem) {
    generation.assertCurrent(scope, context());
    throw problem;
  }
}

/** Recheck at the actual state commit, after the await continuation is scheduled. */
export function commitA2Response(
  generation: A2RequestGeneration,
  scope: A2AsyncScope,
  context: () => A2AsyncContext,
  commit: () => void,
): boolean {
  if (!generation.current(scope, context())) return false;
  commit();
  return true;
}

export function mayPrepareSigning(
  role: string,
  kind: A2SigningRequest["kind"],
): boolean {
  return (
    (role === "human_approver" &&
      ["reserve", "release", "settlement"].includes(kind)) ||
    (role === "recipient" && kind === "receipt") ||
    (role === "service_ai" && ["ai_pre", "ai_final"].includes(kind))
  );
}

export function maySignRequest(
  user: A2User,
  request: A2SigningRequest,
): boolean {
  return (
    mayPrepareSigning(user.role, request.kind) &&
    user.walletAddress.toLowerCase() === request.signer.toLowerCase()
  );
}

export function validUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

export function publicJson(value: unknown): string {
  return JSON.stringify(
    value,
    (key, item: unknown) =>
      /^(token|signature|password|privateKey|authorization)$/i.test(key)
        ? "[redacted]"
        : item,
    2,
  );
}
