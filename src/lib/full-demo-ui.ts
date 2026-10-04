import {
  atomicValue,
  validUuid,
  type A2Project,
  type A2Procurement,
} from "./a2-workbench";
import { hkdCentsToAtomic, parseHkdDisplay } from "./payment-boundary";

export const FULL_DEMO_INTERFACE = "pog-full-demo-api-v0.1";
export type DemoCapability = {
  implemented: boolean;
  enabled: boolean;
  reasonCode: string | null;
};
export type DemoCapabilityMap = Record<string, DemoCapability>;
export type FullDemoContext = {
  interfaceId: string;
  mode: "simulation";
  binding: {
    namespaceId: string;
    runId: string;
    instanceId: string;
    chainId: string;
    [key: string]: unknown;
  };
  capabilities: DemoCapabilityMap;
  defaults: {
    recipientUserId: string;
    humanApproverUserId: string;
    supplierWallet: string;
  } | null;
};
export type DemoWorkspace = {
  documentVersions: import("./a2-workbench").A2Document[];
  allowedActions: DemoCapabilityMap;
  preAssessment?: unknown;
  finalAssessment?: unknown;
  [key: string]: unknown;
};
/** Presentation guard only, always ANDed with server capabilities.
 * Queued projections must never unlock the next immutable business action.
 */
export function earlyActionStage(
  actionId: string,
  project: A2Project | null,
  procurement: A2Procurement | null,
  workspace: DemoWorkspace | null,
): { enabled: boolean; reason: string | null } {
  const deny = (reason: string) => ({ enabled: false, reason });
  const ready = { enabled: true, reason: null };
  if (actionId === "project.chain.create")
    return project?.chainState.status === "off_chain_draft" &&
      project.chainState.verified === false
      ? ready
      : deny("unsent_project_draft_required");
  const stages: Record<string, string> = {
    "procurement.chain.create": "off_chain_draft",
    "procurement.po.record": "created",
    "procurement.invoice.record": "reserved",
    "procurement.reserve.execute": "reserve_approval_pending",
  };
  if (
    actionId !== "procurement.draft.create" &&
    !Object.hasOwn(stages, actionId)
  )
    return ready;
  if (
    !project ||
    project.chainState.status !== "active" ||
    project.chainState.verified !== true
  )
    return deny("confirmed_active_project_required");
  if (actionId === "procurement.draft.create") return ready;
  const wanted = stages[actionId];
  if (
    !procurement ||
    procurement.projectId !== project.id ||
    procurement.chainState.status !== wanted ||
    procurement.chainState.verified !== (wanted !== "off_chain_draft")
  )
    return deny(`confirmed_${wanted}_required`);
  if (
    !workspace ||
    workspace.procurementId !== procurement.id ||
    workspace.chainState !== wanted
  )
    return deny("matching_current_workspace_required");
  return ready;
}
/** Additional presentation facts, never a substitute for server allowedActions.
 * Missing or already-created payment resources must not start a fresh action.
 */
export function paymentActionFacts(
  actionId: string,
  context: FullDemoContext | null,
  workspace: DemoWorkspace | null,
  projectId: string,
  procurementId: string,
): { enabled: boolean; reason: string | null } {
  const ready = { enabled: true, reason: null };
  const deny = (reason: string) => ({ enabled: false, reason });
  if (
    ![
      "foundation.redemption.convert",
      "supplier.payment.execute",
      "settlement.evidence.record",
    ].includes(actionId)
  )
    return ready;
  if (
    !context ||
    !workspace ||
    !validUuid(projectId) ||
    !validUuid(procurementId) ||
    workspace.procurementId !== procurementId ||
    !record(workspace.binding) ||
    !["namespaceId", "runId", "instanceId", "chainId"].every(
      (key) =>
        workspace.binding &&
        record(workspace.binding) &&
        workspace.binding[key] === context.binding[key],
    )
  )
    return deny("matching_current_payment_workspace_required");
  const reconciled = (value: unknown, kind: string) =>
    record(value) &&
    value.kind === kind &&
    value.status === "reconciled" &&
    value.reconciled === true &&
    value.projectId === projectId &&
    value.procurementId === procurementId &&
    validUuid(String(value.id ?? "")) &&
    validUuid(String(value.operationId ?? ""));
  if (actionId === "foundation.redemption.convert") {
    if (!record(workspace.release) || workspace.release.confirmed !== true)
      return deny("confirmed_foundation_release_required");
    return workspace.redemption === null
      ? ready
      : deny("redemption_already_exists_or_is_unknown");
  }
  if (!reconciled(workspace.redemption, "redemption"))
    return deny("reconciled_redemption_required");
  if (actionId === "supplier.payment.execute")
    return workspace.supplierPayment === null
      ? ready
      : deny("supplier_payment_already_exists_or_is_unknown");
  return reconciled(workspace.supplierPayment, "supplier_payment")
    ? ready
    : deny("reconciled_supplier_payment_required");
}
export type DemoExchange = {
  id: string;
  operationId: string;
  kind: "funding" | "redemption" | "supplier_payment";
  status: string;
  reconciled: boolean;
  projectId: string;
  procurementId: string | null;
  hkdCents: string;
  amountAtomic: string;
  allocatedAtomic: string;
  sourceOperationId: string | null;
  evidenceId: string | null;
  journalIds: string[];
  chainProof: unknown;
};
export type DemoQuote = {
  interfaceId: string;
  mode: "simulation";
  binding: FullDemoContext["binding"];
  executed: false;
  quote: {
    direction: "hkd_to_mock" | "mock_to_hkd";
    hkdCents: string;
    amountAtomic: string;
    rate: string;
    feeHkdCents: string;
    eligible: boolean;
    reasonCode: string | null;
  };
};

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
export function parseFullDemoContext(
  value: unknown,
  deployment: Record<string, unknown>,
): FullDemoContext | null {
  if (
    !record(value) ||
    value.interfaceId !== FULL_DEMO_INTERFACE ||
    value.mode !== "simulation" ||
    !record(value.binding) ||
    !record(value.capabilities) ||
    typeof value.binding.runId !== "string" ||
    !value.binding.runId ||
    typeof value.binding.instanceId !== "string" ||
    !value.binding.instanceId ||
    !validUuid(String(value.binding.namespaceId ?? "")) ||
    value.binding.chainId !== "31337" ||
    value.binding.runId !== deployment.runId ||
    value.binding.instanceId !== deployment.instanceId
  )
    return null;
  return value as unknown as FullDemoContext;
}
export function capabilityState(
  context: FullDemoContext | null,
  actionId: string,
  allowedActions?: DemoCapabilityMap | null,
): { enabled: boolean; reason: string | null } {
  if (!context)
    return { enabled: false, reason: "integration_context_unavailable" };
  const global = context.capabilities[actionId];
  const scoped =
    allowedActions === undefined ? global : allowedActions?.[actionId];
  if (
    global?.implemented !== true ||
    global.enabled !== true ||
    scoped?.implemented !== true ||
    scoped.enabled !== true
  )
    return {
      enabled: false,
      reason: scoped?.reasonCode ?? global?.reasonCode ?? "action_unavailable",
    };
  return { enabled: true, reason: null };
}
export function foundationDefaults(
  context: FullDemoContext | null,
): FullDemoContext["defaults"] {
  const defaults = context?.defaults;
  if (
    !defaults ||
    !validUuid(defaults.recipientUserId) ||
    !validUuid(defaults.humanApproverUserId) ||
    !/^0x[0-9a-fA-F]{40}$/.test(defaults.supplierWallet) ||
    /^0x0{40}$/i.test(defaults.supplierWallet)
  )
    return null;
  return defaults;
}
export function fullDemoAmount(display: string): {
  hkdCents: string;
  amountAtomic: string;
} {
  const hkdCents = parseHkdDisplay(display);
  if (atomicValue(hkdCents) === 0n) throw new Error("Amount must be positive");
  return { hkdCents, amountAtomic: hkdCentsToAtomic(hkdCents) };
}
export function fullInvoiceAtomic(display: string): string {
  return fullDemoAmount(display).amountAtomic;
}
export function quoteForCurrentScope(
  value: unknown,
  context: FullDemoContext | null,
): value is DemoQuote {
  if (
    !context ||
    !record(value) ||
    value.interfaceId !== FULL_DEMO_INTERFACE ||
    value.mode !== "simulation" ||
    value.executed !== false ||
    !record(value.binding) ||
    !record(value.quote)
  )
    return false;
  const binding = value.binding;
  if (
    binding.namespaceId !== context.binding.namespaceId ||
    binding.runId !== context.binding.runId ||
    binding.instanceId !== context.binding.instanceId ||
    binding.chainId !== context.binding.chainId ||
    value.quote.feeHkdCents !== "0" ||
    value.quote.rate !== "1 HKD = 1 mHKD" ||
    typeof value.quote.eligible !== "boolean" ||
    typeof value.quote.hkdCents !== "string" ||
    typeof value.quote.amountAtomic !== "string" ||
    !["hkd_to_mock", "mock_to_hkd"].includes(String(value.quote.direction))
  )
    return false;
  try {
    return (
      atomicValue(value.quote.hkdCents) > 0n &&
      hkdCentsToAtomic(value.quote.hkdCents) === value.quote.amountAtomic
    );
  } catch {
    return false;
  }
}
export function currentFunding(
  exchanges: DemoExchange[],
  projectId: string,
): DemoExchange[] {
  return exchanges.filter(
    (item) =>
      item.kind === "funding" &&
      item.projectId === projectId &&
      item.procurementId === null &&
      validUuid(item.id) &&
      validUuid(item.operationId),
  );
}
export function roleHome(role: string): string | null {
  const portal = role === "human_approver" ? "admin" : role;
  return ["foundation", "recipient", "donor", "admin"].includes(portal)
    ? `/${portal}/overview`
    : null;
}
/** Actual API credit accounting; a reconciled mint is not reusable donation credit. */
export function remainingFundingAtomic(item: DemoExchange): string {
  try {
    const remaining =
      atomicValue(item.amountAtomic) - atomicValue(item.allocatedAtomic);
    return remaining > 0n ? remaining.toString() : "0";
  } catch {
    return "0";
  }
}
