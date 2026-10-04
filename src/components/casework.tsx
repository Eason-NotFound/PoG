"use client";
import { useEffect, useState, useRef } from "react";
import { FileText, Upload, ShieldCheck, ClipboardCheck } from "lucide-react";
import { canAct, type AccessUser, type Action } from "@/lib/access";
import type { PageData, Procurement, ReviewCase } from "@/lib/types";
import { formatDate, formatMoney } from "@/lib/i18n";
import { useI18n } from "./language-provider";
import AiDiagnosticPanel, {
  AiDiagnosticReportPanel,
} from "./ai-diagnostic-panel";
import OfflineAiPanel from "./offline-ai-panel";
import {
  portalA2Action,
  portalA2Allowed,
  portalA2Upload,
  type PortalA2State,
} from "@/lib/portal-a2";
import { fullDemoAmount } from "@/lib/full-demo-ui";
import {
  maySignRequest,
  formatAtomic,
  type A2SigningRequest,
} from "@/lib/a2-workbench";

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
const authorizationDeclarations: Partial<
  Record<A2SigningRequest["kind"], string>
> = {
  receipt: "本人已核对实际收货证据",
  reserve: "独立人工预留批准；不自动执行资金",
  release: "独立人工放款批准；不自动执行资金",
  settlement: "独立人工结算批准；不自动执行资金",
};
/** The original casework panels and controls, backed by immutable A2 facts.
 * Each authorization step remains a separate explicit click by its own role.
 */
function ConnectedCasework({
  mode,
  data,
  user,
  integration,
  refresh,
  claimId,
  foundationView,
}: {
  mode: "proof" | "audit" | "appeals";
  data: PageData;
  user: AccessUser;
  integration: PortalA2State;
  refresh: () => Promise<void>;
  claimId?: string;
  foundationView: boolean;
}) {
  const { t, locale } = useI18n();
  const [selected, setSelected] = useState(claimId || "");
  const [filter, setFilter] = useState("pending");
  const [query, setQuery] = useState("");
  const [fileKind, setFileKind] = useState(
    foundationView ? "purchase_order" : "receipt_evidence",
  );
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [liveDiagnosticAvailable, setLiveDiagnosticAvailable] = useState(false);
  const [offlineDemoAvailable, setOfflineDemoAvailable] = useState(false);
  const [request, setRequest] = useState<A2SigningRequest | null>(null);
  const [consent, setConsent] = useState(false);
  const [retry, setRetry] = useState<{
    id: string;
    body: Record<string, unknown>;
    key: string;
    scope: string;
  } | null>(null);
  const scope = `${integration.currentUser.id}:${integration.currentUser.role}:${integration.currentUser.walletAddress.toLowerCase()}:${integration.context.binding.namespaceId}:${integration.context.binding.runId}:${integration.context.binding.instanceId}`;
  const revision = useRef(0);
  useEffect(() => {
    revision.current += 1;
    setRequest(null);
    setConsent(false);
    setSelected(claimId || "");
    setLiveDiagnosticAvailable(false);
    setOfflineDemoAvailable(false);
  }, [scope, claimId]);
  const claims = data.procurements || [];
  const claim = claims.find((c) => c.id === selected);
  const proc = integration.rawProcurements.find((c) => c.id === selected);
  const workspace = integration.workspaces[selected];
  const documents = workspace?.documentVersions || [];
  const human = integration.currentUser.role === "human_approver";
  const recipient = integration.currentUser.role === "recipient";
  const kind: A2SigningRequest["kind"] = recipient
    ? "receipt"
    : [
          "settlement_recorded",
          "settlement_approval_pending",
          "payment_confirmed",
        ].includes(proc?.chainState.status || "")
      ? "settlement"
      : [
            "receipt_confirmed",
            "final_assessed",
            "release_approval_pending",
            "funds_released",
          ].includes(proc?.chainState.status || "")
        ? "release"
        : "reserve";
  const ownRequests = integration.signingRequests.filter(
    (r) =>
      r.procurementId === selected &&
      r.kind === kind &&
      r.signer.toLowerCase() ===
        integration.currentUser.walletAddress.toLowerCase(),
  );
  const material =
    request &&
    request.procurementId === selected &&
    request.kind === kind &&
    request.signer.toLowerCase() ===
      integration.currentUser.walletAddress.toLowerCase()
      ? ownRequests.find((item) => item.id === request.id) || request
      : ownRequests.find((r) => ["prepared", "signed"].includes(r.status)) ||
        ownRequests.at(-1) ||
        null;
  useEffect(() => {
    setConsent(false);
  }, [scope, selected, kind, material?.id, material?.status]);
  const prepareStages: Partial<Record<A2SigningRequest["kind"], string[]>> = {
    reserve: ["pre_assessed", "reserve_approval_pending"],
    receipt: ["invoice_recorded"],
    release: ["final_assessed", "release_approval_pending"],
    settlement: ["settlement_recorded", "settlement_approval_pending"],
  };
  const permitted = (id: string) =>
    (id !== "authorization.prepare" ||
      (proc?.chainState.verified === true &&
        workspace?.procurementId === proc.id &&
        workspace.chainState === proc.chainState.status &&
        prepareStages[kind]?.includes(proc.chainState.status) === true)) &&
    portalA2Allowed(
      material &&
        !integration.signingRequests.some((item) => item.id === material.id) &&
        maySignRequest(integration.currentUser, material)
        ? {
            ...integration,
            signingRequests: [material, ...integration.signingRequests],
          }
        : integration,
      id,
      {
        procurementId: selected,
        projectId: proc?.projectId,
        kind,
        requestId: material?.id,
      },
    ).enabled;
  const blocked = busy || Boolean(retry);
  function select(id: string) {
    if (blocked) return;
    revision.current++;
    setSelected(id);
    setLiveDiagnosticAvailable(false);
    setOfflineDemoAvailable(false);
    setRequest(null);
    setConsent(false);
    setReason("");
    setError("");
    setNotice("");
  }
  async function run(
    id: string,
    body: Record<string, unknown>,
    sameKey = false,
  ) {
    if (busy || (retry && !sameKey)) return;
    const pending =
      sameKey && retry ? retry : { id, body, key: crypto.randomUUID(), scope };
    if (pending.scope !== scope) {
      setError("身份或部署已改变；旧请求不能提交到新的实例。");
      return;
    }
    const current = revision.current;
    setBusy(true);
    setError("");
    try {
      const result = await portalA2Action(
        pending.id,
        pending.body,
        pending.key,
        { expectedState: integration },
      );
      setRetry(null);
      if (current === revision.current) {
        if (result.signingRequest)
          setRequest(result.signingRequest as A2SigningRequest);
        setConsent(false);
        setNotice(
          "接口已受理；签署不广播，提交后须等待 canonical operation 确认。没有自动审批。",
        );
      }
      await refresh();
    } catch (problem) {
      if (
        (problem as { status?: number })?.status === 0 ||
        (problem as { status?: number })?.status === 504
      )
        setRetry(pending);
      setError(problem instanceof Error ? problem.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  async function upload(file?: File) {
    if (!file || !selected || blocked) return;
    setBusy(true);
    setError("");
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error("原件最多 2 MB");
      await portalA2Upload(selected, fileKind, file, {
        expectedState: integration,
      });
      setNotice("原始不可变版本已保存；上传不等于链上登记或收货确认。");
      await refresh();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }
  function version(category: string, name: string) {
    return (
      <label key={name}>
        {t(category)}
        <select name={name} required disabled={blocked} defaultValue="">
          <option value="">{t("选择真实文件版本")}</option>
          {documents
            .filter((doc) => doc.category === category)
            .map((doc) => (
              <option value={doc.versionId} key={doc.versionId}>
                {doc.originalFilename} · {doc.versionId.slice(0, 8)}
              </option>
            ))}
        </select>
      </label>
    );
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
      {retry && (
        <button
          disabled={busy}
          onClick={() => void run(retry.id, retry.body, true)}
        >
          {t("结果未知：同一请求重试，不创建新交易")}
        </button>
      )}
    </>
  );
  const files = (
    <div className="evidence-list">
      {documents.map((doc) => (
        <article key={doc.versionId} className="evidence-item">
          <div className="section-line">
            <strong>{doc.originalFilename}</strong>
            <span className="pill">{t(doc.category)}</span>
          </div>
          <small>
            {t("不可变版本")}: {doc.versionId}
          </small>
          <code className="hash-value">{doc.keccak256}</code>
          <a
            className="button"
            href={`/api/a2/document-versions/${doc.versionId}/content`}
          >
            {t("查看或下载证明")}
          </a>
        </article>
      ))}
    </div>
  );
  const authorizations =
    (human || recipient) && proc ? (
      <>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const f = new FormData(event.currentTarget);
            const body: Record<string, unknown> = {
              procurementId: selected,
              kind,
              deadlineTtlSeconds: 900,
            };
            try {
              if (kind === "reserve")
                body.reserveAmountAtomic = fullDemoAmount(
                  String(f.get("reserveAmount")),
                ).amountAtomic;
              if (kind === "receipt")
                body.receiptEvidenceDocumentVersionId = String(
                  f.get("receiptEvidence"),
                );
              void run("authorization.prepare", body);
            } catch (problem) {
              setError(problem instanceof Error ? problem.message : "输入无效");
            }
          }}
        >
          <label>
            {t("人工审计结论 / 收货声明")}
            <input
              readOnly
              value={t(authorizationDeclarations[kind] || kind)}
            />
          </label>
          {kind === "reserve" && (
            <label>
              {t("批准预留金额 · mHKD")}
              <input
                name="reserveAmount"
                type="number"
                min="0.01"
                step="0.01"
                required
                defaultValue={formatAtomic(proc.budgetCapAtomic)}
                disabled={blocked}
              />
            </label>
          )}
          {kind === "receipt" && version("receipt_evidence", "receiptEvidence")}
          <label>
            {t("审核 / 验收意见")}
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              required
              maxLength={1000}
              disabled={blocked}
            />
          </label>
          <p className="tiny muted">
            {t(
              "意见仅供本次核对；授权材料使用后台冻结 V2 typed data，有效期 900 秒。AI 未接入；synthetic 样例仅验证机械链路。",
            )}
          </p>
          <button
            className="primary"
            disabled={
              blocked ||
              !permitted("authorization.prepare") ||
              Boolean(
                material && ["prepared", "signed"].includes(material.status),
              )
            }
          >
            {t("1. 准备本身份授权材料")}
          </button>
        </form>
        {ownRequests.length > 0 && (
          <label>
            {t("恢复本人授权")}
            <select
              value={material?.id || ""}
              disabled={blocked}
              onChange={(event) => {
                setRequest(
                  ownRequests.find((r) => r.id === event.target.value) || null,
                );
                setConsent(false);
              }}
            >
              {ownRequests.map((r) => (
                <option key={r.id} value={r.id}>
                  {t(r.kind)} · {t(r.status)} · {r.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
        {material && (
          <>
            <p>
              {t(material.kind)} · {t(material.status)}
            </p>
            <code className="hash-value">{material.digest}</code>
            <p className="tiny muted">
              signer {material.signer} · nonce {material.nonce} · deadline{" "}
              {material.deadline}
            </p>
            <details>
              <summary>{t("核对完整 V2 授权材料")}</summary>
              <pre>{JSON.stringify(material.typedData, null, 2)}</pre>
            </details>
            <label className="check-label">
              <input
                type="checkbox"
                checked={consent}
                disabled={
                  blocked || !maySignRequest(integration.currentUser, material)
                }
                onChange={(event) => setConsent(event.target.checked)}
              />
              {t(
                "我已核对本人 signer、项目、采购、金额、证据、nonce 和有效期，明确确认下一次签署或提交。",
              )}
            </label>
            <div className="button-row">
              <button
                className="primary"
                disabled={
                  blocked ||
                  !consent ||
                  material.status !== "prepared" ||
                  !maySignRequest(integration.currentUser, material) ||
                  !permitted("authorization.demo.sign")
                }
                onClick={() =>
                  void run("authorization.demo.sign", {
                    requestId: material.id,
                    confirm: true,
                  })
                }
              >
                {t("2. 确认本人测试钱包签署（不广播）")}
              </button>
              <button
                className="primary"
                disabled={
                  blocked ||
                  !consent ||
                  material.status !== "signed" ||
                  !maySignRequest(integration.currentUser, material) ||
                  !permitted("authorization.saved.submit")
                }
                onClick={() =>
                  void run("authorization.saved.submit", {
                    requestId: material.id,
                    confirm: true,
                  })
                }
              >
                {t("3. 提交已签授权")}
              </button>
            </div>
          </>
        )}
      </>
    ) : null;
  if (mode === "appeals")
    return (
      <section className="panel casework">
        <div className="panel-title">
          <h2>{t("申诉记录")}</h2>
        </div>
        <p className="notice">
          {t("本轮主流程不支持申诉写入；未使用旧演示账本。")}
        </p>
      </section>
    );
  if (mode === "audit") {
    const reviewed = (c: Procurement) =>
      integration.procurementFacts[c.id]?.settlementConfirmed;
    const list = claims
      .filter((c) => (filter === "pending" ? !reviewed(c) : reviewed(c)))
      .filter((c) =>
        (c.name + c.id).toLowerCase().includes(query.toLowerCase()),
      );
    return (
      <div className="casework">
        <div className="notice">
          <ShieldCheck size={18} />
          {t(
            offlineDemoAvailable
              ? "Offline demo uses existing fictional sample reports for reference and explicitly requested synthetic on-chain assessments. No live inference. Independent human prepare, sign and submit remain separate actions; no automatic funds execution."
              : liveDiagnosticAvailable
                ? "真实 AI 诊断报告已取得，供独立人工参考；链上 AI 评估仍待接入。prepare、sign、submit 分开点击，不自动执行资金。"
                : "链上 AI 评估待接入，风险分数留空。独立人工必须核对原件与已取得的诊断报告；prepare、sign、submit 分开点击，不自动执行资金。",
          )}
        </div>
        {messages}
        <div className="case-tabs">
          <button
            className={filter === "pending" ? "primary" : ""}
            onClick={() => {
              if (!blocked) {
                setFilter("pending");
                select("");
              }
            }}
          >
            {t("待审计项目")} <b>{claims.filter((c) => !reviewed(c)).length}</b>
          </button>
          <button
            className={filter === "done" ? "primary" : ""}
            onClick={() => {
              if (!blocked) {
                setFilter("done");
                select("");
              }
            }}
          >
            {t("已审计项目")} <b>{claims.filter(reviewed).length}</b>
          </button>
        </div>
        <label>
          {t("搜索项目或采购单")}
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <section className="panel">
          <div className="panel-title">
            <h2>{t(filter === "pending" ? "待审计项目" : "已审计项目")}</h2>
          </div>
          {list.map((c) => (
            <div className="record-row" key={c.id}>
              <ClipboardCheck size={20} />
              <div className="record-main">
                <strong>
                  {data.projects?.find((p) => p.id === c.projectId)?.name ||
                    c.projectId}
                </strong>
                <small>
                  {c.id} · {t(integration.procurementFacts[c.id]?.statusText)}
                </small>
              </div>
              <button disabled={blocked} onClick={() => select(c.id)}>
                {t("开始人工审计")}
              </button>
            </div>
          ))}
        </section>
        {claim && (
          <section className="panel review-detail">
            <div className="panel-title">
              <h2>{t("人工审计详情")}</h2>
              <span>{claim.id}</span>
            </div>
            <h3>{claim.name}</h3>
            <div className="proof-progress">
              <span>{t(kind)}</span>
              <span>
                {offlineDemoAvailable ? (
                  "Offline demo · No live inference"
                ) : (
                  <>
                    AI ·{" "}
                    {t(
                      liveDiagnosticAvailable
                        ? "链上 AI 评估待接；实时诊断可用"
                        : "待接入",
                    )}{" "}
                    · —
                  </>
                )}
              </span>
            </div>
            {files}
            <OfflineAiPanel
              key={`offline:${scope}:${selected}`}
              integration={integration}
              procurementId={selected}
              readOnly
              onAvailable={setOfflineDemoAvailable}
            />
            {offlineDemoAvailable ? (
              <details>
                <summary>Optional live Qwen diagnostic reports</summary>
                <AiDiagnosticReportPanel
                  key={`${scope}:${selected}`}
                  integration={integration}
                  procurementId={selected}
                  onAvailable={setLiveDiagnosticAvailable}
                />
              </details>
            ) : (
              <AiDiagnosticReportPanel
                key={`${scope}:${selected}`}
                integration={integration}
                procurementId={selected}
                onAvailable={setLiveDiagnosticAvailable}
              />
            )}
            {authorizations}
          </section>
        )}
      </div>
    );
  }
  return (
    <section className="panel casework">
      <div className="panel-title">
        <h2>
          {t(foundationView ? "基金会采购与发货证明" : "受捐机构收货证明")}
        </h2>
        <Upload size={20} />
      </div>
      <p className="notice">
        {t(
          "资金保持冻结。采购单、发票与收货确认完成，最终独立人工审批后才释放给基金会。",
        )}
      </p>
      {messages}
      {!claimId && (
        <label>
          {t("选择项目采购单")}
          <select
            value={selected}
            disabled={blocked}
            onChange={(event) => select(event.target.value)}
          >
            <option value="">{t("请选择采购单")}</option>
            {claims.map((c) => (
              <option value={c.id} key={c.id}>
                {c.name} · {c.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {claim && proc && (
        <>
          <h3>
            {claim.name}
            <small>{claim.id}</small>
          </h3>
          <div className="proof-progress">
            <span>{t(integration.procurementFacts[claim.id]?.statusText)}</span>
            <span>
              {offlineDemoAvailable ? (
                "Offline demo · No live inference"
              ) : (
                <>
                  AI ·{" "}
                  {t(
                    liveDiagnosticAvailable
                      ? "链上 AI 评估待接；实时诊断可用"
                      : "待接入",
                  )}{" "}
                  · —
                </>
              )}
            </span>
          </div>
          {files}
          {foundationView && (
            <OfflineAiPanel
              key={`offline:${scope}:${selected}`}
              integration={integration}
              procurementId={selected}
              refresh={refresh}
              onAvailable={setOfflineDemoAvailable}
            />
          )}
          {foundationView &&
            (offlineDemoAvailable ? (
              <details>
                <summary>Optional live Qwen diagnostic</summary>
                <AiDiagnosticPanel
                  key={`${scope}:${selected}`}
                  integration={integration}
                  procurementId={selected}
                  onAvailable={setLiveDiagnosticAvailable}
                />
              </details>
            ) : (
              <AiDiagnosticPanel
                key={`${scope}:${selected}`}
                integration={integration}
                procurementId={selected}
                onAvailable={setLiveDiagnosticAvailable}
              />
            ))}
          <div className="upload-box">
            <label>
              {t("证明文件类型")}
              <select
                value={fileKind}
                disabled={blocked}
                onChange={(event) => setFileKind(event.target.value)}
              >
                {(foundationView
                  ? [
                      "purchase_order",
                      "request",
                      "goods_request",
                      "invoice",
                      "goods_evidence",
                    ]
                  : ["receipt_evidence"]
                ).map((category) => (
                  <option value={category} key={category}>
                    {t(category)}
                  </option>
                ))}
              </select>
            </label>
            <label className="upload-control">
              <Upload size={18} />
              {t("选择真实 PDF / JPG / PNG 原件，每个最多 2 MB")}
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                disabled={blocked || !permitted("document.upload")}
                onChange={(event) => {
                  void upload(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </label>
          </div>
          {foundationView ? (
            <>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const f = new FormData(event.currentTarget);
                  void run("procurement.po.record", {
                    procurementId: selected,
                    poDocumentVersionId: String(f.get("po")),
                    requestDocumentVersionId: String(f.get("request")),
                    goodsRequestDocumentVersionId: String(
                      f.get("goodsRequest"),
                    ),
                  });
                }}
              >
                {version("purchase_order", "po")}
                {version("request", "request")}
                {version("goods_request", "goodsRequest")}
                <button
                  className="primary"
                  disabled={blocked || !permitted("procurement.po.record")}
                >
                  {t("登记采购 PO 与两份独立需求原件")}
                </button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const f = new FormData(event.currentTarget);
                  try {
                    void run("procurement.invoice.record", {
                      procurementId: selected,
                      invoiceDocumentVersionId: String(f.get("invoice")),
                      goodsDocumentVersionId: String(f.get("goods")),
                      invoiceAmountAtomic: fullDemoAmount(
                        String(f.get("invoiceAmount")),
                      ).amountAtomic,
                    });
                  } catch (problem) {
                    setError(
                      problem instanceof Error ? problem.message : "金额无效",
                    );
                  }
                }}
              >
                {version("invoice", "invoice")}
                {version("goods_evidence", "goods")}
                <label>
                  {t("发票实际金额 · mHKD（不超过预留）")}
                  <input
                    name="invoiceAmount"
                    type="number"
                    min="0.01"
                    step="0.01"
                    required
                    disabled={blocked}
                  />
                </label>
                <button
                  className="primary"
                  disabled={blocked || !permitted("procurement.invoice.record")}
                >
                  {t("登记发票与货物证据")}
                </button>
              </form>
            </>
          ) : (
            authorizations
          )}
        </>
      )}
    </section>
  );
}
export default function Casework({
  mode,
  data,
  user,
  refresh,
  claimId,
  foundationView,
  integration,
}: {
  mode: "proof" | "audit" | "appeals";
  data: PageData;
  user: AccessUser;
  refresh: () => Promise<void>;
  claimId?: string;
  foundationView?: boolean;
  integration?: PortalA2State;
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
  if (integration)
    return (
      <ConnectedCasework
        mode={mode}
        data={data}
        user={user}
        integration={integration}
        refresh={refresh}
        claimId={claimId}
        foundationView={foundation}
      />
    );
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
    return data.projects?.find((p) => p.id === id)?.name || id;
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
                  {projectName(c.projectId)} · {c.name} · {c.id}
                </option>
              ))}
            </select>
          </label>
        )}
        {c && (
          <>
            <h3>
              {c.name} <small>{c.id}</small>
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
                <b>
                  {t("基金会说明")}
                  {locale === "en" ? ": " : "："}
                </b>
                {c.foundationProof.note}
              </p>
            )}
            {c.recipientProof && (
              <p>
                <b>
                  {t("受捐机构说明")}
                  {locale === "en" ? ": " : "："}
                </b>
                {c.recipientProof.note}
              </p>
            )}
            {latest?.reason && (
              <p className="notice">
                <b>
                  {t("审核意见")}
                  {locale === "en" ? ": " : "："}
                </b>
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
              {projectName(review.projectId)} · {c.name}
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
                  <b>
                    {t("基金会说明")}
                    {locale === "en" ? ": " : "："}
                  </b>
                  {c.foundationProof?.note || "—"}
                </p>
                <p>
                  <b>
                    {t("受捐机构说明")}
                    {locale === "en" ? ": " : "："}
                  </b>
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
                <dd>{review.reason || "—"}</dd>
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
                <b>
                  {t("处理意见")}
                  {locale === "en" ? ": " : "："}
                </b>
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
