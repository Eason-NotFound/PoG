/** Original portal presentation over the fixed, same-origin A2 bridge.
 * This module has no demo-store dependency and no local business ledger.
 * The API retains authentication, document provenance and signing authority.
 */
import type { AccessUser, Portal } from "./access";
import { pages } from "./access";
import type {
  AppData,
  Donation,
  Procurement,
  Project,
  ReviewCase,
} from "./types";
import {
  atomicValue,
  freeLockedAtomic,
  mayPrepareSigning,
  maySignRequest,
  validUuid,
  type A2Document,
  type A2Ledger,
  type A2Operation,
  type A2Procurement,
  type A2Project,
  type A2SigningRequest,
  type A2User,
} from "./a2-workbench";
import { atomicToHkdCents, hkdCentsToAtomic } from "./payment-boundary";
import { buildDemoRoute, DEMO_ACTION_CATALOG } from "./demo-action-catalog";
import {
  capabilityState,
  earlyActionStage,
  foundationDefaults,
  parseFullDemoContext,
  paymentActionFacts,
  roleHome,
  type DemoExchange,
  type DemoWorkspace,
  type FullDemoContext,
} from "./full-demo-ui";

export type PortalA2Json = Record<string, unknown>;
type Timed = { createdAt?: string };
export type PortalA2RawProject = A2Project &
  Timed & {
    foundationWallet?: string | null;
    recipientWallet?: string | null;
  };
export type PortalA2RawProcurement = A2Procurement & Timed;
export type PortalA2Assessment = {
  assessmentId: string;
  stage: string;
  outcome: string;
  riskScoreBps: string;
  evidenceHash: string;
  reportHash: string;
  deadline: string;
  synthetic: boolean;
  realAI: boolean;
};
export type PortalA2ProcurementFacts = {
  chainState: string;
  verified: boolean;
  statusText: string;
  preAssessment: PortalA2Assessment | null;
  finalAssessment: PortalA2Assessment | null;
  risk: number | null;
  finalRisk: number | null;
  releaseConfirmed: boolean;
  supplierPaymentReconciled: boolean;
  settlementConfirmed: boolean;
  receiptConfirmed: boolean;
  reviewPassed: { purchase: boolean; delivery: boolean };
  quantityProvided: false;
  unitPriceProvided: false;
};
export type PortalA2State = {
  currentUser: A2User;
  deployment: PortalA2Json;
  context: FullDemoContext;
  rawProjects: PortalA2RawProject[];
  rawProcurements: PortalA2RawProcurement[];
  workspaces: Record<string, DemoWorkspace>;
  ledgers: Record<string, A2Ledger>;
  exchanges: DemoExchange[];
  signingRequests: A2SigningRequest[];
  operations: A2Operation[];
  account: PortalA2Json | null;
  progressByProject: Record<string, PortalA2Json>;
  paymentStatuses: Record<string, PortalA2Json>;
  procurementFacts: Record<string, PortalA2ProcurementFacts>;
};
export type PortalA2AppData = AppData & { integration: PortalA2State };
export type PortalA2Scope = {
  projectId?: string;
  procurementId?: string;
  requestId?: string;
  kind?: A2SigningRequest["kind"];
};
export type PortalA2Options = {
  fetch?: typeof fetch;
  expectedState?: PortalA2State;
};
export type PortalA2ActionResult = PortalA2Json & {
  project?: PortalA2RawProject;
  procurement?: PortalA2RawProcurement;
  operation?: A2Operation;
  signingRequest?: A2SigningRequest;
  exchange?: DemoExchange;
  payment?: DemoExchange;
};

export class PortalA2Error extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public operationId?: string,
  ) {
    super(message);
    this.name = "PortalA2Error";
  }
}
function record(value: unknown): value is PortalA2Json {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function invalid(message: string): never {
  throw new PortalA2Error("invalid_response", message, 502);
}
function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}
function uuid(value: unknown): string {
  if (typeof value !== "string" || !validUuid(value))
    invalid("A2 returned an invalid resource UUID");
  return value;
}
const HEX32 = /^0x[0-9a-fA-F]{64}$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const EMPTY_HASH = /^0x0{64}$/i;

/** Old portal money is HKD cents. Reject rounding and unsafe Number values. */
export function portalA2AtomicToCents(value: string): number {
  const cents = BigInt(atomicToHkdCents(value));
  if (cents > BigInt(Number.MAX_SAFE_INTEGER))
    throw new PortalA2Error(
      "unsafe_portal_amount",
      "金额超出原页面整数范围；请使用精确 API 金额",
    );
  return Number(cents);
}
export function portalA2CentsToAtomic(value: number | string): string {
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value < 0)
      throw new PortalA2Error(
        "invalid_portal_amount",
        "金额须为安全的 HKD 分整数",
      );
    return hkdCentsToAtomic(String(value));
  }
  return hkdCentsToAtomic(value);
}

export function portalA2User(value: unknown): A2User {
  if (
    !record(value) ||
    !validUuid(string(value.id)) ||
    !string(value.username) ||
    !["foundation", "recipient", "donor", "human_approver"].includes(
      string(value.role),
    ) ||
    !ADDRESS.test(string(value.walletAddress)) ||
    /^0x0{40}$/i.test(string(value.walletAddress))
  )
    invalid("A2 identity is not a supported role bound to a local wallet");
  return value as unknown as A2User;
}
function accessUser(user: A2User): AccessUser {
  return {
    id: user.id,
    role: user.role === "human_approver" ? "admin" : (user.role as Portal),
    name:
      string((user as A2User & { displayName?: string }).displayName) ||
      user.username,
    orgId: user.id,
    grants: [],
    active: true,
  };
}
function bound(
  value: unknown,
  context: FullDemoContext,
): value is PortalA2Json {
  return (
    record(value) &&
    value.interfaceId === context.interfaceId &&
    value.mode === "simulation" &&
    record(value.binding) &&
    ["namespaceId", "runId", "instanceId", "chainId"].every(
      (key) => (value.binding as PortalA2Json)[key] === context.binding[key],
    )
  );
}
function requireBound(value: unknown, context: FullDemoContext): PortalA2Json {
  if (!bound(value, context))
    invalid("A2 response belongs to an unavailable or different deployment");
  return value;
}
function rawProject(value: unknown): PortalA2RawProject {
  if (
    !record(value) ||
    !validUuid(string(value.id)) ||
    !HEX32.test(string(value.businessId)) ||
    typeof value.title !== "string" ||
    typeof value.publicSummary !== "string" ||
    !record(value.chainState) ||
    typeof value.chainState.status !== "string" ||
    typeof value.chainState.verified !== "boolean"
  )
    invalid("Invalid A2 project projection");
  return value as unknown as PortalA2RawProject;
}
function rawProcurement(value: unknown): PortalA2RawProcurement {
  if (
    !record(value) ||
    !validUuid(string(value.id)) ||
    !validUuid(string(value.projectId)) ||
    !HEX32.test(string(value.businessId)) ||
    !ADDRESS.test(string(value.vendorWallet)) ||
    typeof value.title !== "string" ||
    typeof value.budgetCapAtomic !== "string" ||
    !record(value.chainState) ||
    typeof value.chainState.status !== "string" ||
    typeof value.chainState.verified !== "boolean"
  )
    invalid("Invalid A2 procurement projection");
  atomicValue(value.budgetCapAtomic);
  return value as unknown as PortalA2RawProcurement;
}
function assessment(
  value: unknown,
  stage: "0" | "1",
): PortalA2Assessment | null {
  if (
    !record(value) ||
    value.stage !== stage ||
    !HEX32.test(string(value.assessmentId)) ||
    EMPTY_HASH.test(string(value.assessmentId)) ||
    !HEX32.test(string(value.evidenceHash)) ||
    EMPTY_HASH.test(string(value.evidenceHash)) ||
    !HEX32.test(string(value.reportHash)) ||
    EMPTY_HASH.test(string(value.reportHash)) ||
    !["0", "1", "2"].includes(string(value.outcome)) ||
    !/^(0|[1-9][0-9]*)$/.test(string(value.riskScoreBps)) ||
    BigInt(string(value.riskScoreBps)) > 10000n ||
    !/^[1-9][0-9]*$/.test(string(value.deadline)) ||
    typeof value.synthetic !== "boolean" ||
    typeof value.realAI !== "boolean" ||
    (value.synthetic === true && value.realAI !== false)
  )
    return null;
  return value as unknown as PortalA2Assessment;
}
const RECEIVED_STATES = new Set([
  "receipt_confirmed",
  "final_assessed",
  "release_approval_pending",
  "funds_released",
  "settlement_recorded",
  "settlement_approval_pending",
  "payment_confirmed",
]);
const STATUS_TEXT: Record<string, string> = {
  off_chain_draft: "链外草稿，尚未创建",
  creation_queued: "创建排队，尚未确认",
  created: "采购已创建，待 PO 证据",
  po_recorded: "PO 已登记，AI 入口待接入",
  pre_assessed: "采购风险证据已登记，待独立人工审批",
  reserve_approval_pending: "人工预留票已登记，待执行",
  reserved: "预算已预留，资金仍在 Escrow",
  invoice_recorded: "发票已登记，待受捐机构确认收货",
  receipt_confirmed: "收货已确认，资金仍在 Escrow",
  final_assessed: "最终风险证据已登记，待人工放款审批",
  release_approval_pending: "人工放款票已登记，待执行",
  funds_released: "已放款到基金会，供应商尚未完成结算",
  settlement_recorded: "模拟付款证据已登记，待人工结算审批",
  settlement_approval_pending: "人工结算票已登记，待执行确认",
  payment_confirmed: "模拟供应商付款及结算已确认",
  cancelled: "采购已取消",
};
function paymentReconciled(value: unknown, proc: A2Procurement): boolean {
  if (
    !record(value) ||
    value.status !== "reconciled" ||
    value.reconciled !== true ||
    typeof value.amountAtomic !== "string"
  )
    return false;
  if (
    Object.hasOwn(value, "kind") &&
    (value.kind !== "supplier_payment" ||
      value.procurementId !== proc.id ||
      value.projectId !== proc.projectId ||
      !validUuid(string(value.id)) ||
      !validUuid(string(value.operationId)))
  )
    return false;
  if (
    !Object.hasOwn(value, "kind") &&
    (Object.keys(value).length !== 3 ||
      !["status", "reconciled", "amountAtomic"].every((key) =>
        Object.hasOwn(value, key),
      ))
  )
    return false;
  return atomicValue(value.amountAtomic) > 0n;
}
/** Paid requires both persistent simulated supplier payment and final settlement. */
export function portalA2ProcurementFacts(
  proc: A2Procurement,
  workspace: DemoWorkspace | null,
  payment?: unknown,
): PortalA2ProcurementFacts {
  const matching =
    workspace?.procurementId === proc.id &&
    workspace.chainState === proc.chainState.status;
  const source = matching ? workspace : null;
  const status = record(payment) ? payment : source;
  const pre = assessment(source?.preAssessment, "0");
  const final = assessment(source?.finalAssessment, "1");
  const release =
    proc.chainState.verified &&
    record(status?.release) &&
    status.release.confirmed === true;
  const amounts = source && record(source.amounts) ? source.amounts : null;
  const supplier =
    proc.chainState.verified &&
    paymentReconciled(status?.supplierPayment, proc) &&
    record(status?.supplierPayment) &&
    status.supplierPayment.amountAtomic === amounts?.invoiceAmountAtomic;
  const settlement =
    proc.chainState.verified &&
    proc.chainState.status === "payment_confirmed" &&
    record(status?.settlement) &&
    status.settlement.confirmed === true &&
    HEX32.test(string(status.settlement.settlementHash)) &&
    !EMPTY_HASH.test(string(status.settlement.settlementHash));
  // Historical state advancement is not evidence of a currently live approval.
  // Current bundle/threshold facts exist for release & settlement only in A2.
  const approval =
    matching && record(source?.approval) ? source.approval : null;
  const currentHuman =
    approval?.policyVerified === true &&
    /^[1-9][0-9]*$/.test(string(approval.threshold)) &&
    /^(0|[1-9][0-9]*)$/.test(string(approval.liveVoteCount)) &&
    BigInt(string(approval.liveVoteCount)) >=
      BigInt(string(approval.threshold)) &&
    HEX32.test(string(approval.termsHash));
  return {
    chainState: proc.chainState.status,
    verified: proc.chainState.verified,
    statusText:
      STATUS_TEXT[proc.chainState.status] ??
      `未支持的链状态：${proc.chainState.status}`,
    preAssessment: pre,
    finalAssessment: final,
    risk:
      pre?.realAI === true && pre.synthetic === false
        ? Number(pre.riskScoreBps) / 100
        : null,
    finalRisk:
      final?.realAI === true && final.synthetic === false
        ? Number(final.riskScoreBps) / 100
        : null,
    releaseConfirmed: Boolean(release),
    supplierPaymentReconciled: supplier,
    settlementConfirmed: settlement,
    receiptConfirmed:
      proc.chainState.verified && RECEIVED_STATES.has(proc.chainState.status),
    reviewPassed: {
      purchase: false,
      delivery: Boolean(
        currentHuman &&
        approval?.action === "1" &&
        final &&
        source?.allowedActions["release.execute"]?.enabled === true,
      ),
    },
    quantityProvided: false,
    unitPriceProvided: false,
  };
}

function oldStatus(facts: PortalA2ProcurementFacts): Procurement["status"] {
  if (!facts.verified) return "needs_info";
  if (facts.supplierPaymentReconciled && facts.settlementConfirmed)
    return "paid";
  if (facts.chainState === "cancelled") return "cancelled";
  if (facts.chainState === "cancellation_approval_pending") return "frozen";
  if (facts.releaseConfirmed) return "funds_released";
  if (facts.chainState === "reserved") return "reserved";
  if (
    RECEIVED_STATES.has(facts.chainState) ||
    facts.chainState === "invoice_recorded"
  )
    return "payment_review";
  if (
    [
      "created",
      "po_recorded",
      "pre_assessed",
      "reserve_approval_pending",
    ].includes(facts.chainState)
  )
    return "human_review";
  return "needs_info";
}

/** A read projection, never a manufactured review decision or audit entry. */
export function projectPortalA2Page(
  pageId: string,
  state: PortalA2State,
): PortalA2AppData {
  const user = accessUser(state.currentUser);
  const page = pages.find((item) => item.id === pageId);
  if (!page || page.portal !== user.role)
    throw new PortalA2Error("role_forbidden", "此页面不属于当前 API 身份", 403);
  if (state.currentUser.role === "donor")
    state = {
      ...state,
      rawProcurements: [],
      workspaces: {},
      paymentStatuses: {},
      signingRequests: [],
      procurementFacts: {},
    };
  const factsById: Record<string, PortalA2ProcurementFacts> = {};
  const procurements: Procurement[] = state.rawProcurements.map((proc) => {
    const ws = state.workspaces[proc.id] ?? null;
    const facts = portalA2ProcurementFacts(
      proc,
      ws,
      state.paymentStatuses[proc.id],
    );
    factsById[proc.id] = facts;
    const amounts = ws && record(ws.amounts) ? ws.amounts : null;
    const invoice = amounts?.invoiceAmountAtomic;
    const amount =
      typeof invoice === "string" && atomicValue(invoice) > 0n
        ? invoice
        : proc.budgetCapAtomic;
    const docs = ws?.documentVersions ?? [];
    return {
      id: proc.id,
      projectId: proc.projectId,
      recipientId:
        state.rawProjects.find((p) => p.id === proc.projectId)
          ?.recipientWallet ?? "",
      vendorId: proc.vendorWallet.toLowerCase(),
      name: proc.title || "名称未提供",
      quantity: NaN,
      unitPrice: NaN,
      amount: portalA2AtomicToCents(amount),
      status: oldStatus(facts),
      risk: facts.risk ?? NaN,
      finalRisk: facts.finalRisk ?? undefined,
      hash: proc.businessId,
      createdAt: proc.createdAt ?? "",
      note: facts.statusText,
      evidenceIds: docs.map((doc) => doc.versionId),
      a2: facts,
    } as Procurement;
  });
  const projects: Project[] = state.rawProjects.map((project) => {
    const ledger = state.ledgers[project.id];
    const confirmed =
      project.chainState.verified && ledger?.chainVerified === true;
    const paid = procurements
      .filter((p) => p.projectId === project.id && p.status === "paid")
      .reduce((sum, p) => sum + BigInt(p.amount), 0n);
    if (paid > BigInt(Number.MAX_SAFE_INTEGER))
      throw new PortalA2Error(
        "unsafe_portal_amount",
        "项目付款总额超出原页面整数范围",
      );
    return {
      id: project.id,
      name: project.title,
      description: project.publicSummary,
      foundationId: project.foundationWallet ?? "",
      recipientId: project.recipientWallet ?? "",
      paymentTracked: true,
      target: NaN,
      deposited: confirmed ? portalA2AtomicToCents(ledger.depositsAtomic) : NaN,
      available: confirmed
        ? portalA2AtomicToCents(freeLockedAtomic(ledger))
        : NaN,
      reserved: confirmed ? portalA2AtomicToCents(ledger.reservedAtomic) : NaN,
      released: confirmed ? portalA2AtomicToCents(ledger.releasedAtomic) : NaN,
      refunded: confirmed ? portalA2AtomicToCents(ledger.refundedAtomic) : NaN,
      paid:
        confirmed && state.currentUser.role !== "donor" ? Number(paid) : NaN,
      rulesHash: "",
      categories: [],
      a2: { chainState: project.chainState, targetProvided: false },
    } as Project;
  });
  const donations: Donation[] =
    state.currentUser.role === "donor"
      ? state.rawProjects.flatMap((project) => {
          const ledger = state.ledgers[project.id];
          if (
            !project.chainState.verified ||
            ledger?.chainVerified !== true ||
            ledger.currentCallerDonorCreditAtomic === null ||
            atomicValue(ledger.currentCallerDonorCreditAtomic) === 0n
          )
            return [];
          return [
            {
              id: `ledger-credit:${project.id}:${user.id}`,
              ownerId: user.id,
              projectId: project.id,
              amount: portalA2AtomicToCents(
                ledger.currentCallerDonorCreditAtomic,
              ),
              allocated: NaN,
              hash: "",
              createdAt: "",
              a2: { aggregate: true, chainVerified: true },
            } as Donation,
          ];
        })
      : [];
  const reviews: ReviewCase[] = state.rawProcurements.flatMap((proc) => {
    const facts = factsById[proc.id];
    if (
      !facts.verified ||
      ![
        "pre_assessed",
        "reserve_approval_pending",
        "final_assessed",
        "release_approval_pending",
        "settlement_recorded",
        "settlement_approval_pending",
      ].includes(facts.chainState)
    )
      return [];
    const stage = ["pre_assessed", "reserve_approval_pending"].includes(
      facts.chainState,
    )
      ? "purchase"
      : "delivery";
    const report =
      stage === "delivery" ? facts.finalAssessment : facts.preAssessment;
    if (
      !report &&
      !["settlement_recorded", "settlement_approval_pending"].includes(
        facts.chainState,
      )
    )
      return [];
    return [
      {
        id: `a2-review:${proc.id}:${stage}`,
        procurementId: proc.id,
        projectId: proc.projectId,
        stage,
        status: "pending",
        sourceHash: report?.evidenceHash ?? "",
        evidenceIds:
          state.workspaces[proc.id]?.documentVersions.map(
            (doc) => doc.versionId,
          ) ?? [],
        risk: (stage === "delivery" ? facts.finalRisk : facts.risk) ?? NaN,
        createdAt: proc.createdAt ?? "",
        a2: {
          readOnlyProjection: true,
          assessment: report,
          chainState: facts.chainState,
        },
      } as ReviewCase,
    ];
  });
  const evidence = state.rawProcurements.flatMap((proc) =>
    (state.workspaces[proc.id]?.documentVersions ?? []).map((doc) => {
      const meta = doc as A2Document & {
        contentType?: string;
        sizeBytes?: number;
        createdAt?: string;
      };
      return {
        id: doc.versionId,
        procurementId: proc.id,
        ownerId: "",
        orgId: "",
        name: doc.originalFilename,
        hash: doc.keccak256,
        mime: meta.contentType ?? "",
        size: meta.sizeBytes ?? 0,
        type: doc.category,
        submittedRole:
          doc.category === "receipt_evidence"
            ? ("recipient" as const)
            : ("foundation" as const),
        createdAt: meta.createdAt,
        a2: {
          documentId: doc.id,
          versionId: doc.versionId,
          referenced: doc.referenced,
        },
      };
    }),
  );
  const defaultSupplier = foundationDefaults(state.context)?.supplierWallet;
  const vendorWallets = [
    ...(defaultSupplier ? [defaultSupplier] : []),
    ...state.rawProcurements.map((proc) => proc.vendorWallet),
  ];
  return {
    user,
    pageId,
    updatedAt: "",
    integration: { ...state, procurementFacts: factsById },
    data: {
      projects,
      procurements,
      donations,
      reviews,
      evidence,
      vendors: [
        ...new Map(
          vendorWallets.map((wallet) => [
            wallet.toLowerCase(),
            {
              id: wallet.toLowerCase(),
              name: "本地配置的固定模拟供应商",
              verified: false,
              wallet,
            },
          ]),
        ).values(),
      ],
      users: [user],
      appeals: [],
      logs: [],
      ledger: [],
      reimbursements: [],
      records: [],
    },
  };
}

export function portalA2Allowed(
  state: PortalA2State | null | undefined,
  actionId: string,
  scope: PortalA2Scope = {},
): { enabled: boolean; reason: string | null } {
  const deny = (reason: string) => ({ enabled: false, reason });
  if (!state) return deny("integration_context_unavailable");
  const action = DEMO_ACTION_CATALOG.find((item) => item.id === actionId);
  if (!action || action.status !== "declared")
    return deny(
      actionId.startsWith("ai.")
        ? "ai_service_unavailable"
        : "action_unavailable",
    );
  if (!action.roles.includes(state.currentUser.role as "foundation"))
    return deny("role_forbidden");
  const proc = scope.procurementId
    ? (state.rawProcurements.find((p) => p.id === scope.procurementId) ?? null)
    : null;
  const projectId = scope.projectId ?? proc?.projectId;
  const project = projectId
    ? (state.rawProjects.find((p) => p.id === projectId) ?? null)
    : null;
  const ws = proc ? (state.workspaces[proc.id] ?? null) : null;
  if (
    scope.procurementId &&
    (!proc || (!ws && actionId !== "procurement.chain.create"))
  )
    return deny("matching_current_workspace_required");
  if (scope.projectId && !project) return deny("matching_project_required");
  if (proc && project && proc.projectId !== project.id)
    return deny("project_procurement_mismatch");
  const cap = capabilityState(
    state.context,
    actionId,
    ws ? ws.allowedActions : undefined,
  );
  if (!cap.enabled) return cap;
  if (
    ["project.draft.create", "procurement.draft.create"].includes(actionId) &&
    !foundationDefaults(state.context)
  )
    return deny("fixed_foundation_defaults_required");
  if (
    !scope.projectId &&
    !scope.procurementId &&
    [
      "donor.funding.convert",
      "donation.funded.deposit",
      "procurement.draft.create",
    ].includes(actionId)
  )
    return state.rawProjects.some(
      (p) => p.chainState.verified === true && p.chainState.status === "active",
    )
      ? cap
      : deny("confirmed_active_project_required");
  if (actionId === "procurement.chain.create") {
    return proc?.chainState.status === "off_chain_draft" &&
      proc.chainState.verified === false &&
      project?.chainState.status === "active" &&
      project.chainState.verified === true
      ? cap
      : deny("unsent_procurement_draft_and_confirmed_active_project_required");
  }
  const early = earlyActionStage(actionId, project, proc, ws);
  if (!early.enabled) return early;
  const payment = paymentActionFacts(
    actionId,
    state.context,
    ws,
    projectId ?? "",
    proc?.id ?? "",
  );
  if (!payment.enabled) return payment;
  if (
    ["donor.funding.convert", "donation.funded.deposit"].includes(actionId) &&
    (project?.chainState.verified !== true ||
      project.chainState.status !== "active")
  )
    return deny("confirmed_active_project_required");
  if (
    actionId === "authorization.prepare" &&
    scope.kind &&
    !mayPrepareSigning(state.currentUser.role, scope.kind)
  )
    return deny("role_forbidden");
  if (
    ["authorization.demo.sign", "authorization.saved.submit"].includes(actionId)
  ) {
    const request = state.signingRequests.find((r) => r.id === scope.requestId);
    if (
      !request ||
      !maySignRequest(state.currentUser, request) ||
      (proc && request.procurementId !== proc.id)
    )
      return deny("own_matching_signing_request_required");
    if (
      request.status !==
      (actionId === "authorization.demo.sign" ? "prepared" : "signed")
    )
      return deny("signing_request_state");
  }
  return cap;
}

async function request(
  actionId: string,
  parameters: Record<string, string> = {},
  options: RequestInit = {},
  dependencies: PortalA2Options = {},
): Promise<PortalA2Json> {
  const route = buildDemoRoute(actionId, parameters);
  let response: Response;
  try {
    response = await (dependencies.fetch ?? fetch)(route.proxyPath, {
      ...options,
      method: route.method,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
  } catch {
    throw new PortalA2Error(
      "transport_unavailable",
      "连接中断，结果未知。请保留同一请求编号与内容核对或重试。",
    );
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = record(body) && record(body.error) ? body.error : {};
    throw new PortalA2Error(
      string(error.code) || "upstream_error",
      string(error.message).slice(0, 600) || "A2 请求失败；未使用演示回退",
      response.status,
      validUuid(string(error.operationId))
        ? string(error.operationId)
        : undefined,
    );
  }
  if (!record(body)) invalid("Invalid A2 JSON response");
  return body;
}
function items(value: PortalA2Json): unknown[] {
  if (!Array.isArray(value.items)) invalid("A2 list response has no items");
  return value.items;
}
function newKey(): string {
  return crypto.randomUUID();
}

/** Refresh only the authority/deployment facts before a displayed intent writes.
 * Never replay a POST when a different login or reset replaced the page state.
 */
export async function guardPortalA2ExpectedState(
  dependencies: PortalA2Options,
): Promise<void> {
  const expected = dependencies.expectedState;
  if (!expected) return;
  const [me, deployment, rawContext] = await Promise.all([
    request("identity.read", {}, {}, dependencies),
    request("deployment.read", {}, {}, dependencies),
    request("integration.context.read", {}, {}, dependencies),
  ]);
  const current = portalA2User(me);
  const context = parseFullDemoContext(rawContext, deployment);
  if (
    !context ||
    deployment.verified !== true ||
    String(deployment.chainId) !== "31337" ||
    current.id !== expected.currentUser.id ||
    current.role !== expected.currentUser.role ||
    current.walletAddress.toLowerCase() !==
      expected.currentUser.walletAddress.toLowerCase() ||
    ["namespaceId", "runId", "instanceId", "chainId"].some(
      (key) => context.binding[key] !== expected.context.binding[key],
    )
  )
    throw new PortalA2Error(
      "stale_portal_context",
      "当前身份或部署已改变；请重新加载后核对本次操作",
      409,
    );
}

/** Fixed JSON reads only. Private content downloads retain their separate byte path. */
export async function portalA2Read(
  actionId: string,
  parameters: Record<string, string> = {},
  dependencies: PortalA2Options = {},
): Promise<PortalA2Json> {
  const action = DEMO_ACTION_CATALOG.find((item) => item.id === actionId);
  if (
    !action ||
    action.status !== "declared" ||
    action.method !== "GET" ||
    action.behavior === "document_content"
  )
    throw new PortalA2Error(
      "action_unavailable",
      "This is not a fixed JSON read route",
    );
  return request(actionId, parameters, {}, dependencies);
}

/** One caller action, one exact route. No AI, sign or submit chain is composed. */
export async function portalA2Action(
  actionId: string,
  body: PortalA2Json = {},
  idempotencyKey?: string,
  dependencies: PortalA2Options = {},
): Promise<PortalA2ActionResult> {
  const action = DEMO_ACTION_CATALOG.find((item) => item.id === actionId);
  if (
    !action ||
    action.status !== "declared" ||
    action.method === "GET" ||
    action.behavior === "document_content"
  )
    throw new PortalA2Error(
      "action_unavailable",
      "此原页面操作没有可用 API；未写入本地演示记录",
    );
  if (
    actionId === "session.login" ||
    actionId === "session.logout" ||
    actionId === "document.upload" ||
    actionId === "authorization.external.submit"
  )
    throw new PortalA2Error(
      "action_unavailable",
      "此操作需要专用身份或证据接口",
    );
  const params: Record<string, string> = {};
  const payload = { ...body };
  for (const match of action.template.matchAll(/\{([A-Za-z]+)\}/g)) {
    params[match[1]] = uuid(payload[match[1]]);
    delete payload[match[1]];
  }
  if (
    [
      "authorization.demo.sign",
      "authorization.saved.submit",
      "donor.funding.convert",
      "donation.funded.deposit",
      "foundation.redemption.convert",
      "supplier.payment.execute",
      "ai.diagnostic.create",
      "ai.offline.submit",
    ].includes(actionId) &&
    payload.confirm !== true
  )
    throw new PortalA2Error("confirmation_required", "请明确确认本次独立操作");
  if (
    actionId === "authorization.prepare" &&
    ["ai_pre", "ai_final"].includes(string(payload.kind))
  )
    throw new PortalA2Error(
      "ai_service_unavailable",
      "真实 AI 入口尚未接入；不自动提交 synthetic 风险报告",
    );
  for (const [key, value] of Object.entries(payload)) {
    if (key.endsWith("Atomic") || key === "hkdCents") {
      if (typeof value !== "string")
        throw new PortalA2Error(
          "invalid_atomic_amount",
          "金额传输须为精确整数字符串",
        );
      atomicValue(value);
    }
  }
  await guardPortalA2ExpectedState(dependencies);
  if (actionId === "mock.exchange.quote") {
    const result = await request(
      actionId,
      params,
      {
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey ?? newKey(),
        },
        body: JSON.stringify(payload),
      },
      dependencies,
    );
    if (result.executed !== false) invalid("A quote must be non-executing");
    return result;
  }
  return request(
    actionId,
    params,
    {
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey ?? newKey(),
      },
      body: JSON.stringify(payload),
    },
    dependencies,
  );
}

export async function portalA2Login(
  username: string,
  password: string,
  dependencies: PortalA2Options = {},
): Promise<{ redirect: string }> {
  const result = await request(
    "session.login",
    {},
    {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    dependencies,
  );
  const user = portalA2User(result.user);
  const redirect = roleHome(user.role);
  if (!redirect) invalid("Unsupported A2 role");
  return { redirect };
}
export async function portalA2Logout(
  dependencies: PortalA2Options = {},
): Promise<void> {
  const result = await request("session.logout", {}, {}, dependencies);
  if (result.loggedOut !== true) invalid("A2 logout has not been confirmed");
}
const DOCUMENT_CATEGORIES = new Set([
  "purchase_order",
  "request",
  "goods_request",
  "invoice",
  "goods_evidence",
  "receipt_evidence",
]);
export async function portalA2Upload(
  procurementId: string,
  kind: string,
  file: File,
  dependencies: PortalA2Options = {},
): Promise<A2Document> {
  uuid(procurementId);
  if (!DOCUMENT_CATEGORIES.has(kind))
    throw new PortalA2Error(
      "unsupported_document_category",
      "请选择对应的独立证据类别",
    );
  if (
    !file ||
    file.size === 0 ||
    file.size > 10 * 1024 * 1024 ||
    !["application/pdf", "image/png", "image/jpeg"].includes(file.type)
  )
    throw new PortalA2Error(
      "invalid_document",
      "证据须为 10 MB 内的 PDF、PNG 或 JPEG",
    );
  const form = new FormData();
  form.set("procurementId", procurementId);
  form.set("category", kind);
  form.set("file", file);
  await guardPortalA2ExpectedState(dependencies);
  const result = await request(
    "document.upload",
    {},
    { body: form, headers: { "Idempotency-Key": newKey() } },
    dependencies,
  );
  if (
    !record(result.document) ||
    result.document.procurementId !== procurementId ||
    result.document.category !== kind ||
    !validUuid(string(result.document.id)) ||
    !validUuid(string(result.document.versionId))
  )
    invalid(
      "Uploaded document did not match the exact procurement and category",
    );
  return result.document as unknown as A2Document;
}

export async function loadPortalA2Page(
  pageId: string,
  dependencies: PortalA2Options = {},
): Promise<PortalA2AppData> {
  const currentUser = portalA2User(
    await request("identity.read", {}, {}, dependencies),
  );
  const page = pages.find((item) => item.id === pageId);
  if (!page || page.portal !== accessUser(currentUser).role)
    throw new PortalA2Error("role_forbidden", "此页面不属于当前 API 身份", 403);
  const [deployment, rawContext, projectList] = await Promise.all([
    request("deployment.read", {}, {}, dependencies),
    request("integration.context.read", {}, {}, dependencies),
    request("project.list", {}, {}, dependencies),
  ]);
  const context = parseFullDemoContext(rawContext, deployment);
  if (
    !context ||
    deployment.verified !== true ||
    String(deployment.chainId) !== "31337"
  )
    invalid("A verified local A2 deployment is required");
  const state: PortalA2State = {
    currentUser,
    deployment,
    context,
    rawProjects: items(projectList).map(rawProject),
    rawProcurements: [],
    workspaces: {},
    ledgers: {},
    exchanges: [],
    signingRequests: [],
    operations: [],
    account: null,
    progressByProject: {},
    paymentStatuses: {},
    procurementFacts: {},
  };
  const requests: Promise<void>[] = [];
  const collect = (
    id: string,
    assign: (value: PortalA2Json) => void,
    binding = true,
  ) =>
    requests.push(
      request(id, {}, {}, dependencies).then((value) =>
        assign(binding ? requireBound(value, context) : value),
      ),
    );
  collect("operation.list", (value) => {
    state.operations = items(value) as A2Operation[];
  });
  if (currentUser.role !== "donor")
    collect(
      "procurement.list",
      (value) => {
        state.rawProcurements = items(value).map(rawProcurement);
      },
      false,
    );
  if (["recipient", "human_approver"].includes(currentUser.role))
    collect("authorization.list", (value) => {
      state.signingRequests = items(value) as A2SigningRequest[];
    });
  if (["donor", "foundation"].includes(currentUser.role)) {
    collect("mock.exchange.list", (value) => {
      state.exchanges = items(value) as DemoExchange[];
    });
    collect("mock.account.read", (value) => {
      if (!record(value.account)) invalid("Invalid own mock HKD account");
      state.account = value.account;
    });
  }
  await Promise.all(requests);
  if (
    state.rawProcurements.some(
      (proc) =>
        !state.rawProjects.some((project) => project.id === proc.projectId),
    )
  )
    invalid("Procurement is outside the returned project scope");
  await Promise.all(
    state.rawProjects
      .filter((project) => project.chainState.verified)
      .map(async (project) => {
        const ledger = await request(
          "project.ledger.read",
          { projectId: project.id },
          {},
          dependencies,
        );
        if (
          ledger.projectId !== project.id ||
          ledger.businessId !== project.businessId ||
          ledger.chainVerified !== true
        )
          invalid("Project ledger lacks exact confirmed scope");
        state.ledgers[project.id] = ledger as unknown as A2Ledger;
        // The private progress endpoint visits every project's procurement on-chain;
        // off-chain drafts have no Registry record and therefore no chain progress.
        if (
          currentUser.role === "donor" ||
          state.rawProcurements
            .filter((proc) => proc.projectId === project.id)
            .every((proc) => proc.chainState.verified)
        ) {
          const progress = await request(
            "project.progress.read",
            { projectId: project.id },
            {},
            dependencies,
          );
          if (progress.projectId !== project.id)
            invalid("Progress belongs to another project");
          state.progressByProject[project.id] = requireBound(progress, context);
        }
      }),
  );
  await Promise.all(
    state.rawProcurements
      .filter((proc) => proc.chainState.verified)
      .map(async (proc) => {
        if (!state.rawProjects.some((project) => project.id === proc.projectId))
          invalid("Procurement is outside the returned project scope");
        const [workspace, payment] = await Promise.all([
          request(
            "procurement.workspace.read",
            { procurementId: proc.id },
            {},
            dependencies,
          ),
          request(
            "procurement.payment.status",
            { procurementId: proc.id },
            {},
            dependencies,
          ),
        ]);
        requireBound(workspace, context);
        requireBound(payment, context);
        if (
          workspace.procurementId !== proc.id ||
          workspace.chainState !== proc.chainState.status ||
          !Array.isArray(workspace.documentVersions) ||
          !record(workspace.allowedActions)
        )
          invalid("Invalid current procurement workspace");
        if (
          workspace.documentVersions.some(
            (doc) =>
              !record(doc) ||
              doc.procurementId !== proc.id ||
              !validUuid(string(doc.versionId)),
          )
        )
          invalid("Document version belongs to a different procurement");
        state.workspaces[proc.id] = workspace as DemoWorkspace;
        state.paymentStatuses[proc.id] = payment;
      }),
  );
  state.operations = await Promise.all(
    state.operations.map((item) =>
      request(
        "operation.read",
        { operationId: uuid(item.operationId) },
        {},
        dependencies,
      ).then((value) => value as A2Operation),
    ),
  );
  // Recheck identity and deployment after parallel reads so cross-login/reset
  // results cannot be returned as a single coherent portal page.
  const [latestUser, latestDeployment] = await Promise.all([
    request("identity.read", {}, {}, dependencies),
    request("deployment.read", {}, {}, dependencies),
  ]);
  const identity = portalA2User(latestUser);
  if (
    identity.id !== currentUser.id ||
    identity.role !== currentUser.role ||
    identity.walletAddress.toLowerCase() !==
      currentUser.walletAddress.toLowerCase() ||
    latestDeployment.runId !== deployment.runId ||
    latestDeployment.instanceId !== deployment.instanceId
  )
    throw new PortalA2Error(
      "stale_portal_response",
      "身份或部署已改变，请重新加载当前工作区",
      409,
    );
  return projectPortalA2Page(pageId, state);
}
