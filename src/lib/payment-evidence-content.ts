/** Read-only validation of the API's fixed canonical payment evidence schema.
 * Never sanitize or regenerate bytes that are themselves a hash commitment.
 */
const BASE = [
  "schemaVersion",
  "kind",
  "mode",
  "binding",
  "projectId",
  "projectChainId",
  "procurementId",
  "procurementChainId",
  "foundation",
  "treasury",
  "vendor",
  "token",
  "amountAtomic",
  "hkdCents",
  "invoiceDocumentVersionId",
  "invoiceHash",
  "releaseOperationId",
  "releaseProof",
  "redemptionResourceId",
  "redemptionOperationId",
  "redemptionProof",
  "redemptionJournalId",
];
const PAYMENT = ["paymentResourceId", "paymentOperationId", "paymentJournalId"];
const BINDING = [
  "namespaceId",
  "runId",
  "instanceId",
  "chainId",
  "registry",
  "escrow",
  "token",
];
const PROOF = [
  "operationId",
  "transactionId",
  "txHash",
  "receiptStatus",
  "blockNumber",
  "blockHash",
  "emitter",
  "logIndex",
  "caller",
  "target",
];
const UUID =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const HASH = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const UINT = /^(0|[1-9][0-9]*)$/;
const MAX = (1n << 256n) - 1n;
function object(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function exact(
  value: unknown,
  keys: string[],
): value is Record<string, unknown> {
  return (
    object(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}
function uint(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 78 &&
    UINT.test(value) &&
    BigInt(value) <= MAX
  );
}
function safe(value: unknown, token: string | null): boolean {
  if (typeof value === "string")
    return (
      /^[\x20-\x7e]*$/.test(value) &&
      value !== token &&
      !/0x[0-9a-fA-F]{130}(?![0-9a-fA-F])|Bearer\s|https?:\/\/|\/(?:Users|private|tmp|var|home|etc)\//i.test(
        value,
      )
    );
  return (
    object(value) && Object.values(value).every((item) => safe(item, token))
  );
}
function sorted(value: unknown): unknown {
  return object(value)
    ? Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, sorted(value[key])]),
      )
    : value;
}
export function validCanonicalPaymentEvidence(
  bytes: Uint8Array,
  token: string | null,
): boolean {
  try {
    const text = new TextDecoder("utf-8", {
      fatal: true,
      ignoreBOM: true,
    }).decode(bytes);
    const data: unknown = JSON.parse(text);
    if (!object(data)) return false;
    const payment = data.schemaVersion === "pog-supplier-payment-evidence-v1";
    if (
      (!payment && data.schemaVersion !== "pog-conversion-evidence-v1") ||
      !exact(data, payment ? [...BASE, ...PAYMENT] : BASE) ||
      data.mode !== "simulation" ||
      data.kind !== (payment ? "supplier_payment" : "conversion") ||
      !safe(data, token) ||
      !exact(data.binding, BINDING) ||
      data.binding.chainId !== "31337"
    )
      return false;
    const binding = data.binding;
    const stringMatches = (value: unknown, re: RegExp) =>
      typeof value === "string" && re.test(value);
    if (
      !stringMatches(binding.namespaceId, UUID) ||
      !["registry", "escrow", "token"].every((key) =>
        stringMatches(binding[key], ADDRESS),
      )
    )
      return false;
    if (
      ![
        "projectId",
        "procurementId",
        "invoiceDocumentVersionId",
        "releaseOperationId",
        "redemptionResourceId",
        "redemptionOperationId",
        "redemptionJournalId",
        ...(payment ? PAYMENT : []),
      ].every((key) => stringMatches(data[key], UUID)) ||
      !["foundation", "treasury", "vendor", "token"].every((key) =>
        stringMatches(data[key], ADDRESS),
      ) ||
      !["projectChainId", "procurementChainId", "invoiceHash"].every((key) =>
        stringMatches(data[key], HASH),
      ) ||
      !uint(data.amountAtomic) ||
      !uint(data.hkdCents) ||
      BigInt(data.hkdCents) <= 0n ||
      BigInt(data.amountAtomic) !== BigInt(data.hkdCents) * 10000n
    )
      return false;
    for (const key of ["releaseProof", "redemptionProof"]) {
      const proof = data[key];
      if (
        !exact(proof, PROOF) ||
        proof.receiptStatus !== "1" ||
        !["operationId", "transactionId"].every((name) =>
          stringMatches(proof[name], UUID),
        ) ||
        !["txHash", "blockHash"].every((name) =>
          stringMatches(proof[name], HASH),
        ) ||
        !["emitter", "caller", "target"].every((name) =>
          stringMatches(proof[name], ADDRESS),
        ) ||
        !uint(proof.blockNumber) ||
        !uint(proof.logIndex)
      )
        return false;
    }
    // Equality also rejects duplicate keys, alternate whitespace/escaping and BOMs.
    return text === JSON.stringify(sorted(data));
  } catch {
    return false;
  }
}
