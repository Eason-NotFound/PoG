/** Off-chain single-PO diagnostics only. This module never projects an AI assessment,
 * changes procurement risk, prepares signatures, or executes a funds action. */
import { validUuid, atomicValue, type A2Document } from "./a2-workbench";
import { fullDemoAmount } from "./full-demo-ui";
import { atomicToHkdCents } from "./payment-boundary";
import {
  portalA2Action,
  portalA2Read,
  PortalA2Error,
  type PortalA2State,
  type PortalA2Options,
} from "./portal-a2";

export const AI_DIAGNOSTIC_INTERFACE = "pog-ai-diagnostic-api-v0.1";
export type DiagnosticContextSource = "user_declared" | "demo_generated";
export type DiagnosticContext = {
  quantity: string;
  quoteAmountAtomic: string;
  unitPriceLimitAtomic: string;
  category: string;
  periodStart: string;
  periodEnd: string;
  description: string;
};
export type DiagnosticForm = {
  quantity: string;
  quoteHKD: string;
  unitPriceLimitHKD: string;
  category: string;
  periodStart: string;
  periodEnd: string;
  description: string;
};
export const EMPTY_DIAGNOSTIC_FORM: DiagnosticForm = {
  quantity: "",
  quoteHKD: "",
  unitPriceLimitHKD: "",
  category: "",
  periodStart: "",
  periodEnd: "",
  description: "",
};
/** Explicit demo assumptions, never extracted document facts or stored chain policy. */
export function demoDiagnosticForm(budgetCapAtomic: string): DiagnosticForm {
  const cap = atomicValue(budgetCapAtomic);
  if (cap <= 0n)
    throw new PortalA2Error(
      "invalid_demo_budget",
      "A confirmed positive procurement budget is required",
    );
  const cents = BigInt(atomicToHkdCents(budgetCapAtomic));
  const hkd = `${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
  const result = {
    quantity: "1",
    quoteHKD: hkd,
    unitPriceLimitHKD: hkd,
    category: "consumer_electronics",
    periodStart: "2026-10-01",
    periodEnd: "2026-10-31",
    description:
      "SIMULATION ONLY. Diagnostic metadata for the uploaded Apple order confirmation screenshot; quantities, monetary values and policy dates are demo assumptions, not extracted document facts.",
  };
  diagnosticContext(result);
  return result;
}
export type AiDiagnostic = {
  id: string;
  operationId: string;
  projectId: string;
  procurementId: string;
  purchaseOrderVersionId: string;
  binding: Record<string, unknown>;
  stage: 0;
  purpose: "diagnostic";
  signingEnabled: false;
  status: "queued" | "completed" | "failed" | "requires_attention";
  contextSource: DiagnosticContextSource;
  syntheticInput: boolean;
  modelReal: boolean;
  realAI: boolean;
  reportProvenance: string | null;
  inputHash: string | null;
  evidenceHash: string | null;
  reportHash: string | null;
  reportSha256: string | null;
  reportSizeBytes: number | null;
  contentType: string | null;
  errorCode: string | null;
  diagnosticContext: DiagnosticContext;
  createdAt?: string;
  summary: {
    text: string;
    outcome: 1;
    riskScoreBps: null;
    completeness: "incomplete";
    findings: unknown[];
    missingInputs: unknown[];
  } | null;
  provenance: {
    executionMode: "model_and_rules" | null;
    versions: Record<string, string | null> | null;
    contextSource: string;
    budgetSource: "registry_procurement_budget_cap";
    reviewScope: "single_po_visible_fields";
    allDocumentsReviewed: false;
  } | null;
};
export type AiDiagnosticList = {
  availability: { enabled: boolean; reasonCode: string | null };
  items: AiDiagnostic[];
};

const HASH = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const VERSION_KEYS = [
  "serviceVersion",
  "ruleSetVersion",
  "extractionSchemaVersion",
  "scorePolicyVersion",
  "modelProvider",
  "modelId",
  "modelVersion",
  "promptVersion",
];
function record(v: unknown): v is Record<string, unknown> {
  return Boolean(v) && typeof v === "object" && !Array.isArray(v);
}
function invalid(): never {
  throw new PortalA2Error(
    "invalid_ai_diagnostic",
    "AI diagnostic did not match this procurement and current deployment",
    502,
  );
}
function sameBinding(
  v: unknown,
  state: PortalA2State,
): v is Record<string, unknown> {
  return (
    record(v) &&
    ["namespaceId", "runId", "instanceId", "chainId"].every(
      (key) => v[key] === state.context.binding[key],
    ) &&
    typeof v.registry === "string" &&
    ADDRESS.test(v.registry) &&
    typeof state.context.binding.registry === "string" &&
    v.registry.toLowerCase() === state.context.binding.registry.toLowerCase()
  );
}
export function diagnosticSources(
  state: PortalA2State,
  procurementId: string,
): A2Document[] | null {
  const ws = state.workspaces[procurementId];
  if (
    !ws ||
    !record(ws.originalSourceVersionIds) ||
    ws.procurementId !== procurementId
  )
    return null;
  const categories = [
    ["poDocumentVersionId", "purchase_order"],
    ["requestDocumentVersionId", "request"],
    ["goodsRequestDocumentVersionId", "goods_request"],
  ];
  const documents = categories.map(([key, category]) =>
    ws.documentVersions.find(
      (d) =>
        d.versionId ===
          (ws.originalSourceVersionIds as Record<string, unknown>)[key] &&
        d.procurementId === procurementId &&
        d.category === category &&
        d.referenced === true,
    ),
  );
  return documents.every(Boolean) ? (documents as A2Document[]) : null;
}
export function diagnosticImageDocument(doc: A2Document): boolean {
  return ["image/png", "image/jpeg"].includes(
    String((doc as unknown as Record<string, unknown>).contentType || ""),
  );
}
function nullableHash(v: unknown): boolean {
  return v === null || (typeof v === "string" && HASH.test(v));
}
export function diagnosticContext(form: DiagnosticForm): DiagnosticContext {
  if (!/^[1-9][0-9]*$/.test(form.quantity))
    throw new PortalA2Error(
      "invalid_diagnostic_context",
      "Quantity must be a positive integer",
    );
  atomicValue(form.quantity);
  const validDate = (v: string) =>
    /^\d{4}-\d{2}-\d{2}$/.test(v) &&
    Number.isFinite(Date.parse(`${v}T00:00:00Z`)) &&
    new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v;
  if (
    !validDate(form.periodStart) ||
    !validDate(form.periodEnd) ||
    form.periodEnd < form.periodStart
  )
    throw new PortalA2Error(
      "invalid_diagnostic_context",
      "A valid diagnostic date range is required",
    );
  if (
    !form.category.trim() ||
    form.category.length > 160 ||
    !form.description.trim() ||
    form.description.length > 8192
  )
    throw new PortalA2Error(
      "invalid_diagnostic_context",
      "Diagnostic category and description are required",
    );
  return {
    quantity: form.quantity,
    quoteAmountAtomic: fullDemoAmount(form.quoteHKD).amountAtomic,
    unitPriceLimitAtomic: fullDemoAmount(form.unitPriceLimitHKD).amountAtomic,
    category: form.category,
    periodStart: form.periodStart,
    periodEnd: form.periodEnd,
    description: form.description,
  };
}
export function parseAiDiagnostic(
  value: unknown,
  state: PortalA2State,
  procurementId: string,
  purchaseOrderVersionId?: string,
): AiDiagnostic {
  const proc = state.rawProcurements.find((p) => p.id === procurementId);
  if (
    !record(value) ||
    !proc ||
    !sameBinding(value.binding, state) ||
    ![value.id, value.operationId].every(
      (id) => typeof id === "string" && validUuid(id),
    ) ||
    value.procurementId !== proc.id ||
    value.projectId !== proc.projectId ||
    typeof value.purchaseOrderVersionId !== "string" ||
    !validUuid(value.purchaseOrderVersionId) ||
    (purchaseOrderVersionId &&
      value.purchaseOrderVersionId !== purchaseOrderVersionId) ||
    value.stage !== 0 ||
    value.purpose !== "diagnostic" ||
    value.signingEnabled !== false ||
    !["queued", "completed", "failed", "requires_attention"].includes(
      String(value.status),
    ) ||
    !["user_declared", "demo_generated"].includes(
      String(value.contextSource),
    ) ||
    value.syntheticInput !== (value.contextSource === "demo_generated") ||
    typeof value.modelReal !== "boolean" ||
    typeof value.realAI !== "boolean" ||
    ![
      value.inputHash,
      value.evidenceHash,
      value.reportHash,
      value.reportSha256,
    ].every(nullableHash)
  )
    invalid();
  if (value.summary !== null) {
    const s = value.summary;
    if (
      !record(s) ||
      typeof s.text !== "string" ||
      s.outcome !== 1 ||
      s.riskScoreBps !== null ||
      s.completeness !== "incomplete" ||
      !Array.isArray(s.findings) ||
      !Array.isArray(s.missingInputs)
    )
      invalid();
  }
  if (value.provenance !== null) {
    const p = value.provenance;
    const versionsValid =
      record(p) &&
      record(p.versions) &&
      Object.keys(p.versions).length === VERSION_KEYS.length &&
      VERSION_KEYS.every(
        (key) =>
          Object.hasOwn(p.versions as object, key) &&
          ((key === "scorePolicyVersion" &&
            (p.versions as Record<string, unknown>)[key] === null) ||
            (typeof (p.versions as Record<string, unknown>)[key] === "string" &&
              String((p.versions as Record<string, unknown>)[key]).trim()
                .length > 0)),
      );
    if (
      !record(p) ||
      !(value.status === "completed"
        ? p.executionMode === "model_and_rules" && versionsValid
        : p.executionMode === null && p.versions === null) ||
      p.budgetSource !== "registry_procurement_budget_cap" ||
      p.reviewScope !== "single_po_visible_fields" ||
      p.allDocumentsReviewed !== false ||
      typeof p.contextSource !== "string"
    )
      invalid();
  }
  const context = value.diagnosticContext;
  if (
    !record(context) ||
    ![
      "quantity",
      "quoteAmountAtomic",
      "unitPriceLimitAtomic",
      "category",
      "periodStart",
      "periodEnd",
      "description",
    ].every((key) => typeof context[key] === "string")
  )
    invalid();
  if (value.status === "completed") {
    if (
      value.modelReal !== true ||
      value.realAI !== true ||
      value.reportProvenance !== "real_qwen_diagnostic" ||
      !value.summary ||
      !value.provenance ||
      typeof value.reportHash !== "string" ||
      typeof value.reportSha256 !== "string" ||
      value.contentType !== "application/json" ||
      !Number.isSafeInteger(value.reportSizeBytes) ||
      Number(value.reportSizeBytes) < 1 ||
      Number(value.reportSizeBytes) > 2 * 1024 * 1024
    )
      invalid();
  } else if (value.modelReal !== false || value.realAI !== false) invalid();
  return value as unknown as AiDiagnostic;
}
export function parseAiDiagnosticList(
  value: unknown,
  state: PortalA2State,
  procurementId: string,
): AiDiagnosticList {
  if (
    !record(value) ||
    value.interfaceId !== AI_DIAGNOSTIC_INTERFACE ||
    value.signingEnabled !== false ||
    !sameBinding(value.binding, state) ||
    !record(value.availability) ||
    typeof value.availability.enabled !== "boolean" ||
    !(
      value.availability.reasonCode === null ||
      typeof value.availability.reasonCode === "string"
    ) ||
    !Array.isArray(value.items) ||
    value.items.length > 20 ||
    value.nextCursor !== null
  )
    invalid();
  return {
    availability: value.availability as AiDiagnosticList["availability"],
    items: value.items.map((item) =>
      parseAiDiagnostic(item, state, procurementId),
    ),
  };
}
export async function loadAiDiagnostics(
  state: PortalA2State,
  procurementId: string,
  options: PortalA2Options = {},
): Promise<AiDiagnosticList> {
  return parseAiDiagnosticList(
    await portalA2Read("ai.diagnostic.list", { procurementId }, options),
    state,
    procurementId,
  );
}
export async function runAiDiagnostic(
  state: PortalA2State,
  procurementId: string,
  purchaseOrderVersionId: string,
  form: DiagnosticForm,
  contextSource: DiagnosticContextSource,
  key: string,
  options: PortalA2Options = {},
): Promise<AiDiagnostic> {
  const documents = diagnosticSources(state, procurementId);
  const doc = documents?.find(
    (d) =>
      d.versionId === purchaseOrderVersionId && d.category === "purchase_order",
  );
  if (
    state.currentUser.role !== "foundation" ||
    !doc ||
    !documents?.every(diagnosticImageDocument)
  )
    throw new PortalA2Error(
      "unsupported_ai_document",
      "Select the confirmed PO with three bound PNG/JPEG originals; PDF diagnostics are unsupported",
      422,
    );
  const result = await portalA2Action(
    "ai.diagnostic.create",
    {
      procurementId,
      purchaseOrderVersionId,
      confirm: true,
      contextSource,
      diagnosticContext: diagnosticContext(form),
    },
    key,
    { ...options, expectedState: state },
  );
  return parseAiDiagnostic(
    result.diagnostic,
    state,
    procurementId,
    purchaseOrderVersionId,
  );
}
