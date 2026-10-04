"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { LanguageSwitcher, useI18n } from "@/components/language-provider";
import {
  A2RequestGeneration,
  StaleA2Response,
  commitA2Response,
  guardA2Response,
  decimalToAtomic,
  deploymentIdentity,
  formatAtomic,
  freeLockedAtomic,
  isChainConfirmed,
  isPendingOperation,
  mayPrepareSigning,
  maySignRequest,
  publicJson,
  validUuid,
  type A2User,
  type A2Project,
  type A2Procurement,
  type A2Operation,
  type A2Document,
  type A2SigningRequest,
  type A2Ledger,
  type A2AsyncScope,
} from "@/lib/a2-workbench";
import { buildDemoRoute, matchDemoRoute } from "@/lib/demo-action-catalog";
import {
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
  type DemoWorkspace,
  type DemoExchange,
  type DemoQuote,
} from "@/lib/full-demo-ui";
import { formatHkdCents } from "@/lib/payment-boundary";

type Json = Record<string, unknown>;
type Mutation = {
  path: string;
  body: Json | FormData;
  key: string;
  scope: A2AsyncScope;
};

class WorkbenchError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public operationId?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/a2/${path}`, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new WorkbenchError(
      0,
      "transport_unavailable",
      "連線中斷；結果未知，請用同一請求重試。 Connection unavailable; outcome unknown. Retry the exact request.",
    );
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = body && typeof body === "object" ? (body as Json) : {};
    const error =
      envelope.error && typeof envelope.error === "object"
        ? (envelope.error as Json)
        : {};
    throw new WorkbenchError(
      response.status,
      typeof error.code === "string" ? error.code : "upstream_error",
      typeof error.message === "string"
        ? error.message.slice(0, 600)
        : "A2 is unavailable; no demo fallback.",
      typeof error.operationId === "string" && validUuid(error.operationId)
        ? error.operationId
        : undefined,
    );
  }
  if (!body || typeof body !== "object")
    throw new WorkbenchError(
      502,
      "invalid_response",
      "Invalid A2 JSON response",
    );
  return body as T;
}

function Field({
  label,
  name,
  required = true,
  ...props
}: {
  label: string;
  name: string;
  required?: boolean;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label>
      {label}
      <input name={name} required={required} {...props} />
    </label>
  );
}

function Panel({
  title,
  children,
  className = "",
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`a2-panel ${className}`}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function formValue(form: FormData, name: string): string {
  const value = form.get(name);
  if (typeof value !== "string" || !value.trim())
    throw new Error(`${name} is required`);
  return value.trim();
}

function uuidValue(form: FormData, name: string): string {
  const value = formValue(form, name);
  if (!validUuid(value))
    throw new Error(
      `${name}: enter the API UUID, not a business ID / 請填 API UUID`,
    );
  return value;
}

function VersionSelect({
  name,
  category,
  documents,
}: {
  name: string;
  category: string;
  documents: A2Document[];
}) {
  return (
    <label>
      {category}
      <select name={name} required defaultValue="">
        <option value="">Select uploaded evidence / 選擇已上傳證據</option>
        {documents
          .filter((doc) => doc.category === category)
          .map((doc) => (
            <option key={doc.versionId} value={doc.versionId}>
              {doc.originalFilename} · {doc.versionId.slice(0, 8)}
            </option>
          ))}
      </select>
    </label>
  );
}

export function A2Workbench({
  portal,
}: {
  portal?: "foundation" | "recipient" | "donor" | "admin";
}) {
  const { locale } = useI18n();
  const label = (zh: string, en: string) => (locale === "en" ? en : zh);
  const [user, setUser] = useState<A2User | null>(null);
  const userRef = useRef<A2User | null>(null);
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [deployment, setDeployment] = useState<Json | null>(null);
  const deploymentRef = useRef<Json | null>(null);
  const generationRef = useRef(new A2RequestGeneration());
  const [projects, setProjects] = useState<A2Project[]>([]);
  const [procurements, setProcurements] = useState<A2Procurement[]>([]);
  const [projectId, setProjectId] = useState("");
  const [procurementId, setProcurementId] = useState("");
  const [ledger, setLedger] = useState<A2Ledger | null>(null);
  const [documents, setDocuments] = useState<A2Document[]>([]);
  const [operations, setOperations] = useState<A2Operation[]>([]);
  const [signing, setSigning] = useState<A2SigningRequest | null>(null);
  const [kind, setKind] = useState<A2SigningRequest["kind"]>("reserve");
  const [consent, setConsent] = useState(false);
  const [retry, setRetry] = useState<Mutation | null>(null);
  const [lastKey, setLastKey] = useState("");
  const [integration, setIntegration] = useState<FullDemoContext | null>(null);
  const integrationRef = useRef<FullDemoContext | null>(null);
  const [workspace, setWorkspace] = useState<DemoWorkspace | null>(null);
  const [workspaceProcurementId, setWorkspaceProcurementId] = useState("");
  const [paymentStatus, setPaymentStatus] = useState<Json | null>(null);
  const [progress, setProgress] = useState<Json | null>(null);
  const [account, setAccount] = useState<Json | null>(null);
  const [exchanges, setExchanges] = useState<DemoExchange[]>([]);
  const [ownRequests, setOwnRequests] = useState<A2SigningRequest[]>([]);
  const [quote, setQuote] = useState<DemoQuote | null>(null);
  const [fundingDisplay, setFundingDisplay] = useState("100");
  const [fundingId, setFundingId] = useState("");
  const viewRef = useRef({ projectId, procurementId, fundingDisplay });
  const viewRevisionRef = useRef(0);
  viewRef.current = { projectId, procurementId, fundingDisplay };

  const asyncContext = useCallback(
    () => ({
      identity: userRef.current
        ? `${userRef.current.id}:${userRef.current.role}:${userRef.current.walletAddress.toLowerCase()}`
        : null,
      deployment: deploymentRef.current
        ? deploymentIdentity(deploymentRef.current)
        : null,
    }),
    [],
  );

  const captureScope = useCallback(
    () => generationRef.current.capture(asyncContext()),
    [asyncContext],
  );
  const scopeCurrent = useCallback(
    (scope: A2AsyncScope) =>
      generationRef.current.current(scope, asyncContext()),
    [asyncContext],
  );
  const scopedRequest = useCallback(
    <T,>(scope: A2AsyncScope, path: string, options: RequestInit = {}) =>
      guardA2Response(generationRef.current, scope, asyncContext, () =>
        request<T>(path, options),
      ),
    [asyncContext],
  );
  const assertScope = useCallback(
    (scope: A2AsyncScope) =>
      generationRef.current.assertCurrent(scope, asyncContext()),
    [asyncContext],
  );
  const commitScope = useCallback(
    (scope: A2AsyncScope, commit: () => void) =>
      commitA2Response(generationRef.current, scope, asyncContext, commit),
    [asyncContext],
  );

  const resetPrivateState = useCallback(() => {
    generationRef.current.invalidate();
    busyRef.current = false;
    setBusy(false);
    setNotice("");
    setError("");
    setLastKey("");
    setProjects([]);
    setProcurements([]);
    setProjectId("");
    setProcurementId("");
    setLedger(null);
    setDocuments([]);
    setOperations([]);
    setSigning(null);
    setConsent(false);
    setRetry(null);
    integrationRef.current = null;
    setIntegration(null);
    setWorkspace(null);
    setWorkspaceProcurementId("");
    setPaymentStatus(null);
    setProgress(null);
    setAccount(null);
    setExchanges([]);
    setOwnRequests([]);
    setQuote(null);
    setFundingId("");
  }, []);

  const setIdentity = useCallback(
    (next: A2User | null) => {
      userRef.current = next;
      setUser(next);
      resetPrivateState();
      setKind(next?.role === "recipient" ? "receipt" : "reserve");
    },
    [resetPrivateState],
  );

  const recordOperation = useCallback(
    (operation: A2Operation, scope: A2AsyncScope) => {
      if (!scopeCurrent(scope)) return;
      setOperations((items) =>
        !scopeCurrent(scope)
          ? items
          : [
              operation,
              ...items.filter(
                (item) => item.operationId !== operation.operationId,
              ),
            ].slice(0, 12),
      );
    },
    [scopeCurrent],
  );

  const loadLists = useCallback(
    async (current: A2User, scope: A2AsyncScope) => {
      const results = await scopedRequest<{ items: A2Project[] }>(
        scope,
        "projects",
      );
      assertScope(scope);
      setProjects(results.items);
      setProjectId((existing) =>
        !scopeCurrent(scope)
          ? existing
          : results.items.some((item) => item.id === existing)
            ? existing
            : (results.items[0]?.id ?? ""),
      );
      if (
        ["foundation", "recipient", "human_approver"].includes(current.role)
      ) {
        const privateResults = await scopedRequest<{ items: A2Procurement[] }>(
          scope,
          "procurements",
        );
        assertScope(scope);
        setProcurements(privateResults.items);
      } else setProcurements([]);
      try {
        const value = await scopedRequest<Json>(scope, "integration-context");
        assertScope(scope);
        const context = parseFullDemoContext(
          value,
          deploymentRef.current ?? {},
        );
        integrationRef.current = context;
        setIntegration(context);
        if (!context) return;
        const opList = await scopedRequest<{ items: A2Operation[] }>(
          scope,
          "operations",
        );
        // The actual list returns summaries with chainVerified=false. Restore
        // owned detail receipts rather than relabel confirmed chain work off-chain.
        const operationFacts = await Promise.all(
          opList.items.map((item) =>
            scopedRequest<A2Operation>(scope, `operations/${item.operationId}`),
          ),
        );
        assertScope(scope);
        setOperations(operationFacts);
        if (["human_approver", "recipient"].includes(current.role)) {
          const list = await scopedRequest<{ items: A2SigningRequest[] }>(
            scope,
            "signing-requests",
          );
          assertScope(scope);
          setOwnRequests(list.items);
          setSigning((previous) =>
            previous
              ? (list.items.find((item) => item.id === previous.id) ?? previous)
              : null,
          );
        }
        if (["donor", "foundation"].includes(current.role)) {
          const list = await scopedRequest<{ items: DemoExchange[] }>(
            scope,
            "mock-exchanges",
          );
          assertScope(scope);
          setExchanges(list.items);
          const ownAccount = await scopedRequest<Json>(
            scope,
            "mock-hkd/accounts/me",
          );
          commitScope(scope, () => setAccount(ownAccount));
        }
      } catch (problem) {
        if (
          problem instanceof WorkbenchError &&
          [404, 503].includes(problem.status)
        ) {
          assertScope(scope);
          integrationRef.current = null;
          setIntegration(null);
        } else throw problem;
      }
    },
    [assertScope, scopedRequest, scopeCurrent, commitScope],
  );

  const freshIdentity = useCallback(
    async (scope: A2AsyncScope) => {
      const current = await scopedRequest<A2User>(scope, "me");
      assertScope(scope);
      if (
        !userRef.current ||
        current.id !== userRef.current.id ||
        current.role !== userRef.current.role ||
        current.walletAddress.toLowerCase() !==
          userRef.current.walletAddress.toLowerCase()
      ) {
        setIdentity(current);
        setError(
          "409 · identity_changed: Session identity changed; refresh before continuing. / 登入身份已變更，請重新載入。",
        );
        throw new StaleA2Response();
      }
      const config = await scopedRequest<Json>(scope, "deployment-config");
      assertScope(scope);
      const previousInstance =
        deploymentRef.current && deploymentIdentity(deploymentRef.current);
      const nextInstance = deploymentIdentity(config);
      deploymentRef.current = config;
      setDeployment(config);
      if (previousInstance !== nextInstance) {
        resetPrivateState();
        setError(
          "409 · deployment_changed: Chain deployment changed; cached facts cleared. Refresh the new namespace. / 部署已改變，舊快取已清除，請重新載入。",
        );
        throw new StaleA2Response();
      }
      return current;
    },
    [assertScope, resetPrivateState, scopedRequest, setIdentity],
  );

  const showError = useCallback(
    (value: unknown, scope: A2AsyncScope) => {
      if (!scopeCurrent(scope) || value instanceof StaleA2Response) return;
      if (value instanceof WorkbenchError) {
        if (value.status === 401) setIdentity(null);
        setError(
          `${value.status || "NETWORK"} · ${value.code}: ${value.message}`,
        );
      } else
        setError(value instanceof Error ? value.message : "Request failed");
    },
    [scopeCurrent, setIdentity],
  );

  useEffect(() => {
    let active = true;
    let scope = captureScope();
    void (async () => {
      try {
        const current = await scopedRequest<A2User>(scope, "me");
        if (!active) return;
        assertScope(scope);
        setIdentity(current);
        scope = captureScope();
        const config = await scopedRequest<Json>(scope, "deployment-config");
        if (!active) return;
        assertScope(scope);
        deploymentRef.current = config;
        setDeployment(config);
        scope = captureScope();
        await loadLists(current, scope);
      } catch (problem) {
        if (
          active &&
          !(problem instanceof WorkbenchError && problem.status === 401)
        )
          showError(problem, scope);
      } finally {
        if (active && scopeCurrent(scope)) setBooting(false);
      }
    })();
    return () => {
      active = false;
      generationRef.current.invalidate();
    };
  }, [
    assertScope,
    captureScope,
    loadLists,
    scopedRequest,
    scopeCurrent,
    setIdentity,
    showError,
  ]);

  useEffect(() => {
    const visible = procurements.filter((item) => item.projectId === projectId);
    setProcurementId((existing) =>
      visible.some((item) => item.id === existing)
        ? existing
        : (visible[0]?.id ?? ""),
    );
  }, [projectId, procurements]);

  useEffect(() => {
    viewRevisionRef.current += 1;
    setLedger(null);
    setSigning(null);
    setConsent(false);
    setQuote(null);
    setWorkspace(null);
    setWorkspaceProcurementId("");
    setPaymentStatus(null);
    setProgress(null);
    setFundingId("");
  }, [projectId]);

  useEffect(() => {
    viewRevisionRef.current += 1;
    setSigning(null);
    setConsent(false);
    setQuote(null);
    setWorkspace(null);
    setWorkspaceProcurementId("");
    setPaymentStatus(null);
  }, [procurementId]);

  const refresh = useCallback(
    async (scope: A2AsyncScope) => {
      const selectedProject = projectId;
      const selectedProcurement = procurementId;
      const selectedRevision = viewRevisionRef.current;
      const current = await freshIdentity(scope);
      await loadLists(current, scope);
      if (projectId) {
        const result = await scopedRequest<A2Ledger>(
          scope,
          `projects/${projectId}/ledger`,
        );
        commitScope(scope, () => {
          if (
            viewRevisionRef.current === selectedRevision &&
            viewRef.current.projectId === selectedProject
          )
            setLedger(result);
        });
        if (integrationRef.current) {
          const shared = await scopedRequest<Json>(
            scope,
            `projects/${projectId}/progress`,
          );
          commitScope(scope, () => {
            if (
              viewRevisionRef.current === selectedRevision &&
              viewRef.current.projectId === selectedProject
            )
              setProgress(shared);
          });
        }
      }
      if (procurementId && current.role !== "donor" && integrationRef.current) {
        const shared = await scopedRequest<DemoWorkspace>(
          scope,
          `procurements/${procurementId}/workspace`,
        );
        if (
          !Array.isArray(shared.documentVersions) ||
          !shared.allowedActions ||
          typeof shared.allowedActions !== "object"
        )
          throw new Error(
            "Invalid workspace contract; actions remain unavailable",
          );
        commitScope(scope, () => {
          if (
            viewRevisionRef.current !== selectedRevision ||
            viewRef.current.projectId !== selectedProject ||
            viewRef.current.procurementId !== selectedProcurement
          )
            return;
          setWorkspace(shared);
          setWorkspaceProcurementId(selectedProcurement);
          setDocuments(shared.documentVersions);
        });
        const status = await scopedRequest<Json>(
          scope,
          `procurements/${procurementId}/payment-status`,
        );
        commitScope(scope, () => {
          if (
            viewRevisionRef.current === selectedRevision &&
            viewRef.current.projectId === selectedProject &&
            viewRef.current.procurementId === selectedProcurement
          )
            setPaymentStatus(status);
        });
      }
    },
    [
      commitScope,
      freshIdentity,
      loadLists,
      projectId,
      procurementId,
      scopedRequest,
    ],
  );

  // Every role reads shared server projections; never another principal's operations.
  useEffect(() => {
    if (!user || booting) return;
    const tick = () => {
      if (!document.hidden && !busyRef.current) void action(refresh, true);
    };
    tick();
    const timer = setInterval(tick, 5000);
    return () => clearInterval(timer);
  }, [user?.id, booting, projectId, procurementId, refresh]);

  // Poll only actual owned operations. Never fabricate completion or hashes.
  useEffect(() => {
    if (!user || !operations.some(isPendingOperation)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!active) return;
      const scope = captureScope();
      if (!document.hidden) {
        try {
          const current = await freshIdentity(scope);
          for (const previous of operations.filter(isPendingOperation)) {
            const result = await scopedRequest<A2Operation>(
              scope,
              `operations/${previous.operationId}`,
            );
            if (!active || !scopeCurrent(scope)) return;
            recordOperation(result, scope);
            if (result.status === "confirmed") await loadLists(current, scope);
          }
        } catch (problem) {
          if (active) showError(problem, scope);
        }
      }
      if (active && scopeCurrent(scope)) timer = setTimeout(poll, 3000);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [
    user,
    operations,
    captureScope,
    freshIdentity,
    recordOperation,
    loadLists,
    scopedRequest,
    scopeCurrent,
    showError,
  ]);

  async function action(
    work: (scope: A2AsyncScope) => Promise<void>,
    background = false,
  ) {
    if (busyRef.current) {
      if (!background)
        setError(
          "正在處理／刷新；此操作尚未送出，請稍後明確重試。 Busy refreshing; this action was not sent. Please explicitly retry.",
        );
      return;
    }
    const scope = captureScope();
    busyRef.current = true;
    setBusy(true);
    if (!background) {
      setError("");
      setNotice("");
    }
    try {
      await work(scope);
    } catch (problem) {
      showError(problem, scope);
    } finally {
      if (scopeCurrent(scope)) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  async function sendMutation(input: Mutation) {
    const scope = input.scope;
    const current = await freshIdentity(scope);
    assertScope(scope);
    setLastKey(input.key);
    setRetry(input);
    let result: Json;
    try {
      result = await scopedRequest<Json>(scope, input.path, {
        method: "POST",
        headers:
          input.body instanceof FormData
            ? { "Idempotency-Key": input.key }
            : {
                "Content-Type": "application/json",
                "Idempotency-Key": input.key,
              },
        body:
          input.body instanceof FormData
            ? input.body
            : JSON.stringify(input.body),
      });
    } catch (problem) {
      if (!scopeCurrent(scope)) throw new StaleA2Response();
      if (problem instanceof WorkbenchError && problem.operationId) {
        const operation = await scopedRequest<A2Operation>(
          scope,
          `operations/${problem.operationId}`,
        ).catch(() => null);
        if (!scopeCurrent(scope)) throw new StaleA2Response();
        if (operation) recordOperation(operation, scope);
      }
      // Network/5xx may have queued work: hold the exact key/body for explicit retry.
      if (
        problem instanceof WorkbenchError &&
        problem.status > 0 &&
        problem.status < 500
      )
        setRetry(null);
      throw problem;
    }
    if (!scopeCurrent(scope)) throw new StaleA2Response();
    setRetry(null);
    const operation = result.operation as A2Operation | undefined;
    if (operation) recordOperation(operation, scope);
    if (result.project) setProjectId((result.project as A2Project).id);
    if (result.procurement)
      setProcurementId((result.procurement as A2Procurement).id);
    if (result.document) {
      const doc = result.document as A2Document;
      setDocuments((items) => [
        ...(!scopeCurrent(scope)
          ? items
          : [doc, ...items.filter((item) => item.versionId !== doc.versionId)]),
      ]);
    }
    if (result.signingRequest) {
      setSigning(result.signingRequest as A2SigningRequest);
      setConsent(false);
    }
    if (result.exchange || result.payment) setQuote(null);
    await loadLists(current, scope);
    if (!scopeCurrent(scope)) throw new StaleA2Response();
    setNotice(
      operation && isChainConfirmed(operation)
        ? "A2 已確認 canonical receipt / Canonical chain operation confirmed"
        : "請查看 operation 狀態。HTTP 202 不代表鏈上成功。 Check operation facts; HTTP 202 is not chain confirmation.",
    );
  }

  function mutate(path: string, body: Json | FormData) {
    const declaration = matchDemoRoute("POST", path.split("/"));
    if (!declaration) throw new Error("Unknown business route");
    const scoped = path.startsWith("procurements/");
    const permission = capabilityState(
      integrationRef.current,
      declaration.id,
      scoped
        ? workspaceProcurementId === procurementId
          ? (workspace?.allowedActions ?? null)
          : null
        : undefined,
    );
    if (!permission.enabled)
      throw new Error(permission.reason ?? "action_unavailable");
    const stage = earlyActionStage(
      declaration.id,
      project ?? null,
      procurement ?? null,
      workspaceProcurementId === procurementId ? workspace : null,
    );
    if (!stage.enabled) throw new Error(stage.reason ?? "stage_unavailable");
    const facts = paymentActionFacts(
      declaration.id,
      integrationRef.current,
      workspaceProcurementId === procurementId ? workspace : null,
      projectId,
      procurementId,
    );
    if (!facts.enabled) throw new Error(facts.reason ?? "payment_not_ready");
    return sendMutation({
      path,
      body,
      key: crypto.randomUUID(),
      scope: captureScope(),
    });
  }

  function submitForm(
    event: FormEvent<HTMLFormElement>,
    work: (form: FormData, scope: A2AsyncScope) => Promise<void>,
  ) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void action((scope) => work(form, scope));
  }

  const project = projects.find((item) => item.id === projectId);
  const procurement = procurements.find((item) => item.id === procurementId);
  const scopedDocs = documents.filter(
    (item) => item.procurementId === procurementId,
  );
  const foundation = user?.role === "foundation";
  const recipient = user?.role === "recipient";
  const human = user?.role === "human_approver";
  const blocked = busy || Boolean(retry);
  const defaults = foundationDefaults(integration);
  const cap = (id: string, scoped = false) => {
    const permission = capabilityState(
      integration,
      id,
      scoped || id === "procurement.chain.create"
        ? workspaceProcurementId === procurementId
          ? (workspace?.allowedActions ?? null)
          : null
        : undefined,
    );
    if (!permission.enabled) return permission;
    const stage = earlyActionStage(
      id,
      project ?? null,
      procurement ?? null,
      workspaceProcurementId === procurementId ? workspace : null,
    );
    if (!stage.enabled) return stage;
    return paymentActionFacts(
      id,
      integration,
      workspaceProcurementId === procurementId ? workspace : null,
      projectId,
      procurementId,
    );
  };
  const disabled = (id: string, scoped = false) =>
    blocked || !cap(id, scoped).enabled;
  const approvalAction =
    kind === "release"
      ? "release.human.approve"
      : kind === "settlement"
        ? "settlement.human.approve"
        : null;
  const roleTitle =
    user?.role === "foundation"
      ? label("基金會工作台", "Foundation workspace")
      : user?.role === "recipient"
        ? label("受助方收貨工作台", "Recipient workspace")
        : user?.role === "donor"
          ? label("捐款者工作台", "Donor workspace")
          : user?.role === "human_approver"
            ? label("獨立人工審批工作台", "Independent human approval")
            : label("登入你的工作區", "Sign in to your workspace");
  const expectedRole = portal === "admin" ? "human_approver" : portal;
  const workflowButton = (id: string, title: string, body: Json = {}) => (
    <div className="a2-fact">
      <button
        disabled={disabled(id, true) || !procurementId}
        onClick={() =>
          void action(() =>
            mutate(buildDemoRoute(id, { procurementId }).path, body),
          )
        }
      >
        {title}
      </button>
      {!cap(id, true).enabled && (
        <small className="muted">{cap(id, true).reason}</small>
      )}
    </div>
  );
  if (user && expectedRole && user.role !== expectedRole)
    return (
      <main className="a2-workbench">
        <LanguageSwitcher />
        <h1>
          {label(
            "此工作區不屬於當前身份",
            "This workspace does not match your identity",
          )}
        </h1>
        <p>
          {user.username} · {user.role}
        </p>
        <Link href={roleHome(user.role) ?? "/integration"}>
          {label("返回自己的工作區", "Return to your workspace")}
        </Link>
      </main>
    );
  let freeAmount: string | null = null;
  try {
    if (ledger) freeAmount = formatAtomic(freeLockedAtomic(ledger));
  } catch {
    /* Do not render a manufactured balance. */
  }

  return (
    <main className="a2-workbench">
      <header className="a2-header">
        <div>
          <p className="eyebrow">PoG · PROOF OF GIVING</p>
          <h1>{roleTitle}</h1>
        </div>
        <div className="a2-header-actions">
          <LanguageSwitcher />
          {user && (
            <Link href={roleHome(user.role) ?? "/integration"}>
              {label("我的角色頁面", "My role page")}
            </Link>
          )}
        </div>
      </header>
      <aside className="a2-scope-banner">
        <strong>
          {label(
            "本地 Anvil · 無價值 MockHKD · 非真實金融服務",
            "Local Anvil · Valueless MockHKD · No real financial services",
          )}
        </strong>
        <p>
          {label(
            "指定項目捐款先冻结；收貨及最終人工審批後才釋放發票金額給基金會，再模擬兌換、供應商付款和獨立人審結算。",
            "Project donations are locked. Receipt and final human approval precede invoice-limited Foundation release, simulated redemption, supplier payment and independent settlement.",
          )}
        </p>
        <p>
          {label(
            "真實 AI 未交付時保留待審；合成機械測試證據會明確標記，四個頁面身份均不可代 AI 簽署。本頁只使用共享 API／資料庫／鏈上事實，不使用舊 JSON 賬本。",
            "Real AI remains pending until delivered. Synthetic mechanical evidence is explicitly labelled; none of the four UI identities can sign for AI. Only shared API/database/chain facts are used, never the old JSON ledger.",
          )}
        </p>
      </aside>
      {error && (
        <div className="a2-message a2-error" role="alert">
          {error}
          <p>
            {label(
              "409／過期／舊部署需核對最新狀態並重新準備；不會自動重簽或重發。",
              "For 409, expiry or an old deployment, inspect current state and prepare fresh authorization. No automatic re-signing or re-broadcast.",
            )}
          </p>
        </div>
      )}
      {notice && (
        <div className="a2-message" role="status">
          {notice}
        </div>
      )}
      {booting && (
        <p role="status">
          {label("正在核對 A2 session…", "Checking A2 session…")}
        </p>
      )}
      <Panel
        title={label(
          "1 · 應用身份（不是鏈 owner）",
          "1 · Application identity (not chain owner)",
        )}
      >
        {user ? (
          <>
            <div className="a2-identity">
              <strong>
                {user.username} · {user.role}
              </strong>
              <code>{user.walletAddress}</code>
              <small>User UUID: {user.id}</small>
            </div>
            <p className="muted">
              {label(
                "admin 僅以 human_approver 身份簽署自身項目，不能代替 Foundation、Recipient 或 AI。三台電腦使用各自登入；同瀏覽器分頁共享 session。",
                "admin signs only as its bound human_approver, never as Foundation, Recipient or AI. Three computers use independent sessions; tabs in one browser share a session.",
              )}
            </p>
            <div className="button-row">
              <button disabled={busy} onClick={() => void action(refresh)}>
                {label("核對身份／刷新", "Verify identity / refresh")}
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void action(async (scope) => {
                    await scopedRequest(scope, "sessions/current", {
                      method: "DELETE",
                    });
                    assertScope(scope);
                    setIdentity(null);
                    setNotice("Logged out / 已登出");
                  })
                }
              >
                {label("登出並清除本頁資料", "Log out and clear workspace")}
              </button>
            </div>
          </>
        ) : (
          <form
            className="a2-form"
            onSubmit={(event) =>
              submitForm(event, async (form, scope) => {
                const password = form.get("password");
                if (typeof password !== "string" || !password.length)
                  throw new Error("Password is required");
                const response = await scopedRequest<{
                  user: A2User;
                  expiresAt: string;
                }>(scope, "sessions", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    username: formValue(form, "username"),
                    password,
                  }),
                });
                assertScope(scope);
                setIdentity(response.user);
                let authenticatedScope = captureScope();
                try {
                  const config = await scopedRequest<Json>(
                    authenticatedScope,
                    "deployment-config",
                  );
                  assertScope(authenticatedScope);
                  deploymentRef.current = config;
                  setDeployment(config);
                  authenticatedScope = captureScope();
                  await loadLists(response.user, authenticatedScope);
                  if (scopeCurrent(authenticatedScope))
                    setNotice("A2 session established / 已登入 A2");
                } catch (problem) {
                  showError(problem, authenticatedScope);
                }
                const home = roleHome(response.user.role);
                if (home) window.location.assign(home);
              })
            }
          >
            <fieldset disabled={busy || booting} className="a2-grid">
              <label>
                {label("標準帳號", "Standard account")}
                <select name="username" defaultValue="foundation">
                  <option>foundation</option>
                  <option>recipient</option>
                  <option>donor</option>
                  <option>admin</option>
                </select>
              </label>
              <Field
                label={label(
                  "A2 密碼（不同於舊演示）",
                  "A2 password (separate from legacy)",
                )}
                name="password"
                type="password"
                autoComplete="current-password"
              />
              <button type="submit" className="primary">
                {label("登入 A2", "Sign in to A2")}
              </button>
            </fieldset>
          </form>
        )}
      </Panel>

      {user && (
        <nav className="button-row" aria-label="Role workflow navigation">
          <a className="button" href="#project-workspace">
            {label("項目與進度", "Project & progress")}
          </a>
          {foundation && (
            <>
              <a className="button" href="#foundation-create">
                {label("建立項目／採購", "Create project / procurement")}
              </a>
              <a className="button" href="#evidence-workspace">
                {label("採購證據", "Evidence")}
              </a>
              <a className="button" href="#payment-workspace">
                {label("放款／付款／結算", "Release / payment / settlement")}
              </a>
            </>
          )}
          {recipient && (
            <a className="button" href="#evidence-workspace">
              {label("收貨照片與簽署", "Receipt evidence & signature")}
            </a>
          )}
          {human && (
            <a className="button" href="#authorization-workspace">
              {label("獨立人工審批", "Independent approvals")}
            </a>
          )}
          {user.role === "donor" && (
            <a className="button" href="#donation-workspace">
              {label("兌換與捐款", "Convert & donate")}
            </a>
          )}
          <a className="button" href="#operation-workspace">
            {label("我的操作", "My operations")}
          </a>
        </nav>
      )}
      {user && !integration && (
        <p className="a2-message a2-warning" role="status">
          {label(
            "全流程服務／能力未就緒；業務按鈕保持停用，不會回退假成功。",
            "Full-demo service/capabilities are not ready. Business actions remain disabled; no mock-success fallback.",
          )}
        </p>
      )}
      {account && (
        <Panel title={label("我的模擬 HKD 賬戶", "My simulated HKD account")}>
          <pre>{publicJson(account)}</pre>
        </Panel>
      )}

      {integration && (
        <details className="a2-panel">
          <summary>
            {label(
              "按鈕不可用的後端原因",
              "Backend reasons for disabled actions",
            )}
          </summary>
          <ul>
            {Object.entries({
              ...integration.capabilities,
              ...(workspaceProcurementId === procurementId
                ? (workspace?.allowedActions ?? {})
                : {}),
            })
              .filter(([, value]) => !value.implemented || !value.enabled)
              .map(([id, value]) => (
                <li key={id}>
                  <code>{id}</code> · {value.reasonCode ?? "action_unavailable"}
                </li>
              ))}
          </ul>
          {foundation && !defaults && (
            <p>
              {label(
                "受控 Foundation 預設身份尚未初始化，不能建立項目。",
                "Controlled Foundation defaults have not been provisioned; project creation is disabled.",
              )}
            </p>
          )}
        </details>
      )}

      <Panel
        title={label(
          "部署證據／可用能力",
          "Deployment facts / available capabilities",
        )}
      >
        {!deployment ? (
          <p>
            {label(
              "A2 未配置或無法連線；不提供演示資料回退。",
              "A2 is unconfigured or unreachable. No demo fallback.",
            )}
          </p>
        ) : (
          <>
            <p>
              {label("由 A2 回傳：", "Returned by A2:")}{" "}
              <strong>{String(deployment.mode ?? "unknown")}</strong> · chain
              verified:{" "}
              <strong>{deployment.verified === true ? "true" : "false"}</strong>
            </p>
            <details>
              <summary>
                {label(
                  "查看部署／adapter 原始資料（唯讀）",
                  "Inspect deployment / adapter facts (read-only)",
                )}
              </summary>
              <pre>{publicJson(deployment)}</pre>
            </details>
          </>
        )}
      </Panel>

      {user && (
        <>
          {retry && (
            <Panel
              title={label(
                "結果未知：保留原請求",
                "Unknown outcome: retain exact request",
              )}
              className="a2-warning"
            >
              <p>
                {label(
                  "先核對 operation；以下重試使用同一路徑、內容與 Idempotency-Key，不會創造新請求。",
                  "Inspect operation status first. Retry below preserves the same path, body and Idempotency-Key.",
                )}
              </p>
              <code>
                {retry.path} · {retry.key}
              </code>
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() => void action(() => sendMutation(retry))}
                >
                  {label("重試原請求", "Retry exact request")}
                </button>
              </div>
            </Panel>
          )}
          <Panel
            title={label(
              "2 · 項目／採購選擇（API UUID）",
              "2 · Project / procurement (API UUID)",
            )}
          >
            <div id="project-workspace" />
            <div className="a2-grid">
              <label>
                {label("項目", "Project")}
                <select
                  value={projectId}
                  disabled={blocked}
                  onChange={(event) => {
                    viewRevisionRef.current += 1;
                    viewRef.current.projectId = event.target.value;
                    setQuote(null);
                    setProjectId(event.target.value);
                  }}
                >
                  <option value="">{label("尚無項目", "No project")}</option>
                  {projects.map((item) => (
                    <option value={item.id} key={item.id}>
                      {item.title} · {item.chainState.status}
                    </option>
                  ))}
                </select>
              </label>
              {user.role !== "donor" && (
                <label>
                  {label("此項目採購", "Project procurement")}
                  <select
                    value={procurementId}
                    disabled={blocked}
                    onChange={(event) => {
                      viewRevisionRef.current += 1;
                      viewRef.current.procurementId = event.target.value;
                      setQuote(null);
                      setProcurementId(event.target.value);
                    }}
                  >
                    <option value="">
                      {label("尚無採購", "No procurement")}
                    </option>
                    {procurements
                      .filter((item) => item.projectId === projectId)
                      .map((item) => (
                        <option value={item.id} key={item.id}>
                          {item.title} · {item.chainState.status}
                        </option>
                      ))}
                  </select>
                </label>
              )}
            </div>
            {project && (
              <div className="a2-fact">
                <strong>{project.title}</strong>
                <p>{project.publicSummary}</p>
                <code>UUID {project.id}</code>
                <code>businessId {project.businessId}</code>
                <p>
                  A2 state: {project.chainState.status} · verified:{" "}
                  {String(project.chainState.verified)}
                </p>
              </div>
            )}
            {procurement && (
              <div className="a2-fact">
                <strong>{procurement.title}</strong>
                <code>UUID {procurement.id}</code>
                <code>businessId {procurement.businessId}</code>
                <code>vendor {procurement.vendorWallet}</code>
                <p>
                  {label("預算上限", "Budget cap")}:{" "}
                  {formatAtomic(procurement.budgetCapAtomic)} mHKD · A2 state:{" "}
                  {procurement.chainState.status}
                </p>
              </div>
            )}
            {project && (
              <button
                disabled={busy}
                onClick={() =>
                  void action(async (scope) => {
                    await freshIdentity(scope);
                    const result = await scopedRequest<A2Ledger>(
                      scope,
                      `projects/${projectId}/ledger`,
                    );
                    commitScope(scope, () => setLedger(result));
                  })
                }
              >
                {label("讀取鏈上賬本", "Read on-chain ledger")}
              </button>
            )}
            {ledger?.projectId === projectId && (
              <>
                <div className="a2-ledger">
                  <div>
                    <small>{label("捐款", "Deposits")}</small>
                    <strong>{formatAtomic(ledger.depositsAtomic)}</strong>
                  </div>
                  <div>
                    <small>{label("預留", "Reserved")}</small>
                    <strong>{formatAtomic(ledger.reservedAtomic)}</strong>
                  </div>
                  <div>
                    <small>
                      {label("已釋放給 Foundation", "Released to Foundation")}
                    </small>
                    <strong>{formatAtomic(ledger.releasedAtomic)}</strong>
                  </div>
                  <div>
                    <small>
                      {label(
                        "未預留且仍冻结（推導）",
                        "Free and still locked (derived)",
                      )}
                    </small>
                    <strong>{freeAmount ?? "unavailable"}</strong>
                  </div>
                </div>
                {ledger.currentCallerDonorCreditAtomic !== null && (
                  <p>
                    {label(
                      "僅當前 Donor 捐款信用",
                      "Current Donor credit only",
                    )}
                    : {formatAtomic(ledger.currentCallerDonorCreditAtomic)} mHKD
                  </p>
                )}
                <details>
                  <summary>
                    {label("查看賬本原始資料", "Inspect ledger facts")}
                  </summary>
                  <pre>{publicJson(ledger)}</pre>
                </details>
              </>
            )}
          </Panel>

          <Panel
            title={label(
              "共享進度與風險證據",
              "Shared progress & risk evidence",
            )}
          >
            <p>
              {label(
                "所有角色讀同一後端事實，各自只能看到獲授權的投影。未收到證據不會自動通過。",
                "Roles read the same backend facts through authorized projections. Missing evidence never passes automatically.",
              )}
            </p>
            {progress ? (
              <details open>
                <summary>{label("項目進度", "Project progress")}</summary>
                <pre>{publicJson(progress)}</pre>
              </details>
            ) : (
              <p className="muted">
                {label("共享進度尚未可用", "Shared progress unavailable")}
              </p>
            )}
            {user.role !== "donor" && (
              <div className="a2-grid">
                <div>
                  <strong>PRE</strong>
                  <pre>
                    {workspace?.preAssessment
                      ? publicJson(workspace.preAssessment)
                      : "pending / unavailable"}
                  </pre>
                </div>
                <div>
                  <strong>FINAL</strong>
                  <pre>
                    {workspace?.finalAssessment
                      ? publicJson(workspace.finalAssessment)
                      : "pending / unavailable"}
                  </pre>
                </div>
              </div>
            )}
            <p className="muted">
              {label(
                "真實 AI 服務後接。若機械聯調使用 synthetic 風險樣例，後端 provenance 必須明示；這不是實際 AI 檢測，四種業務角色不能代簽 AI。",
                "Real AI connects later. Synthetic mechanical-test risk samples must disclose provenance; they are not model detection and business roles cannot sign AI.",
              )}
            </p>
          </Panel>

          {foundation && (
            <Panel
              title={label(
                "Foundation · 建立項目與採購",
                "Foundation · Create project and procurement",
              )}
            >
              <div id="foundation-create" />
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) =>
                    mutate("projects", {
                      title: formValue(form, "title"),
                      publicSummary: formValue(form, "publicSummary"),
                      recipientUserId: defaults?.recipientUserId,
                      humanApproverUserId: defaults?.humanApproverUserId,
                    }),
                  )
                }
              >
                <fieldset
                  disabled={disabled("project.draft.create") || !defaults}
                  className="a2-grid"
                >
                  <Field
                    label={label("項目名稱", "Project title")}
                    name="title"
                    maxLength={160}
                  />
                  <Field
                    label={label("公開摘要", "Public summary")}
                    name="publicSummary"
                    maxLength={4000}
                  />
                  <button type="submit">
                    {label("建立 off-chain 草稿", "Create off-chain draft")}
                  </button>
                </fieldset>
              </form>
              <p className="muted">
                {label(
                  "受助方、獨立人審及固定供應商由後端受控初始化預置，不需要手填 UUID／錢包，也不能用此表修改身份。",
                  "Recipient, independent human and fixed supplier come from controlled backend defaults. No manual UUID/wallet entry or role changes.",
                )}
              </p>
              <div className="button-row">
                <button
                  disabled={disabled("project.chain.create") || !projectId}
                  onClick={() =>
                    void action(() =>
                      mutate(`projects/${projectId}/chain/create`, {}),
                    )
                  }
                >
                  {label(
                    "提交所選項目鏈上建立",
                    "Queue selected project on chain",
                  )}
                </button>
              </div>
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) => {
                    if (!projectId) throw new Error("Select a project first");
                    await mutate("procurements", {
                      projectId,
                      title: formValue(form, "title"),
                      vendorWallet: defaults?.supplierWallet,
                      budgetCapAtomic: decimalToAtomic(
                        formValue(form, "budget"),
                      ),
                    });
                  })
                }
              >
                <fieldset
                  disabled={
                    disabled("procurement.draft.create") ||
                    !projectId ||
                    !defaults
                  }
                  className="a2-grid"
                >
                  <Field
                    label={label("採購名稱", "Procurement title")}
                    name="title"
                    maxLength={160}
                  />
                  <Field
                    label={label("固定供應商錢包", "Fixed vendor wallet")}
                    name="vendorWallet"
                    value={defaults?.supplierWallet ?? ""}
                    readOnly
                  />
                  <Field
                    label={label("預算上限（mHKD）", "Budget cap (mHKD)")}
                    name="budget"
                    inputMode="decimal"
                  />
                  <button type="submit">
                    {label("建立採購草稿", "Create procurement draft")}
                  </button>
                </fieldset>
              </form>
              <div className="button-row">
                <button
                  disabled={
                    disabled("procurement.chain.create") || !procurementId
                  }
                  onClick={() =>
                    void action(() =>
                      mutate(`procurements/${procurementId}/chain/create`, {}),
                    )
                  }
                >
                  {label(
                    "提交所選採購鏈上建立",
                    "Queue selected procurement on chain",
                  )}
                </button>
              </div>
            </Panel>
          )}

          {user.role === "donor" && (
            <Panel
              title={label(
                "Donor · 模擬兌換後捐入指定項目",
                "Donor · Simulated conversion, then project donation",
              )}
            >
              <p>
                {label(
                  "先讀取純報價，再明確兌換模擬 HKD；只有服務端完成 HKD 扣賬與鏈上新 mint 對賬的 funding 才能捐入所選項目。faucet 餘額不能代替兑换額度。",
                  "Read a quote, explicitly convert simulated HKD, then donate reconciled new-mint funding to its bound project. Faucet tokens never replace funded credit.",
                )}
              </p>
              <div id="donation-workspace" />
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (_form, scope) => {
                    if (!projectId) throw new Error("Select a project first");
                    if (!cap("mock.exchange.quote").enabled)
                      throw new Error("quote_unavailable");
                    const quoteView = { ...viewRef.current };
                    const quoteRevision = viewRevisionRef.current;
                    await freshIdentity(scope);
                    const amount = fullDemoAmount(fundingDisplay);
                    const result = await scopedRequest<DemoQuote>(
                      scope,
                      "mock-exchanges/quote",
                      {
                        method: "POST",
                        headers: {
                          "Content-Type": "application/json",
                          "Idempotency-Key": crypto.randomUUID(),
                        },
                        body: JSON.stringify({
                          direction: "hkd_to_mock",
                          projectId,
                          hkdCents: amount.hkdCents,
                        }),
                      },
                    );
                    if (
                      !quoteForCurrentScope(result, integrationRef.current) ||
                      result.quote.direction !== "hkd_to_mock" ||
                      result.quote.hkdCents !== amount.hkdCents ||
                      result.quote.amountAtomic !== amount.amountAtomic
                    )
                      throw new Error("Invalid quote binding / precision");
                    commitScope(scope, () => {
                      if (
                        viewRevisionRef.current === quoteRevision &&
                        viewRef.current.projectId === quoteView.projectId &&
                        viewRef.current.fundingDisplay ===
                          quoteView.fundingDisplay
                      )
                        setQuote(result);
                    });
                  })
                }
              >
                <fieldset
                  disabled={disabled("mock.exchange.quote") || !projectId}
                  className="a2-grid"
                >
                  <Field
                    label={label(
                      "模擬 HKD 金額（最多兩位小數）",
                      "Simulated HKD (up to two decimals)",
                    )}
                    name="hkd"
                    inputMode="decimal"
                    value={fundingDisplay}
                    onChange={(event) => {
                      viewRevisionRef.current += 1;
                      viewRef.current.fundingDisplay = event.target.value;
                      setFundingDisplay(event.target.value);
                      setQuote(null);
                    }}
                  />
                  <button type="submit">
                    {label("取得純報價（不執行）", "Get quote (no execution)")}
                  </button>
                </fieldset>
              </form>
              {quote?.quote.direction === "hkd_to_mock" && (
                <div className="a2-fact">
                  <p>
                    {formatHkdCents(quote.quote.hkdCents)} HKD →{" "}
                    {formatAtomic(quote.quote.amountAtomic)} mHKD ·{" "}
                    {quote.quote.rate}
                  </p>
                  <p>
                    executed=false ·{" "}
                    {quote.quote.reasonCode ??
                      (quote.quote.eligible ? "eligible" : "unavailable")}
                  </p>
                  <button
                    disabled={
                      disabled("donor.funding.convert") || !quote.quote.eligible
                    }
                    onClick={() =>
                      void action(() =>
                        mutate("mock-exchanges/funding", {
                          projectId,
                          hkdCents: fullDemoAmount(fundingDisplay).hkdCents,
                          confirm: true,
                        }),
                      )
                    }
                  >
                    {label(
                      "明確確認模擬兌換",
                      "Explicitly confirm simulated conversion",
                    )}
                  </button>
                </div>
              )}
              <label>
                {label("本項目自己的 funding", "Own funding for this project")}
                <select
                  value={fundingId}
                  onChange={(event) => setFundingId(event.target.value)}
                >
                  <option value="">
                    {label("選擇已對賬 funding", "Choose reconciled funding")}
                  </option>
                  {currentFunding(exchanges, projectId).map((item) => (
                    <option key={item.id} value={item.id}>
                      {formatHkdCents(item.hkdCents)} HKD · {item.status} ·{" "}
                      {formatAtomic(remainingFundingAtomic(item))} mHKD
                      available · {item.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="primary"
                disabled={
                  disabled("donation.funded.deposit") ||
                  !currentFunding(exchanges, projectId).some(
                    (item) =>
                      item.id === fundingId &&
                      item.reconciled &&
                      item.status === "reconciled" &&
                      remainingFundingAtomic(item) !== "0",
                  )
                }
                onClick={() =>
                  void action(async () => {
                    const funding = currentFunding(exchanges, projectId).find(
                      (item) => item.id === fundingId,
                    );
                    if (
                      !funding ||
                      !funding.reconciled ||
                      funding.status !== "reconciled" ||
                      remainingFundingAtomic(funding) === "0"
                    )
                      throw new Error("funding_not_reconciled");
                    await mutate(`projects/${projectId}/funded-donations`, {
                      fundingOperationId: funding.operationId,
                      amountAtomic: remainingFundingAtomic(funding),
                      confirm: true,
                    });
                  })
                }
              >
                {label(
                  "確認捐入所選項目並冻结",
                  "Confirm funded donation and lock in project",
                )}
              </button>
              <p className="muted">
                {cap("donor.funding.convert").reason ??
                  cap("donation.funded.deposit").reason}
              </p>
            </Panel>
          )}

          {(foundation || recipient) && (
            <Panel
              title={label(
                "3 · 上傳不可變證據版本",
                "3 · Upload immutable evidence versions",
              )}
            >
              <div id="evidence-workspace" />
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) => {
                    if (!procurementId)
                      throw new Error("Select a procurement first");
                    const file = form.get("file");
                    if (!(file instanceof File) || !file.size)
                      throw new Error("Select a nonempty PDF / PNG / JPEG");
                    if (file.size > 10 * 1024 * 1024)
                      throw new Error("File exceeds 10 MiB");
                    const upload = new FormData();
                    upload.set("procurementId", procurementId);
                    upload.set("category", formValue(form, "category"));
                    upload.set("file", file);
                    await mutate("documents", upload);
                  })
                }
              >
                <fieldset
                  disabled={disabled("document.upload") || !procurementId}
                  className="a2-grid"
                >
                  <label>
                    {label("證據種類", "Evidence category")}
                    <select name="category">
                      {(recipient
                        ? ["receipt_evidence"]
                        : [
                            "purchase_order",
                            "request",
                            "goods_request",
                            "invoice",
                            "goods_evidence",
                          ]
                      ).map((category) => (
                        <option key={category}>{category}</option>
                      ))}
                    </select>
                  </label>
                  <Field
                    label={label(
                      "檔案（PDF／PNG／JPEG ≤10 MiB）",
                      "File (PDF / PNG / JPEG ≤10 MiB)",
                    )}
                    name="file"
                    type="file"
                    accept="application/pdf,image/png,image/jpeg"
                  />
                  <button type="submit">
                    {label(
                      "上傳至私有 A2 文件庫",
                      "Upload to private A2 storage",
                    )}
                  </button>
                </fieldset>
              </form>
              <p className="muted">
                {label(
                  "上傳不是 AI 審核或鏈上登記。Recipient 收貨證據必須自己上傳；以下保留後端 workspace 原始不可變版本，刷新不丟失。",
                  "Upload is not AI review or chain registration. Recipient uploads its own evidence. The backend workspace preserves original immutable versions across refresh.",
                )}
              </p>
              {scopedDocs.map((doc) => (
                <div className="a2-fact" key={doc.versionId}>
                  <strong>
                    {doc.category} · {doc.originalFilename}
                  </strong>
                  <code>versionId {doc.versionId}</code>
                  <code>keccak256 {doc.keccak256}</code>
                  <code>sha256 {doc.sha256}</code>
                  <p>
                    {label(
                      "不可變版本，使用精確 versionId 引用。",
                      "Reference this exact immutable versionId.",
                    )}{" "}
                    · referenced: {String(doc.referenced)}
                  </p>
                  <a
                    className="button"
                    href={`/api/a2/document-versions/${doc.versionId}/content`}
                  >
                    {label("下載此精確版本", "Download this exact version")}
                  </a>
                </div>
              ))}
            </Panel>
          )}

          {foundation && (
            <Panel
              title={label(
                "4 · 記錄 PO／Invoice／Goods",
                "4 · Record PO / Invoice / Goods",
              )}
            >
              <p>
                {label(
                  "先 PO＋request＋goods_request，預留成功後才登記 Invoice＋goods。使用 API 文檔版本 UUID，不輸入或改寫哈希。",
                  "PO + request + goods_request come first. Invoice + goods follow confirmed reservation. Reference API document-version UUIDs, never caller-provided hashes.",
                )}
              </p>
              <datalist id="a2-version-ids">
                {scopedDocs.map((doc) => (
                  <option key={doc.versionId} value={doc.versionId}>
                    {doc.category}
                  </option>
                ))}
              </datalist>
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) =>
                    mutate(
                      `procurements/${procurementId}/chain/purchase-order`,
                      {
                        poDocumentVersionId: uuidValue(form, "po"),
                        requestDocumentVersionId: uuidValue(form, "request"),
                        goodsRequestDocumentVersionId: uuidValue(
                          form,
                          "goodsRequest",
                        ),
                      },
                    ),
                  )
                }
              >
                <fieldset
                  disabled={
                    disabled("procurement.po.record", true) || !procurementId
                  }
                  className="a2-grid"
                >
                  <VersionSelect
                    name="po"
                    category="purchase_order"
                    documents={scopedDocs}
                  />
                  <VersionSelect
                    name="request"
                    category="request"
                    documents={scopedDocs}
                  />
                  <VersionSelect
                    name="goodsRequest"
                    category="goods_request"
                    documents={scopedDocs}
                  />
                  <button type="submit">
                    {label("提交 PO 登記", "Queue PO registration")}
                  </button>
                </fieldset>
                {!cap("procurement.po.record", true).enabled && (
                  <p className="muted">
                    {cap("procurement.po.record", true).reason}
                  </p>
                )}
              </form>
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) =>
                    mutate(
                      `procurements/${procurementId}/chain/invoice-and-goods`,
                      {
                        invoiceDocumentVersionId: uuidValue(form, "invoice"),
                        goodsDocumentVersionId: uuidValue(form, "goods"),
                        invoiceAmountAtomic: fullInvoiceAtomic(
                          formValue(form, "amount"),
                        ),
                      },
                    ),
                  )
                }
              >
                <fieldset
                  disabled={
                    disabled("procurement.invoice.record", true) ||
                    !procurementId
                  }
                  className="a2-grid"
                >
                  <VersionSelect
                    name="invoice"
                    category="invoice"
                    documents={scopedDocs}
                  />
                  <VersionSelect
                    name="goods"
                    category="goods_evidence"
                    documents={scopedDocs}
                  />
                  <Field
                    label={label(
                      "發票金額（mHKD ≤ 預留，最多兩位小數）",
                      "Invoice (mHKD ≤ reserved, up to two decimals)",
                    )}
                    name="amount"
                    inputMode="decimal"
                  />
                  <button type="submit">
                    {label(
                      "提交 Invoice／Goods 登記",
                      "Queue Invoice / Goods registration",
                    )}
                  </button>
                </fieldset>
                {!cap("procurement.invoice.record", true).enabled && (
                  <p className="muted">
                    {cap("procurement.invoice.record", true).reason}
                  </p>
                )}
              </form>
            </Panel>
          )}

          {human && (
            <Panel
              title={label(
                "人工核對原始證據（唯讀）",
                "Review original evidence (read-only)",
              )}
            >
              {scopedDocs.length ? (
                scopedDocs.map((doc) => (
                  <div className="a2-fact" key={doc.versionId}>
                    <strong>
                      {doc.category} · {doc.originalFilename}
                    </strong>
                    <code>versionId {doc.versionId}</code>
                    <code>keccak256 {doc.keccak256}</code>
                    <a
                      href={`/api/a2/document-versions/${doc.versionId}/content`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {label(
                        "下載精確不可變版本供審批核對",
                        "Download exact immutable version for approval review",
                      )}
                    </a>
                  </div>
                ))
              ) : (
                <p className="muted">
                  {label(
                    "後端尚未提供本採購的可讀證據",
                    "No authorized evidence available for this procurement",
                  )}
                </p>
              )}
            </Panel>
          )}

          {(human || recipient) && (
            <Panel
              title={label(
                "5 · 簽名：準備 → 明確簽署 → 明確提交",
                "5 · Signature: prepare → explicitly sign → explicitly submit",
              )}
            >
              <div id="authorization-workspace" />
              <p>
                {label(
                  "只使用 A2 產生的完整 V2 EIP-712 材料；本頁不修改 chainId、domain、nonce 或 typed data。AI PRE 只能由獨立 service_ai 處理，四個頁面身份都不能代簽。",
                  "Use only complete V2 EIP-712 material prepared by A2. This page cannot edit chainId, domain, nonce or typed data. AI PRE belongs to the separate service_ai; none of the four page identities may impersonate it.",
                )}
              </p>
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) => {
                    if (
                      !procurementId ||
                      !user ||
                      !mayPrepareSigning(user.role, kind)
                    )
                      throw new Error(
                        "Current identity cannot prepare this signing kind",
                      );
                    const ttl = formValue(form, "ttl");
                    if (
                      !/^[0-9]+$/.test(ttl) ||
                      Number(ttl) < 60 ||
                      Number(ttl) > 3600
                    )
                      throw new Error("TTL must be 60–3600 seconds");
                    const body: Json = {
                      kind,
                      deadlineTtlSeconds: Number(ttl),
                    };
                    if (approvalAction && !cap(approvalAction, true).enabled)
                      throw new Error(
                        cap(approvalAction, true).reason ??
                          "approval_unavailable",
                      );
                    if (kind === "reserve")
                      body.reserveAmountAtomic = decimalToAtomic(
                        formValue(form, "reserveAmount"),
                      );
                    if (kind === "receipt")
                      body.receiptEvidenceDocumentVersionId = uuidValue(
                        form,
                        "receiptEvidence",
                      );
                    await mutate(
                      `procurements/${procurementId}/signing-requests`,
                      body,
                    );
                  })
                }
              >
                <fieldset
                  disabled={
                    disabled("authorization.prepare", true) || !procurementId
                  }
                  className="a2-grid"
                >
                  <label>
                    {label("簽名種類", "Signature kind")}
                    <select
                      value={kind}
                      onChange={(event) => {
                        setKind(event.target.value as A2SigningRequest["kind"]);
                        setSigning(null);
                        setConsent(false);
                      }}
                    >
                      <option value="reserve" disabled={!human}>
                        reserve · Human approval
                      </option>
                      <option value="receipt" disabled={!recipient}>
                        receipt · Recipient confirmation
                      </option>
                      <option value="release" disabled={!human}>
                        release · Final human decision
                      </option>
                      <option value="settlement" disabled={!human}>
                        settlement · Independent reconciliation
                      </option>
                      <option value="ai_pre" disabled>
                        ai_pre · service_ai pending
                      </option>
                    </select>
                  </label>
                  {kind === "reserve" && (
                    <Field
                      label={label("預留金額（mHKD）", "Reserve amount (mHKD)")}
                      name="reserveAmount"
                      inputMode="decimal"
                    />
                  )}
                  {kind === "receipt" && (
                    <VersionSelect
                      name="receiptEvidence"
                      category="receipt_evidence"
                      documents={scopedDocs}
                    />
                  )}
                  <Field
                    label={label(
                      "有效期（秒，60–3600）",
                      "TTL seconds (60–3600)",
                    )}
                    name="ttl"
                    type="number"
                    min={60}
                    max={3600}
                    defaultValue={900}
                  />
                  <button
                    type="submit"
                    disabled={Boolean(
                      approvalAction && disabled(approvalAction, true),
                    )}
                  >
                    {label(
                      "準備本身份授權材料",
                      "Prepare authorization for this identity",
                    )}
                  </button>
                </fieldset>
                {approvalAction && !cap(approvalAction, true).enabled && (
                  <p className="muted">{cap(approvalAction, true).reason}</p>
                )}
              </form>
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form, scope) => {
                    await freshIdentity(scope);
                    const result = await scopedRequest<A2SigningRequest>(
                      scope,
                      `signing-requests/${uuidValue(form, "requestId")}`,
                    );
                    commitScope(scope, () => {
                      setSigning(result);
                      setConsent(false);
                    });
                  })
                }
              >
                <fieldset disabled={busy} className="a2-grid">
                  <label>
                    {label("恢復本人的簽名請求", "Restore my signing request")}
                    <select name="requestId" required defaultValue="">
                      <option value="">
                        {label("選擇本人的請求", "Select my request")}
                      </option>
                      {ownRequests
                        .filter((item) => item.procurementId === procurementId)
                        .map((item) => (
                          <option value={item.id} key={item.id}>
                            {item.kind} · {item.status} · {item.id.slice(0, 8)}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button type="submit">
                    {label("讀取唯讀材料", "Read exact material")}
                  </button>
                </fieldset>
              </form>
              {signing && (
                <div className="a2-fact">
                  <strong>
                    {signing.kind} · {signing.status}
                    {signing.synthetic ? " · SYNTHETIC" : ""}
                  </strong>
                  <code>requestId {signing.id}</code>
                  <code>procurement UUID {signing.procurementId}</code>
                  <code>signer {signing.signer}</code>
                  <code>digest {signing.digest}</code>
                  <p>
                    nonce {signing.nonce} · deadline {signing.deadline} · policy
                    epoch {signing.policyEpoch}
                  </p>
                  <details>
                    <summary>
                      {label(
                        "核對完整 typed data（唯讀）",
                        "Review full typed data (read-only)",
                      )}
                    </summary>
                    <pre>{publicJson(signing.typedData)}</pre>
                  </details>
                  {signing.procurementId !== procurementId && (
                    <p className="a2-error">
                      {label(
                        "此請求不屬於目前選中的採購；請先選擇其採購。",
                        "This request targets another procurement. Select its procurement before signing.",
                      )}
                    </p>
                  )}
                  <label className="a2-consent">
                    <input
                      type="checkbox"
                      checked={consent}
                      onChange={(event) => setConsent(event.target.checked)}
                      disabled={
                        blocked ||
                        signing.procurementId !== procurementId ||
                        !maySignRequest(user, signing)
                      }
                    />
                    <span>
                      {label(
                        "我已核對 signer、項目／採購、金額、證據、nonce 及有效期，明確確認下一次簽署／提交。",
                        "I reviewed signer, project/procurement, amount, evidence, nonce and deadline, and explicitly authorize the next sign/submit action.",
                      )}
                    </span>
                  </label>
                  <div className="button-row">
                    <button
                      disabled={
                        disabled("authorization.demo.sign") ||
                        !consent ||
                        signing.procurementId !== procurementId ||
                        signing.status !== "prepared" ||
                        !maySignRequest(user, signing)
                      }
                      onClick={() =>
                        void action(() =>
                          mutate(`signing-requests/${signing.id}/sign-demo`, {
                            confirm: true,
                          }),
                        )
                      }
                    >
                      {label(
                        "確認本地測試錢包簽署（不廣播）",
                        "Confirm local demo signing (no broadcast)",
                      )}
                    </button>
                    <button
                      disabled={
                        disabled("authorization.saved.submit") ||
                        !consent ||
                        signing.procurementId !== procurementId ||
                        signing.status !== "signed" ||
                        !maySignRequest(user, signing)
                      }
                      onClick={() =>
                        void action(() =>
                          mutate(
                            `signing-requests/${signing.id}/submit-signed`,
                            { confirm: true },
                          ),
                        )
                      }
                    >
                      {label(
                        "明確提交已簽授權",
                        "Explicitly submit signed authorization",
                      )}
                    </button>
                  </div>
                  <p className="muted">
                    {label(
                      "提交是否可用由已審後端能力決定；簽署、提交及後續資金執行分開確認。前端不讀資料庫簽名、不顯示簽名、不回退演示。",
                      "Reviewed backend capabilities decide availability. Signing, submission and fund execution require separate confirmations. No database signature reads, signature disclosure or demo fallback.",
                    )}
                    {!cap("authorization.saved.submit").enabled && (
                      <span> · {cap("authorization.saved.submit").reason}</span>
                    )}
                  </p>
                </div>
              )}
            </Panel>
          )}

          {(foundation || human) && (
            <Panel
              title={label(
                "6 · 執行已批準的預留（不是放款）",
                "6 · Execute approved reservation (not release)",
              )}
            >
              <form
                className="a2-form"
                onSubmit={(event) =>
                  submitForm(event, async (form) =>
                    mutate(`procurements/${procurementId}/chain/reserve`, {
                      reserveAmountAtomic: decimalToAtomic(
                        formValue(form, "amount"),
                      ),
                    }),
                  )
                }
              >
                <fieldset
                  disabled={
                    disabled("procurement.reserve.execute", true) ||
                    !procurementId
                  }
                  className="a2-grid"
                >
                  <Field
                    label={label(
                      "與已確認人工票相同的預留金額（mHKD）",
                      "Reserve amount matching confirmed human approval (mHKD)",
                    )}
                    name="amount"
                    inputMode="decimal"
                  />
                  <button type="submit">
                    {label("確認執行 reserve", "Confirm reserve execution")}
                  </button>
                </fieldset>
                {!cap("procurement.reserve.execute", true).enabled && (
                  <p className="muted">
                    {cap("procurement.reserve.execute", true).reason}
                  </p>
                )}
              </form>
              <p>
                {label(
                  "需要當前 AI 證據＋有效人工票；AI 續評會令舊票失效。reserve 只鎖定預算，Foundation 不會收到資金。",
                  "Requires current AI evidence and valid human votes. AI renewal invalidates old votes. Reservation locks budget only; Foundation receives no funds.",
                )}
              </p>
            </Panel>
          )}

          <Panel
            title={label(
              "7 · Operation／鏈上確認證據",
              "7 · Operation / confirmation evidence",
            )}
          >
            <div id="operation-workspace" />
            <p>
              {label(
                "HTTP 202＝受理或排隊，不是捐款、收貨、放款成功。只顯示 A2 返回的步驟、receipt、canonical block 與哈希。",
                "HTTP 202 means accepted or queued, not donation, receipt or release success. Only A2-returned steps, receipts, canonical blocks and hashes are displayed.",
              )}
            </p>
            {lastKey && (
              <p className="tiny">
                Last Idempotency-Key: <code>{lastKey}</code>
              </p>
            )}
            <form
              className="a2-form"
              onSubmit={(event) =>
                submitForm(event, async (form, scope) => {
                  await freshIdentity(scope);
                  recordOperation(
                    await scopedRequest<A2Operation>(
                      scope,
                      `operations/${uuidValue(form, "operationId")}`,
                    ),
                    scope,
                  );
                })
              }
            >
              <fieldset disabled={busy} className="a2-grid">
                <label>
                  {label("本人操作", "My operation")}
                  <select name="operationId" required defaultValue="">
                    <option value="">Select my operation / 選擇本人操作</option>
                    {operations.map((item) => (
                      <option key={item.operationId} value={item.operationId}>
                        {item.operationKind} · {item.status} ·{" "}
                        {item.operationId.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="submit">
                  {label("讀取實際 operation", "Read actual operation")}
                </button>
              </fieldset>
            </form>
            {!operations.length && (
              <p className="muted">
                {label(
                  "尚無本 session operation。",
                  "No operation in this session yet.",
                )}
              </p>
            )}
            {operations.map((operation) => (
              <div className="a2-fact" key={operation.operationId}>
                <strong>
                  {operation.operationKind} · {operation.status}
                </strong>
                <code>{operation.operationId}</code>
                <p>
                  {isChainConfirmed(operation)
                    ? label(
                        "Canonical 鏈上確認（API 提供）",
                        "Canonical chain confirmation (API supplied)",
                      )
                    : operation.status === "confirmed" &&
                        !operation.chainVerified
                      ? label(
                          "操作已完成；未證明整筆操作均為鏈上確認（混合／鏈下步驟須分別核對）",
                          "Operation completed; not wholly chain-confirmed. Review mixed/off-chain steps and resource reconciliation separately.",
                        )
                      : label(
                          "尚未具備完整鏈上確認證據",
                          "No complete chain-confirmation evidence yet",
                        )}
                </p>
                {operation.errorCode && (
                  <p className="a2-error">
                    {operation.errorCode}: {operation.errorMessage}
                  </p>
                )}
                <details>
                  <summary>
                    {label(
                      "查看步驟／receipt 原始資料",
                      "Inspect step / receipt facts",
                    )}
                  </summary>
                  <pre>{publicJson(operation)}</pre>
                </details>
              </div>
            ))}
          </Panel>
        </>
      )}

      {user && user.role !== "donor" && (
        <Panel
          title={label(
            "付款後段：逐步明確確認",
            "Payment tail: separate explicit confirmations",
          )}
          className="a2-payment-pending"
        >
          <div id="payment-workspace" />
          <p>
            {label(
              "ReceiptConfirmed ≠ 最終人工批款 ≠ FundsReleased ≠ SupplierPaid。",
              "ReceiptConfirmed ≠ final human release approval ≠ FundsReleased ≠ SupplierPaid.",
            )}
          </p>
          <p>
            {label(
              "後續順序：FINAL AI 風險證據 → 最終人工審批 → 只釋放發票金額給 Foundation → 平台模擬 stablecoin 兌 HKD → Foundation 付固定供應商 → 獨立付款證據及人工確認。不能以舊 Portal 賬本中的「已付款」替代。",
              "Next: FINAL AI risk evidence → final human approval → invoice-limited release to Foundation → simulated platform stablecoin-to-HKD conversion → Foundation pays fixed supplier → separate payment evidence and human confirmation. Legacy Portal 'paid' records are not proof.",
            )}
          </p>
          <div className="a2-grid">
            {["release", "redemption", "supplierPayment", "settlement"].map(
              (stage) => (
                <div className="a2-fact" key={stage}>
                  <strong>{stage}</strong>
                  <pre>
                    {paymentStatus?.[stage]
                      ? publicJson(paymentStatus[stage])
                      : "pending / unavailable"}
                  </pre>
                </div>
              ),
            )}
          </div>
          {human &&
            ["redemption", "supplierPayment"].map((stage) => {
              const resource = paymentStatus?.[stage] as
                { evidenceId?: unknown } | null | undefined;
              return typeof resource?.evidenceId === "string" &&
                validUuid(resource.evidenceId) ? (
                <p key={stage}>
                  <a
                    href={`/api/a2/payment-evidence/${resource.evidenceId}/content`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {label(
                      "人工核對不可變付款證據",
                      "Review immutable payment evidence",
                    )}{" "}
                    · {stage}
                  </a>
                </p>
              ) : null;
            })}
          {human && (
            <p>
              {label(
                "先在獨立人工審批區選 release 或 settlement，準備→核對→本人簽署→明確提交；鏈上確認後才能執行下一步。",
                "Choose release or settlement in independent approvals: prepare → review → own signature → explicitly submit. Wait for canonical confirmation before execution.",
              )}
            </p>
          )}
          {(foundation || human) &&
            workflowButton(
              "release.execute",
              label(
                "明確執行已批准的發票限額放款",
                "Explicitly execute approved invoice-limited release",
              ),
            )}
          {foundation && (
            <>
              <button
                disabled={disabled("mock.exchange.quote") || !procurementId}
                onClick={() =>
                  void action(async (scope) => {
                    const quoteView = { ...viewRef.current };
                    const quoteRevision = viewRevisionRef.current;
                    await freshIdentity(scope);
                    const result = await scopedRequest<unknown>(
                      scope,
                      "mock-exchanges/quote",
                      {
                        method: "POST",
                        headers: {
                          "Content-Type": "application/json",
                          "Idempotency-Key": crypto.randomUUID(),
                        },
                        body: JSON.stringify({
                          direction: "mock_to_hkd",
                          procurementId,
                        }),
                      },
                    );
                    if (
                      !quoteForCurrentScope(result, integrationRef.current) ||
                      result.quote.direction !== "mock_to_hkd"
                    )
                      throw new Error("Quote is not bound to this deployment");
                    commitScope(scope, () => {
                      if (
                        viewRevisionRef.current === quoteRevision &&
                        viewRef.current.projectId === quoteView.projectId &&
                        viewRef.current.procurementId ===
                          quoteView.procurementId
                      )
                        setQuote(result);
                    });
                  })
                }
              >
                {label(
                  "取得純查詢兌回 HKD 報價（不執行）",
                  "Read redemption quote (no execution)",
                )}
              </button>
              {quote?.quote.direction === "mock_to_hkd" && (
                <div className="a2-fact">
                  <p>
                    {formatHkdCents(quote.quote.hkdCents)} HKD ·{" "}
                    {formatAtomic(quote.quote.amountAtomic)} mHKD ·
                    executed=false
                  </p>
                  <p>
                    {quote.quote.eligible
                      ? label(
                          "符合報價條件；尚未兑换",
                          "Eligible quote; not yet converted",
                        )
                      : quote.quote.reasonCode}
                  </p>
                  <button
                    disabled={
                      disabled("foundation.redemption.convert", true) ||
                      !procurementId ||
                      !quote.quote.eligible
                    }
                    onClick={() =>
                      void action(() =>
                        mutate(
                          `procurements/${procurementId}/mock-redemption`,
                          { confirm: true },
                        ),
                      )
                    }
                  >
                    {label(
                      "明確確認模擬兌回 HKD",
                      "Explicitly confirm simulated redemption",
                    )}
                  </button>
                </div>
              )}
              {workflowButton(
                "supplier.payment.execute",
                label(
                  "明確支付固定供應商（模擬 HKD）",
                  "Explicitly pay fixed supplier (simulated HKD)",
                ),
                { confirm: true },
              )}
              {workflowButton(
                "settlement.evidence.record",
                label(
                  "將獨立兌換及付款證據登記上鏈",
                  "Record separate conversion & payment evidence on chain",
                ),
              )}
              {exchanges
                .filter((item) => item.procurementId === procurementId)
                .map((item) => (
                  <div className="a2-fact" key={item.id}>
                    <strong>
                      {item.kind} · {item.status}
                    </strong>
                    <p>reconciled: {String(item.reconciled)}</p>
                    <code>{item.operationId}</code>
                    {item.evidenceId && (
                      <a
                        href={`/api/a2/payment-evidence/${item.evidenceId}/content`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {label(
                          "下載原始不可變結算證據",
                          "Download original immutable settlement evidence",
                        )}
                      </a>
                    )}
                  </div>
                ))}
            </>
          )}
          {(foundation || human) &&
            workflowButton(
              "settlement.execute",
              label(
                "明確執行已批准的最終結算確認",
                "Explicitly execute approved final settlement",
              ),
            )}
          <p className="muted">
            {label(
              "每個按鈕由當前 namespace 能力與採購 allowedActions 決定；未交付就禁用，不以 queued、UI 按鈕或模擬賬本表示成功。项目仍 Active 的餘額繼續冻结。",
              "Each button requires current-namespace capability and procurement allowedActions. Missing capabilities stay disabled. Queued operations and UI buttons are not success. Remaining funds stay locked while the project is Active.",
            )}
          </p>
        </Panel>
      )}
    </main>
  );
}
