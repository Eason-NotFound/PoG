"use client";
import { useEffect, useState } from "react";
import { FileText, Upload, ShieldCheck, ClipboardCheck } from "lucide-react";
import { canAct, type AccessUser, type Action } from "@/lib/access";
import type { PageData, Procurement, ReviewCase } from "@/lib/types";
import { formatDate, formatMoney } from "@/lib/i18n";
import { useI18n } from "./language-provider";

const decisions = {
  approved: "审计通过",
  rejected: "审计未通过",
  more_info: "要求补充资料",
};
const evidenceNames: Record<string, string> = {
  invoice: "采购发票",
  dispatch: "发货证明",
  photo: "收货照片",
  grn: "验收单",
  quotation: "报价文件",
};
export default function Casework({
  mode,
  data,
  user,
  refresh,
  claimId,
  foundationView,
}: {
  mode: "proof" | "audit" | "appeals";
  data: PageData;
  user: AccessUser;
  refresh: () => Promise<void>;
  claimId?: string;
  foundationView?: boolean;
}) {
  const { t, locale } = useI18n();
  const [selected, setSelected] = useState(claimId || ""),
    [filter, setFilter] = useState("pending"),
    [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [fileKind, setFileKind] = useState(
    user.role === "foundation" ? "invoice" : "photo",
  );
  const [reason, setReason] = useState(""),
    [decision, setDecision] = useState("approved"),
    [appealReview, setAppealReview] = useState("");
  useEffect(() => {
    setSelected(claimId || "");
    setReason("");
    setError("");
    setNotice("");
    setFilter("pending");
  }, [mode, claimId]);
  const claims = data.procurements || [],
    reviews = data.reviews || [],
    appeals = data.appeals || [];
  const foundation = foundationView ?? user.role === "foundation";
  const date = (value?: string) => (value ? formatDate(value, locale) : "—");
  async function action(action: Action, body: Record<string, unknown>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/actions/" + action, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": Array.from(
            crypto.getRandomValues(new Uint8Array(16)),
          )
            .map((x) => x.toString(16).padStart(2, "0"))
            .join(""),
        },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      setNotice(result.message);
      setReason("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  async function upload(file?: File) {
    if (!file || !selected || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (file.size > 1048576) throw new Error("本地演示每个文件最多 1 MB");
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("文件读取失败"));
        reader.readAsDataURL(file);
      });
      const response = await fetch("/api/evidence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: file.name,
          content,
          procurementId: selected,
          type:
            claims.find((c) => c.id === selected)?.status === "needs_info"
              ? "quotation"
              : fileKind,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      setNotice("证明文件已保存，可使用凭证编号查询");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }
  function files(c: Procurement, ids = c.evidenceIds) {
    const files = (data.evidence || []).filter((e) => ids.includes(e.id));
    return (
      <div className="evidence-list">
        {files.length ? (
          files.map((file) => (
            <article key={file.id} className="evidence-item">
              <div className="section-line">
                <strong>{file.name}</strong>
                <span className="pill">
                  {t(
                    file.submittedRole === "foundation" ? "基金会" : "受捐机构",
                  )}{" "}
                  · {t(evidenceNames[file.type] || file.type)}
                </span>
              </div>
              <small>{t("凭证编号")}</small>
              <code className="hash-value">{file.hash}</code>
              <a className="button" href={"/api/evidence/" + file.id}>
                {t("查看或下载证明")}
              </a>
            </article>
          ))
        ) : (
          <p className="muted">{t("暂无可查看的证明文件")}</p>
        )}
      </div>
    );
  }
  function projectName(id: string) {
    return t(data.projects?.find((p) => p.id === id)?.name || id);
  }
  const messages = (
    <>
      {error && (
        <p className="error" role="alert">
          {t(error)}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {t(notice)}
        </p>
      )}
    </>
  );
  function tabs(pending: number, done: number) {
    return (
      <div className="case-tabs" aria-label={t("处理状态")}>
        <button
          type="button"
          className={filter === "pending" ? "primary" : ""}
          onClick={() => {
            setFilter("pending");
            setSelected("");
            setReason("");
          }}
        >
          {t(mode === "audit" ? "待审计项目" : "未处理")} <b>{pending}</b>
        </button>
        <button
          type="button"
          className={filter !== "pending" ? "primary" : ""}
          onClick={() => {
            setFilter("done");
            setSelected("");
            setReason("");
          }}
        >
          {t(mode === "audit" ? "已审计项目" : "已处理")} <b>{done}</b>
        </button>
      </div>
    );
  }
  if (mode === "proof") {
    const c = claims.find((c) => c.id === selected);
    const latest =
      c &&
      reviews
        .filter((r) => r.procurementId === c.id && r.stage === "delivery")
        .at(-1);
    const supplement = foundation && c?.status === "needs_info";
    const canSubmit = canAct(
      user,
      foundation ? "submitFoundationProof" : "deliver",
    );
    const editable =
      c &&
      (supplement ||
        c.status === "reserved" ||
        (c.status === "payment_review" &&
          latest?.status === "reviewed" &&
          latest.decision !== "approved"));
    return (
      <section className="panel casework">
        <div className="panel-title">
          <h2>{t(foundation ? "基金会采购与发货证明" : "受捐机构收货证明")}</h2>
          <Upload size={20} />
        </div>
        <p className="notice">
          {t(
            "捐款拨付基金会后，由基金会采购并发送物资；基金会提供发票，受捐机构提供收货照片，双方材料共同接受审计。",
          )}
        </p>
        {messages}
        {!claimId && (
          <label>
            {t("选择项目采购单")}
            <select
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
                setError("");
                setNotice("");
                setReason("");
              }}
            >
              <option value="">{t("请选择采购单")}</option>
              {claims.map((c) => (
                <option key={c.id} value={c.id}>
                  {projectName(c.projectId)} · {t(c.name)} · {c.id}
                </option>
              ))}
            </select>
          </label>
        )}
        {c && (
          <>
            <h3>
              {t(c.name)} <small>{c.id}</small>
            </h3>
            <div className="proof-progress">
              <span className={c.foundationProof ? "complete" : ""}>
                {t("基金会发票")} · {t(c.foundationProof ? "已提交" : "待提交")}
              </span>
              <span className={c.recipientProof ? "complete" : ""}>
                {t("受捐机构收货照片")} ·{" "}
                {t(c.recipientProof ? "已提交" : "待提交")}
              </span>
            </div>
            <p className="tiny muted">{t("凭证编号")}</p>
            <code className="hash-value">{c.hash}</code>
            {c.foundationProof && (
              <p>
                <b>{t("基金会说明")}：</b>
                {c.foundationProof.note}
              </p>
            )}
            {c.recipientProof && (
              <p>
                <b>{t("受捐机构说明")}：</b>
                {c.recipientProof.note}
              </p>
            )}
            {latest?.reason && (
              <p className="notice">
                <b>{t("审核意见")}：</b>
                {latest.reason}
              </p>
            )}
            {files(c)}
            {canSubmit && editable ? (
              <>
                <div className="upload-box">
                  <label>
                    {t("证明文件类型")}
                    <select
                      value={supplement ? "quotation" : fileKind}
                      onChange={(e) => setFileKind(e.target.value)}
                    >
                      {(supplement
                        ? ["quotation"]
                        : foundation
                          ? ["invoice", "dispatch"]
                          : ["photo", "grn"]
                      ).map((kind) => (
                        <option key={kind} value={kind}>
                          {t(evidenceNames[kind])}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="upload-control">
                    <Upload size={18} />
                    {t(
                      fileKind === "photo"
                        ? "选择收货照片 PNG / JPG，每个最多 1 MB"
                        : "选择 PDF / JPG / PNG，每个最多 1 MB",
                    )}
                    <input
                      type="file"
                      accept={
                        fileKind === "photo"
                          ? ".png,.jpg,.jpeg"
                          : ".pdf,.png,.jpg,.jpeg"
                      }
                      disabled={busy}
                      onChange={(e) => {
                        void upload(e.target.files?.[0]);
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void action(
                      supplement
                        ? "resubmitFoundationProcurement"
                        : foundation
                          ? "submitFoundationProof"
                          : "deliver",
                      {
                        procurementId: c.id,
                        note: reason,
                        ...(!foundation
                          ? { quantity: Number(f.get("quantity")) }
                          : {}),
                      },
                    );
                  }}
                >
                  {!foundation && (
                    <label>
                      {t("实际验收数量")}
                      <input
                        key={c.id}
                        name="quantity"
                        type="number"
                        required
                        min="1"
                        step="1"
                        defaultValue={c.quantity}
                      />
                    </label>
                  )}
                  <label>
                    {t("证明说明")}
                    <textarea
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      required
                      maxLength={supplement ? 500 : 1000}
                    />
                  </label>
                  <p className="tiny muted">
                    {t(
                      supplement
                        ? "补充采购资料"
                        : foundation
                          ? "至少上传一份采购发票，再提交证明。"
                          : "至少上传一张实际收货照片，再提交验收。",
                    )}
                  </p>
                  <button
                    className="primary"
                    disabled={busy || (!supplement && c.status !== "reserved")}
                  >
                    {t(
                      supplement
                        ? "保存新版本并重新提交"
                        : foundation
                          ? "提交基金会证明"
                          : "提交收货证明",
                    )}
                  </button>
                  {c.status === "payment_review" && (
                    <p className="muted">
                      {t("请先上传补充文件，再重新提交证明。")}
                    </p>
                  )}
                </form>
              </>
            ) : (
              <p className="notice">
                {t(
                  canSubmit
                    ? "此阶段只可查看证明；请等待采购批准或人工审计结果。"
                    : "当前账户只可查看，不能代替基金会或受捐机构提交证明。",
                )}
              </p>
            )}
          </>
        )}
      </section>
    );
  }
  if (mode === "audit") {
    const review = reviews.find((r) => r.id === selected),
      c = claims.find((c) => c.id === review?.procurementId);
    const pending = reviews.filter((r) => r.status === "pending"),
      done = reviews.filter((r) => r.status === "reviewed");
    const list = (filter === "pending" ? pending : done).filter((r) =>
      (
        projectName(r.projectId) +
        " " +
        r.procurementId +
        " " +
        (claims.find((c) => c.id === r.procurementId)?.name || "")
      )
        .toLowerCase()
        .includes(query.toLowerCase()),
    );
    return (
      <div className="casework">
        <div className="notice">
          <ShieldCheck size={18} />
          {t(
            "AI 仅提供演示风险提示；管理员必须查看证明并填写人工审核意见。审计通过不自动执行资金操作。",
          )}
        </div>
        {messages}
        {tabs(pending.length, done.length)}
        <label>
          {t("搜索项目或采购单")}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("输入项目名称或采购编号")}
          />
        </label>
        <section className="panel">
          <div className="panel-title">
            <h2>{t(filter === "pending" ? "待审计项目" : "已审计项目")}</h2>
          </div>
          {list.length ? (
            list
              .slice()
              .reverse()
              .map((r) => (
                <div className="record-row" key={r.id}>
                  <ClipboardCheck size={20} />
                  <div className="record-main">
                    <strong>{projectName(r.projectId)}</strong>
                    <small>
                      {r.procurementId} ·{" "}
                      {t(
                        r.stage === "purchase" ? "采购前审计" : "交付证明审计",
                      )}{" "}
                      · {date(r.createdAt)}
                    </small>
                    <span>
                      {t(
                        r.legacy
                          ? "历史演示记录"
                          : r.decision
                            ? decisions[r.decision]
                            : "等待人工审计",
                      )}
                    </span>
                  </div>
                  <button
                    onClick={() => {
                      setSelected(r.id);
                      setReason("");
                      setDecision(r.risk >= 80 ? "more_info" : "approved");
                    }}
                  >
                    {t(
                      r.status === "pending" ? "开始人工审计" : "查看审计结果",
                    )}
                  </button>
                </div>
              ))
          ) : (
            <p className="empty">{t("此分类暂无记录")}</p>
          )}
        </section>
        {review && c && (
          <section className="panel review-detail">
            <div className="panel-title">
              <h2>{t("人工审计详情")}</h2>
              <span>{review.id}</span>
            </div>
            <h3>
              {projectName(review.projectId)} · {t(c.name)}
            </h3>
            <div className="proof-progress">
              <span>
                {t(review.stage === "purchase" ? "采购前审计" : "交付证明审计")}
              </span>
              <span>
                AI · {t("演示结果")} · {review.risk}/100
              </span>
            </div>
            <p>{t("本次审计的凭证编号")}</p>
            <code className="hash-value">{review.sourceHash}</code>
            {review.sourceHash !== c.hash && (
              <p className="notice">{t("这是历史版本，当前单据已有更新。")}</p>
            )}
            {review.stage === "delivery" && review.sourceHash === c.hash && (
              <>
                <p>
                  <b>{t("基金会说明")}：</b>
                  {c.foundationProof?.note || "—"}
                </p>
                <p>
                  <b>{t("受捐机构说明")}：</b>
                  {c.recipientProof?.note || "—"}
                </p>
              </>
            )}
            <dl className="audit-facts">
              <dt>{t("数量 × 单价")}</dt>
              <dd>
                {c.quantity} × {formatMoney(c.unitPrice, locale)} mHKD
              </dd>
              <dt>{t("供应商")}</dt>
              <dd>{c.vendorId}</dd>
            </dl>
            {review.risk >= 80 && (
              <p className="error">
                {t("高风险冻结项目不能直接通过，请要求补件并重新审核")}
              </p>
            )}
            {files(c, review.evidenceIds)}
            {review.status === "pending" && canAct(user, "reviewCase") ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void action("reviewCase", {
                    reviewId: review.id,
                    decision,
                    reason,
                  });
                }}
              >
                <label>
                  {t("人工审计结论")}
                  <select
                    value={decision}
                    onChange={(e) => setDecision(e.target.value)}
                  >
                    <option value="approved" disabled={review.risk >= 80}>
                      {t("审计通过")}
                    </option>
                    <option value="rejected">{t("审计未通过")}</option>
                    <option value="more_info">{t("要求补充资料")}</option>
                  </select>
                </label>
                <label>
                  {t("审核意见")}
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    required
                    maxLength={1000}
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || review.sourceHash !== c.hash}
                >
                  {t("保存人工审计结论")}
                </button>
              </form>
            ) : (
              <dl>
                <dt>{t("人工审计结论")}</dt>
                <dd>
                  {t(
                    review.decision
                      ? decisions[review.decision]
                      : "等待人工审计",
                  )}
                </dd>
                <dt>{t("审核意见")}</dt>
                <dd>{t(review.reason || "—")}</dd>
                <dt>{t("处理人及时间")}</dt>
                <dd>
                  {review.reviewerId || "—"} · {date(review.reviewedAt)}
                </dd>
              </dl>
            )}
          </section>
        )}
        <details className="panel activity-log">
          <summary>{t("操作日志")}</summary>
          {data.logs?.slice(0, 50).map((log) => (
            <p key={log.id}>
              <b>{t(log.action)}</b> · {log.actorName} · {log.target} ·{" "}
              {date(log.at)}
            </p>
          ))}
        </details>
      </div>
    );
  }
  const canAppeal = canAct(
    user,
    foundation ? "foundationAppeal" : "recipientAppeal",
  );
  const eligible = reviews.filter(
    (r) =>
      r.status === "reviewed" &&
      r.decision !== "approved" &&
      !appeals.some((a) => a.reviewId === r.id && a.status === "pending") &&
      reviews
        .filter(
          (x) => x.procurementId === r.procurementId && x.stage === r.stage,
        )
        .at(-1)?.id === r.id,
  );
  const list = appeals.filter((a) =>
      filter === "pending" ? a.status === "pending" : a.status === "resolved",
    ),
    appeal = appeals.find((a) => a.id === selected);
  return (
    <div className="casework">
      {messages}
      {tabs(
        appeals.filter((a) => a.status === "pending").length,
        appeals.filter((a) => a.status === "resolved").length,
      )}
      {canAppeal && (
        <section className="panel">
          <h2>{t("提交申诉")}</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void action(foundation ? "foundationAppeal" : "recipientAppeal", {
                reviewId: appealReview,
                reason: f.get("reason"),
              });
            }}
          >
            <label>
              {t("选择需要申诉的审计")}
              <select
                required
                value={appealReview}
                onChange={(e) => setAppealReview(e.target.value)}
              >
                <option value="">{t("请选择审计记录")}</option>
                {eligible.map((r) => (
                  <option key={r.id} value={r.id}>
                    {projectName(r.projectId)} · {r.procurementId} · {r.id} ·{" "}
                    {t(decisions[r.decision!])}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("申诉理由")}
              <textarea name="reason" required maxLength={1500} />
            </label>
            <button className="primary" disabled={busy || !appealReview}>
              {t("提交申诉")}
            </button>
          </form>
        </section>
      )}
      <section className="panel">
        <h2>{t(filter === "pending" ? "未处理申诉" : "已处理申诉")}</h2>
        {list.length ? (
          list.map((a) => (
            <div className="record-row" key={a.id}>
              <FileText size={20} />
              <div className="record-main">
                <strong>{projectName(a.projectId)}</strong>
                <small>
                  {a.id} · {a.procurementId} · {date(a.createdAt)}
                </small>
                <span>
                  {t(
                    a.resolution === "accepted"
                      ? "已受理并重新审计"
                      : a.resolution === "rejected"
                        ? "申诉已驳回"
                        : "等待处理",
                  )}
                </span>
              </div>
              <button
                onClick={() => {
                  setSelected(a.id);
                  setReason("");
                  setDecision("accepted");
                }}
              >
                {t(
                  a.status === "pending" && canAct(user, "resolveAppeal")
                    ? "处理申诉"
                    : "查看详情",
                )}
              </button>
            </div>
          ))
        ) : (
          <p className="empty">{t("此分类暂无记录")}</p>
        )}
      </section>
      {appeal && (
        <section className="panel">
          <h2>{t("申诉详情")}</h2>
          <p>{appeal.reason}</p>
          <p>{t("凭证编号")}</p>
          <code className="hash-value">{appeal.hash}</code>
          {appeal.status === "pending" && canAct(user, "resolveAppeal") ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action("resolveAppeal", {
                  appealId: appeal.id,
                  resolution: decision,
                  response: reason,
                });
              }}
            >
              <label>
                {t("处理结果")}
                <select
                  value={decision}
                  onChange={(e) => setDecision(e.target.value)}
                >
                  <option value="accepted">{t("受理并重新审计")}</option>
                  <option value="rejected">{t("驳回申诉")}</option>
                </select>
              </label>
              <label>
                {t("处理意见")}
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  maxLength={1500}
                />
              </label>
              <button className="primary" disabled={busy}>
                {t("保存申诉处理结果")}
              </button>
            </form>
          ) : (
            <>
              <p>
                <b>{t("处理意见")}：</b>
                {appeal.response || t("等待处理")}
              </p>
              <p>
                {appeal.reviewerId || "—"} · {date(appeal.resolvedAt)}
              </p>
            </>
          )}
        </section>
      )}
    </div>
  );
}
