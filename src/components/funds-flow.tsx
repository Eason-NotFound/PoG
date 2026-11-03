"use client";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import Link from "next/link";
import {
  ArrowDownLeft,
  ArrowRight,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  ExternalLink,
  Eye,
  Landmark,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useI18n } from "./language-provider";
import type { AccessUser } from "@/lib/access";
import type { FundsView } from "@/lib/payment-types";
import { atomicValue, decimalToAtomic, formatAtomic, freeLockedAtomic } from "@/lib/a2-workbench";
import { formatHkdCents, parseHkdDisplay } from "@/lib/payment-boundary";
import { PortalA2Error, portalA2Action, portalA2Allowed, type PortalA2Json, type PortalA2State } from "@/lib/portal-a2";
import type { DemoQuote } from "@/lib/full-demo-ui";
const eventNames: Record<string, string> = {
  funding: "模拟 HKD 换币",
  redemption: "基金会模拟 HKD 兑回",
  supplier_payment: "模拟供应商付款",
  unknown: "资金操作",
  "exchange-frozen": "HKD 已冻结，等待兑换确认",
  exchanged: "模拟币已到 Donor 钱包",
  "exchange-cancelled": "兑换取消，HKD 已解冻",
  donated: "捐款已锁入项目池",
  reserved: "采购预算已预留",
  released: "模拟币已交付基金会",
  redeemed: "基金会模拟 HKD 已入账",
  returned: "未兑付拨款已退回项目",
  pause: "项目已暂停",
  resume: "项目已恢复",
  closing: "项目进入关闭核账",
  "refund-ready": "剩余资金可领取退款",
  refunded: "退款已回原 Donor 钱包",
  cashout: "Donor 模拟 HKD 已入账",
  "external-reconciled": "外部凭证已核账",
  "procurement-cancelled": "采购取消，预留已释放",
};
const stateNames = {
  Active: "进行中",
  Closing: "关闭核账中",
  Refundable: "可领取退款",
  Closed: "已关闭",
  Pending: "尚未链上确认",
};
const statusNames: Record<string, string> = {
  unknown: "未知状态",
  held: "模拟 HKD 已冻结",
  queued: "等待处理",
  prepared: "等待处理",
  processing: "处理中",
  sending: "处理中",
  chain_submitted: "已提交链上交易",
  broadcast: "已广播",
  submitted: "已提交链上交易",
  confirming: "等待链上确认",
  pending: "等待链上确认",
  confirmed: "已确认",
  reconciled: "已对账",
  completed: "资金操作已完成",
  requires_attention: "需要核对",
  failed: "操作失败",
  invalidated_instance: "部署实例已失效",
};
function newKey() {
  return crypto.randomUUID();
}
function object(value: unknown): PortalA2Json | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as PortalA2Json : null;
}
function atomicOrNull(value: unknown): string | null {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value)) return null;
  try { atomicValue(value); return value; } catch { return null; }
}
/** API read projection only. Cumulative releases are never wallet balances. */
function projectFunds(state: PortalA2State): FundsView {
  const knownMoney = (key: string) => atomicOrNull(state.account?.[key]);
  return {
    mode: "a2-local-simulation", updatedAt: new Date().toISOString(),
    user: { id: state.currentUser.id, role: state.currentUser.role, hkdCents: null,
      availableHkdCents: knownMoney("availableHkdCents"), holdCents: knownMoney("heldHkdCents"), tokens: null },
    globalDonors: { tokens: null, frozenHkdCents: null }, exchanges: [],
    projects: state.rawProjects.map(raw => {
      const ledger = state.ledgers[raw.id];
      const confirmed = raw.chainState.verified && ledger?.chainVerified === true;
      const free = confirmed ? freeLockedAtomic(ledger) : null;
      const locked = confirmed ? (atomicValue(free!) + atomicValue(ledger.reservedAtomic)).toString() : null;
      const procurements = state.rawProcurements.filter(item => item.projectId === raw.id);
      const progress = state.progressByProject[raw.id];
      const rows = Array.isArray(progress?.procurements) ? progress.procurements.map(object).filter((row): row is PortalA2Json => row !== null) : [];
      const statusOf = (id: string) => state.paymentStatuses[id] ?? rows.find(row => row.id === id);
      const visiblePayments = state.currentUser.role !== "donor" && procurements.every(proc => proc.chainState.verified && statusOf(proc.id));
      const redemptions = procurements.map(proc => object(statusOf(proc.id)?.redemption));
      const redeemed = visiblePayments ? redemptions.reduce((total, resource) => total + (resource?.status === "reconciled" && resource.reconciled === true ? atomicValue(String(resource.amountAtomic)) : 0n), 0n).toString() : null;
      const ownFunding = Array.isArray(progress?.ownFunding) ? progress.ownFunding.map(object).filter((row): row is PortalA2Json => row !== null) : [];
      const resources = [...state.exchanges.filter(resource => resource.projectId === raw.id) as unknown as PortalA2Json[], ...ownFunding,
        ...procurements.flatMap(proc => [object(statusOf(proc.id)?.redemption), object(statusOf(proc.id)?.supplierPayment)].filter((resource): resource is PortalA2Json => resource !== null))];
      const uniqueResources = [...new Map(resources.filter(resource => typeof resource.id === "string" && typeof resource.operationId === "string").map(resource => [String(resource.id), resource])).values()];
      const stateName = raw.chainState.verified ? ({ active: "Active", closing: "Closing", refundable: "Refundable", closed: "Closed" } as const)[raw.chainState.status as "active"] ?? "Pending" : "Pending";
      return { id: raw.id, name: raw.title, state: stateName, paused: raw.chainState.status === "paused",
        deposited: confirmed ? ledger.depositsAtomic : null, locked,
        reserved: confirmed ? ledger.reservedAtomic : null, free,
        foundationTokens: confirmed ? ledger.releasedAtomic : null,
        released: confirmed ? ledger.releasedAtomic : null, returned: confirmed ? ledger.returnedAtomic : null,
        redeemed, refunded: confirmed ? ledger.refundedAtomic : null, returnable: null,
        donorCount: null, myDonation: confirmed ? ledger.currentCallerDonorCreditAtomic : null, myRefund: null, myClaimed: false,
        balanced: confirmed ? atomicValue(ledger.depositsAtomic) + atomicValue(ledger.returnedAtomic) === atomicValue(locked!) + atomicValue(ledger.releasedAtomic) + atomicValue(ledger.refundedAtomic) : null,
        unresolved: procurements.filter(proc => !["payment_confirmed", "cancelled"].includes(proc.chainState.status)).length,
        canManage: state.currentUser.role === "foundation" && raw.foundationWallet?.toLowerCase() === state.currentUser.walletAddress.toLowerCase(),
        releases: procurements.map(proc => {
          const amounts = object(state.workspaces[proc.id]?.amounts);
          const resource = object(statusOf(proc.id)?.redemption);
          return { id: proc.id, projectId: raw.id, procurementId: proc.id, foundationId: raw.foundationWallet ?? "",
            amount: atomicOrNull(amounts?.invoiceAmountAtomic), redeemed: resource?.status === "reconciled" && resource.reconciled === true ? atomicOrNull(resource.amountAtomic) : null, returned: null };
        }),
        events: uniqueResources.map(resource => {
          const proof = object(resource.chainProof);
          return { id: String(resource.id), projectId: raw.id, kind: typeof resource.kind === "string" ? resource.kind : "unknown", status: typeof resource.status === "string" ? resource.status : "unknown",
            actorRole: resource.kind === "funding" ? "donor" : ["redemption", "supplier_payment"].includes(String(resource.kind)) ? "foundation" : "API 未提供角色",
            amount: atomicOrNull(resource.amountAtomic) ?? "0", unit: resource.kind === "supplier_payment" ? "HKD" as const : "mHKD" as const,
            from: resource.kind === "funding" ? "Donor 模拟 HKD 账户" : typeof resource.actorWallet === "string" ? resource.actorWallet : "API 未提供钱包",
            to: resource.kind === "funding" ? "本项目兑换额度（以对账状态为准）" : typeof resource.counterpartyWallet === "string" ? resource.counterpartyWallet : "API 未提供钱包",
            at: typeof resource.createdAt === "string" ? resource.createdAt : "",
            reference: `Operation: ${String(resource.operationId)}${typeof proof?.transactionHash === "string" ? ` · Tx: ${proof.transactionHash}` : ""}${typeof resource.evidenceId === "string" ? ` · Evidence: ${resource.evidenceId}` : ""}`,
            receiptHash: "" };
        }) };
    }),
  };
}
export default function FundsFlow({
  user,
  compact = false,
  integration,
  onRefresh,
}: {
  user: AccessUser;
  compact?: boolean;
  integration?: PortalA2State;
  onRefresh?: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const data = useMemo(() => integration ? projectFunds(integration) : null, [integration]);
  const [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [noticeStatus, setNoticeStatus] = useState(""),
    [reviewReason, setReviewReason] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [node, setNode] = useState("pool"),
    [fundingAmount, setFundingAmount] = useState("60"),
    [procurementId, setProcurementId] = useState(""),
    [quote, setQuote] = useState<{ projectId: string; amount: string; value: DemoQuote } | null>(null),
    [redemptionQuote, setRedemptionQuote] = useState<{ procurementId: string; value: DemoQuote } | null>(null),
    [retry, setRetry] = useState<{ kind: string; body: PortalA2Json; key: string; scope: string } | null>(null);
  const working = useRef(false);
  const currentScope = `${integration?.currentUser.id}:${integration?.context.binding.namespaceId}:${integration?.context.binding.runId}:${integration?.context.binding.instanceId}`;
  const scopeRef = useRef(currentScope);
  scopeRef.current = currentScope;
  const refresh = useCallback(async () => {
    if (!onRefresh) throw new Error("尚未连接实际 API 工作区");
    await onRefresh();
  }, [onRefresh]);
  useEffect(() => {
    if (!data) return;
    setSelected((old) => {
      old =
        old || new URLSearchParams(window.location.search).get("project") || "";
      return data.projects.some((p) => p.id === old)
        ? old
        : data.projects[0]?.id || "";
    });
  }, [data]);
  useEffect(() => {
    const candidates = data?.projects.find(p => p.id === selected)?.releases ?? [];
    setProcurementId(old => candidates.some(p => p.procurementId === old) ? old : candidates[0]?.procurementId ?? "");
  }, [selected, data?.projects]);
  useEffect(() => { setQuote(null); setRedemptionQuote(null); }, [selected, procurementId, currentScope]);
  useEffect(() => { setRetry(null); }, [currentScope]);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (!working.current && document.visibilityState === "visible")
        refresh().catch((e) => alive && setError(e.message));
    };
    if (!integration) return;
    const timer = setInterval(load, 8000);
    window.addEventListener("pog-funds-changed", load);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener("pog-funds-changed", load);
    };
  }, [refresh, Boolean(integration)]);
  const token = (v: string | null) => v === null ? "—" : formatAtomic(v);
  const cash = (c: string | null) => c === null ? "—" : formatHkdCents(c);
  const statusText = (status: string) => t(statusNames[status] || status);
  const project = data?.projects.find((p) => p.id === selected);
  const quoteReady = Boolean(quote && quote.projectId === selected && quote.amount === fundingAmount && quote.value.quote.eligible &&
    integration && ["namespaceId", "runId", "instanceId", "chainId"].every(field => quote.value.binding[field] === integration.context.binding[field]));
  const selectedInvoice = project?.releases.find(item => item.procurementId === procurementId)?.amount ?? null;
  const redemptionQuoteReady = Boolean(redemptionQuote && redemptionQuote.procurementId === procurementId && redemptionQuote.value.quote.eligible && redemptionQuote.value.quote.amountAtomic === selectedInvoice &&
    integration && ["namespaceId", "runId", "instanceId", "chainId"].every(field => redemptionQuote.value.binding[field] === integration.context.binding[field]));
  const enabled = (id: string, scopedProcurement = "") => {
    if (["release.execute", "foundation.redemption.convert", "supplier.payment.execute", "settlement.evidence.record", "settlement.execute"].includes(id) && !scopedProcurement)
      return { enabled: false, reason: "请选择采购" };
    return portalA2Allowed(integration, id, {
      projectId: selected || undefined, procurementId: scopedProcurement || undefined,
    });
  };
  async function act(kind: string, body: Record<string, unknown>, retainedKey?: string) {
    if (working.current) return;
    if (retry && retainedKey !== retry.key) { setError("上一笔操作结果未知，请先使用相同请求编号与内容重试核对"); return; }
    if (retainedKey && (!retry || retry.key !== retainedKey || retry.scope !== currentScope || JSON.stringify(retry.body) !== JSON.stringify(body))) {
      setError("重试必须保留原身份、部署、请求编号与完整内容"); return;
    }
    working.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setNoticeStatus("");
    const originalScope = currentScope;
    let usedKey = retainedKey || "";
    try {
      const scopedProcurement = typeof body.procurementId === "string" ? body.procurementId : "";
      const actionProject = typeof body.projectId === "string" ? body.projectId : integration?.rawProcurements.find(item => item.id === scopedProcurement)?.projectId ?? selected;
      const permission = portalA2Allowed(integration, kind, { projectId: actionProject || undefined, procurementId: scopedProcurement || undefined });
      // An exact uncertain request may replay after its original operation has
      // advanced the stage; the backend's idempotency and role checks decide it.
      if (!permission.enabled && !retainedKey) throw new Error(permission.reason || "此操作本轮尚未接入");
      const payload = { ...body },
        storage = `pog-a2-funds:${integration?.context.binding.namespaceId}:${user.id}:${kind}:${JSON.stringify(payload)}`;
      let key = retainedKey || sessionStorage.getItem(storage);
      if (!key) {
        key = newKey();
        sessionStorage.setItem(storage, key);
      }
      usedKey = key;
      const v = await portalA2Action(kind, payload, key, { expectedState: integration });
      if (scopeRef.current !== originalScope) return;
      setRetry(null);
      if (kind === "mock.exchange.quote") {
        const value = v as unknown as DemoQuote;
        if (!value.quote || value.executed !== false || value.quote.direction !== body.direction || (body.direction === "hkd_to_mock" && value.quote.hkdCents !== body.hkdCents) || !integration ||
          ["namespaceId", "runId", "instanceId", "chainId"].some(field => value.binding?.[field] !== integration.context.binding[field]))
          throw new Error("兑换报价与当前项目或部署不一致");
        if (body.direction === "hkd_to_mock") setQuote({ projectId: selected, amount: fundingAmount, value });
        else setRedemptionQuote({ procurementId: String(body.procurementId), value });
        setNotice(value.quote.eligible ? "报价已取得；请独立确认模拟兑换" : value.quote.reasonCode || "本次报价不可执行");
        return;
      }
      setNotice(v.operation ? "请求已登记" : "API 操作已登记，请查看确认状态");
      setNoticeStatus(v.operation?.status || "");
      if (kind === "donor.funding.convert") setQuote(null);
      await refresh();
      window.dispatchEvent(new Event("pog-funds-changed"));
    } catch (e) {
      if (scopeRef.current !== originalScope) return;
      setError(e instanceof Error ? e.message : t("请求失败"));
      if (e instanceof PortalA2Error && (e.code === "transport_unavailable" || e.status >= 500) && usedKey)
        setRetry({ kind, body: { ...body }, key: usedKey, scope: originalScope });
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  const form = (kind: string) => (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const values = Object.fromEntries(new FormData(e.currentTarget));
    try {
      if (kind === "quote") void act("mock.exchange.quote", { direction: "hkd_to_mock", projectId: selected, hkdCents: parseHkdDisplay(String(values.amount)) });
      else if (kind === "donate") {
        const amountAtomic = decimalToAtomic(String(values.amount));
        const funding = integration?.exchanges.find(item => item.operationId === values.fundingOperationId && item.kind === "funding" && item.projectId === selected && item.status === "reconciled" && item.reconciled === true);
        if (!funding || atomicValue(funding.amountAtomic) - atomicValue(funding.allocatedAtomic) < atomicValue(amountAtomic)) throw new Error("请选择本项目已对账且额度足够的兑换记录");
        void act("donation.funded.deposit", { projectId: selected, fundingOperationId: funding.operationId, amountAtomic, confirm: true });
      } else setError("此操作本轮尚未接入");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "金额无效"); }
  };
  const fresh = () => {
    for (const k of Object.keys(sessionStorage))
      if (k.startsWith(`pog-a2-funds:${integration?.context.binding.namespaceId}:${user.id}:`))
        sessionStorage.removeItem(k);
    setNotice("已开始新一笔操作");
    setNoticeStatus("");
  };
  if (!data)
    return (
      <section className="funds-shell">
        <div className="funds-loading">
          <CircleDollarSign size={28} />
          <h2>{t("透明资金链")}</h2>
          <p>{t(error || "等待实际 API 资金工作区…")}</p>
          <button onClick={() => refresh().catch((e) => setError(e.message))}>
            {t("刷新")}
          </button>
        </div>
      </section>
    );
  const own = user.role === "donor" || user.role === "foundation";
  const stages = project
    ? [
        {
          id: "donor",
          label: "Donor 钱包",
          value: token(data.globalDonors.tokens),
          unit: "mHKD",
          sub: "全站持币总余额 · API 未提供",
          icon: Wallet,
          amount: null,
        },
        {
          id: "pool",
          label: "项目资金池",
          value: token(project.locked),
          unit: "mHKD",
          sub: "仍锁在本项目中",
          icon: LockKeyhole,
          amount: project.locked === null ? null : BigInt(project.locked),
        },
        {
          id: "foundation",
          label: "Foundation 累计拨款",
          value: token(project.foundationTokens),
          unit: "mHKD",
          sub: "链上累计拨出 · 非钱包余额",
          icon: Landmark,
          amount: project.foundationTokens === null ? null : BigInt(project.foundationTokens),
        },
        {
          id: "redemption",
          label: "模拟 HKD 累计兑回",
          value: token(project.redeemed),
          unit: "mHKD",
          sub: "已对账兑回汇总 · 非钱包余额",
          icon: CircleDollarSign,
          amount: project.redeemed === null ? null : BigInt(project.redeemed),
        },
      ]
    : [];
  return (
    <section className="funds-shell" aria-label={t("透明资金链")}>
      <div className="funds-heading">
        <div>
          <span className="funds-eyebrow">
            <Eye size={13} /> SHARED FUNDS / {t("模拟演示")}
          </span>
          <h2>{t("每一笔善款，现在在哪里？")}</h2>
          <p>{t("三方共享同一份账本，资金移动后自动更新。")}</p>
        </div>
        <div className="funds-tools">
          <span className={"funds-live" + (error ? " offline" : "")}>
            <i />
            {error ? t("连接中断") : t("自动更新")}
          </span>
          <button
            className="icon-button"
            aria-label={t("刷新资金链")}
            disabled={busy}
            onClick={() => refresh().catch((e) => setError(e.message))}
          >
            <RefreshCw size={17} />
          </button>
        </div>
      </div>
      <div className="funds-filter">
        <label>
          {t("查看项目")}
          <select
            value={selected}
            disabled={busy}
            onChange={(e) => setSelected(e.target.value)}
          >
            {data.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {project && (
          <span
            className={
              "funds-state" +
              (project.state === "Active" && project.paused ? " paused" : "")
            }
          >
            {t(
              project.state === "Active" && project.paused
                ? "已暂停"
                : stateNames[project.state],
            )}
          </span>
        )}
        <span className="funds-demo">{t("本地模拟币 · 无真实资金")}</span>
      </div>
      {own && (
        <div className="funds-wallet-strip">
          <div>
            <Wallet size={18} />
            <span>
              {t("我的可用模拟 HKD")}
              <b>HK$ {cash(data.user.availableHkdCents)}</b>
            </span>
          </div>
          <div className={data.user.holdCents ? "holding" : ""}>
            <LockKeyhole size={18} />
            <span>
              {t("我的冻结 HKD")}
              <b>HK$ {cash(data.user.holdCents)}</b>
            </span>
          </div>
          <div>
            <CircleDollarSign size={18} />
            <span>
              {t("我的模拟币余额 · API 未提供")}
              <b>{token(data.user.tokens)} mHKD</b>
            </span>
          </div>
        </div>
      )}
      {project ? (
        <>
          <div className="funds-freeze">
            <Clock3 size={15} />
            <span>
              {t("全站 Donor 冻结 HKD：API 未提供汇总")}{" "}
              <strong>HK$ {cash(data.globalDonors.frozenHkdCents)}</strong> ·{" "}
              {t("个人冻结金额见模拟账户；兑换由后台独立确认")}
            </span>
          </div>
          <div
            className="funds-rail"
            role="group"
            aria-label={t("资金当前位置")}
          >
            {stages.map((s, i) => (
              <div className="funds-stage-wrap" key={s.id}>
                <button
                  className={`funds-stage ${s.amount !== null && s.amount > 0n ? "has-funds" : ""} ${node === s.id ? "selected" : ""}`}
                  onClick={() => setNode(s.id)}
                  aria-pressed={node === s.id}
                >
                  <span className="funds-stage-top">
                    <span className="funds-stage-icon">
                      <s.icon size={22} />
                    </span>
                    <small>0{i + 1}</small>
                  </span>
                  <span className="funds-stage-name">{t(s.label)}</span>
                  <span className="funds-amount">
                    {s.value}
                    <small>{s.unit}</small>
                  </span>
                  <span className="funds-stage-note">{t(s.sub)}</span>
                  {s.amount !== null && s.amount > 0n && (
                    <span className="funds-here">
                      <i />
                      {t(s.id === "pool" ? "资金在这里" : "已记录累计流向")}
                    </span>
                  )}
                </button>
                {i < stages.length - 1 && (
                  <span className="funds-arrow">
                    <ArrowRight size={18} />
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className="funds-detail" aria-live="polite">
            {node === "donor" ? (
              <>
                <Wallet size={19} />
                <p>
                  {t(
                    "API 尚未提供全站钱包余额。你只能把本项目已对账的兑换额度独立捐入项目；模拟 HKD 冻结与后台铸币确认分别记录。",
                  )}
                </p>
              </>
            ) : node === "pool" ? (
              <>
                <LockKeyhole size={19} />
                <p>
                  {t("池内待使用")} <b>{token(project.free)} mHKD</b>{" + "}
                  {t("采购预留")} <b>{token(project.reserved)} mHKD</b>{locale === "en" ? ". " : "。"}
                  {t(
                    "预留只是锁定用途，资金仍在项目池；收货验收和人工审计后才能拨给基金会。",
                  )}
                </p>
              </>
            ) : node === "foundation" ? (
              <>
                <Landmark size={19} />
                <p>
                  {t(
                    "这是本项目链上累计拨给基金会的金额，并非基金会当前钱包余额。Recipient 负责独立验收；拨款不代表供应商已付款。",
                  )}
                </p>
              </>
            ) : (
              <>
                <CircleDollarSign size={19} />
                <p>
                  {t(
                    "这里只汇总本项目已对账的模拟 HKD 兑回记录，并非兑付钱包余额。供应商模拟付款及人工结算确认仍是后续独立动作。",
                  )}
                </p>
              </>
            )}
          </div>
          <div className="funds-refund">
            <span className="funds-return-icon">
              <ArrowDownLeft size={20} />
            </span>
            <div>
              <b>{t("退款 → 原 Donor 钱包")}</b>
              <p>
                {t("累计已退")} <strong>{token(project.refunded)} mHKD</strong>
                {project.state === "Refundable"
                  ? " · " + t("剩余锁款等待原捐款人领取")
                  : ""}{" "}
                · {t("累计流向，退款后可再次捐出")}
              </p>
            </div>
            {user.role === "donor" && (
              <span>
                {t("我的可退")}{" "}
                <b>{project.myClaimed ? "0" : token(project.myRefund)} mHKD</b>
              </span>
            )}
          </div>
          <div className="funds-equation">
            <ShieldCheck size={17} />
            <span>
              {t("链上累计捐入")} <b>{token(project.deposited)}</b> + {t("累计退回")}{" "}
              {token(project.returned)} = {t("池中")} {token(project.locked)} + {t("累计拨出")}{" "}
              {token(project.released)} + {t("累计退款")}{" "}
              {token(project.refunded)}
            </span>
            <strong className={project.balanced ? "balanced" : "unbalanced"}>
              {t(project.balanced === null ? "等待链上账本" : project.balanced ? "链上账本一致" : "需要核账")}
            </strong>
          </div>
          {(error || notice) && (
            <p
              role={error ? "alert" : "status"}
              className={error ? "error" : "notice compact"}
            >
              {t(error || notice)}
              {!error && noticeStatus && <>: {statusText(noticeStatus)} · {t("以实际对账及链上确认状态为准")}</>}
            </p>
          )}
          {retry && retry.scope === currentScope && (
            <button disabled={busy} onClick={() => void act(retry.kind, retry.body, retry.key)}>
              {t("使用相同请求编号与内容重试")}
            </button>
          )}
          {compact ? (
            <div className="funds-compact-footer">
              <span>{t("所有角色看到相同的项目资金分布")}</span>
              <Link href={`/${user.role}/funds`} className="button primary">
                {t(
                  user.role === "recipient"
                    ? "查看完整资金流水"
                    : "打开资金操作与流水",
                )}
                <ArrowRight size={16} />
              </Link>
            </div>
          ) : (
            <>
              <div className="funds-section-title">
                <h3>
                  {t(
                    user.role === "recipient"
                      ? "受捐机构 · 只读资金视图"
                      : "资金操作",
                  )}
                </h3>
                {user.role !== "recipient" && (
                  <button disabled={busy || Boolean(retry)} className="text-link" onClick={fresh}>
                    {t("开始新一笔操作")}
                  </button>
                )}
              </div>
              {user.role === "recipient" && (
                <p className="notice compact">
                  {t(
                    "你可以查看资金状态与每笔凭证；提交收货验收后，由人工审计决定是否向基金会拨款。",
                  )}{" "}
                  <Link href="/recipient/delivery">
                    {t("前往收货验收")} <ExternalLink size={13} />
                  </Link>
                </p>
              )}
              <div className="funds-action-grid">
                {user.role === "donor" && (
                  <>
                    <form onSubmit={form("quote")}>
                      <h4>{t("① 模拟 HKD 换币")}</h4>
                      <p>{t("先取得报价并独立确认兑换；后台对账完成后才能捐入项目。")}</p>
                      <label>
                        {t("金额")}
                        <input
                          name="amount"
                          inputMode="decimal"
                          value={fundingAmount}
                          disabled={busy}
                          onChange={(e) => { setFundingAmount(e.target.value); setQuote(null); }}
                          required
                        />
                      </label>
                      <button
                        className="primary"
                        disabled={
                          busy || !enabled("mock.exchange.quote").enabled || project.paused || project.state !== "Active"
                        }
                      >
                        {t("取得模拟兑换报价")}
                      </button>
                      {quote && quote.projectId === selected && quote.amount === fundingAmount && (
                        <p>{quote.value.quote.rate} · HK$ {cash(quote.value.quote.hkdCents)} → {token(quote.value.quote.amountAtomic)} mHKD · {t("手续费")} HK$ {cash(quote.value.quote.feeHkdCents)}</p>
                      )}
                      <button type="button" className="primary" disabled={busy || !quoteReady || !enabled("donor.funding.convert").enabled}
                        onClick={() => quoteReady && quote && void act("donor.funding.convert", { projectId: selected, hkdCents: quote.value.quote.hkdCents, confirm: true })}>
                        {t("确认模拟 HKD 兑换")}
                      </button>
                    </form>
                    <form onSubmit={form("donate")}>
                      <h4>{t("② 模拟币捐入项目")}</h4>
                      <p>{t("从你的钱包扣币，进入该项目锁定池。")}</p>
                      <label>
                        {t("本项目已对账兑换")}
                        <select name="fundingOperationId" required disabled={busy}>
                          <option value="">{t("选择已对账兑换记录")}</option>
                          {integration?.exchanges.filter(item => item.kind === "funding" && item.projectId === selected && item.status === "reconciled" && item.reconciled === true && atomicValue(item.amountAtomic) > atomicValue(item.allocatedAtomic)).map(item => (
                            <option key={item.id} value={item.operationId}>{item.operationId} · {token((atomicValue(item.amountAtomic) - atomicValue(item.allocatedAtomic)).toString())} mHKD</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {t("金额")}
                        <input
                          name="amount"
                          inputMode="decimal"
                          defaultValue="60"
                          required
                        />
                      </label>
                      <button
                        className="primary"
                        disabled={
                          busy || !enabled("donation.funded.deposit").enabled || project.paused || project.state !== "Active" || !integration?.exchanges.some(item => item.kind === "funding" && item.projectId === selected && item.status === "reconciled" && item.reconciled === true && atomicValue(item.amountAtomic) > atomicValue(item.allocatedAtomic))
                        }
                      >
                        {t("捐入项目池")}
                      </button>
                    </form>
                    <form onSubmit={form("cashout")}>
                      <h4>{t("退款与换回 HKD")}</h4>
                      <p>{t("退款与 Donor 换回 HKD 本轮尚未接入。")}</p>
                      <button
                        type="button"
                        disabled
                      >
                        {t("领取我的退款")}
                      </button>
                      <label>
                        {t("换回金额")}
                        <input
                          name="amount"
                          inputMode="decimal"
                          defaultValue="16.80"
                          required
                        />
                      </label>
                      <button disabled>{t("换回模拟 HKD · 本轮尚未接入")}</button>
                    </form>
                  </>
                )}
                {project.canManage && (
                  <>
                    <form onSubmit={e => { e.preventDefault(); if (redemptionQuoteReady) void act("foundation.redemption.convert", { procurementId, confirm: true }); }}>
                      <h4>{t("基金会模拟兑付与供应商付款")}</h4>
                      <p>{t("人工放款批准后独立执行；兑回、模拟供应商付款与人工结算确认分别记录。")}</p>
                      <label>
                        {t("选择采购")}
                        <select name="releaseId" value={procurementId} onChange={e => setProcurementId(e.target.value)} required disabled={busy}>
                          {project.releases.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.procurementId} ·{" "}
                              {token(r.amount)}{" "}
                              mHKD
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {t("当前绑定发票金额")}
                        <input
                          value={token(selectedInvoice)}
                          readOnly
                          inputMode="decimal"
                          required
                        />
                      </label>
                      <button type="button" disabled={busy || !enabled("release.execute", procurementId).enabled} onClick={() => void act("release.execute", { procurementId })}>
                        {t("执行已独立人工批准的放款")}
                      </button>
                      <button type="button" disabled={busy || !enabled("foundation.redemption.convert", procurementId).enabled || !enabled("mock.exchange.quote", procurementId).enabled} onClick={() => void act("mock.exchange.quote", { direction: "mock_to_hkd", procurementId })}>
                        {t("取得模拟兑回报价")}
                      </button>
                      {redemptionQuote && redemptionQuote.procurementId === procurementId && (
                        <p>{redemptionQuote.value.quote.rate} · {token(redemptionQuote.value.quote.amountAtomic)} mHKD → HK$ {cash(redemptionQuote.value.quote.hkdCents)}</p>
                      )}
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !redemptionQuoteReady || !enabled("foundation.redemption.convert", procurementId).enabled
                        }
                      >
                        {t("确认模拟 HKD 兑回")}
                      </button>
                      <button type="button" disabled={busy || !enabled("supplier.payment.execute", procurementId).enabled} onClick={() => void act("supplier.payment.execute", { procurementId, confirm: true })}>
                        {t("执行模拟供应商付款")}
                      </button>
                      <button type="button" disabled={busy || !enabled("settlement.evidence.record", procurementId).enabled} onClick={() => void act("settlement.evidence.record", { procurementId })}>
                        {t("记录已对账结算证据")}
                      </button>
                      <button type="button" disabled={busy || !enabled("settlement.execute", procurementId).enabled} onClick={() => void act("settlement.execute", { procurementId })}>
                        {t("执行已独立人工批准的结算")}
                      </button>
                      {procurementId && <p>{t(integration?.procurementFacts[procurementId]?.statusText || "等待 API 确认")}</p>}
                    </form>
                    <form onSubmit={form(project.paused ? "resume" : "pause")}>
                      <h4>{t("项目暂停与关闭")}</h4>
                      <p>{t("暂停、关闭与退款本轮尚未接入。")}</p>
                      <label>
                        {t("原因")}
                        <input
                          name="reason"
                          value={reviewReason ?? t("项目资金核查")}
                          onChange={event => setReviewReason(event.target.value)}
                          required
                        />
                      </label>
                      <button disabled>
                        {t(project.paused ? "恢复项目" : "暂停项目")}
                      </button>
                      <button
                        type="button"
                        disabled
                      >
                        {t("申请关闭核账")}
                      </button>
                    </form>
                    <div className="funds-action-info">
                      <h4>{t("沿用原采购与验收流程")}</h4>
                      <p>
                        {t(
                          "建立采购 → 风险证据（AI 待接）→ 独立人工审批 → 预算预留 → 发票与收货证据 → 独立人工放款审批。",
                        )}
                      </p>
                      <Link href="/foundation/evidence" className="button">
                        {t("采购与证明")}
                        <ArrowRight size={14} />
                      </Link>
                      <Link href="/foundation/payment" className="text-link">
                        {t("查看待拨款采购")}
                      </Link>
                    </div>
                  </>
                )}
                {user.role === "admin" && (
                  <>
                    <form onSubmit={form("external-reconciliation")}>
                      <h4>{t("外部凭证核账")}</h4>
                      <p>
                        {t(
                          "外部凭证核账本轮尚未接入；人工审批仍须分别准备、签名和提交。",
                        )}
                      </p>
                      <label>
                        {t("选择拨款")}
                        <select name="releaseId" required>
                          {project.releases.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.procurementId}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {t("凭证编号")}
                        <input name="reference" required />
                      </label>
                      <button disabled>
                        {t("登记核账结果")}
                      </button>
                    </form>
                    <div className="funds-action-info">
                      <h4>{t("关闭与退款快照")}</h4>
                      <p>
                        {t("未结采购")}{locale === "en" ? ": " : "："}{project.unresolved} · {t("采购预留")}
                        {locale === "en" ? ": " : "："}{token(project.reserved)} mHKD
                      </p>
                      <button
                        className="primary"
                        disabled
                      >
                        {t("核准关闭并计算退款 · 本轮尚未接入")}
                      </button>
                      <Link href="/admin/audit" className="text-link">
                        {t("前往人工审计")}
                      </Link>
                    </div>
                    <form onSubmit={form("cancel-procurement")}>
                      <h4>{t("取消未收货采购")}</h4>
                      <p>
                        {t("取消采购与释放预留本轮尚未接入。")}
                      </p>
                      <label>
                        {t("采购编号")}
                        <input name="procurementId" required />
                      </label>
                      <label>
                        {t("原因")}
                        <input name="reason" required />
                      </label>
                      <button disabled>{t("取消采购并释放预留")}</button>
                    </form>
                  </>
                )}
              </div>
              {user.role === "donor" &&
                integration?.exchanges.some((o) => o.kind === "funding" && o.projectId === selected && o.status !== "reconciled") && (
                  <div className="funds-pending">
                    <h3>
                      <Clock3 size={18} />
                      {t("本项目兑换处理状态 · 后台独立对账")}
                    </h3>
                    {integration.exchanges
                      .filter((o) => o.kind === "funding" && o.projectId === selected && o.status !== "reconciled")
                      .map((o) => (
                        <div className="funds-pending-row" key={o.id}>
                          <span>
                            <b>HK$ {cash(o.hkdCents)}</b>
                            <small>
                              {o.operationId} · {statusText(o.status)}
                            </small>
                          </span>
                          <button
                            className="primary"
                            disabled={busy}
                            onClick={() => refresh().catch(e => setError(e.message))}
                          >
                            {t("读取实际确认状态")}
                          </button>
                          <button
                            disabled
                          >
                            {t("取消与解冻尚未接入")}
                          </button>
                        </div>
                      ))}
                  </div>
                )}
              <div className="funds-section-title">
                <h3>{t("可追溯 API 兑换与付款记录")}</h3>
                <button
                  onClick={() => {
                    const blob = new Blob(
                      [
                        JSON.stringify(
                          {
                            mode: data.mode,
                            projectId: project.id,
                            events: project.events.map(event => ({
                              ...event,
                              label: t(eventNames[event.kind] || event.kind),
                              statusLabel: event.status ? statusText(event.status) : undefined,
                              from: t(event.from),
                              to: t(event.to),
                              actorRole: t(event.actorRole),
                            })),
                          },
                          null,
                          2,
                        ),
                      ],
                      { type: "application/json" },
                    );
                    const url = URL.createObjectURL(blob),
                      a = document.createElement("a");
                    a.href = url;
                    a.download = project.id + "-funds.json";
                    a.click();
                    setTimeout(() => URL.revokeObjectURL(url), 500);
                  }}
                >
                  {t("下载项目流水")}
                </button>
              </div>
              <ol className="funds-events">
                {[...project.events].reverse().map((e) => (
                  <li key={e.id}>
                    <span className="funds-event-dot" />
                    <div>
                      <strong>{t(eventNames[e.kind] || e.kind)}{e.status ? " · " + statusText(e.status) : ""}</strong>
                      <p>
                        {t(e.from)} <ArrowRight size={12} /> {t(e.to)}
                      </p>
                      <details>
                        <summary>{t("查看关联凭证")}</summary>
                        <code>
                          {e.id}
                          <br />
                          {e.reference}
                          <br />
                          {t("凭证 Hash")}: {e.receiptHash || t("见 API 关联凭证；未提供 Hash")}
                        </code>
                      </details>
                    </div>
                    <aside>
                      <b>
                        {e.amount !== "0"
                          ? token(e.amount) +
                            " " +
                            (e.unit || "mHKD")
                          : "—"}
                      </b>
                      <small>
                        {e.at ? new Date(e.at).toLocaleString(
                          locale === "en" ? "en-HK" : "zh-HK",
                        ) : t("API 未提供时间")}
                      </small>
                      <small>{t(e.actorRole)}</small>
                    </aside>
                  </li>
                ))}
                {!project.events.length && (
                  <li className="funds-empty">
                    {t(
                      "当前角色没有可见的兑换或付款记录；项目捐款以链上账本为准。",
                    )}
                  </li>
                )}
              </ol>
            </>
          )}
          <div className="funds-footnote">
            <CheckCircle2 size={14} />
            {t("资金记录来自当前 API、Anvil 项目账本与模拟 Payment；放款不等于供应商付款。")}
            <time>
              {t("读取于")}{" "}
              {new Date(data.updatedAt).toLocaleTimeString(
                locale === "en" ? "en-HK" : "zh-HK",
              )}
            </time>
          </div>
        </>
      ) : (
        <p>{t("暂无可查看的资金链项目")}</p>
      )}
    </section>
  );
}
