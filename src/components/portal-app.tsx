"use client";
import { useI18n, LanguageSwitcher } from "@/components/language-provider";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Link from "next/link";
import Casework from "./casework";
import FundsFlow from "./funds-flow";
import { formatMoney, formatDate } from "@/lib/i18n";
import {
  ArrowUpRight,
  ArrowRight,
  LogOut,
  ChevronDown,
  ChevronRight,
  Search,
  Plus,
  X,
  Copy,
  Check,
  RefreshCw,
  ShieldCheck,
  LockKeyhole,
  Menu,
  HeartHandshake,
  Fingerprint,
  LayoutDashboard,
  Compass,
  Route,
  FolderKanban,
  ClipboardCheck,
  BadgeCheck,
  Store,
  BookOpen,
  Wallet,
  ShoppingBag,
  PackageCheck,
  Receipt,
  Building2,
  ScrollText,
  Cable,
  FileText,
  Upload,
  AlertCircle,
  CheckCircle2,
  Circle,
  type LucideIcon,
} from "lucide-react";
import {
  allowedPages,
  canAct,
  canRead,
  pages,
  portalOrder,
  roleNames,
  type AccessUser,
  type Action,
  type PageDef,
  type Portal,
} from "@/lib/access";
import type {
  AppData,
  Donation,
  Procurement,
  Project,
  RecordSnapshot,
} from "@/lib/types";
import {
  loadPortalA2Page,
  portalA2Action,
  portalA2Allowed,
  portalA2Logout,
  portalA2Upload,
  type PortalA2AppData,
  type PortalA2State,
} from "@/lib/portal-a2";
import {
  capabilityState,
  fullDemoAmount,
  quoteForCurrentScope,
  remainingFundingAtomic,
  currentFunding,
  type DemoQuote,
} from "@/lib/full-demo-ui";
import { isChainConfirmed } from "@/lib/a2-workbench";
const icons: Record<string, LucideIcon> = {
  LayoutDashboard,
  Compass,
  HeartHandshake,
  Route,
  FolderKanban,
  ClipboardCheck,
  BadgeCheck,
  Store,
  BookOpen,
  Wallet,
  ShoppingBag,
  PackageCheck,
  Receipt,
  Building2,
  ScrollText,
  Cable,
  Fingerprint,
  ShieldCheck,
};
const labels: Record<Procurement["status"], string> = {
  human_review: "待采购审批",
  reserved: "预算已预留",
  payment_review: "待付款审批",
  paid: "供应商已收款",
  funds_released: "基金会已收到模拟币",
  cancelled: "已取消",
  needs_info: "待补充资料",
  frozen: "已冻结",
};
function minor(v: FormDataEntryValue | null) {
  const s = String(v || "").trim();
  if (!/^\d+(\.\d{1,2})?$/.test(s))
    throw new Error("金额需为正数，最多两位小数");
  const [whole, decimal = ""] = s.split(".");
  const cents = BigInt(whole) * 100n + BigInt(decimal.padEnd(2, "0"));
  if (cents <= 0n || cents > BigInt(Number.MAX_SAFE_INTEGER))
    throw new Error("金额超出安全范围");
  return Number(cents);
}
function requestKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
function Metric({
  label,
  value,
  foot,
  icon: Icon,
}: {
  label: string;
  value: string;
  foot: string;
  icon?: LucideIcon;
}) {
  const { t } = useI18n();

  return (
    <section className="metric">
      <div className="metric-label">
        {t(label)}
        {t(Icon && <Icon size={16} />)}
      </div>
      <strong>{t(value)}</strong>
      <small>{t(foot)}</small>
    </section>
  );
}
function Panel({
  title,
  extra,
  children,
  className = "",
  translateTitle = true,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
  translateTitle?: boolean;
}) {
  const { t } = useI18n();

  return (
    <section className={"panel " + className}>
      <div className="panel-title">
        <h2>{translateTitle ? t(title) : title}</h2>
        {t(extra)}
      </div>
      {t(children)}
    </section>
  );
}
function Empty({
  text = "暂无记录",
  children,
}: {
  text?: string;
  children?: ReactNode;
}) {
  const { t } = useI18n();

  return (
    <div className="empty">
      <FileText size={28} />
      <p>{t(text)}</p>
      {t(children)}
    </div>
  );
}
function Status({ status }: { status: Procurement["status"] }) {
  const { t } = useI18n();

  return (
    <span
      className={
        "status " +
        (status === "frozen"
          ? "danger"
          : status === "needs_info"
            ? "warning"
            : status === "paid"
              ? "success"
              : "neutral")
      }
    >
      <span />
      {t(labels[status])}
    </span>
  );
}
function Timeline({
  claim,
  chainStatus,
}: {
  claim: Procurement;
  chainStatus?: string;
}) {
  const { t } = useI18n();

  const end = chainStatus
    ? ({
        created: 0,
        po_recorded: 0,
        pre_assessed: 0,
        reserve_approval_pending: 2,
        reserved: 3,
        invoice_recorded: 3,
        receipt_confirmed: 4,
        final_assessed: 4,
        release_approval_pending: 6,
        funds_released: 6,
        settlement_recorded: 6,
        settlement_approval_pending: 6,
        payment_confirmed: 7,
      }[chainStatus] ?? -1)
    : {
        human_review: 1,
        reserved: 3,
        payment_review: 5,
        paid: 7,
        funds_released: 7,
        cancelled: -1,
        needs_info: 1,
        frozen: 1,
      }[claim.status];
  return (
    <div className="timeline">
      {t(
        [
          "提交采购",
          "AI 采购前审核",
          "人工批准",
          "预算预留",
          "交付验收",
          "AI 最终审核",
          "人工批准付款",
          claim.status === "funds_released" ? "基金会收到模拟币" : "供应商收款",
        ].map((s, i) => (
          <div
            key={s}
            className={
              i <= end && (!chainStatus || (i !== 1 && i !== 5)) ? "done" : ""
            }
          >
            {t(
              i <= end && (!chainStatus || (i !== 1 && i !== 5)) ? (
                <CheckCircle2 size={16} />
              ) : (
                <Circle size={16} />
              ),
            )}
            <span>{t(s)}</span>
            {t(
              i === 1 || i === 5 ? (
                <small>{t(chainStatus ? "AI 待接入" : "演示结果")}</small>
              ) : null,
            )}
          </div>
        )),
      )}
    </div>
  );
}
type ModalState = {
  type: string;
  project?: Project;
  claim?: Procurement;
  donation?: Donation;
  user?: AccessUser;
};
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const { t } = useI18n();

  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      className="modal"
    >
      <div className="modal-title">
        <h2>{t(title)}</h2>
        <LanguageSwitcher />
        <button className="icon-button" onClick={close} aria-label={t("关闭")}>
          <X size={20} />
        </button>
      </div>
      <div className="modal-body">{t(children)}</div>
    </dialog>
  );
}
export function PortalA2Entry({ pageId }: { pageId: string }) {
  const { t } = useI18n();
  const [initial, setInitial] = useState<PortalA2AppData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void loadPortalA2Page(pageId)
      .then((value) => {
        if (active) setInitial(value);
      })
      .catch((problem) => {
        if (!active) return;
        if (problem?.status === 401) window.location.assign("/login");
        else
          setError(
            problem instanceof Error ? problem.message : "无法读取真实工作区",
          );
      });
    return () => {
      active = false;
    };
  }, [pageId]);
  if (!initial)
    return (
      <main>
        <p role={error ? "alert" : "status"}>
          {t(error || "正在读取本人工作区…")}
        </p>
        <Link href="/login">{t("返回登录")}</Link>
      </main>
    );
  return <PortalApp initial={initial} />;
}
export default function PortalApp({
  initial,
}: {
  initial: AppData & { integration?: PortalA2State };
}) {
  const { t, locale } = useI18n();
  const cash = (value = 0) =>
    Number.isFinite(value) ? formatMoney(value, locale) : "—";
  const date = (value: string) => (value ? formatDate(value, locale) : "—");

  const [app, setApp] = useState(initial),
    [modal, setModal] = useState<ModalState | null>(null),
    [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [sidebar, setSidebar] = useState(false),
    [search, setSearch] = useState("");
  const [hash, setHash] = useState(""),
    [matches, setMatches] = useState<RecordSnapshot[] | null>(null),
    [grants, setGrants] = useState<string[]>([]),
    [staffActive, setStaffActive] = useState(true);
  const [uploadKind, setUploadKind] = useState("invoice"),
    [uploaded, setUploaded] = useState<
      {
        id: string;
        name: string;
        hash: string;
        type: string;
      }[]
    >([]),
    [copyState, setCopyState] = useState("");
  const page = pages.find((p) => p.id === app.pageId)!;
  const accessible = allowedPages(app.user).filter(
      (p) => !app.integration || p.portal === app.user.role,
    ),
    portals = portalOrder.filter((p) => accessible.some((x) => x.portal === p)),
    nav = accessible.filter((p) => p.portal === page.portal);
  const [showLegacy, setShowLegacy] = useState(false);
  const source = app.data;
  const visibleProjects = source.projects?.filter((p) =>
    showLegacy ? !p.paymentTracked : p.paymentTracked,
  );
  const visibleIds = new Set(visibleProjects?.map((p) => p.id));
  const d = {
      ...source,
      projects: visibleProjects,
      donations: source.donations?.filter((x) => visibleIds.has(x.projectId)),
      procurements: source.procurements?.filter((x) =>
        visibleIds.has(x.projectId),
      ),
      reviews: source.reviews?.filter((x) => visibleIds.has(x.projectId)),
      appeals: source.appeals?.filter((x) => visibleIds.has(x.projectId)),
      ledger: source.ledger?.filter((x) => visibleIds.has(x.projectId)),
    },
    projects = (d.projects || [])
      .slice()
      .sort((a, b) => Number(!!b.paymentTracked) - Number(!!a.paymentTracked)),
    claims = d.procurements || [],
    donations = d.donations || [];
  const [tab, setTab] = useState("all");
  const liveProjects = projects.filter((p) => p.paymentTracked);
  const liveDonations = donations.filter((x) =>
    liveProjects.some((p) => p.id === x.projectId),
  );
  const integration = app.integration;
  const [donationAmount, setDonationAmount] = useState("100");
  const [donationQuote, setDonationQuote] = useState<DemoQuote | null>(null);
  const [selectedFunding, setSelectedFunding] = useState("");
  const [reserveDisplay, setReserveDisplay] = useState("");
  const [pendingA2, setPendingA2] = useState<{
    id: string;
    body: Record<string, unknown>;
    key: string;
    scope: string;
  } | null>(null);
  const a2Scope = integration
    ? `${integration.currentUser.id}:${integration.currentUser.role}:${integration.currentUser.walletAddress.toLowerCase()}:${integration.context.binding.namespaceId}:${integration.context.binding.runId}:${integration.context.binding.instanceId}`
    : "";
  useEffect(() => {
    modalRevision.current += 1;
    setDonationQuote(null);
    setSelectedFunding("");
  }, [a2Scope]);
  const modalRevision = useRef(0);
  useEffect(() => {
    setApp(initial);
    setModal(null);
    setSearch("");
    setTab("all");
    setMatches(null);
  }, [initial]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(t);
  }, [toast]);
  const refresh = useCallback(async () => {
    if (initial.integration) {
      const value = await loadPortalA2Page(initial.pageId);
      setApp(value);
      return;
    }
    const r = await fetch(
      "/api/page?id=" + encodeURIComponent(initial.pageId),
      { cache: "no-store" },
    );
    if (r.status === 401) {
      window.location.assign("/login");
      return;
    }
    if (r.status === 403) {
      window.location.assign("/");
      return;
    }
    const v = await r.json();
    if (!r.ok) throw new Error(v.error);
    setApp(v);
  }, [initial.pageId, initial.integration]);
  useEffect(() => {
    const timer = setInterval(
      () => {
        if (document.visibilityState === "visible") refresh().catch(() => {});
      },
      initial.integration ? 5000 : 15000,
    );
    return () => clearInterval(timer);
  }, [refresh]);
  function open(m: ModalState) {
    if (pendingA2) {
      setError(
        "上一次请求结果未知；先用同一请求重试或刷新核对，不发起新交易。",
      );
      return;
    }
    modalRevision.current += 1;
    setDonationAmount("100");
    setDonationQuote(null);
    setSelectedFunding("");
    setReserveDisplay("");
    setError("");
    setUploaded([]);
    setUploadKind(m.type === "supplement" ? "quotation" : "invoice");
    setModal(m);
    if (m.user) {
      setGrants(m.user.grants);
      setStaffActive(m.user.active);
    }
  }
  async function perform(action: Action, body: Record<string, unknown>) {
    if (integration) {
      if (action === "createProject") {
        await runA2("project.draft.create", {
          title: body.name,
          publicSummary: body.description,
          recipientUserId: integration.context?.defaults?.recipientUserId,
          humanApproverUserId:
            integration.context?.defaults?.humanApproverUserId,
        });
      } else if (action === "foundationProcurement") {
        if (
          !Number.isSafeInteger(body.quantity) ||
          !Number.isSafeInteger(body.unitPrice)
        ) {
          setError("预算输入超出安全范围");
          return;
        }
        const budget =
          BigInt(body.quantity as number) *
          BigInt(body.unitPrice as number) *
          10000n;
        await runA2("procurement.draft.create", {
          projectId: body.projectId,
          title: body.name,
          vendorWallet: integration.context?.defaults?.supplierWallet,
          budgetCapAtomic: budget.toString(),
        });
      } else if (action === "approvePurchase")
        await runA2("procurement.reserve.execute", body);
      else if (action === "approvePayment")
        await runA2("release.execute", body);
      else setError("此功能尚未接入真实接口，未执行任何旧演示账本写入。");
      return;
    }
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/actions/" + action, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": requestKey(),
        },
        body: JSON.stringify(body),
      });
      const v = await r.json();
      if (r.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (!r.ok) throw new Error(v.error);
      setModal(null);
      setToast(v.message);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  async function runA2(
    id: string,
    body: Record<string, unknown>,
    retry = false,
  ) {
    if (!integration || busy || (pendingA2 && !retry)) return;
    const candidate =
      retry && pendingA2
        ? pendingA2
        : { id, body, key: requestKey(), scope: a2Scope };
    if (candidate.scope !== a2Scope) {
      setError(
        "身份或部署已改变；不能把旧请求转发到新实例，请重新读取原 operation。",
      );
      return;
    }
    const revision = modalRevision.current;
    setBusy(true);
    setError("");
    try {
      const result = await portalA2Action(
        candidate.id,
        candidate.body,
        candidate.key,
        { expectedState: integration },
      );
      setPendingA2(null);
      setToast("接口已受理；等待 operation 和链上确认，不代表资金已到账。");
      const fresh = await loadPortalA2Page(initial.pageId);
      setApp(fresh);
      const createdProject = fresh.data.projects?.find(
        (item) => item.id === result.project?.id,
      );
      const createdProcurement = fresh.data.procurements?.find(
        (item) => item.id === result.procurement?.id,
      );
      if (revision === modalRevision.current && createdProject)
        setModal({ type: "project", project: createdProject });
      if (revision === modalRevision.current && createdProcurement)
        setModal({ type: "claim", claim: createdProcurement });
    } catch (problem) {
      if (
        (problem as { status?: number })?.status === 0 ||
        (problem as { status?: number })?.status === 504
      )
        setPendingA2(candidate);
      setError(problem instanceof Error ? problem.message : "接口请求失败");
    } finally {
      setBusy(false);
    }
  }
  const allowedA2 = (id: string, procurementId?: string, projectId?: string) =>
    Boolean(
      integration &&
      portalA2Allowed(integration, id, { procurementId, projectId }).enabled &&
      !busy &&
      !pendingA2,
    );
  async function quoteDonation(projectId: string) {
    if (!integration || !allowedA2("mock.exchange.quote", undefined, projectId))
      return;
    const revision = modalRevision.current;
    setBusy(true);
    setError("");
    setDonationQuote(null);
    try {
      const amount = fullDemoAmount(donationAmount);
      const result = await portalA2Action(
        "mock.exchange.quote",
        { direction: "hkd_to_mock", projectId, hkdCents: amount.hkdCents },
        requestKey(),
        { expectedState: integration },
      );
      if (
        !quoteForCurrentScope(result, integration.context) ||
        result.quote.direction !== "hkd_to_mock" ||
        result.quote.hkdCents !== amount.hkdCents ||
        result.quote.amountAtomic !== amount.amountAtomic
      )
        throw new Error("报价金额或部署绑定不一致");
      if (revision === modalRevision.current) setDonationQuote(result);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "报价失败");
    } finally {
      setBusy(false);
    }
  }
  async function onSubmit(
    e: React.FormEvent<HTMLFormElement>,
    action: Action,
    extra: Record<string, unknown> = {},
  ) {
    e.preventDefault();
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      const body: Record<string, unknown> = {
        ...Object.fromEntries(f.entries()),
        ...extra,
      };
      for (const k of ["amount", "target", "unitPrice"])
        if (f.has(k)) body[k] = minor(f.get(k));
      if (f.has("quantity")) body.quantity = Number(f.get("quantity"));
      await perform(action, body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "输入有误");
    }
  }
  async function copy(s: string) {
    try {
      await navigator.clipboard.writeText(s);
      setCopyState(s);
      setTimeout(() => setCopyState(""), 2000);
    } catch {
      setToast("复制不可用，请选中完整 Hash 手动复制。");
    }
  }
  async function queryHash(e: React.FormEvent) {
    e.preventDefault();
    if (integration) {
      setError("Hash 查询适配待接入；请在采购证明页面读取真实不可变文件。");
      return;
    }
    setBusy(true);
    setError("");
    setMatches(null);
    try {
      const r = await fetch(
        "/api/records/by-hash?page=" +
          app.pageId +
          "&hash=" +
          encodeURIComponent(hash),
      );
      const v = await r.json();
      if (!r.ok) throw new Error(v.error);
      setMatches(v.records);
    } catch (e) {
      setError(e instanceof Error ? e.message : "查询失败");
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File | undefined) {
    if (!file || !modal?.claim) return;
    setError("");
    if (file.size > 1048576) {
      setError("本地演示每个文件最多 1 MB");
      return;
    }
    setBusy(true);
    try {
      if (integration) {
        const result = await portalA2Upload(modal.claim.id, uploadKind, file, {
          expectedState: integration,
        });
        setUploaded((items) => [
          ...items,
          {
            id: result.versionId,
            name: result.originalFilename,
            hash: result.keccak256,
            type: result.category,
          },
        ]);
        setToast("不可变文件版本已保存；尚未登记到链上。");
        await refresh();
        return;
      }
      const content = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(",")[1]);
        r.onerror = () => reject(new Error("文件读取失败"));
        r.readAsDataURL(file);
      });
      const r = await fetch("/api/evidence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: file.name,
          content,
          procurementId: modal.claim.id,
          type: uploadKind,
        }),
      });
      const result = await r.json();
      if (!r.ok) throw new Error(result.error);
      setUploaded((x) => [...x, result]);
      setToast("私有文件已保存，SHA-256 已生成");
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }
  async function signout() {
    if (integration) {
      try {
        await portalA2Logout();
        window.location.assign("/login");
      } catch (problem) {
        setError(problem instanceof Error ? problem.message : "登出失败");
      }
      return;
    }
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    window.location.assign("/login");
  }
  const write = (a: Action) =>
    integration
      ? Boolean(
          {
            createProject:
              app.user.role === "foundation" &&
              allowedA2("project.draft.create"),
            foundationProcurement:
              app.user.role === "foundation" &&
              capabilityState(integration.context, "procurement.draft.create")
                .enabled &&
              !busy &&
              !pendingA2,
            donate:
              app.user.role === "donor" &&
              capabilityState(integration.context, "donation.funded.deposit")
                .enabled &&
              !busy &&
              !pendingA2,
            approvePurchase: app.user.role === "foundation",
            approvePayment: app.user.role === "foundation",
          }[a as "createProject"],
        )
      : canAct(app.user, a);
  const sum = (
    key: "deposited" | "available" | "reserved" | "paid" | "released",
  ) =>
    liveProjects.reduce((s, p) => s + (p[key] ?? (integration ? NaN : 0)), 0);
  const filteredClaims = claims.filter(
    (c) =>
      (tab === "all" || c.status === tab) &&
      (c.id + " " + c.name).toLowerCase().includes(search.toLowerCase()),
  );
  const readyHumanCount =
    integration?.rawProcurements.filter(
      (proc) =>
        proc.chainState.verified &&
        ["pre_assessed", "final_assessed", "settlement_recorded"].includes(
          proc.chainState.status,
        ) &&
        portalA2Allowed(integration, "authorization.prepare", {
          procurementId: proc.id,
          projectId: proc.projectId,
          kind:
            proc.chainState.status === "pre_assessed"
              ? "reserve"
              : proc.chainState.status === "final_assessed"
                ? "release"
                : "settlement",
        }).enabled,
    ).length ?? 0;
  const waitingAiCount =
    integration?.rawProcurements.filter(
      (proc) =>
        proc.chainState.verified &&
        ["po_recorded", "receipt_confirmed"].includes(proc.chainState.status),
    ).length ?? 0;
  function claimRow(c: Procurement, button = "查看详情", type = "claim") {
    return (
      <div className="record-row" key={c.id}>
        <div className="record-icon">
          <ShoppingBag size={19} />
        </div>
        <div className="record-main">
          <strong>{c.name}</strong>
          <small>
            {t(c.id)} · {t(date(c.createdAt))}
          </small>
        </div>
        <div className="record-amount">
          {t(cash(c.amount))}
          <small>mHKD</small>
        </div>
        <Status status={c.status} />
        <button
          className="text-button"
          onClick={() => open({ type, claim: c })}
        >
          {t(button)}
          <ChevronRight size={15} />
        </button>
      </div>
    );
  }
  function projectCard(p: Project) {
    return (
      <article className="project-card" key={p.id}>
        <div className="project-art">
          <div className="art-orbit" />
          <BookOpen size={48} />
          <span className="pill">{t("教育 · 社区支持")}</span>
        </div>
        <div className="project-content">
          <div className="eyebrow">CHENGUANG FOUNDATION</div>
          <h2>{p.name}</h2>
          <p>{p.description}</p>
          <div className="project-meta">
            <span>{t("晨光基金会")}</span>
            <span className="verified">
              <BadgeCheck size={14} />
              {t("演示认证")}
            </span>
          </div>
          <div className="project-rule">
            {t(
              p.paymentTracked
                ? "模拟币资金链 · 验收后拨给基金会"
                : "历史演示示例 · 独立于当前资金链",
            )}
          </div>
          <div className="button-row">
            {t(
              page.portal === "donor" ? (
                <>
                  <button
                    className="primary"
                    disabled={
                      !write("donate") ||
                      Boolean(
                        integration &&
                        !allowedA2("donation.funded.deposit", undefined, p.id),
                      )
                    }
                    onClick={() =>
                      window.location.assign(
                        "/donor/funds?project=" + encodeURIComponent(p.id),
                      )
                    }
                  >
                    {t("支持这个项目")}
                    <ArrowUpRight size={15} />
                  </button>
                  <button onClick={() => open({ type: "project", project: p })}>
                    {t("查看详情")}
                  </button>
                </>
              ) : (
                <>
                  <button onClick={() => open({ type: "project", project: p })}>
                    {t("查看预算")}
                  </button>
                  {t(
                    page.portal === "foundation" && (
                      <button
                        className="primary"
                        disabled={!write("deposit") || !!p.paymentTracked}
                        onClick={() => open({ type: "deposit", project: p })}
                      >
                        {t("模拟注资")}
                      </button>
                    ),
                  )}
                </>
              ),
            )}
          </div>
        </div>
      </article>
    );
  }
  function donationRow(x: Donation) {
    const project = projects.find((p) => p.id === x.projectId);
    return (
      <div className="record-row" key={x.id}>
        <div className="record-icon">
          <HeartHandshake size={18} />
        </div>
        <div className="record-main">
          <strong>{project?.name || x.projectId}</strong>
          <small>
            {t(x.id)} · {t(date(x.createdAt))}
          </small>
        </div>
        <div className="record-amount">
          HK$ {t(cash(x.amount))}
          <small>
            {t(
              app.integration
                ? "本人链上累计捐款；非逐笔凭证"
                : "模拟支付已确认",
            )}
          </small>
        </div>
        <button
          className="text-button"
          onClick={() => open({ type: "donation", donation: x })}
        >
          {t("查看凭证")}
          <ChevronRight size={15} />
        </button>
      </div>
    );
  }
  function primaryAction() {
    if (page.id === "foundation.evidence")
      return (
        <button
          className="primary"
          disabled={!write("foundationProcurement")}
          onClick={() => open({ type: "procurement-new" })}
        >
          {t("新建采购")}
        </button>
      );
    if (page.id === "foundation.projects")
      return (
        <button
          className="primary"
          disabled={!write("createProject")}
          onClick={() => open({ type: "project-new" })}
        >
          <Plus size={16} />
          {t("创建项目")}
        </button>
      );
    if (page.id === "recipient.procurements")
      return (
        <button
          className="primary"
          disabled={!write("createProcurement") || !projects.length}
          onClick={() => open({ type: "procurement-new" })}
        >
          <Plus size={16} />
          {t("新建采购")}
        </button>
      );
    if (page.id === "recipient.reimbursements")
      return (
        <button
          className="primary"
          disabled={!write("reimbursement")}
          onClick={() => open({ type: "reimbursement" })}
        >
          <Plus size={16} />
          {t("新增报销草稿")}
        </button>
      );
    if (page.id === "admin.users")
      return (
        <button
          className="primary"
          disabled={!write("createMaintainer")}
          onClick={() => open({ type: "staff-new" })}
        >
          <Plus size={16} />
          {t("添加维护人员")}
        </button>
      );
    return (
      <button
        onClick={() =>
          refresh()
            .then(() => setToast("数据已刷新"))
            .catch((e) => setToast(e.message))
        }
      >
        <RefreshCw size={15} />
        {t("刷新")}
      </button>
    );
  }
  function content(): ReactNode {
    if (page.slug === "funds")
      return app.user.role === "maintainer" ? (
        <p className="notice">{t("维护权限不包含个人资金账户与流水")}</p>
      ) : null;
    if (page.id === "foundation.evidence" || page.id === "recipient.delivery")
      return (
        <Casework
          key={page.id}
          mode="proof"
          foundationView={page.portal === "foundation"}
          data={d}
          user={app.user}
          refresh={refresh}
          integration={integration}
        />
      );
    if (page.id === "admin.audit")
      return (
        <Casework
          mode="audit"
          data={d}
          user={app.user}
          refresh={refresh}
          integration={integration}
        />
      );
    if (page.slug === "appeals")
      return (
        <Casework
          key={page.id}
          mode="appeals"
          data={d}
          user={app.user}
          refresh={refresh}
          integration={integration}
        />
      );
    if (page.slug === "hash")
      return (
        <>
          <Panel
            title={t("凭证查询")}
            extra={
              <span className="pill">
                <LockKeyhole size={12} />
                {t("按权限返回")}
              </span>
            }
          >
            <form className="hash-search" onSubmit={queryHash}>
              <label>
                {t("单据 / 文件 SHA-256")}
                <input
                  value={hash}
                  onChange={(e) => setHash(e.target.value)}
                  placeholder={t("0x… 或 64 位十六进制 Hash")}
                  required
                />
              </label>
              <button className="primary" disabled={busy}>
                <Search size={17} />
                {t("查询")}
              </button>
            </form>
            <p className="tiny muted">
              {t(
                "可从捐款凭证、采购详情或上传结果复制 Hash。交易 Hash、Merkle Root 查询等待区块链接入。",
              )}
            </p>
          </Panel>
          {t(
            matches !== null && (
              <Panel title={t("查询结果")}>
                {t(
                  matches.length ? (
                    matches.map((r) => (
                      <article className="hash-result" key={r.id}>
                        <div className="section-line">
                          <div>
                            <h3>{r.title}</h3>
                            <p className="muted">
                              {t(r.kind)} · {t(r.id)} · v{t(r.version)}
                            </p>
                          </div>
                          <span className="status neutral">
                            {t("本地记录")}
                          </span>
                        </div>
                        <code className="hash-value">{t(r.hash)}</code>
                        <p>
                          {t(r.amount ? cash(r.amount) + " " + r.unit : "")}
                        </p>
                        <div className="button-row">
                          <button onClick={() => copy(r.hash)}>
                            <Copy size={14} />
                            {t("复制 Hash")}
                          </button>
                          {t(
                            r.kind === "file" ? (
                              <a
                                className="button"
                                href={"/api/evidence/" + r.id}
                              >
                                {t("下载授权附件")}
                              </a>
                            ) : (
                              <a
                                className="button"
                                href={"/api/records/" + r.id + "/export"}
                              >
                                {t("下载凭证")}
                              </a>
                            ),
                          )}
                        </div>
                        <details>
                          <summary>{t("原始提交快照")}</summary>
                          <pre>{JSON.stringify(r.snapshot, null, 2)}</pre>
                        </details>
                      </article>
                    ))
                  ) : (
                    <Empty text={t("未找到可访问记录")} />
                  ),
                )}
              </Panel>
            ),
          )}
        </>
      );
    if (page.id === "donor.overview")
      return (
        <>
          <div className="metrics">
            {t([
              <Metric
                key="a"
                label={t("累计捐款 · 模拟 HKD")}
                value={
                  "HK$ " + cash(liveDonations.reduce((s, x) => s + x.amount, 0))
                }
                foot={t("已确认的个人捐款")}
                icon={HeartHandshake}
              />,
              <Metric
                key="b"
                label={t("历史分配（资金链项目）")}
                value={
                  "HK$ " +
                  cash(liveDonations.reduce((s, x) => s + x.allocated, 0))
                }
                foot={t("资金当前位置见上方透明资金链")}
                icon={Route}
              />,
              <Metric
                key="c"
                label={t("尚未分配")}
                value={
                  "HK$ " +
                  cash(
                    liveDonations.reduce(
                      (s, x) => s + x.amount - x.allocated,
                      0,
                    ),
                  )
                }
                foot={t("不等同于可提现余额")}
                icon={Wallet}
              />,
            ])}
          </div>
          <div className="content-grid">
            <Panel
              title={t("你正在支持")}
              extra={
                canRead(app.user, "donor.projects") && (
                  <Link href="/donor/projects" className="text-link">
                    {t("探索项目")}
                    <ArrowUpRight size={14} />
                  </Link>
                )
              }
            >
              {t(projects[0] ? projectCard(projects[0]) : <Empty />)}
            </Panel>
            <Panel title={t("善意的下一站")} className="impact-panel">
              <span className="large-icon">
                <PackageCheck size={30} />
              </span>
              <h3>
                {t("从一笔捐款，")}
                <br />
                {t("到一份看得见的交付。")}
              </h3>
              <p>{t("供应商收款后，才能确认你的捐款支持了哪些采购。")}</p>
              {t(
                canRead(app.user, "donor.allocations") && (
                  <Link href="/donor/allocations" className="button">
                    {t("查看资金去向")}
                    <ArrowRight size={15} />
                  </Link>
                ),
              )}
              <div className="mini-path">
                <span>{t("捐款")}</span>
                <span>{t("受控采购")}</span>
                <span>{t("交付与证明")}</span>
              </div>
            </Panel>
          </div>
          <Panel title={t("最近捐款")}>
            {t(
              donations.length ? (
                donations.slice(-4).reverse().map(donationRow)
              ) : (
                <Empty text={t("你还没有捐款记录")}>
                  <Link href="/donor/projects">{t("去探索项目")}</Link>
                </Empty>
              ),
            )}
          </Panel>
        </>
      );
    if (page.id === "donor.projects")
      return <div className="project-grid">{t(projects.map(projectCard))}</div>;
    if (page.id === "donor.donations")
      return (
        <Panel title={t("我的捐款 · " + donations.length + " 笔")}>
          {t(
            donations.length ? (
              donations.slice().reverse().map(donationRow)
            ) : (
              <Empty text={t("暂无捐款，开始支持一个项目吧")} />
            ),
          )}
        </Panel>
      );
    if (page.id === "donor.allocations")
      return (
        <>
          {t(
            donations.map((x) => (
              <Panel
                key={x.id}
                title={t(x.id)}
                extra={
                  <span className="pill">
                    {t("捐款 HK$")}
                    {t(cash(x.amount))}
                  </span>
                }
              >
                <div className="allocation">
                  <Metric
                    label={t("已分配金额")}
                    value={"HK$ " + cash(x.allocated)}
                    foot={t("初始演示批次 AL-001")}
                  />
                  <Metric
                    label={t("尚未分配")}
                    value={"HK$ " + cash(x.amount - x.allocated)}
                    foot={t("新支出等待真实 Allocation 接入")}
                  />
                </div>
                {t(
                  x.allocated > 0 ? (
                    <div className="record-row">
                      <div className="record-icon">
                        <BookOpen size={20} />
                      </div>
                      <div className="record-main">
                        <strong>{t("PR-001 · 阅读练习册")}</strong>
                        <small>{t("供应商已收款 · 示例支出 200 mHKD")}</small>
                      </div>
                      <b>HK$ {t(cash(x.allocated))}</b>
                      <span className="status neutral">
                        {t("分配明细示例")}
                      </span>
                    </div>
                  ) : (
                    <p className="muted">{t("该捐款尚无最终支出分配。")}</p>
                  ),
                )}
                <div className="notice compact">
                  {t("Merkle 验证尚未接入；这里没有生成虚假的链上验证结果。")}
                </div>
              </Panel>
            )),
          )}
          {t(!donations.length && <Empty text={t("暂无可查询的个人捐款")} />)}
        </>
      );
    if (page.id === "foundation.overview")
      return (
        <>
          <div className="metrics">
            <Metric
              label={t("可用预算 · mHKD")}
              value={cash(sum("available"))}
              foot={t("累计注资 " + cash(sum("deposited")))}
              icon={Wallet}
            />
            <Metric
              label={t("预算预留 · mHKD")}
              value={cash(sum("reserved"))}
              foot={t("待交付与最终付款")}
              icon={LockKeyhole}
            />
            <Metric
              label={t("已拨付基金会 · mHKD")}
              value={cash(sum("released"))}
              foot={t(
                integration
                  ? "真实本地链释放；不代表供应商已收款"
                  : "本地演示账本",
              )}
              icon={BadgeCheck}
            />
          </div>
          <Panel
            title={t("现在需要你处理")}
            extra={<span className="pill">{t("两阶段独立审批")}</span>}
          >
            {t(
              claims
                .filter((c) =>
                  [
                    "human_review",
                    "payment_review",
                    "needs_info",
                    "frozen",
                  ].includes(c.status),
                )
                .map((c) =>
                  claimRow(
                    c,
                    "处理审批",
                    c.status === "payment_review" ? "payment" : "review",
                  ),
                ),
            )}
          </Panel>
          <Panel title={t("资金状态")}>
            <div className="balance-strip">
              <div>
                <span>Available</span>
                <b>{t(cash(sum("available")))}</b>
              </div>
              <Plus size={16} />
              <div>
                <span>Reserved</span>
                <b>{t(cash(sum("reserved")))}</b>
              </div>
              <Plus size={16} />
              <div>
                <span>Released</span>
                <b>{t(cash(sum("released")))}</b>
              </div>
              <span>=</span>
              <div>
                <span>{t("当前净存入")}</span>
                <b>
                  {t(
                    cash(sum("available") + sum("reserved") + sum("released")),
                  )}
                </b>
              </div>
            </div>
          </Panel>
        </>
      );
    if (page.id === "foundation.projects" || page.id === "recipient.budget")
      return (
        <div className="project-grid">
          {t(
            projects.map((p) => (
              <Panel
                key={p.id}
                title={p.name}
                translateTitle={false}
                extra={<span className="pill">{t("规则 v1")}</span>}
              >
                <p>{p.description}</p>
                <div className="budget-mini">
                  <div>
                    <small>{t("可用")}</small>
                    <b>{t(cash(p.available))} mHKD</b>
                  </div>
                  <div>
                    <small>{t("预留")}</small>
                    <b>{t(cash(p.reserved))} mHKD</b>
                  </div>
                  <div>
                    <small>
                      {t(p.paymentTracked ? "已拨基金会" : "历史已付")}
                    </small>
                    <b>
                      {t(cash(p.paymentTracked ? (p.released ?? NaN) : p.paid))}{" "}
                      mHKD
                    </b>
                  </div>
                </div>
                <div className="category-tags">
                  {t(p.categories.map((c) => <span key={c}>{t(c)}</span>))}
                </div>
                <div className="button-row">
                  <button onClick={() => open({ type: "project", project: p })}>
                    {t("查看项目与规则")}
                  </button>
                  {t(
                    page.portal === "foundation" ? (
                      <button
                        className="primary"
                        disabled={!write("deposit") || !!p.paymentTracked}
                        onClick={() => open({ type: "deposit", project: p })}
                      >
                        {t(
                          p.paymentTracked
                            ? "由 Donor 模拟币捐款注资"
                            : "历史模拟注资",
                        )}
                      </button>
                    ) : (
                      write("createProcurement") && (
                        <button
                          className="primary"
                          onClick={() =>
                            open({ type: "procurement-new", project: p })
                          }
                        >
                          {t("申请采购")}
                        </button>
                      )
                    ),
                  )}
                </div>
              </Panel>
            )),
          )}
        </div>
      );
    if (
      [
        "foundation.review",
        "foundation.payment",
        "recipient.procurements",
        "recipient.delivery",
      ].includes(page.id)
    ) {
      const type =
        page.id === "foundation.review"
          ? "review"
          : page.id === "foundation.payment"
            ? "payment"
            : page.id === "recipient.delivery"
              ? "delivery"
              : "claim";
      return (
        <Panel
          title={t(page.label)}
          extra={
            <span className="muted tiny">
              {t(claims.length)}
              {t("条采购")}
            </span>
          }
        >
          <div className="list-toolbar">
            <div className="search-input">
              <Search size={16} />
              <input
                aria-label={t("搜索采购")}
                placeholder={t("搜索采购编号或名称")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              aria-label={t("筛选采购状态")}
              value={tab}
              onChange={(e) => setTab(e.target.value)}
            >
              <option value="all">{t("全部状态")}</option>
              {t(
                Object.entries(labels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {t(label)}
                  </option>
                )),
              )}
            </select>
          </div>
          {t(
            filteredClaims.length ? (
              filteredClaims.map((c) => claimRow(c, "查看与处理", type))
            ) : (
              <Empty text={t("没有匹配的采购")} />
            ),
          )}
        </Panel>
      );
    }
    if (page.id === "recipient.overview")
      return (
        <>
          <div className="metrics">
            <Metric
              label={t("项目可用预算")}
              value={cash(sum("available")) + " mHKD"}
              foot={t("以提交时服务器校验为准")}
              icon={Wallet}
            />
            <Metric
              label={t("待交付验收")}
              value={
                claims.filter((c) => c.status === "reserved").length + " 笔"
              }
              foot={t("请准备 GRN、发票和照片")}
              icon={PackageCheck}
            />
            <Metric
              label={t("需要补充资料")}
              value={
                claims.filter((c) => c.status === "needs_info").length + " 笔"
              }
              foot={t("根据审核意见补充证据")}
              icon={FileText}
            />
          </div>
          <Panel title={t("继续处理采购")}>
            {t(
              claims
                .filter((c) => c.status !== "paid")
                .map((c) =>
                  claimRow(
                    c,
                    c.status === "reserved" ? "提交验收" : "查看进度",
                    c.status === "reserved" ? "delivery" : "claim",
                  ),
                ),
            )}
          </Panel>
          <div className="notice">
            <ShieldCheck size={19} />
            <span>
              {t(
                "受捐机构负责申请与验收；审计与最终审批后模拟币拨给基金会，受捐机构不持有这笔资金。",
              )}
            </span>
          </div>
        </>
      );
    if (page.id === "recipient.reimbursements")
      return (
        <>
          <div className="notice">
            <AlertCircle size={18} />
            <span>
              {t(
                "小额报销与供应商直付分开处理。本版可保存草稿，尚未接入报销审核与支付。",
              )}
            </span>
          </div>
          <Panel title={t("报销草稿")}>
            {t(
              d.reimbursements?.length ? (
                d.reimbursements.map((r) => (
                  <div className="record-row" key={r.id}>
                    <Receipt size={19} />
                    <div className="record-main">
                      <b>{r.name}</b>
                      <small>{t(r.id)}</small>
                    </div>
                    <b>HK$ {t(cash(r.amount))}</b>
                    <span className="pill">{t(r.status)}</span>
                  </div>
                ))
              ) : (
                <Empty text={t("暂无报销草稿")} />
              ),
            )}
          </Panel>
        </>
      );
    if (page.id === "foundation.vendors" || page.id === "admin.organisations")
      return (
        <>
          <Panel title={t("认证供应商")}>
            {t(
              d.vendors?.map((v) => (
                <div key={v.id} className="vendor-row">
                  <div className="record-icon">
                    <Store size={23} />
                  </div>
                  <div>
                    <h3>{t(v.name)}</h3>
                    <p>
                      {t(v.id)} ·{" "}
                      <span className="verified">{t("演示认证已通过")}</span>
                    </p>
                    <code className="hash-value">{t(v.wallet)}</code>
                  </div>
                </div>
              )),
            )}
          </Panel>
          {t(
            page.portal === "admin" && (
              <Panel title={t("业务机构")}>
                <div className="record-row">
                  <Building2 />
                  <div className="record-main">
                    <b>{t("晨光基金会")}</b>
                    <small>Foundation · org-foundation</small>
                  </div>
                  <span className="pill">{t("演示认证")}</span>
                </div>
                <div className="record-row">
                  <Building2 />
                  <div className="record-main">
                    <b>{t("同心社区学习中心")}</b>
                    <small>Recipient · org-recipient</small>
                  </div>
                  <span className="pill">{t("演示认证")}</span>
                </div>
              </Panel>
            ),
          )}
          <p className="muted tiny">
            {t(
              "认证、钱包变更和链上 Registry 写入接口待接入；当前资料为演示种子数据。",
            )}
          </p>
        </>
      );
    if (page.id === "foundation.ledger")
      return (
        <Panel title={t("项目账本")}>
          {t(
            d.ledger?.map((l) => (
              <div className="record-row" key={l.id}>
                <BookOpen size={18} />
                <div className="record-main">
                  <b>{t(l.kind)}</b>
                  <small>
                    {t(l.reference)} · {t(date(l.at))}
                  </small>
                </div>
                <b>{t(cash(l.amount))} mHKD</b>
                <span className="status neutral">{t("本地模拟")}</span>
              </div>
            )),
          )}
        </Panel>
      );
    if (page.id === "admin.users")
      return (
        <>
          <div className="notice">
            <ShieldCheck size={19} />
            <span>
              {t(
                integration
                  ? "本轮仅显示当前已登录的受控身份；用户与权限管理写入接口未接入，不能跨角色代操作。"
                  : "固定角色控制工作区范围。维护人员默认只读，按页面独立授权；权限变更在下次请求时生效。",
              )}
            </span>
          </div>
          <Panel title={t("账户与页面权限")}>
            {t(
              d.users?.map((u) => (
                <div className="user-row" key={u.id}>
                  <div className="avatar">{u.name.slice(0, 1)}</div>
                  <div className="record-main">
                    <strong>{u.name}</strong>
                    <small>
                      {t(u.id)} · {t(roleNames[u.role])} ·{t(" ")}
                      {t(u.active ? "启用" : "已停用")}
                    </small>
                    <div className="access-tags">
                      {t(
                        u.role === "maintainer" ? (
                          <span>
                            {t(u.grants.length)}
                            {t("个已分配页面")}
                          </span>
                        ) : (
                          (integration
                            ? [u.role]
                            : portalOrder.filter((p) =>
                                allowedPages(u).some((x) => x.portal === p),
                              )
                          ).map((p) => <span key={p}>{t(roleNames[p])}</span>)
                        ),
                      )}
                    </div>
                  </div>
                  {t(
                    u.role === "maintainer" ? (
                      <button
                        disabled={!write("updatePermissions")}
                        onClick={() => open({ type: "permissions", user: u })}
                      >
                        {t("分配页面")}
                      </button>
                    ) : (
                      <span className="tiny muted">{t("固定角色规则")}</span>
                    ),
                  )}
                </div>
              )),
            )}
          </Panel>
        </>
      );
    if (page.id === "admin.connections")
      return (
        <>
          <Panel title={t("模块接入状态")}>
            <div className="connection-grid">
              {t(
                [
                  ["前端门户", "已运行", "Next.js · 当前应用"],
                  [
                    "会话与权限 API",
                    "已实现",
                    "HttpOnly Cookie + 服务端权限校验",
                  ],
                  [
                    "演示持久化",
                    "已实现",
                    integration
                      ? "仅 API 持久化事实，不使用本机演示 JSON"
                      : "本机 .data/pog-demo.json",
                  ],
                  [
                    "PostgreSQL / FastAPI",
                    integration ? "已接入" : "待接入",
                    integration
                      ? "本地受控 FastAPI / 数据库模拟实例"
                      : "生产数据层与正式业务接口",
                  ],
                  [
                    "Anvil / 三个合约",
                    integration?.deployment.verified === true
                      ? "已接入"
                      : "待接入",
                    integration
                      ? "链上事实来自受控实例 canonical receipts"
                      : "没有生成虚假 txHash",
                  ],
                  [
                    "AI / Relayer / Indexer",
                    "待接入",
                    integration
                      ? "链上 AI 评估待接；诊断报告在采购证明与人工审计页面独立查询"
                      : "当前使用固定演示审核结果",
                  ],
                ].map(([title, status, note]) => (
                  <div className="connection" key={title}>
                    <span
                      className={
                        "connection-dot " +
                        (status === "待接入" ? "pending" : "")
                      }
                    />
                    <div>
                      <h3>{t(title)}</h3>
                      <small>{t(note)}</small>
                    </div>
                    <span className="pill">{t(status)}</span>
                  </div>
                )),
              )}
            </div>
          </Panel>
          <Panel title={t("开发连接约定")}>
            <div className="budget-mini">
              <div>
                <small>Web</small>
                <b>3000</b>
              </div>
              <div>
                <small>{t("FastAPI（规划）")}</small>
                <b>8000</b>
              </div>
              <div>
                <small>{t("Anvil（规划）")}</small>
                <b>8545</b>
              </div>
            </div>
            <p className="muted">
              {t(
                "三台电脑访问同一中央主机，不能各用自己的 localhost。真实 ABI、合约地址、签名参数尚待区块链组提供。",
              )}
            </p>
          </Panel>
        </>
      );
    if (page.id === "admin.overview")
      return (
        <>
          <div className="metrics">
            <Metric
              label={t("角色与授权")}
              value={integration ? "4 类受控身份" : "5 类身份"}
              foot={t(
                integration
                  ? "各自工作区 · 独立人工 human_approver"
                  : "四工作区 + 按页维护人员",
              )}
              icon={ShieldCheck}
            />
            <Metric
              label={t("权限校验")}
              value="页面 + API"
              foot={t("直接输入网址同样检查")}
              icon={LockKeyhole}
            />
            <Metric
              label={t("运行模式")}
              value="本地演示"
              foot={t(
                integration
                  ? "无真实资金 · 有本地 Anvil 链上交易"
                  : "没有真实资金或链上交易",
              )}
              icon={Cable}
            />
          </div>
          <div className="content-grid">
            <Panel title={t("管理入口")}>
              <Link className="admin-link" href="/admin/audit">
                <ShieldCheck />
                <div>
                  <b>{t("待审计项目")}</b>
                  <small>
                    {integration ? (
                      <>
                        {readyHumanCount} {t("可人工审核")} · {waitingAiCount}{" "}
                        {t("等待 AI 接入")}
                      </>
                    ) : (
                      d.reviews?.filter((r) => r.status === "pending").length ||
                      0
                    )}
                  </small>
                </div>
                <ArrowUpRight />
              </Link>
              <Link className="admin-link" href="/admin/appeals">
                <ClipboardCheck />
                <div>
                  <b>{t(integration ? "申诉入口 · 未接入" : "未处理申诉")}</b>
                  <small>
                    {d.appeals?.filter((a) => a.status === "pending").length ||
                      0}
                  </small>
                </div>
                <ArrowUpRight />
              </Link>
              {t(
                canRead(app.user, "admin.users") && (
                  <Link className="admin-link" href="/admin/users">
                    <div className="record-icon">
                      <ShieldCheck />
                    </div>
                    <div>
                      <b>{t("管理页面访问权限")}</b>
                      <small>
                        {t(
                          integration
                            ? "写入未接入，仅查看当前受控身份"
                            : "为维护人员分配负责的页面",
                        )}
                      </small>
                    </div>
                    <ArrowUpRight size={17} />
                  </Link>
                ),
              )}
              {t(
                canRead(app.user, "admin.connections") && (
                  <Link className="admin-link" href="/admin/connections">
                    <div className="record-icon">
                      <Cable />
                    </div>
                    <div>
                      <b>{t("查看系统连接")}</b>
                      <small>{t("分清已实现与待接入模块")}</small>
                    </div>
                    <ArrowUpRight size={17} />
                  </Link>
                ),
              )}
              {t(
                canRead(app.user, "admin.audit") && (
                  <Link className="admin-link" href="/admin/audit">
                    <div className="record-icon">
                      <ScrollText />
                    </div>
                    <div>
                      <b>{t("审计操作记录")}</b>
                      <small>
                        {t(
                          integration
                            ? "独立本人授权材料与链上状态"
                            : "权限调整与业务操作留痕",
                        )}
                      </small>
                    </div>
                    <ArrowUpRight size={17} />
                  </Link>
                ),
              )}
            </Panel>
            <Panel title={t("权限规则")}>
              <div className="permission-summary">
                <p>
                  <b>{t("捐款人")}</b>
                  <span>{t("捐款人工作区")}</span>
                </p>
                <p>
                  <b>{t("基金会")}</b>
                  <span>
                    {t(
                      integration
                        ? "仅基金会工作区，不代捐款人"
                        : "捐款人 + 基金会",
                    )}
                  </span>
                </p>
                <p>
                  <b>{t("受捐机构")}</b>
                  <span>{t("受捐机构工作区")}</span>
                </p>
                <p>
                  <b>{t(integration ? "独立人工审批" : "管理员")}</b>
                  <span>
                    {t(
                      integration
                        ? "仅 human_approver，不代基金会、收货人或 AI"
                        : "四个工作区",
                    )}
                  </span>
                </p>
                <p>
                  <b>{t("维护人员")}</b>
                  <span>
                    {t(
                      integration
                        ? "本轮未启用 · 页面权限管理未接入"
                        : "仅获授权页面 · 只读",
                    )}
                  </span>
                </p>
              </div>
            </Panel>
          </div>
        </>
      );
    return <Empty />;
  }
  function reviewPassed(c: Procurement, stage: "purchase" | "delivery") {
    const review = d.reviews
      ?.filter((r) => r.procurementId === c.id && r.stage === stage)
      .at(-1);
    return review?.decision === "approved" && review.sourceHash === c.hash;
  }
  function integrationPhaseNotice(c: Procurement, mode: "review" | "payment") {
    const facts = integration?.procurementFacts[c.id];
    if (!facts?.verified)
      return "当前采购阶段尚未得到链上确认；请核对后台事实，不根据按钮状态推断审批完成。";
    if (facts.releaseConfirmed)
      return "资金已释放给 Foundation，不代表供应商 Paid；兑换、供应商付款与结算仍是独立事实。";
    if (mode === "review") {
      if (
        [
          "reserved",
          "invoice_recorded",
          "receipt_confirmed",
          "final_assessed",
          "release_approval_pending",
        ].includes(facts.chainState)
      )
        return "预留阶段已完成，继续交付证据与收货确认；资金不会因此自动释放。";
      if (facts.chainState === "reserve_approval_pending")
        return "此处仅执行后台重新核验后的独立人审票；提交请求不等于预留已确认。";
      return "尚未完成预留执行阶段；请核对采购证据与独立人工授权，不能代审批或自动预留。";
    }
    if (facts.chainState === "release_approval_pending")
      return "此处仅执行后台重新核验后的独立最终人审票，按发票限额释放给 Foundation。";
    return "资金仍在 Escrow；核对交付与收货证据，等待最终风险证据及独立人工放款授权。";
  }
  function claimDetail(c: Procurement) {
    return (
      <>
        <div className="section-line">
          <div>
            <h3>{c.name}</h3>
            <p className="muted">
              {t(c.id)} · {t(cash(c.amount))} mHKD
            </p>
          </div>
          <Status status={c.status} />
        </div>
        <Timeline
          claim={c}
          chainStatus={
            integration?.rawProcurements.find((p) => p.id === c.id)?.chainState
              .status
          }
        />
        {integration && (
          <p className="notice">
            {t("后端链上状态")}:{" "}
            {t(
              integration.rawProcurements.find((p) => p.id === c.id)?.chainState
                .status || "未提供",
            )}{" "}
            · {t("预留、释放给基金会和供应商结算是不同事实")}
          </p>
        )}
        {c.note && (
          <div className="notice compact">
            {integration ? t(c.note) : c.note}
          </div>
        )}
        <div className="review-grid">
          <div className="review-facts">
            <h3>{t("采购与证据")}</h3>
            <dl>
              <dt>{t("供应商")}</dt>
              <dd>
                {t(
                  d.vendors?.find((v) => v.id === c.vendorId)?.name ||
                    c.vendorId,
                )}
              </dd>
              <dt>{t("数量 × 单价")}</dt>
              <dd>
                {t(Number.isFinite(c.quantity) ? c.quantity : "未提供")} ×{" "}
                {t(cash(c.unitPrice))} mHKD
              </dd>
              <dt>{t("已上传附件")}</dt>
              <dd>
                {t(c.evidenceIds.length)}
                {t("个")}
              </dd>
            </dl>
            <div className="tiny muted">{t("单据内容 Hash")}</div>
            <code className="hash-value">{t(c.hash)}</code>
            <div className="button-row">
              <button onClick={() => copy(c.hash)}>
                <Copy size={13} />
                {t("复制 Hash")}
              </button>
              <a
                className="button"
                href={
                  integration
                    ? "/foundation/evidence"
                    : "/api/records/" + c.id + "/export"
                }
              >
                {t("下载单据")}
              </a>
            </div>
          </div>
          <div className="risk-panel">
            <span className="eyebrow">
              {t(modal?.type === "payment" ? "最终" : "采购前")}
              {locale === "en" ? " " : ""}
              {t(integration ? "链上 AI 评估 · 待接入" : "AI 报告 · 模拟")}
            </span>
            <div className={"risk-score " + (c.risk >= 80 ? "red" : "")}>
              {t(
                integration
                  ? "—"
                  : modal?.type === "payment"
                    ? (c.finalRisk ?? "—")
                    : c.risk,
              )}
              <small>/100</small>
            </div>
            <p>
              {t(
                integration
                  ? "链上 AI 评估尚未交付；独立诊断报告不生成风险分数或自动批准。"
                  : c.risk >= 80
                    ? "风险冻结，禁止预留与付款。"
                    : modal?.type === "payment" && c.finalRisk === undefined
                      ? "尚未提交交付证据。"
                      : "演示结果：未发现阻止正常审批的问题。",
              )}
            </p>
            <small>
              {t(
                integration
                  ? integration.workspaces[c.id]?.preAssessment ||
                    integration.workspaces[c.id]?.finalAssessment
                    ? "此采购含 synthetic 技术样例，仅证明机械链路，不是真实 AI。"
                    : "等待 AI 项目组接入；此入口不自动签署。"
                  : "这里使用固定示例结果，不能当作真实 AI 风控结论。",
              )}
            </small>
          </div>
        </div>
      </>
    );
  }
  function modalBody(): ReactNode {
    if (!modal) return null;
    if (modal.type === "permissions" && modal.user)
      return (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            perform("updatePermissions", {
              userId: modal.user!.id,
              grants,
              active: staffActive,
            });
          }}
        >
          <p className="muted">
            {t("维护人员：")}
            {modal.user.name}
            {t(
              "。仅授予所选页面的读取权限，不授予捐款、采购审批、付款或权限管理操作。",
            )}
          </p>
          <label className="check-label">
            <input
              type="checkbox"
              checked={staffActive}
              onChange={(e) => setStaffActive(e.target.checked)}
            />
            {t("启用账户")}
          </label>
          {t(
            portalOrder.map((p) => (
              <fieldset className="grant-group" key={p}>
                <legend>
                  {t(roleNames[p])}
                  {t("工作区")}
                </legend>
                {t(
                  pages
                    .filter((x) => x.portal === p)
                    .map((x) => (
                      <label className="check-label" key={x.id}>
                        <input
                          type="checkbox"
                          checked={grants.includes(x.id)}
                          onChange={(e) =>
                            setGrants((g) =>
                              e.target.checked
                                ? [...g, x.id]
                                : g.filter((v) => v !== x.id),
                            )
                          }
                        />
                        {t(x.label)}
                        <code>{t(x.id)}</code>
                      </label>
                    )),
                )}
              </fieldset>
            )),
          )}
          <div className="modal-actions">
            <span className="muted">
              {t("已选择")}
              {t(grants.length)}
              {t("个页面")}
            </span>
            <button className="primary" disabled={busy}>
              {t("保存页面授权")}
            </button>
          </div>
        </form>
      );
    if (modal.type === "staff-new")
      return (
        <form
          onSubmit={(e) => onSubmit(e, "createMaintainer")}
          className="form-grid"
        >
          <label>
            {t("工作人员姓名")}
            <input name="name" required maxLength={60} />
          </label>
          <label>
            {t("登录账号")}
            <input name="username" pattern="[a-zA-Z0-9_-]{3,40}" required />
          </label>
          <label className="full">
            {t("初始密码")}
            <input
              type="password"
              name="password"
              minLength={10}
              maxLength={120}
              required
              autoComplete="new-password"
            />
          </label>
          <p className="full muted">
            {t("新账号默认没有页面权限。创建后点击“分配页面”进行设置。")}
          </p>
          <div className="modal-actions full">
            <button className="primary" disabled={busy}>
              {t("创建维护人员")}
            </button>
          </div>
        </form>
      );
    if (modal.type === "donate" || modal.type === "deposit") {
      const deposit = modal.type === "deposit";
      if (integration) {
        const projectId = modal.project!.id;
        const funds = currentFunding(integration.exchanges, projectId);
        const funding = funds.find((item) => item.id === selectedFunding);
        return (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void quoteDonation(projectId);
            }}
          >
            <div className="notice compact">{modal.project?.name}</div>
            <label>
              {t("捐款金额 · HK$")}
              <input
                name="amount"
                type="number"
                min="0.01"
                step="0.01"
                value={donationAmount}
                required
                disabled={busy || Boolean(pendingA2)}
                onChange={(event) => {
                  modalRevision.current += 1;
                  setDonationAmount(event.target.value);
                  setDonationQuote(null);
                }}
              />
            </label>
            <p className="muted">
              {t(
                "模拟 HKD → MockHKD → 捐入当前项目 Escrow 冻结。兑换不等于捐款，不扣取真实资金。",
              )}
            </p>
            <div className="modal-actions">
              <button
                className="primary"
                disabled={
                  deposit ||
                  !allowedA2("mock.exchange.quote", undefined, projectId)
                }
              >
                {t("查询报价（不执行）")}
              </button>
            </div>
            {donationQuote && (
              <>
                <p>
                  {donationQuote.quote.rate} ·{" "}
                  {t(
                    donationQuote.quote.eligible
                      ? "eligible"
                      : donationQuote.quote.reasonCode,
                  )}{" "}
                  · {t("未执行")}
                </p>
                <button
                  type="button"
                  className="primary"
                  disabled={
                    !donationQuote.quote.eligible ||
                    !allowedA2("donor.funding.convert", undefined, projectId)
                  }
                  onClick={() => {
                    setDonationQuote(null);
                    void runA2("donor.funding.convert", {
                      projectId,
                      hkdCents: fullDemoAmount(donationAmount).hkdCents,
                      confirm: true,
                    });
                  }}
                >
                  {t("确认模拟兑换")}
                </button>
              </>
            )}
            <label>
              {t("本人已兑换资金")}
              <select
                value={selectedFunding}
                disabled={busy || Boolean(pendingA2)}
                onChange={(event) => setSelectedFunding(event.target.value)}
              >
                <option value="">{t("选择已对账资金")}</option>
                {funds.map((item) => (
                  <option key={item.id} value={item.id}>
                    {t(item.status)} · {item.id.slice(0, 8)} ·{" "}
                    {remainingFundingAtomic(item)} atomic
                  </option>
                ))}
              </select>
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="primary"
                disabled={
                  !funding ||
                  funding.status !== "reconciled" ||
                  !funding.reconciled ||
                  remainingFundingAtomic(funding) === "0" ||
                  !allowedA2("donation.funded.deposit", undefined, projectId)
                }
                onClick={() =>
                  funding &&
                  void runA2("donation.funded.deposit", {
                    projectId,
                    fundingOperationId: funding.operationId,
                    amountAtomic: remainingFundingAtomic(funding),
                    confirm: true,
                  })
                }
              >
                {t("确认捐入项目并冻结")}
              </button>
            </div>
            {integration.operations.slice(0, 3).map((operation) => (
              <p className="tiny muted" key={operation.operationId}>
                {t(operation.operationKind)} · {t(operation.status)} ·{" "}
                {t(
                  isChainConfirmed(operation)
                    ? "canonical confirmed"
                    : "待核对链上 / 未全部链上确认",
                )}{" "}
                · {operation.errorCode || operation.operationId}
              </p>
            ))}
          </form>
        );
      }
      return (
        <form
          onSubmit={(e) =>
            onSubmit(e, deposit ? "deposit" : "donate", {
              projectId: modal.project!.id,
            })
          }
        >
          <div className="notice compact">{modal.project?.name}</div>
          <label>
            {t(deposit ? "注资金额 · mHKD" : "捐款金额 · HK$")}
            <input
              name="amount"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue="100"
              required
            />
          </label>
          <p className="muted">
            {t(
              deposit
                ? "只更新本地演示资金池，不执行合约调用。"
                : "使用模拟支付，不扣取真实资金；捐款确认不会自动增加链上资金池。",
            )}
          </p>
          <div className="modal-actions">
            <button className="primary" disabled={busy}>
              {t(deposit ? "确认模拟注资" : "确认模拟捐款")}
            </button>
          </div>
        </form>
      );
    }
    if (modal.type === "project-new")
      return (
        <form
          className="form-grid"
          onSubmit={(e) => onSubmit(e, "createProject")}
        >
          <label className="full">
            {t("项目名称")}
            <input name="name" required maxLength={120} />
          </label>
          <label>
            {t("筹款目标 · HK$")}
            <input name="target" type="number" min="1" step="0.01" required />
          </label>
          <label>
            {t("受捐机构")}
            <input
              readOnly
              value={t(
                integration
                  ? integration.context?.defaults?.recipientUserId ||
                      "受控默认身份未提供"
                  : "同心社区学习中心",
              )}
            />
          </label>
          <label className="full">
            {t("项目说明")}
            <textarea name="description" maxLength={1000} required />
          </label>
          <p className="muted full">
            {t(
              integration
                ? "先建立后端草稿，再在项目详情独立提交 Anvil 链上创建。筹款目标仅是原表单字段，本轮接口不保存或据此释放资金。"
                : "演示类别：教育物资、设备。项目规则版本与 Hash 自动生成，未发布到真实合约。",
            )}
          </p>
          <div className="modal-actions full">
            <button className="primary" disabled={busy}>
              {t(integration ? "创建项目草稿" : "创建演示项目")}
            </button>
          </div>
        </form>
      );
    if (modal.type === "procurement-new")
      return (
        <form
          className="form-grid"
          onSubmit={(e) =>
            onSubmit(
              e,
              app.user.role === "foundation"
                ? "foundationProcurement"
                : "createProcurement",
            )
          }
        >
          <label>
            {t("项目")}
            <select name="projectId" defaultValue={modal.project?.id}>
              {t(
                projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                )),
              )}
            </select>
          </label>
          <label>
            {t(integration ? "模拟固定供应商" : "认证供应商")}
            <select name="vendorId">
              {t(
                (integration
                  ? [
                      {
                        id: integration.context.defaults?.supplierWallet || "",
                        name: "模拟固定供应商",
                      },
                    ]
                  : d.vendors || [{ id: "V-001", name: "知行文具" }]
                ).map((v) => (
                  <option key={v.id} value={v.id}>
                    {t(v.name)}
                  </option>
                )),
              )}
            </select>
          </label>
          <label className="full">
            {t("采购名称")}
            <input name="name" required maxLength={160} />
          </label>
          <label>
            {t("数量")}
            <input
              name="quantity"
              type="number"
              min="1"
              max="10000"
              step="1"
              defaultValue="1"
              required
            />
          </label>
          <label>
            {t("单价 · mHKD")}
            <input
              name="unitPrice"
              type="number"
              min="0.01"
              step="0.01"
              required
            />
          </label>
          <p className="full muted">
            {t(
              integration
                ? "数量 × 单价仅用于输入预算上限；后端存储精确 atomic 预算，不生成 AI 分数。先创建草稿，再独立提交上链。"
                : "总额由服务端根据数量和单价计算。提交后使用固定演示 AI 结果，进入人工初审。",
            )}
          </p>
          <div className="modal-actions full">
            <button className="primary" disabled={busy}>
              {t("提交采购申请")}
            </button>
          </div>
        </form>
      );
    if (modal.type === "reimbursement")
      return (
        <form
          onSubmit={(e) => onSubmit(e, "reimbursement")}
          className="form-grid"
        >
          <label className="full">
            {t("报销事由")}
            <input name="name" required maxLength={200} />
          </label>
          <label>
            {t("金额 · HK$")}
            <input
              name="amount"
              type="number"
              min="0.01"
              step="0.01"
              required
            />
          </label>
          <p className="muted full">
            {t("本版保存报销草稿和内容 Hash，报销证据审核及付款另行接入。")}
          </p>
          <div className="modal-actions full">
            <button className="primary" disabled={busy}>
              {t("保存报销草稿")}
            </button>
          </div>
        </form>
      );
    if (modal.type === "project" && modal.project) {
      const p =
        projects.find((item) => item.id === modal.project?.id) || modal.project;
      return (
        <>
          <p>{p.description}</p>
          <div className="budget-mini">
            <div>
              <small>{t("可用")}</small>
              <b>{t(cash(p.available))} mHKD</b>
            </div>
            <div>
              <small>{t("预留")}</small>
              <b>{t(cash(p.reserved))} mHKD</b>
            </div>
            <div>
              <small>{t(p.paymentTracked ? "已拨基金会" : "历史已付")}</small>
              <b>
                {t(cash(p.paymentTracked ? (p.released ?? NaN) : p.paid))} mHKD
              </b>
            </div>
          </div>
          <h3>{t("项目规则 v1")}</h3>
          <p>
            {t("允许用途：")}
            {t(
              p.categories
                .map((category) => t(category))
                .join(locale === "en" ? ", " : "、"),
            )}
            {t(
              integration
                ? "。资金先冻结；收货和最终人工审批后仅释放发票金额给基金会，再由平台模拟兑换与供应商付款。"
                : "。人工采购批准后预留预算，交付验收与最终审批后直接支付供应商。",
            )}
          </p>
          <code className="hash-value">{t(p.rulesHash)}</code>
          {integration && (
            <div className="modal-actions">
              <p>
                {t(
                  integration.rawProjects.find((item) => item.id === p.id)
                    ?.chainState.status || "尚未读取",
                )}
              </p>
              <button
                className="primary"
                disabled={!allowedA2("project.chain.create", undefined, p.id)}
                onClick={() =>
                  void runA2("project.chain.create", { projectId: p.id })
                }
              >
                {t("提交项目链上创建")}
              </button>
            </div>
          )}
          <button onClick={() => copy(p.rulesHash)}>
            <Copy size={14} />
            {t("复制规则 Hash")}
          </button>
        </>
      );
    }
    if (modal.type === "donation" && modal.donation) {
      const x = modal.donation;
      return (
        <>
          <div className="receipt-banner">
            <HeartHandshake size={30} />
            <span>PROOF OF GIVING</span>
            <h3>{t("感谢你的支持")}</h3>
            <strong>HK$ {t(cash(x.amount))}</strong>
            <small>
              {t("模拟支付凭证 ·")}
              {t(x.id)}
            </small>
          </div>
          <code className="hash-value">{t(x.hash)}</code>
          <div className="button-row">
            <button onClick={() => copy(x.hash)}>
              {t(
                copyState === x.hash ? <Check size={15} /> : <Copy size={15} />,
              )}
              {t("复制 Hash")}
            </button>
            <a
              className="button primary"
              href={"/api/records/" + x.id + "/export"}
            >
              {t("下载凭证 JSON")}
            </a>
          </div>
          <p className="tiny muted">
            {t("不是税务扣减收据；未发生真实支付或真实链上存证。")}
          </p>
        </>
      );
    }
    if (modal.claim) {
      const c = claims.find((x) => x.id === modal.claim!.id) || modal.claim;
      if (modal.type === "delivery")
        return (
          <Casework
            mode="proof"
            data={d}
            user={app.user}
            refresh={refresh}
            claimId={c.id}
            integration={integration}
          />
        );
      return (
        <>
          {t(claimDetail(c))}
          {integration && app.user.role === "foundation" && (
            <div className="modal-actions">
              <button
                className="primary"
                disabled={
                  !allowedA2("procurement.chain.create", c.id, c.projectId)
                }
                onClick={() =>
                  void runA2("procurement.chain.create", {
                    procurementId: c.id,
                  })
                }
              >
                {t("提交采购链上创建")}
              </button>
              <Link className="button" href="/foundation/evidence">
                {t("核对与登记采购证据")}
              </Link>
              <Link className="button" href="/foundation/review">
                {t("核对并执行预留")}
              </Link>
            </div>
          )}
          {(modal.type === "review" || modal.type === "payment") && (
            <p className="notice">
              {t(
                integration
                  ? integrationPhaseNotice(c, modal.type)
                  : reviewPassed(
                        c,
                        modal.type === "review" ? "purchase" : "delivery",
                      )
                    ? "管理员已通过当前版本审计，可以继续基金会业务操作。"
                    : "请先完成当前版本的管理员人工审计",
              )}
            </p>
          )}
          {t(
            c.status === "needs_info" &&
              write("resubmitProcurement") &&
              modal.type !== "supplement" && (
                <button
                  className="primary"
                  onClick={() => open({ type: "supplement", claim: c })}
                >
                  {t("补充采购资料")}
                </button>
              ),
          )}
          {t(
            modal.type === "supplement" && (
              <>
                <div className="upload-box">
                  <label>
                    {t("补充报价文件")}
                    <input
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg"
                      disabled={busy}
                      onChange={(e) => upload(e.target.files?.[0])}
                    />
                  </label>
                  {t(
                    uploaded.map((f) => (
                      <p key={f.id} className="tiny">
                        ✓ {f.name}
                      </p>
                    )),
                  )}
                </div>
                <form
                  onSubmit={(e) =>
                    onSubmit(e, "resubmitProcurement", { procurementId: c.id })
                  }
                >
                  <label>
                    {t("补充说明")}
                    <textarea name="note" required maxLength={500} />
                  </label>
                  <div className="modal-actions">
                    <button className="primary" disabled={busy}>
                      {t("保存新版本并重新提交")}
                    </button>
                  </div>
                </form>
              </>
            ),
          )}
          {t(
            modal.type === "review" && (
              <>
                <div className="notice compact">
                  {t(
                    integration
                      ? "此按钮只执行已登记的独立人工预留授权，不签署审批、不调用真实 AI。"
                      : "采购批准后才能预留预算。这里的“模拟批准”不会调用钱包、AI Oracle 或 Relayer。",
                  )}
                </div>
                {integration && (
                  <label>
                    {t("核对已批准的预留金额 · mHKD")}
                    <input
                      name="reserveAmount"
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={reserveDisplay}
                      disabled={busy || Boolean(pendingA2)}
                      required
                      onChange={(event) =>
                        setReserveDisplay(event.target.value)
                      }
                    />
                  </label>
                )}
                <div className="modal-actions">
                  <button
                    disabled={
                      !write("requestInfo") ||
                      c.status !== "human_review" ||
                      busy
                    }
                    onClick={() => open({ type: "request-info", claim: c })}
                  >
                    {t("要求补充")}
                  </button>
                  <button
                    className="primary"
                    disabled={
                      !write("approvePurchase") ||
                      (Boolean(integration) && !reserveDisplay) ||
                      (integration
                        ? !allowedA2(
                            "procurement.reserve.execute",
                            c.id,
                            c.projectId,
                          )
                        : !reviewPassed(c, "purchase") ||
                          c.status !== "human_review") ||
                      busy
                    }
                    onClick={() =>
                      (() => {
                        try {
                          void perform("approvePurchase", {
                            procurementId: c.id,
                            ...(integration
                              ? {
                                  reserveAmountAtomic:
                                    fullDemoAmount(reserveDisplay).amountAtomic,
                                }
                              : {}),
                          });
                        } catch (problem) {
                          setError(
                            problem instanceof Error
                              ? problem.message
                              : "金额无效",
                          );
                        }
                      })()
                    }
                  >
                    {t(
                      integration
                        ? "执行已获独立审批的预算预留"
                        : "模拟批准采购与预留",
                    )}
                  </button>
                </div>
              </>
            ),
          )}
          {t(
            modal.type === "request-info" && (
              <form
                onSubmit={(e) =>
                  onSubmit(e, "requestInfo", { procurementId: c.id })
                }
              >
                <label>
                  {t("需要补充什么资料？")}
                  <textarea name="note" required maxLength={500} />
                </label>
                <div className="modal-actions">
                  <button className="primary" disabled={busy}>
                    {t("发送补件要求")}
                  </button>
                </div>
              </form>
            ),
          )}
          {t(
            modal.type === "payment" && (
              <>
                <div className="conditions">
                  <p>
                    {t(
                      c.status === "payment_review" || c.status === "paid" ? (
                        <CheckCircle2 />
                      ) : (
                        <Circle />
                      ),
                    )}
                    {t("交付验收与最终发票已提交")}
                  </p>
                  <p>
                    {t(
                      c.finalRisk !== undefined ? <CheckCircle2 /> : <Circle />,
                    )}
                    {t(
                      integration
                        ? "链上 AI 评估待接入；诊断报告不自动通过审批"
                        : "AI 最终审核完成（演示）",
                    )}
                  </p>
                  <p>
                    <ShieldCheck />
                    {t(
                      integration
                        ? "拨款对象为项目基金会，不代表供应商收款"
                        : "收款对象为采购单绑定的认证 Vendor",
                    )}
                  </p>
                </div>
                <button
                  className="primary"
                  disabled={
                    !write("approvePayment") ||
                    (integration
                      ? !allowedA2("release.execute", c.id, c.projectId)
                      : !reviewPassed(c, "delivery") ||
                        c.status !== "payment_review") ||
                    busy
                  }
                  onClick={() =>
                    perform("approvePayment", { procurementId: c.id })
                  }
                >
                  {t(
                    integration
                      ? "执行已获独立审批的发票限额释放"
                      : "模拟批准供应商付款",
                  )}
                </button>
              </>
            ),
          )}
        </>
      );
    }
    return null;
  }
  const modalTitles: Record<string, string> = {
    permissions: "配置页面权限",
    "staff-new": "添加页面维护人员",
    donate: "确认模拟捐款",
    deposit: "项目资金池注资",
    "project-new": "创建公益项目",
    "procurement-new": "新建采购申请",
    reimbursement: "小额报销草稿",
    project: "项目与预算规则",
    donation: "捐款凭证",
    claim: "采购详情与时间线",
    review: "采购前审批",
    payment: "交付与付款审核",
    delivery: "提交交付证据",
    supplement: "补充采购资料",
    "request-info": "要求补充资料",
  };
  return (
    <div className="app-shell">
      <aside className={"sidebar " + (sidebar ? "open" : "")}>
        <Link href="/" className="brand">
          <span className="brand-mark">PoG</span>
          <div>
            <b>Proof of Giving</b>
            <span>{t("善意，有迹可循")}</span>
          </div>
        </Link>
        <div className="workspace-picker">
          <label>
            {t("当前工作区")}
            <select
              aria-label={t("切换工作区")}
              value={page.portal}
              onChange={(e) => {
                const target = accessible.find(
                  (p) => p.portal === e.target.value,
                )!;
                window.location.assign("/" + target.portal + "/" + target.slug);
              }}
            >
              {t(
                portals.map((p) => (
                  <option key={p} value={p}>
                    {t(roleNames[p])}
                    {t("工作区")}
                  </option>
                )),
              )}
            </select>
          </label>
          <ChevronDown size={14} />
        </div>
        <div className="nav-label">{t("WORKSPACE")}</div>
        <nav aria-label={t("工作区页面")}>
          {t(
            nav.map((p) => {
              const Icon = icons[p.icon] || FileText;
              return (
                <Link
                  onClick={() => setSidebar(false)}
                  key={p.id}
                  href={"/" + p.portal + "/" + p.slug}
                  className={p.id === page.id ? "active" : ""}
                >
                  <Icon size={18} />
                  <span>{t(p.label)}</span>
                  {t(p.id === page.id && <span className="active-dot" />)}
                </Link>
              );
            }),
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="mode-card">
            <span className="status-dot" />
            <b>{t("本地演示模式")}</b>
            <p>
              {t("MockHKD 无真实价值")}
              <br />
              {t(
                integration
                  ? "本地链真实交易 · 法币模拟 · 链上 AI 评估待接入"
                  : "支付 / AI / 链上操作为模拟",
              )}
            </p>
          </div>
          <div className="account">
            <div className="avatar">{app.user.name.slice(0, 1)}</div>
            <div>
              <b>{app.user.name}</b>
              <small>{t(roleNames[app.user.role])}</small>
            </div>
            <button
              className="icon-button"
              onClick={signout}
              aria-label={t("退出登录")}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      {t(
        sidebar && (
          <button
            className="sidebar-scrim"
            aria-label={t("关闭侧边栏")}
            onClick={() => setSidebar(false)}
          />
        ),
      )}
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label={t("打开导航")}
              onClick={() => setSidebar(true)}
            >
              <Menu size={20} />
            </button>
            <span>{t(roleNames[page.portal])}</span>
            <ChevronRight size={14} />
            <b>{t(page.label)}</b>
          </div>
          <div className="topbar-right">
            {!!source.projects?.length && page.slug !== "funds" && (
              <label
                className="history-switch"
                title={
                  integration
                    ? t("本轮只读真实 API，不载入旧 JSON 历史示例")
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={showLegacy}
                  disabled={Boolean(integration)}
                  onChange={(event) => setShowLegacy(event.target.checked)}
                />
                {t("查看历史示例")}
              </label>
            )}
            <LanguageSwitcher />
            <span className="environment">{t("DEMO")}</span>
            <span className="tiny muted">
              <ShieldCheck size={14} />
              {t("已授权")}
              {t(accessible.length)}
              {t("个页面")}
            </span>
          </div>
        </header>
        <main className="main-content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                PROOF OF GIVING / {t(page.portal.toUpperCase())}
              </p>
              <h1>{t(page.title)}</h1>
              <p className="muted">{t(page.description)}</p>
            </div>
            {t(primaryAction())}
          </div>
          {t(
            (app.user.role === "maintainer" ||
              (app.user.role === "admin" && page.portal !== "admin")) && (
              <div className="notice readonly">
                <LockKeyhole size={17} />
                <span>
                  {t(
                    app.user.role === "maintainer"
                      ? "页面维护模式：你只能查看被分配的页面，不能执行资金或审批操作。"
                      : "管理员查看模式：拥有页面访问权，不自动获得基金会业务审批权限。",
                  )}
                </span>
              </div>
            ),
          )}
          {t(
            error && !modal && (
              <p className="error" role="alert">
                {t(error)}
              </p>
            ),
          )}
          {showLegacy && (
            <p className="notice compact">
              {t("以下为原有历史演示记录，与当前模拟币资金链分开保存。")}
            </p>
          )}
          {!showLegacy &&
            app.user.role !== "maintainer" &&
            (page.slug === "funds" || page.slug === "overview") && (
              <FundsFlow
                user={app.user}
                compact={page.slug !== "funds"}
                integration={integration}
                onRefresh={refresh}
              />
            )}
          {t(content())}
          <footer className="page-footer">
            <span>{t("PoG · 透明、可核验、按规则使用")}</span>
            <span>
              {t("更新于")}
              {t(date(app.updatedAt))}
              {t("· 香港时间")}
            </span>
          </footer>
        </main>
      </div>
      {t(
        toast && (
          <div className="toast" role="status">
            <CheckCircle2 size={19} />
            <span>{t(toast)}</span>
            <button
              className="icon-button"
              aria-label={t("关闭提示")}
              onClick={() => setToast("")}
            >
              <X size={16} />
            </button>
          </div>
        ),
      )}
      {t(
        modal && (
          <Modal
            title={t(modalTitles[modal.type] || "详情")}
            close={() => {
              if (!busy && !pendingA2) {
                modalRevision.current += 1;
                setModal(null);
              }
            }}
          >
            {t(
              error && (
                <p role="alert" className="error">
                  {t(error)}
                </p>
              ),
            )}
            {t(modalBody())}
            {integration && pendingA2 && (
              <button
                disabled={busy}
                onClick={() => void runA2(pendingA2.id, pendingA2.body, true)}
              >
                {t("结果未知：保留同一请求重试")}
              </button>
            )}
            {t(
              busy && (
                <p className="tiny muted" role="status">
                  {t("正在处理，请稍候…")}
                </p>
              ),
            )}
          </Modal>
        ),
      )}
    </div>
  );
}
