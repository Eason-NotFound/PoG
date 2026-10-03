"use client";
import {
  useCallback,
  useEffect,
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
const eventNames: Record<string, string> = {
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
};
function newKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
export default function FundsFlow({
  user,
  compact = false,
}: {
  user: AccessUser;
  compact?: boolean;
}) {
  const { t, locale } = useI18n();
  const [data, setData] = useState<FundsView | null>(null),
    [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [node, setNode] = useState("pool");
  const working = useRef(false);
  const refresh = useCallback(async () => {
    const r = await fetch("/api/payment/state", { cache: "no-store" });
    const v = await r.json();
    if (!r.ok) throw new Error(v.error);
    setData(v);
    setError("");
    setSelected((old) => {
      old =
        old || new URLSearchParams(window.location.search).get("project") || "";
      return v.projects.some((p: { id: string }) => p.id === old)
        ? old
        : v.projects[0]?.id || "";
    });
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (!working.current && document.visibilityState === "visible")
        refresh().catch((e) => alive && setError(e.message));
    };
    load();
    const timer = setInterval(load, 8000);
    window.addEventListener("pog-funds-changed", load);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener("pog-funds-changed", load);
    };
  }, [refresh]);
  const token = (v: string) =>
    new Intl.NumberFormat(locale === "en" ? "en-HK" : "zh-HK", {
      maximumFractionDigits: 6,
    }).format(Number(v) / 1e6);
  const cash = (c: number) =>
    new Intl.NumberFormat(locale === "en" ? "en-HK" : "zh-HK", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(c / 100);
  const project = data?.projects.find((p) => p.id === selected);
  async function act(kind: string, body: Record<string, unknown>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = { projectId: selected, ...body },
        storage = `pog-site-payment:${user.id}:${kind}:${JSON.stringify(payload)}`;
      let key = sessionStorage.getItem(storage);
      if (!key) {
        key = newKey();
        sessionStorage.setItem(storage, key);
      }
      const r = await fetch("/api/payment/operations/" + kind, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(payload),
      });
      const v = await r.json();
      if (!r.ok) throw new Error(v.error);
      setNotice(v.message || t("资金状态已更新"));
      await refresh();
      window.dispatchEvent(new Event("pog-funds-changed"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("请求失败"));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  const form = (kind: string) => (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    void act(kind, Object.fromEntries(new FormData(e.currentTarget)));
  };
  const fresh = () => {
    for (const k of Object.keys(sessionStorage))
      if (k.startsWith(`pog-site-payment:${user.id}:`))
        sessionStorage.removeItem(k);
    setNotice(t("已开始新一笔操作"));
  };
  if (!data)
    return (
      <section className="funds-shell">
        <div className="funds-loading">
          <CircleDollarSign size={28} />
          <h2>{t("透明资金链")}</h2>
          <p>{error || t("正在读取同一份资金账本…")}</p>
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
          sub: "全站 Donor 当前持币 · 项目外",
          icon: Wallet,
          amount: BigInt(data.globalDonors.tokens),
        },
        {
          id: "pool",
          label: "项目资金池",
          value: token(project.locked),
          unit: "mHKD",
          sub: "仍锁在本项目中",
          icon: LockKeyhole,
          amount: BigInt(project.locked),
        },
        {
          id: "foundation",
          label: "Foundation 钱包",
          value: token(project.foundationTokens),
          unit: "mHKD",
          sub: "本项目已拨出、尚未兑付",
          icon: Landmark,
          amount: BigInt(project.foundationTokens),
        },
        {
          id: "redemption",
          label: "模拟兑付钱包",
          value: token(project.redeemed),
          unit: "mHKD",
          sub: "对应 HKD 已入基金会账本",
          icon: CircleDollarSign,
          amount: BigInt(project.redeemed),
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
            onChange={(e) => setSelected(e.target.value)}
          >
            {data.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {t(p.name)}
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
              {t("我的模拟币")}
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
              {t("兑换待确认：全站 Donor 冻结 HKD")}{" "}
              <strong>HK$ {cash(data.globalDonors.frozenHkdCents)}</strong> ·{" "}
              {t("仍在 Donor 账户，尚未进入项目池")}
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
                  className={`funds-stage ${s.amount > 0n ? "has-funds" : ""} ${node === s.id ? "selected" : ""}`}
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
                  {s.amount > 0n && (
                    <span className="funds-here">
                      <i />
                      {t("资金在这里")}
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
                    "钱包里的模拟币尚未捐出，可捐入项目。兑换冻结的是 HKD，确认后才发放模拟币。这里展示全站汇总，不计入下方项目守恒公式。",
                  )}
                </p>
              </>
            ) : node === "pool" ? (
              <>
                <LockKeyhole size={19} />
                <p>
                  {t("池内待使用")} <b>{token(project.free)} mHKD</b>　+　
                  {t("采购预留")} <b>{token(project.reserved)} mHKD</b>。
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
                    "这是基金会收到、仍持有的本项目模拟币。收款人工作台代表物资受捐机构，负责验收，不接收这笔资金。",
                  )}
                </p>
              </>
            ) : (
              <>
                <CircleDollarSign size={19} />
                <p>
                  {t(
                    "模拟币转入兑付钱包，同时基金会模拟 HKD 入账。两者是同一次兑换的两面，不重复计算资金，也不代表供应商已收款。",
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
              {t("项目累计捐入")} <b>{token(project.deposited)}</b> ={" "}
              {t("池中")} {token(project.locked)} + {t("基金会持币")}{" "}
              {token(project.foundationTokens)} + {t("已兑付")}{" "}
              {token(project.redeemed)} + {t("累计退款")}{" "}
              {token(project.refunded)}
            </span>
            <strong className={project.balanced ? "balanced" : "unbalanced"}>
              {t(project.balanced ? "总额一致" : "需要核账")}
            </strong>
          </div>
          {(error || notice) && (
            <p
              role={error ? "alert" : "status"}
              className={error ? "error" : "notice compact"}
            >
              {t(error || notice)}
            </p>
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
                  <button disabled={busy} className="text-link" onClick={fresh}>
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
                    <form onSubmit={form("exchange")}>
                      <h4>{t("① 模拟 HKD 换币")}</h4>
                      <p>{t("先冻结 HKD，再确认模拟币到账。")}</p>
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
                          busy || project.paused || project.state !== "Active"
                        }
                      >
                        {t("冻结并创建兑换")}
                      </button>
                    </form>
                    <form onSubmit={form("donate")}>
                      <h4>{t("② 模拟币捐入项目")}</h4>
                      <p>{t("从你的钱包扣币，进入该项目锁定池。")}</p>
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
                          busy || project.paused || project.state !== "Active"
                        }
                      >
                        {t("捐入项目池")}
                      </button>
                    </form>
                    <form onSubmit={form("cashout")}>
                      <h4>{t("退款与换回 HKD")}</h4>
                      <p>{t("退款先到原钱包，再按需换回模拟 HKD。")}</p>
                      <button
                        type="button"
                        disabled={
                          busy ||
                          project.state !== "Refundable" ||
                          project.myClaimed ||
                          project.myDonation === "0"
                        }
                        onClick={() => act("claim", {})}
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
                      <button disabled={busy}>{t("换回模拟 HKD")}</button>
                    </form>
                  </>
                )}
                {project.canManage && (
                  <>
                    <form onSubmit={form("redeem")}>
                      <h4>{t("基金会模拟兑付")}</h4>
                      <p>{t("收到的模拟币转入兑付钱包，模拟 HKD 到账。")}</p>
                      <label>
                        {t("选择拨款")}
                        <select name="releaseId" required>
                          {project.releases.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.procurementId} ·{" "}
                              {token(
                                String(
                                  BigInt(r.amount) -
                                    BigInt(r.redeemed) -
                                    BigInt(r.returned),
                                ),
                              )}{" "}
                              mHKD
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {t("金额")}
                        <input
                          name="amount"
                          defaultValue="72"
                          inputMode="decimal"
                          required
                        />
                      </label>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !project.releases.length ||
                          project.paused ||
                          project.state !== "Active"
                        }
                      >
                        {t("确认模拟兑付")}
                      </button>
                    </form>
                    <form onSubmit={form(project.paused ? "resume" : "pause")}>
                      <h4>{t("项目暂停与关闭")}</h4>
                      <p>{t("暂停阻止新捐款和拨款；关闭核账后才能退款。")}</p>
                      <label>
                        {t("原因")}
                        <input
                          name="reason"
                          defaultValue={t("项目资金核查")}
                          required
                        />
                      </label>
                      <button disabled={busy || project.state !== "Active"}>
                        {t(project.paused ? "恢复项目" : "暂停项目")}
                      </button>
                      <button
                        type="button"
                        disabled={busy || project.state !== "Active"}
                        onClick={() =>
                          act("closing", {
                            reason: t("项目结束，申请关闭核账"),
                          })
                        }
                      >
                        {t("申请关闭核账")}
                      </button>
                    </form>
                    <div className="funds-action-info">
                      <h4>{t("沿用原采购与验收流程")}</h4>
                      <p>
                        {t(
                          "建立采购 → 管理员初审 → 预算预留 → 双方证明 → 管理员终审 → 拨款给基金会。",
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
                          "只登记基金会在系统外处理后的凭证，不执行供应商付款。",
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
                      <button disabled={busy || !project.releases.length}>
                        {t("登记核账结果")}
                      </button>
                    </form>
                    <div className="funds-action-info">
                      <h4>{t("关闭与退款快照")}</h4>
                      <p>
                        {t("未结采购")}：{project.unresolved} · {t("采购预留")}
                        ：{token(project.reserved)} mHKD
                      </p>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          project.state !== "Closing" ||
                          project.unresolved > 0 ||
                          project.reserved !== "0"
                        }
                        onClick={() => act("close", {})}
                      >
                        {t("核准关闭并计算退款")}
                      </button>
                      <Link href="/admin/audit" className="text-link">
                        {t("前往人工审计")}
                      </Link>
                    </div>
                    <form onSubmit={form("cancel-procurement")}>
                      <h4>{t("取消未收货采购")}</h4>
                      <p>
                        {t("释放预留，保留记录。已经收货的采购不能在此取消。")}
                      </p>
                      <label>
                        {t("采购编号")}
                        <input name="procurementId" required />
                      </label>
                      <label>
                        {t("原因")}
                        <input name="reason" required />
                      </label>
                      <button disabled={busy}>{t("取消采购并释放预留")}</button>
                    </form>
                  </>
                )}
              </div>
              {user.role === "donor" &&
                data.exchanges.some((o) => o.status === "frozen") && (
                  <div className="funds-pending">
                    <h3>
                      <Clock3 size={18} />
                      {t("待确认兑换 · HKD 仍冻结在你的账户")}
                    </h3>
                    {data.exchanges
                      .filter((o) => o.status === "frozen")
                      .map((o) => (
                        <div className="funds-pending-row" key={o.id}>
                          <span>
                            <b>HK$ {cash(o.cents)}</b>
                            <small>
                              {o.id} · {o.projectId}
                            </small>
                          </span>
                          <button
                            className="primary"
                            disabled={busy}
                            onClick={() =>
                              act("confirm-exchange", { orderId: o.id })
                            }
                          >
                            {t("确认模拟币到账")}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() =>
                              act("cancel-exchange", { orderId: o.id })
                            }
                          >
                            {t("取消并解冻")}
                          </button>
                        </div>
                      ))}
                  </div>
                )}
              <div className="funds-section-title">
                <h3>{t("可追溯资金流水")}</h3>
                <button
                  onClick={() => {
                    const blob = new Blob(
                      [
                        JSON.stringify(
                          {
                            mode: data.mode,
                            projectId: project.id,
                            events: project.events,
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
                      <strong>{t(eventNames[e.kind] || e.kind)}</strong>
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
                          SHA-256: {e.receiptHash}
                        </code>
                      </details>
                    </div>
                    <aside>
                      <b>
                        {e.amount !== "0"
                          ? token(e.amount) +
                            " " +
                            (e.kind.includes("exchange-") ? "HKD" : "mHKD")
                          : "—"}
                      </b>
                      <small>
                        {new Date(e.at).toLocaleString(
                          locale === "en" ? "en-HK" : "zh-HK",
                        )}
                      </small>
                      <small>{t(e.actorRole)}</small>
                    </aside>
                  </li>
                ))}
                {!project.events.length && (
                  <li className="funds-empty">
                    {t(
                      "还没有资金移动。Donor 完成第一笔兑换后，流水会出现在这里。",
                    )}
                  </li>
                )}
              </ol>
            </>
          )}
          <div className="funds-footnote">
            <CheckCircle2 size={14} />
            {t("资金记录来自网站模拟账本，不生成虚假的链上交易 Hash。")}
            <time>
              {t("更新于")}{" "}
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
