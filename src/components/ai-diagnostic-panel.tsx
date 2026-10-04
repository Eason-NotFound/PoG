"use client";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "./language-provider";
import { formatAtomic } from "@/lib/a2-workbench";
import type { PortalA2State } from "@/lib/portal-a2";
import {
  EMPTY_DIAGNOSTIC_FORM,
  diagnosticContext,
  diagnosticSources,
  diagnosticImageDocument,
  demoDiagnosticForm,
  loadAiDiagnostics,
  runAiDiagnostic,
  type AiDiagnostic,
  type AiDiagnosticList,
  type DiagnosticContextSource,
  type DiagnosticForm,
} from "@/lib/ai-diagnostic";

export default function AiDiagnosticPanel({
  integration,
  procurementId,
  onAvailable,
}: {
  integration: PortalA2State;
  procurementId: string;
  onAvailable: (available: boolean) => void;
}) {
  const { t } = useI18n();
  const [poVersion, setPoVersion] = useState("");
  const [form, setForm] = useState<DiagnosticForm>({
    ...EMPTY_DIAGNOSTIC_FORM,
  });
  const [source, setSource] =
    useState<DiagnosticContextSource>("user_declared");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [list, setList] = useState<AiDiagnosticList | null>(null);
  const [requestId, setRequestId] = useState("");
  const [pending, setPending] = useState<{
    key: string;
    versionId: string;
    form: DiagnosticForm;
    source: DiagnosticContextSource;
    scope: string;
  } | null>(null);
  const stateRef = useRef(integration);
  stateRef.current = integration;
  const revision = useRef(0);
  const scope = diagnosticScope(integration, procurementId);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const documents =
    integration.workspaces[procurementId]?.documentVersions || [];
  const purchaseOrders = documents.filter(
    (d) => d.category === "purchase_order",
  );
  const proc = integration.rawProcurements.find((p) => p.id === procurementId);
  const preState =
    proc?.chainState.verified === true &&
    ["po_recorded", "pre_assessed", "reserve_approval_pending"].includes(
      proc.chainState.status,
    );
  const result =
    list?.items.find(
      (d) => d.id === requestId && d.purchaseOrderVersionId === poVersion,
    ) ||
    list?.items.find((d) => d.purchaseOrderVersionId === poVersion) ||
    null;
  const queued = list?.items.some(
    (d) => d.purchaseOrderVersionId === poVersion && d.status === "queued",
  );
  const doc = purchaseOrders.find((d) => d.versionId === poVersion);
  const sources = diagnosticSources(integration, procurementId);
  const supported = Boolean(
    doc &&
    sources?.some(
      (d) => d.versionId === poVersion && d.category === "purchase_order",
    ) &&
    sources.every(diagnosticImageDocument),
  );
  const blocked = busy || Boolean(pending);
  let validForm = false;
  try {
    diagnosticContext(form);
    validForm = true;
  } catch {
    /* Empty forms are deliberate. */
  }
  useEffect(() => {
    let active = true;
    const current = ++revision.current;
    setList(null);
    setRequestId("");
    setPoVersion("");
    setPending(null);
    setForm({ ...EMPTY_DIAGNOSTIC_FORM });
    setSource("user_declared");
    setConsent(false);
    setError("");
    onAvailable(false);
    async function reload() {
      try {
        const value = await loadAiDiagnostics(stateRef.current, procurementId);
        if (active && current === revision.current) setList(value);
      } catch (problem) {
        if (active && current === revision.current)
          setError(
            problem instanceof Error ? problem.message : "AI 诊断列表暂不可用",
          );
      }
    }
    void reload();
    const timer = setInterval(() => void reload(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
      revision.current++;
    };
    // Scope, not locale or a refreshed DTO object, owns draft input and diagnostics.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  useEffect(() => {
    onAvailable(
      Boolean(
        result &&
        result.status === "completed" &&
        result.realAI &&
        result.modelReal,
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, result?.id, result?.status]);
  function update(key: keyof DiagnosticForm, value: string) {
    if (blocked) return;
    setForm((f) => ({ ...f, [key]: value }));
    setConsent(false);
  }
  async function run(retry = false) {
    if (busy || (pending && !retry)) return;
    // Enter-key form submission must require the same explicit consent and
    // current document/capability gates as the visible Run button.
    if (
      !retry &&
      (!consent ||
        !validForm ||
        !supported ||
        !preState ||
        queued ||
        list?.availability.enabled !== true)
    )
      return;
    const intent =
      retry && pending
        ? pending
        : {
            key: crypto.randomUUID(),
            versionId: poVersion,
            form: { ...form },
            source,
            scope,
          };
    if (intent.scope !== scopeRef.current) {
      setError("身份或部署已改变；旧诊断不能提交到新的实例。");
      return;
    }
    const current = revision.current;
    setBusy(true);
    setError("");
    setConsent(false);
    try {
      const diagnostic = await runAiDiagnostic(
        stateRef.current,
        procurementId,
        intent.versionId,
        intent.form,
        intent.source,
        intent.key,
      );
      if (current !== revision.current) return;
      setPending(null);
      setRequestId(diagnostic.id);
      setList((value) =>
        value
          ? {
              ...value,
              items: [
                diagnostic,
                ...value.items.filter((item) => item.id !== diagnostic.id),
              ].slice(0, 20),
            }
          : null,
      );
    } catch (problem) {
      if (current !== revision.current) return;
      const failure = problem as { status?: number; code?: string };
      if (
        failure.status === 0 ||
        failure.status === 504 ||
        failure.status === 502
      )
        setPending(intent);
      setError(problem instanceof Error ? problem.message : "AI 诊断请求失败");
    } finally {
      if (current === revision.current) setBusy(false);
    }
  }
  const field = (key: keyof DiagnosticForm, label: string, type = "text") => (
    <label key={key}>
      {t(label)}
      <input
        type={type}
        value={form[key]}
        required
        disabled={blocked}
        min={
          type === "number" ? (key === "quantity" ? "1" : "0.01") : undefined
        }
        step={
          type === "number" ? (key === "quantity" ? "1" : "0.01") : undefined
        }
        maxLength={key === "category" ? 160 : undefined}
        onChange={(event) => update(key, event.target.value)}
      />
    </label>
  );
  return (
    <section className="panel review-detail">
      <div className="panel-title">
        <h2>{t("AI 文档诊断")}</h2>
        <span className="pill">{t("不自动审批")}</span>
      </div>
      <p className="notice">
        {t(
          "真实 Qwen 诊断独立于链上 AI 评估；不签署、不改变采购风险或阶段、不执行预留或放款。FINAL 诊断本轮不支持。",
        )}
      </p>
      <p className="tiny muted">
        {t("后台执行终点（浏览器不直接连接）")}:{" "}
        <code>http://192.168.0.246:18765/internal/v1/assess-bytes</code>
      </p>
      <p className="notice">
        {t(
          "将发送本采购链上绑定的 PO、request、goods_request 原件字节及明确声明的诊断上下文；后台再次核验原件版本与 Hash。不会上传到 GitHub。",
        )}
      </p>
      {sources && (
        <div className="evidence-list">
          {sources.map((d) => (
            <p key={d.versionId}>
              {t(d.category)} · {d.originalFilename} · {d.versionId}
              <br />
              <code className="hash-value">{d.keccak256}</code>
            </p>
          ))}
        </div>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <label>
          {t("选择确切采购单原件版本")}
          <select
            value={poVersion}
            disabled={blocked}
            required
            onChange={(event) => {
              setPoVersion(event.target.value);
              setRequestId("");
              setConsent(false);
              onAvailable(false);
            }}
          >
            <option value="">{t("选择真实文件版本")}</option>
            {purchaseOrders.map((d) => (
              <option key={d.versionId} value={d.versionId}>
                {d.originalFilename} · {d.versionId.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <p className="tiny muted">
          {t(
            "本轮三份链上绑定原件均须为 PNG / JPEG；PDF 诊断未接入。上传或选择文件不自动运行模型。",
          )}
        </p>
        <h3>{t("诊断上下文（不是项目或链上政策）")}</h3>
        <label>
          {t("上下文来源")}
          <select
            value={source}
            disabled={blocked}
            onChange={(event) => {
              setSource(event.target.value as DiagnosticContextSource);
              setForm({ ...EMPTY_DIAGNOSTIC_FORM });
              setConsent(false);
            }}
          >
            <option value="user_declared">{t("本人提供的诊断上下文")}</option>
            <option value="demo_generated">
              {t("明确模拟的演示诊断上下文")}
            </option>
          </select>
        </label>
        <p className="notice">
          {t(
            source === "demo_generated"
              ? "真实 Qwen 模型 · 模拟诊断上下文 · 不自动审批"
              : "以下字段由调用人填写，只用于本次独立诊断，不补写项目政策、采购单或签名材料。",
          )}
        </p>
        <button
          type="button"
          disabled={blocked || !proc?.chainState.verified}
          onClick={() => {
            try {
              if (!proc?.chainState.verified) return;
              setForm(demoDiagnosticForm(proc.budgetCapAtomic));
              setSource("demo_generated");
              setConsent(false);
              setError("");
            } catch (problem) {
              setError(
                problem instanceof Error
                  ? problem.message
                  : "无法生成演示诊断上下文",
              );
            }
          }}
        >
          {t("使用明确模拟的演示诊断值（不运行模型）")}
        </button>
        <p className="tiny muted">
          {t("后台预算来源：Registry 当前采购预算上限")} ·{" "}
          {proc ? formatAtomic(proc.budgetCapAtomic) : "—"} mHKD
        </p>
        <div className="form-grid">
          {field("quantity", "诊断数量", "number")}
          {field("quoteHKD", "报价总额 · HKD", "number")}
          {field("unitPriceLimitHKD", "诊断单价限额 · HKD", "number")}
          {field("category", "诊断类别")}
          {field("periodStart", "诊断期间开始", "date")}
          {field("periodEnd", "诊断期间结束", "date")}
          <label className="full">
            {t("诊断说明")}
            <textarea
              value={form.description}
              required
              maxLength={8192}
              disabled={blocked}
              onChange={(event) => update("description", event.target.value)}
            />
          </label>
        </div>
        <label className="check-label">
          <input
            type="checkbox"
            checked={consent}
            disabled={blocked}
            onChange={(event) => setConsent(event.target.checked)}
          />
          {t(
            "我确认上述局域网终点、三份原件版本及上下文来源，明确请求一次独立模型诊断；这不会自动审批或生成链上 AI 签名。",
          )}
        </label>
        <button
          className="primary"
          disabled={
            blocked ||
            queued ||
            !consent ||
            !validForm ||
            !supported ||
            !preState ||
            list?.availability.enabled !== true
          }
        >
          {t(busy ? "正在请求诊断…" : "运行 AI 文档诊断")}
        </button>
      </form>
      {list?.availability.enabled !== true && (
        <p className="notice">
          {t("诊断接口暂不可用")} ·{" "}
          {list?.availability.reasonCode || t("等待后台诊断能力")}
        </p>
      )}
      {poVersion && !supported && (
        <p className="notice">
          {t(
            "请选择当前链上绑定的 PO，且三份原件的后台 MIME 均为 PNG / JPEG；未运行模型。",
          )}
        </p>
      )}
      {!preState && (
        <p className="notice">
          {t("此采购不在已确认的 PRE 诊断阶段；既有报告仍可供独立人工阅读。")}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {t(error)}
        </p>
      )}
      {pending && (
        <button disabled={busy} onClick={() => void run(true)}>
          {t("结果未知：同一诊断请求重试，不创建新请求")}
        </button>
      )}
      {result && <DiagnosticResult value={result} />}
    </section>
  );
}

/** Independent humans can read the same private, immutable report. No run/sign action. */
export function AiDiagnosticReportPanel({
  integration,
  procurementId,
  onAvailable,
}: {
  integration: PortalA2State;
  procurementId: string;
  onAvailable: (available: boolean) => void;
}) {
  const { t } = useI18n();
  const [list, setList] = useState<AiDiagnosticList | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const stateRef = useRef(integration);
  stateRef.current = integration;
  const scope = diagnosticScope(integration, procurementId);
  const result = selected
    ? list?.items.find((item) => item.id === selected)
    : list?.items[0];
  useEffect(() => {
    let active = true;
    setList(null);
    setSelected("");
    setError("");
    onAvailable(false);
    async function reload() {
      try {
        const value = await loadAiDiagnostics(stateRef.current, procurementId);
        if (active) setList(value);
      } catch (problem) {
        if (active)
          setError(
            problem instanceof Error ? problem.message : "AI 诊断列表暂不可用",
          );
      }
    }
    void reload();
    const timer = setInterval(() => void reload(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // Scope changes discard old namespaces; locale never alters report or human input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  useEffect(() => {
    onAvailable(
      Boolean(
        result?.status === "completed" && result.realAI && result.modelReal,
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, result?.id, result?.status]);
  return (
    <section className="panel review-detail">
      <div className="panel-title">
        <h3>{t("真实 AI 报告 · 供独立人工参考")}</h3>
      </div>
      <p className="notice">
        {t(
          "报告不会自动批准资金。此处只读单份 PO 诊断，不是 FINAL 发票、货物或收货评估；原审批门控仍生效。",
        )}
      </p>
      {error && (
        <p className="error" role="alert">
          {t(error)}
        </p>
      )}
      {list?.items.length ? (
        <>
          <label>
            {t("选择本采购的诊断报告")}
            <select
              value={result?.id || ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              {list.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {t(item.status)} · {item.purchaseOrderVersionId.slice(0, 8)} ·{" "}
                  {item.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          {result && <DiagnosticResult value={result} />}
        </>
      ) : (
        <p role="status">{t("真实报告尚未取得，不能推断 AI 已完成。")}</p>
      )}
    </section>
  );
}

function DiagnosticResult({ value }: { value: AiDiagnostic }) {
  const { t } = useI18n();
  return (
    <div className="risk-panel">
      <h3>{t("诊断结果（不是人工审批）")}</h3>
      <p>
        {t(value.status)} · {value.id}
      </p>
      <p>
        {t("采购单原件版本")} · {value.purchaseOrderVersionId}
      </p>
      <p>
        {t("独立诊断 operation")} · {value.operationId}
      </p>
      <p>
        {t(value.modelReal ? "真实模型执行" : "尚未验证模型完成")} ·{" "}
        {t(
          value.contextSource === "demo_generated"
            ? "模拟诊断上下文"
            : "调用人声明上下文",
        )}
      </p>
      {value.provenance?.versions && (
        <p>
          {value.provenance.versions.modelProvider} ·{" "}
          {value.provenance.versions.modelId} ·{" "}
          {value.provenance.versions.modelVersion}
        </p>
      )}
      <details>
        <summary>{t("诊断上下文原值及来源")}</summary>
        <pre>
          {JSON.stringify(
            {
              contextSource: value.contextSource,
              syntheticInput: value.syntheticInput,
              diagnosticContext: value.diagnosticContext,
            },
            null,
            2,
          )}
        </pre>
      </details>
      {value.summary && (
        <>
          <p>{t("结论：Review / incomplete，不表示通过")}</p>
          <p>{t("风险分数")} · —</p>
          <p>{value.summary.text}</p>
          <details open>
            <summary>{t("抽取字段与发现")}</summary>
            <pre>{JSON.stringify(value.summary.findings, null, 2)}</pre>
          </details>
          <details open>
            <summary>{t("缺失资料")}</summary>
            <pre>{JSON.stringify(value.summary.missingInputs, null, 2)}</pre>
          </details>
        </>
      )}
      {value.errorCode && (
        <p className="error" role="alert">
          {value.errorCode}
        </p>
      )}
      {value.reportHash && (
        <>
          <small>{t("原始报告 Keccak-256")}</small>
          <code className="hash-value">{value.reportHash}</code>
        </>
      )}
      {value.reportSha256 && (
        <>
          <small>SHA-256</small>
          <code className="hash-value">{value.reportSha256}</code>
        </>
      )}
      {value.status === "completed" && value.reportHash && (
        <a
          className="button"
          href={`/api/a2/ai-diagnostics/${value.id}/report`}
        >
          {t("下载原始诊断报告")}
        </a>
      )}
      <p className="tiny muted">
        {t(
          "报告供独立人工参考；不自动审批，不替代链上 AI 签名、人工票或资金释放证明。",
        )}
      </p>
    </div>
  );
}

function diagnosticScope(state: PortalA2State, procurementId: string): string {
  const b = state.context.binding;
  return [
    state.currentUser.id,
    state.currentUser.role,
    state.currentUser.walletAddress,
    b.namespaceId,
    b.runId,
    b.instanceId,
    b.chainId,
    b.registry,
    procurementId,
  ].join(":");
}
