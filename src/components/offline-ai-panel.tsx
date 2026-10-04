"use client";
import { useEffect, useRef, useState } from "react";
import type { PortalA2State } from "@/lib/portal-a2";
import {
  loadOfflineAi,
  offlineScope,
  offlineStageReady,
  offlineDefaultStage,
  submitOfflineAi,
  type OfflineAi,
  type OfflineStage,
} from "@/lib/offline-ai";

/** Existing sample text and current technical fixtures stay visibly separate.
 * There is no model fallback hidden behind the live Qwen action. */
export default function OfflineAiPanel({
  integration,
  procurementId,
  readOnly = false,
  refresh,
  onAvailable,
}: {
  integration: PortalA2State;
  procurementId: string;
  readOnly?: boolean;
  refresh?: () => Promise<void>;
  onAvailable?: (available: boolean) => void;
}) {
  const [value, setValue] = useState<OfflineAi | null>(null);
  const [stage, setStage] = useState<OfflineStage>("PRE");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<{
    key: string;
    stage: OfflineStage;
    scope: string;
  } | null>(null);
  const stateRef = useRef(integration);
  stateRef.current = integration;
  const scope = offlineScope(integration, procurementId);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const revision = useRef(0);
  const canonicalStage = integration.rawProcurements.find(
    (p) => p.id === procurementId,
  )?.chainState.status;
  useEffect(
    () => {
      if (!busy && !pending) {
        setStage(offlineDefaultStage(stateRef.current, procurementId));
        setConsent(false);
      }
    },
    // A confirmed business-stage transition selects the relevant review report.
    // Manual report selection remains independent of polling and locale changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canonicalStage],
  );
  useEffect(() => {
    onAvailable?.(value?.availability.enabled === true);
  }, [scope, value?.availability.enabled, onAvailable]);
  useEffect(() => {
    let active = true;
    const current = ++revision.current;
    setValue(null);
    setConsent(false);
    setPending(null);
    setError("");
    setNotice("");
    setBusy(false);
    setStage(offlineDefaultStage(stateRef.current, procurementId));
    async function reload() {
      try {
        const next = await loadOfflineAi(stateRef.current, procurementId);
        if (active && current === revision.current) {
          setValue(next);
        }
      } catch (problem) {
        if (active && current === revision.current)
          setError(
            problem instanceof Error
              ? problem.message
              : "Offline demo is unavailable.",
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
    // Only identity, procurement and deployment changes discard local intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  const ready = offlineStageReady(integration, procurementId, stage);
  const sample = value?.reports[stage];
  const fixtures = value?.items.filter((item) => item.stage === stage) || [];
  const blocked = busy || Boolean(pending);
  async function run(retry = false) {
    if (readOnly || busy || (pending && !retry)) return;
    if (!retry && (!ready || !consent || value?.availability.enabled !== true))
      return;
    const intent =
      retry && pending ? pending : { key: crypto.randomUUID(), stage, scope };
    if (intent.scope !== scopeRef.current) {
      setError(
        "Identity or deployment changed. Reload before submitting a new intent.",
      );
      return;
    }
    const current = revision.current;
    setBusy(true);
    setConsent(false);
    setError("");
    setNotice("");
    try {
      const next = await submitOfflineAi(
        stateRef.current,
        procurementId,
        intent.stage,
        true,
        intent.key,
        {},
        retry,
      );
      if (current !== revision.current) return;
      setValue(next);
      setPending(null);
      setNotice(
        "The local AI fixture request was accepted. Wait for the confirmed chain stage; human approval and funds execution remain separate actions.",
      );
      await refresh?.();
    } catch (problem) {
      if (current !== revision.current) return;
      const status = (problem as { status?: number }).status;
      if (status === 0 || status === 502 || status === 504) setPending(intent);
      setError(
        problem instanceof Error
          ? problem.message
          : "Offline fixture request failed.",
      );
    } finally {
      if (current === revision.current) setBusy(false);
    }
  }
  return (
    <section className="panel review-detail">
      <div className="panel-title">
        <h3>Offline AI demo fallback</h3>
        <span className="pill">MOCK DEMO · No Qwen inference</span>
      </div>
      <p className="notice">
        Existing sample reports are from a fictional office-chair case. They are
        not model findings about this procurement or its uploaded evidence. No
        live inference is performed.
      </p>
      <p className="tiny muted">
        Selected procurement: {procurementId}. The current deployment and
        procurement IDs are supplied automatically; no UUID copying is required.
      </p>
      <label>
        Report stage
        <select
          value={stage}
          disabled={blocked}
          onChange={(event) => {
            setStage(event.target.value as OfflineStage);
            setConsent(false);
          }}
        >
          <option value="PRE">PRE · Purchase-order review sample</option>
          <option value="FINAL">
            FINAL · Invoice and receipt review sample
          </option>
        </select>
      </label>
      {sample && (
        <div className="risk-panel">
          <h3>Existing report sample · {stage}</h3>
          <p>{sample.title}</p>
          <p>
            Historical sample risk: {sample.sampleRiskScoreBps / 100}/100 · MOCK
            DEMO · Not the current on-chain score
          </p>
          <details>
            <summary>Read the existing report</summary>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                fontFamily: "inherit",
              }}
            >
              {sample.markdown}
            </pre>
          </details>
          <details>
            <summary>Sample provenance</summary>
            <p>
              Source: PoG Complete Dataset EN · fictional office-chair case ·
              historical reference, not current evidence
            </p>
            <p>Sample file SHA-256 (not the chain assessment report hash)</p>
            <code className="hash-value">{sample.sourceSha256}</code>
          </details>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {value?.availability.enabled !== true && (
        <p className="notice">
          Offline demo is not enabled ·{" "}
          {value?.availability.reasonCode || "Waiting for the local backend"}
        </p>
      )}
      {!readOnly && (
        <>
          <p className="notice">
            An explicit click below asks the independent local AI test identity
            to sign and submit a current synthetic technical fixture (100 bps =
            1/100). It does not sign for a human, confirm receipt, reserve
            funds, release funds or execute payment.
          </p>
          <label className="check-label">
            <input
              type="checkbox"
              checked={consent}
              disabled={
                blocked || !ready || value?.availability.enabled !== true
              }
              onChange={(event) => setConsent(event.target.checked)}
            />
            I understand that this is a local technical fixture, not Qwen
            inference. I request only the {stage} fixture for the selected
            procurement.
          </label>
          <button
            type="button"
            className="primary"
            disabled={
              blocked ||
              !ready ||
              !consent ||
              value?.availability.enabled !== true
            }
            onClick={() => void run()}
          >
            {busy
              ? "Submitting local fixture…"
              : `Submit offline ${stage} technical fixture`}
          </button>
          {!ready && (
            <p className="tiny muted">
              {stage === "PRE"
                ? "PRE requires a confirmed purchase order."
                : "FINAL requires the Recipient's confirmed receipt."}{" "}
              Already confirmed stages are not submitted again.
            </p>
          )}
          {pending && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(true)}
            >
              Outcome unknown: retry the same {pending.stage} request (same key)
            </button>
          )}
        </>
      )}
      {fixtures.map((item) => (
        <div className="risk-panel" key={item.requestId}>
          <h3>
            Current {item.stage} technical fixture · Synthetic, not a model
            report
          </h3>
          <p>
            Test risk: {item.technicalRiskScoreBps} bps (1/100) · Request:{" "}
            {item.status}
          </p>
          <p>
            Operation: {item.operation?.operationId || "Not submitted"} ·{" "}
            {item.operation?.status || "No operation"}
          </p>
          <p>
            Canonical procurement stage:{" "}
            {integration.rawProcurements.find((p) => p.id === procurementId)
              ?.chainState.status || "Unknown"}
          </p>
          <details>
            <summary>
              Current technical commitment (separate from the sample report)
            </summary>
            <p>Fixture marker report hash</p>
            <code className="hash-value">{item.technicalReportHash}</code>
            <p>Current evidence hash</p>
            <code className="hash-value">{item.evidenceHash}</code>
            <p>
              Nonce: {item.nonce} · Deadline: {item.deadline} · Request ID:{" "}
              {item.requestId}
            </p>
          </details>
        </div>
      ))}
      <p className="tiny muted">
        Independent human decisions, Recipient receipt and all existing contract
        checks remain required. A sample report or accepted HTTP request is not
        proof of approval, release or supplier payment.
      </p>
    </section>
  );
}
