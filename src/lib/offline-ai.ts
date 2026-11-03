/** Explicit local AI technical fixtures and separately labelled existing reports.
 * No cached/sample text is treated as a live inference or human funds decision. */
import { validUuid, type A2Operation } from "./a2-workbench";
import {
  portalA2Action,
  portalA2Read,
  PortalA2Error,
  type PortalA2State,
  type PortalA2Options,
} from "./portal-a2";

export const OFFLINE_AI_INTERFACE = "pog-offline-demo-v0.1";
export type OfflineStage = "PRE" | "FINAL";
export type OfflineSample = {
  title: string;
  sampleRiskScoreBps: number;
  markdown: string;
  provenance: "mock_demo";
  sourceSha256: string;
  context: "historical_reference_not_current_evidence";
};
export type OfflineFixture = {
  stage: OfflineStage;
  requestId: string;
  status: string;
  synthetic: true;
  technicalRiskScoreBps: 100;
  technicalReportHash: string;
  evidenceHash: string;
  nonce: string;
  deadline: string;
  operation: A2Operation | null;
};
export type OfflineAi = {
  interfaceId: typeof OFFLINE_AI_INTERFACE;
  binding: Record<string, unknown>;
  availability: { enabled: boolean; reasonCode: string | null };
  procurement: {
    id: string;
    projectId: string;
    businessId: string;
    sourceVersionIds: Record<string, unknown>;
  };
  provenance: "mock_demo";
  actualModelExecuted: false;
  reports: Record<OfflineStage, OfflineSample>;
  items: OfflineFixture[];
};
const HASH = /^0x[0-9a-fA-F]{64}$/;
const SHA = /^(?:0x)?[0-9a-fA-F]{64}$/;
const UINT = /^(0|[1-9][0-9]*)$/;
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function invalid(): never {
  throw new PortalA2Error(
    "invalid_offline_demo",
    "Offline demo data does not match this procurement and current deployment.",
    502,
  );
}
export function offlineScope(
  state: PortalA2State,
  procurementId: string,
): string {
  return [
    state.currentUser.id,
    state.currentUser.role,
    state.currentUser.walletAddress.toLowerCase(),
    state.context.binding.namespaceId,
    state.context.binding.runId,
    state.context.binding.instanceId,
    state.context.binding.chainId,
    procurementId,
  ].join(":");
}
export function offlineDefaultStage(
  state: PortalA2State,
  procurementId: string,
): OfflineStage {
  const status = state.rawProcurements.find((p) => p.id === procurementId)
    ?.chainState.status;
  return [
    "receipt_confirmed",
    "final_assessed",
    "release_approval_pending",
    "funds_released",
    "settlement_recorded",
    "settlement_approval_pending",
    "payment_confirmed",
  ].includes(status || "")
    ? "FINAL"
    : "PRE";
}
export function offlineStageReady(
  state: PortalA2State,
  procurementId: string,
  stage: OfflineStage,
): boolean {
  const proc = state.rawProcurements.find(
    (value) => value.id === procurementId,
  );
  const workspace = state.workspaces[procurementId];
  return (
    state.currentUser.role === "foundation" &&
    Boolean(
      proc?.chainState.verified === true &&
      proc.chainState.status ===
        (stage === "PRE" ? "po_recorded" : "receipt_confirmed") &&
      workspace?.procurementId === procurementId &&
      workspace.chainState === proc.chainState.status,
    )
  );
}
export function parseOfflineAi(
  value: unknown,
  state: PortalA2State,
  procurementId: string,
): OfflineAi {
  const proc = state.rawProcurements.find((p) => p.id === procurementId);
  if (
    !record(value) ||
    !proc ||
    value.interfaceId !== OFFLINE_AI_INTERFACE ||
    value.provenance !== "mock_demo" ||
    value.actualModelExecuted !== false ||
    !record(value.binding) ||
    !record(value.procurement) ||
    !record(value.availability) ||
    typeof value.availability.enabled !== "boolean" ||
    !(
      value.availability.reasonCode === null ||
      typeof value.availability.reasonCode === "string"
    ) ||
    value.procurement.id !== proc.id ||
    value.procurement.projectId !== proc.projectId ||
    value.procurement.businessId !== proc.businessId ||
    !record(value.procurement.sourceVersionIds) ||
    !record(value.reports) ||
    !Array.isArray(value.items)
  )
    invalid();
  const binding = value.binding;
  if (
    ["namespaceId", "runId", "instanceId", "chainId"].some(
      (key) => binding[key] !== state.context.binding[key],
    )
  )
    invalid();
  for (const key of ["registry", "escrow", "token"]) {
    const expected = state.context.binding[key];
    if (
      typeof expected === "string" &&
      (typeof binding[key] !== "string" ||
        binding[key].toLowerCase() !== expected.toLowerCase())
    )
      invalid();
  }
  const reports: Partial<Record<OfflineStage, OfflineSample>> = {};
  for (const stage of ["PRE", "FINAL"] as const) {
    const sample = value.reports[stage];
    const score = record(sample) ? sample.sampleRiskScoreBps : null;
    const expected = stage === "PRE" ? 1600 : 1000;
    if (
      !record(sample) ||
      ![expected, String(expected)].includes(score as never) ||
      typeof sample.title !== "string" ||
      !sample.title ||
      typeof sample.markdown !== "string" ||
      !sample.markdown ||
      sample.markdown.length > 131072 ||
      sample.provenance !== "mock_demo" ||
      sample.context !== "historical_reference_not_current_evidence" ||
      typeof sample.sourceSha256 !== "string" ||
      !SHA.test(sample.sourceSha256)
    )
      invalid();
    reports[stage] = {
      ...sample,
      sampleRiskScoreBps: expected,
    } as OfflineSample;
  }
  const items = value.items.map((item) => {
    if (
      !record(item) ||
      !["PRE", "FINAL"].includes(String(item.stage)) ||
      typeof item.requestId !== "string" ||
      !validUuid(item.requestId) ||
      typeof item.status !== "string" ||
      !/^[a-z_]{1,64}$/.test(item.status) ||
      item.synthetic !== true ||
      ![100, "100"].includes(item.technicalRiskScoreBps as never) ||
      typeof item.technicalReportHash !== "string" ||
      !HASH.test(item.technicalReportHash) ||
      typeof item.evidenceHash !== "string" ||
      !HASH.test(item.evidenceHash) ||
      typeof item.nonce !== "string" ||
      !UINT.test(item.nonce) ||
      typeof item.deadline !== "string" ||
      !UINT.test(item.deadline)
    )
      invalid();
    if (
      item.operation !== null &&
      (!record(item.operation) ||
        typeof item.operation.operationId !== "string" ||
        !validUuid(item.operation.operationId) ||
        typeof item.operation.status !== "string" ||
        typeof item.operation.chainVerified !== "boolean")
    )
      invalid();
    return { ...item, technicalRiskScoreBps: 100 } as OfflineFixture;
  });
  return { ...value, reports, items } as OfflineAi;
}
export async function loadOfflineAi(
  state: PortalA2State,
  procurementId: string,
  options: PortalA2Options = {},
): Promise<OfflineAi> {
  return parseOfflineAi(
    await portalA2Read("ai.offline.read", { procurementId }, options),
    state,
    procurementId,
  );
}
export async function submitOfflineAi(
  state: PortalA2State,
  procurementId: string,
  stage: OfflineStage,
  consent: boolean,
  key: string,
  options: PortalA2Options = {},
  retrySameIntent = false,
): Promise<OfflineAi> {
  if (consent !== true)
    throw new PortalA2Error(
      "confirmation_required",
      "Explicit confirmation is required.",
    );
  if (
    state.currentUser.role !== "foundation" ||
    (!retrySameIntent && !offlineStageReady(state, procurementId, stage))
  )
    throw new PortalA2Error(
      "offline_stage_not_ready",
      "The confirmed procurement stage is not ready for this offline fixture.",
      409,
    );
  const result = await portalA2Action(
    "ai.offline.submit",
    {
      procurementId,
      stage,
      confirm: true,
      expectedNamespaceId: state.context.binding.namespaceId,
      expectedRunId: state.context.binding.runId,
      expectedInstanceId: state.context.binding.instanceId,
      chainId: state.context.binding.chainId,
    },
    key,
    { ...options, expectedState: state },
  );
  return parseOfflineAi(result, state, procurementId);
}
