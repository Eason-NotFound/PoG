/**
 * Static route inventory, not a runtime capability or an authorization grant.
 * The backend alone decides role bindings, business freshness and execution.
 * A declared route does not prove that its service exists or an action succeeded.
 */
export type DemoRole =
  "foundation" | "recipient" | "donor" | "human_approver" | "service_ai";
export type DemoHttpMethod = "GET" | "POST" | "DELETE";
export type DemoFactCategory =
  | "session"
  | "identity"
  | "deployment"
  | "project_projection"
  | "procurement_projection"
  | "private_document"
  | "operation_projection"
  | "chain_ledger"
  | "off_chain_draft"
  | "chain_operation"
  | "authorization_material"
  | "local_demo_signature"
  | "simulated_conversion"
  | "ai_risk_evidence"
  | "ai_diagnostic"
  | "simulated_supplier_payment"
  | "settlement_evidence";
type RouteBehavior = "standard" | "login" | "logout" | "document_content";

export type DemoRouteDeclaration = Readonly<{
  id: string;
  status: "declared";
  method: DemoHttpMethod;
  /** Relative to /v2 on the backend and /api/a2 in the browser. */
  template: string;
  factCategory: DemoFactCategory;
  /** Descriptive only: no frontend or proxy role authorization is inferred. */
  roles: readonly DemoRole[];
  scopeNote: string;
  behavior: RouteBehavior;
  backendStatus: "existing_a2" | "pending_backend_review";
  provesExecution: false;
  provesChainConfirmation: false;
}>;
export type PlannedDemoAction = Readonly<{
  id: string;
  status: "unavailable";
  method: null;
  template: null;
  factCategory: DemoFactCategory;
  roles: readonly DemoRole[];
  scopeNote: string;
  provesExecution: false;
  provesChainConfirmation: false;
}>;
export type LocalDemoAction = Readonly<{
  id: string;
  status: "local_only";
  method: null;
  template: null;
  scopeNote: string;
  provesExecution: false;
  provesChainConfirmation: false;
}>;
export type DemoAction =
  DemoRouteDeclaration | PlannedDemoAction | LocalDemoAction;

const ALL_ROLES: readonly DemoRole[] = Object.freeze([
  "foundation",
  "recipient",
  "donor",
  "human_approver",
  "service_ai",
]);
const PROJECT_ROLES: readonly DemoRole[] = Object.freeze([
  "foundation",
  "recipient",
  "donor",
  "human_approver",
]);
const PRIVATE_PROJECT_ROLES: readonly DemoRole[] = Object.freeze([
  "foundation",
  "recipient",
  "human_approver",
]);
const SIGNING_ROLES: readonly DemoRole[] = Object.freeze([
  "human_approver",
  "recipient",
  "service_ai",
]);

function route(
  id: string,
  method: DemoHttpMethod,
  template: string,
  factCategory: DemoFactCategory,
  roles: readonly DemoRole[],
  scopeNote: string,
  behavior: RouteBehavior = "standard",
  backendStatus: DemoRouteDeclaration["backendStatus"] = "existing_a2",
): DemoRouteDeclaration {
  return Object.freeze({
    id,
    status: "declared" as const,
    method,
    template,
    factCategory,
    roles: Object.freeze([...roles]),
    scopeNote,
    behavior,
    backendStatus,
    provesExecution: false as const,
    provesChainConfirmation: false as const,
  });
}

/** The only source of the existing BFF route allowlist. No new route is added. */
export const DEMO_A2_ROUTES: readonly DemoRouteDeclaration[] = Object.freeze([
  route(
    "session.login",
    "POST",
    "sessions",
    "session",
    ALL_ROLES,
    "Credentials establish a backend session; role choices are not grants.",
    "login",
  ),
  route(
    "session.logout",
    "DELETE",
    "sessions/current",
    "session",
    ALL_ROLES,
    "Revoke only the current session.",
    "logout",
  ),
  route(
    "identity.read",
    "GET",
    "me",
    "identity",
    ALL_ROLES,
    "Current authenticated principal and active role-wallet binding.",
  ),
  route(
    "deployment.read",
    "GET",
    "deployment-config",
    "deployment",
    ALL_ROLES,
    "BFF requires a session; backend deployment and adapter facts remain authoritative.",
  ),
  route(
    "project.list",
    "GET",
    "projects",
    "project_projection",
    PROJECT_ROLES,
    "Public Donor projection or backend-scoped project membership.",
  ),
  route(
    "project.read",
    "GET",
    "projects/{projectId}",
    "project_projection",
    PROJECT_ROLES,
    "Public Donor projection or membership of this exact project.",
  ),
  route(
    "project.draft.create",
    "POST",
    "projects",
    "off_chain_draft",
    ["foundation"],
    "Foundation binds fixed Recipient and human user UUIDs; not chain creation.",
  ),
  route(
    "project.chain.create",
    "POST",
    "projects/{projectId}/chain/create",
    "chain_operation",
    ["foundation"],
    "Owning Foundation and its bound wallet; queued is not canonical confirmation.",
  ),
  route(
    "donation.deposit",
    "POST",
    "projects/{projectId}/donations",
    "chain_operation",
    ["donor"],
    "Current Donor approves and deposits exact atomic tokens; not proof of HKD conversion.",
  ),
  route(
    "project.ledger.read",
    "GET",
    "projects/{projectId}/ledger",
    "chain_ledger",
    PROJECT_ROLES,
    "Backend-confirmed ledger and current caller donor credit; no browser balance manufacture.",
  ),
  route(
    "procurement.list",
    "GET",
    "procurements",
    "procurement_projection",
    PRIVATE_PROJECT_ROLES,
    "Backend-scoped private project procurement list; not a Donor evidence grant.",
  ),
  route(
    "procurement.read",
    "GET",
    "procurements/{procurementId}",
    "procurement_projection",
    PRIVATE_PROJECT_ROLES,
    "Private membership of the procurement's fixed project.",
  ),
  route(
    "procurement.draft.create",
    "POST",
    "procurements",
    "off_chain_draft",
    ["foundation"],
    "Owning Foundation chooses fixed supplier and budget; Recipient cannot create this draft.",
  ),
  route(
    "procurement.chain.create",
    "POST",
    "procurements/{procurementId}/chain/create",
    "chain_operation",
    ["foundation"],
    "Owning Foundation queues the fixed procurement; event confirmation is separate.",
  ),
  route(
    "document.upload",
    "POST",
    "documents",
    "private_document",
    ["foundation", "recipient"],
    "Private immutable version; Recipient uploads its own receipt evidence only.",
  ),
  route(
    "document.read",
    "GET",
    "documents/{documentId}",
    "private_document",
    PRIVATE_PROJECT_ROLES,
    "Backend private-project scope and persisted immutable version metadata.",
  ),
  route(
    "document.content.read",
    "GET",
    "documents/{documentId}/content",
    "private_document",
    PRIVATE_PROJECT_ROLES,
    "Authorized private attachment; never a public Donor evidence download.",
    "document_content",
  ),
  route(
    "procurement.po.record",
    "POST",
    "procurements/{procurementId}/chain/purchase-order",
    "chain_operation",
    ["foundation"],
    "Owning Foundation references exact PO/request/goods-request versions.",
  ),
  route(
    "procurement.invoice.record",
    "POST",
    "procurements/{procurementId}/chain/invoice-and-goods",
    "chain_operation",
    ["foundation"],
    "Owning Foundation after reserve; exact invoice/goods versions and bounded invoice amount.",
  ),
  route(
    "authorization.prepare",
    "POST",
    "procurements/{procurementId}/signing-requests",
    "authorization_material",
    SIGNING_ROLES,
    "Kind-specific backend identity: reserve human, receipt Recipient, synthetic PRE service_ai.",
  ),
  route(
    "authorization.read",
    "GET",
    "signing-requests/{requestId}",
    "authorization_material",
    SIGNING_ROLES,
    "Original intended signer and current namespace; redacted persisted V2 material.",
  ),
  route(
    "authorization.demo.sign",
    "POST",
    "signing-requests/{requestId}/sign-demo",
    "local_demo_signature",
    SIGNING_ROLES,
    "Explicit original signer confirmation; local test signature saved server-side, no broadcast.",
  ),
  route(
    "authorization.external.submit",
    "POST",
    "signing-requests/{requestId}/submit",
    "chain_operation",
    SIGNING_ROLES,
    "Existing external-signature API; original signer and all freshness checks are server-owned.",
  ),
  route(
    "authorization.saved.submit",
    "POST",
    "signing-requests/{requestId}/submit-signed",
    "chain_operation",
    SIGNING_ROLES,
    "Pre-existing BFF transport declaration only; disabled UI until exact backend candidate is reviewed.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "procurement.reserve.execute",
    "POST",
    "procurements/{procurementId}/chain/reserve",
    "chain_operation",
    ["foundation", "human_approver"],
    "Owning Foundation or project human executes current AI-backed valid human reserve vote.",
  ),
  route(
    "operation.read",
    "GET",
    "operations/{operationId}",
    "operation_projection",
    ALL_ROLES,
    "Original operation principal only; HTTP 202 or queued status proves no execution.",
  ),
]);

function planned(
  id: string,
  factCategory: DemoFactCategory,
  roles: readonly DemoRole[],
  scopeNote: string,
): PlannedDemoAction {
  return Object.freeze({
    id,
    status: "unavailable" as const,
    method: null,
    template: null,
    factCategory,
    roles: Object.freeze([...roles]),
    scopeNote,
    provesExecution: false as const,
    provesChainConfirmation: false as const,
  });
}

/** Frozen full-demo transport only; runtime capabilities still gate every action. */
export const FULL_DEMO_ROUTES: readonly DemoRouteDeclaration[] = Object.freeze([
  route(
    "integration.context.read",
    "GET",
    "integration-context",
    "deployment",
    ALL_ROLES,
    "Role-filtered defaults and runtime capabilities.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "mock.account.read",
    "GET",
    "mock-hkd/accounts/me",
    "chain_ledger",
    ["donor", "foundation"],
    "Own simulated HKD account only.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "mock.exchange.quote",
    "POST",
    "mock-exchanges/quote",
    "simulated_conversion",
    ["donor", "foundation"],
    "Pure server quote, executed=false, no economic operation.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "donor.funding.convert",
    "POST",
    "mock-exchanges/funding",
    "simulated_conversion",
    ["donor"],
    "Project-bound simulated HKD hold and canonical restricted mint.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "mock.exchange.list",
    "GET",
    "mock-exchanges",
    "simulated_conversion",
    ["donor", "foundation"],
    "Original actor's resources only.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "mock.exchange.read",
    "GET",
    "mock-exchanges/{exchangeId}",
    "simulated_conversion",
    ["donor", "foundation"],
    "Original actor's exact resource and reconciliation facts.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "donation.funded.deposit",
    "POST",
    "projects/{projectId}/funded-donations",
    "chain_operation",
    ["donor"],
    "Consume one owned current project funding claim, never faucet credit.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "procurement.workspace.read",
    "GET",
    "procurements/{procurementId}/workspace",
    "procurement_projection",
    PRIVATE_PROJECT_ROLES,
    "Original immutable versions and safe risk/allowed-action facts.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "operation.list",
    "GET",
    "operations",
    "operation_projection",
    ALL_ROLES,
    "Only current principal operations.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "authorization.list",
    "GET",
    "signing-requests",
    "authorization_material",
    SIGNING_ROLES,
    "Only original signer requests; never another user's signatures.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "release.execute",
    "POST",
    "procurements/{procurementId}/chain/release",
    "chain_operation",
    ["foundation", "human_approver"],
    "Explicit invoice-limited Foundation release after current human threshold.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "foundation.redemption.convert",
    "POST",
    "procurements/{procurementId}/mock-redemption",
    "simulated_conversion",
    ["foundation"],
    "Full exact confirmed invoice release to fixed treasury, then HKD reconcile.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "supplier.payment.execute",
    "POST",
    "procurements/{procurementId}/mock-supplier-payment",
    "simulated_supplier_payment",
    ["foundation"],
    "Full invoice persistent simulated transfer to fixed supplier.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "settlement.evidence.record",
    "POST",
    "procurements/{procurementId}/chain/settlement-evidence",
    "settlement_evidence",
    ["foundation"],
    "Backend derives immutable conversion/payment hashes, no client proof.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "settlement.execute",
    "POST",
    "procurements/{procurementId}/chain/settlement-confirmation",
    "chain_operation",
    ["foundation", "human_approver"],
    "Distinct current human settlement threshold and canonical state12.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "procurement.payment.status",
    "GET",
    "procurements/{procurementId}/payment-status",
    "procurement_projection",
    PRIVATE_PROJECT_ROLES,
    "Separate release/redemption/supplierPayment/settlement facts, role-filtered.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "project.progress.read",
    "GET",
    "projects/{projectId}/progress",
    "project_projection",
    PROJECT_ROLES,
    "Public progress and own funding only for Donors.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "payment.evidence.read",
    "GET",
    "payment-evidence/{evidenceId}",
    "settlement_evidence",
    ["foundation", "human_approver"],
    "Immutable evidence metadata, no Recipient/Donor private bytes.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "payment.evidence.content",
    "GET",
    "payment-evidence/{evidenceId}/content",
    "settlement_evidence",
    ["foundation", "human_approver"],
    "Exact authorized immutable evidence bytes.",
    "document_content",
    "pending_backend_review",
  ),
  route(
    "document.version.read",
    "GET",
    "document-versions/{versionId}",
    "private_document",
    PRIVATE_PROJECT_ROLES,
    "Exact original version metadata, never latest-version substitution.",
    "standard",
    "pending_backend_review",
  ),
  route(
    "document.version.content",
    "GET",
    "document-versions/{versionId}/content",
    "private_document",
    PRIVATE_PROJECT_ROLES,
    "Exact original version download within project private scope.",
    "document_content",
    "pending_backend_review",
  ),
]);

/** Private off-chain document diagnostics. None is an AI assessment or signature. */
export const AI_DIAGNOSTIC_ROUTES: readonly DemoRouteDeclaration[] =
  Object.freeze([
    route(
      "ai.diagnostic.create",
      "POST",
      "procurements/{procurementId}/ai-diagnostics",
      "ai_diagnostic",
      ["foundation"],
      "Owning Foundation explicitly requests a single immutable PO diagnostic; no approval or chain execution.",
      "standard",
      "pending_backend_review",
    ),
    route(
      "ai.diagnostic.list",
      "GET",
      "procurements/{procurementId}/ai-diagnostics",
      "ai_diagnostic",
      ["foundation", "human_approver"],
      "Private diagnostics for this exact procurement; no Donor or Recipient access.",
      "standard",
      "pending_backend_review",
    ),
    route(
      "ai.diagnostic.read",
      "GET",
      "ai-diagnostics/{diagnosticId}",
      "ai_diagnostic",
      ["foundation", "human_approver"],
      "Read the exact private diagnostic, never on-chain AI risk or a human vote.",
      "standard",
      "pending_backend_review",
    ),
    route(
      "ai.diagnostic.report",
      "GET",
      "ai-diagnostics/{diagnosticId}/report",
      "ai_diagnostic",
      ["foundation", "human_approver"],
      "Download authorized exact canonical report bytes; no signatures or funds decision.",
      "document_content",
      "pending_backend_review",
    ),
  ]);

/** Explicit local technical-fixture fallback. Never a live model or human vote. */
export const OFFLINE_AI_ROUTES: readonly DemoRouteDeclaration[] = Object.freeze(
  [
    route(
      "ai.offline.read",
      "GET",
      "procurements/{procurementId}/offline-demo-ai",
      "ai_risk_evidence",
      ["foundation", "human_approver"],
      "Private existing sample reports and separately labelled current technical fixtures.",
      "standard",
      "pending_backend_review",
    ),
    route(
      "ai.offline.submit",
      "POST",
      "procurements/{procurementId}/offline-demo-ai",
      "ai_risk_evidence",
      ["foundation"],
      "Explicit owning Foundation intent requests a current local AI fixture; no human approval, receipt or funds action.",
      "standard",
      "pending_backend_review",
    ),
  ],
);

/** Product gaps only: no guessed method, endpoint or executable path. */
export const PLANNED_DEMO_ACTIONS: readonly PlannedDemoAction[] = Object.freeze(
  [
    planned(
      "ai.pre.detect",
      "ai_risk_evidence",
      ["service_ai"],
      "Actual PO risk detection is not the existing synthetic PRE signing fixture.",
    ),
    planned(
      "ai.final.detect",
      "ai_risk_evidence",
      ["service_ai"],
      "Actual invoice/goods/receipt risk evidence; never automatic release approval.",
    ),
    planned(
      "release.human.approve",
      "authorization_material",
      ["human_approver"],
      "Independent final human decision after receipt and current FINAL evidence.",
    ),
    planned(
      "settlement.human.approve",
      "authorization_material",
      ["human_approver"],
      "Separate current human reconciliation; a payment response is not settlement.",
    ),
  ],
);

function local(id: string, scopeNote: string): LocalDemoAction {
  return Object.freeze({
    id,
    status: "local_only" as const,
    method: null,
    template: null,
    scopeNote,
    provesExecution: false as const,
    provesChainConfirmation: false as const,
  });
}
export const LOCAL_DEMO_ACTIONS: readonly LocalDemoAction[] = Object.freeze([
  local(
    "ui.navigation",
    "Page/sidebar/modal navigation; no business mutation.",
  ),
  local("ui.language", "Local presentation locale and preference cookie."),
  local("ui.selection", "Select or filter already-authorized view data."),
  local(
    "ui.input",
    "Stage form/file input; submission is a separate declared action.",
  ),
  local("ui.expand", "Expand redacted already-returned details."),
  local(
    "ui.clipboard",
    "Copy a displayed value; no proof or funding operation.",
  ),
  local("ui.dismiss", "Dismiss message, modal or sidebar."),
  local("ui.consent", "Explicit local intent; does not itself sign or submit."),
]);
export const DEMO_ACTION_CATALOG: readonly DemoAction[] = Object.freeze([
  ...DEMO_A2_ROUTES,
  ...FULL_DEMO_ROUTES,
  ...AI_DIAGNOSTIC_ROUTES,
  ...OFFLINE_AI_ROUTES,
  ...PLANNED_DEMO_ACTIONS,
  ...LOCAL_DEMO_ACTIONS,
]);

const UUID =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const PARAMETER = /^\{([a-zA-Z][a-zA-Z0-9]*)\}$/;

export function isDemoProxyMethod(method: string): method is DemoHttpMethod {
  return method === "GET" || method === "POST" || method === "DELETE";
}

/** Match only the fixed catalogue. URL/context validation remains in the BFF. */
export function matchDemoRoute(
  method: string,
  path: readonly string[],
): DemoRouteDeclaration | null {
  if (
    !isDemoProxyMethod(method) ||
    !Array.isArray(path) ||
    !path.length ||
    path.some(
      (part) => typeof part !== "string" || !/^[A-Za-z0-9-]+$/.test(part),
    )
  )
    return null;
  return (
    [
      ...DEMO_A2_ROUTES,
      ...FULL_DEMO_ROUTES,
      ...AI_DIAGNOSTIC_ROUTES,
      ...OFFLINE_AI_ROUTES,
    ].find((item) => {
      if (item.method !== method) return false;
      const segments = item.template.split("/");
      return (
        segments.length === path.length &&
        segments.every((segment, index) =>
          PARAMETER.test(segment)
            ? UUID.test(path[index])
            : segment === path[index],
        )
      );
    }) ?? null
  );
}

export class DemoActionCatalogError extends Error {
  constructor(
    public readonly code:
      "unknown_action" | "action_unavailable" | "invalid_route_parameters",
  ) {
    super(code);
    this.name = "DemoActionCatalogError";
  }
}

export type BuiltDemoRoute = Readonly<{
  actionId: string;
  method: DemoHttpMethod;
  path: string;
  proxyPath: string;
  backendPath: string;
  status: "declared";
  backendStatus: DemoRouteDeclaration["backendStatus"];
  provesExecution: false;
  provesChainConfirmation: false;
}>;

/** UUID-only parameters cannot encode a slash, query, host or URL override. */
export function buildDemoRoute(
  actionId: string,
  parameters: Readonly<Record<string, string>> = {},
): BuiltDemoRoute {
  const action = DEMO_ACTION_CATALOG.find((item) => item.id === actionId);
  if (!action) throw new DemoActionCatalogError("unknown_action");
  if (action.status !== "declared")
    throw new DemoActionCatalogError("action_unavailable");
  const names = action.template.split("/").flatMap((segment) => {
    const match = PARAMETER.exec(segment);
    return match ? [match[1]] : [];
  });
  if (
    !parameters ||
    typeof parameters !== "object" ||
    Array.isArray(parameters) ||
    Object.keys(parameters).length !== names.length ||
    names.some(
      (name) =>
        !Object.hasOwn(parameters, name) ||
        typeof parameters[name] !== "string" ||
        !UUID.test(parameters[name]),
    )
  )
    throw new DemoActionCatalogError("invalid_route_parameters");
  const path = action.template
    .split("/")
    .map((segment) => {
      const match = PARAMETER.exec(segment);
      return match ? parameters[match[1]] : segment;
    })
    .join("/");
  return Object.freeze({
    actionId: action.id,
    method: action.method,
    path,
    proxyPath: `/api/a2/${path}`,
    backendPath: `/v2/${path}`,
    status: "declared" as const,
    backendStatus: action.backendStatus,
    provesExecution: false as const,
    provesChainConfirmation: false as const,
  });
}
