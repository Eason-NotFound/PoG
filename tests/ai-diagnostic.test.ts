import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  AI_DIAGNOSTIC_INTERFACE,
  demoDiagnosticForm,
  diagnosticContext,
  diagnosticSources,
  diagnosticImageDocument,
  parseAiDiagnostic,
  parseAiDiagnosticList,
  type DiagnosticForm,
} from "../src/lib/ai-diagnostic";
import {
  AI_DIAGNOSTIC_ROUTES,
  buildDemoRoute,
  matchDemoRoute,
} from "../src/lib/demo-action-catalog";
import { A2_SESSION_COOKIE, handleA2Proxy } from "../src/lib/a2-proxy";
import { validAiDiagnosticReport } from "../src/lib/ai-diagnostic-content";
import type { PortalA2State } from "../src/lib/portal-a2";

const PROC = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";
const PO = "33333333-3333-4333-8333-333333333333";
const ID = "44444444-4444-4444-8444-444444444444";
const OP = "55555555-5555-4555-8555-555555555555";
const REQUEST = "66666666-6666-4666-8666-666666666666";
const GOODS = "77777777-7777-4777-8777-777777777777";
const HASH = `0x${"1".repeat(64)}`;
const TOKEN = "a".repeat(43);
const binding = {
  namespaceId: ID,
  runId: "run",
  instanceId: "instance",
  chainId: "31337",
  registry: `0x${"1".repeat(40)}`,
};
function state(): PortalA2State {
  return {
    currentUser: {
      id: ID,
      username: "foundation",
      role: "foundation",
      walletAddress: binding.registry,
    },
    context: { binding },
    rawProcurements: [
      {
        id: PROC,
        projectId: PROJECT,
        chainState: { status: "po_recorded", verified: true },
      },
    ],
    workspaces: {
      [PROC]: {
        procurementId: PROC,
        originalSourceVersionIds: {
          poDocumentVersionId: PO,
          requestDocumentVersionId: REQUEST,
          goodsRequestDocumentVersionId: GOODS,
        },
        documentVersions: [
          [PO, "purchase_order"],
          [REQUEST, "request"],
          [GOODS, "goods_request"],
        ].map(([versionId, category]) => ({
          versionId,
          category,
          procurementId: PROC,
          referenced: true,
          contentType: "image/png",
        })),
      },
    },
  } as unknown as PortalA2State;
}
const form: DiagnosticForm = {
  quantity: "1",
  quoteHKD: "80.00",
  unitPriceLimitHKD: "80.00",
  category: "electronics",
  periodStart: "2026-10-01",
  periodEnd: "2026-10-31",
  description: "User text stays unchanged",
};
function diagnostic(status = "queued"): Record<string, unknown> {
  const completed = status === "completed";
  return {
    id: ID,
    operationId: OP,
    projectId: PROJECT,
    procurementId: PROC,
    purchaseOrderVersionId: PO,
    binding,
    stage: 0,
    purpose: "diagnostic",
    signingEnabled: false,
    status,
    contextSource: "demo_generated",
    syntheticInput: true,
    modelReal: completed,
    realAI: completed,
    reportProvenance: completed ? "real_qwen_diagnostic" : null,
    inputHash: HASH,
    evidenceHash: HASH,
    reportHash: completed ? HASH : null,
    reportSha256: completed ? HASH : null,
    reportSizeBytes: completed ? 123 : null,
    contentType: completed ? "application/json" : null,
    errorCode: null,
    diagnosticContext: diagnosticContext(form),
    summary: completed
      ? {
          text: "Visible PO fields",
          outcome: 1,
          riskScoreBps: null,
          completeness: "incomplete",
          findings: [],
          missingInputs: ["FINAL documents"],
        }
      : null,
    provenance: {
      executionMode: completed ? "model_and_rules" : null,
      versions: completed
        ? {
            serviceVersion: "1",
            ruleSetVersion: "1",
            extractionSchemaVersion: "2",
            scorePolicyVersion: null,
            modelProvider: "local_transformers",
            modelId: "Qwen/Qwen3-VL-2B-Instruct",
            modelVersion: "pinned",
            promptVersion: "2.3",
          }
        : null,
      contextSource: "demo_generated_diagnostic_context",
      budgetSource: "registry_procurement_budget_cap",
      reviewScope: "single_po_visible_fields",
      allDocumentsReviewed: false,
    },
  };
}
test("diagnostic context keeps exact atomic values and raw user text", () => {
  assert.equal(diagnosticContext(form).quoteAmountAtomic, "80000000");
  assert.equal(
    diagnosticContext({ ...form, description: " human 原文 " }).description,
    " human 原文 ",
  );
  for (const change of [
    { quantity: "0" },
    { quantity: "01" },
    { quoteHKD: "0" },
    { quoteHKD: "1.001" },
    { periodStart: "2026-02-30" },
    { periodEnd: "2026-09-01" },
    { category: " " },
  ])
    assert.throws(() => diagnosticContext({ ...form, ...change }));
});
test("demo values are explicitly simulated and derived only from the confirmed cap", () => {
  const demo = demoDiagnosticForm("123450000");
  assert.equal(demo.quoteHKD, "123.45");
  assert.equal(demo.unitPriceLimitHKD, "123.45");
  assert.match(demo.description, /^SIMULATION ONLY/);
  assert.match(demo.description, /not extracted document facts/);
  assert.throws(() => demoDiagnosticForm("0"));
  assert.throws(() => demoDiagnosticForm("1"));
});
test("only three distinct chain-bound PNG/JPEG originals qualify", () => {
  const s = state();
  const docs = diagnosticSources(s, PROC)!;
  assert.equal(docs.length, 3);
  assert.equal(docs.every(diagnosticImageDocument), true);
  (docs[0] as unknown as { contentType: string }).contentType =
    "application/pdf";
  assert.equal(docs.every(diagnosticImageDocument), false);
  s.workspaces[PROC].documentVersions[1].referenced = false;
  assert.equal(diagnosticSources(s, PROC), null);
});
test("actual queued and validated Review/incomplete DTOs parse without fabricating risk", () => {
  const s = state();
  assert.equal(parseAiDiagnostic(diagnostic(), s, PROC, PO).modelReal, false);
  const completed = parseAiDiagnostic(diagnostic("completed"), s, PROC, PO);
  assert.equal(completed.summary?.riskScoreBps, null);
  assert.equal(completed.syntheticInput, true);
  assert.equal(completed.realAI, true);
  assert.equal(completed.provenance?.allDocumentsReviewed, false);
  const list = {
    interfaceId: AI_DIAGNOSTIC_INTERFACE,
    binding,
    signingEnabled: false,
    availability: { enabled: true, reasonCode: null },
    items: [diagnostic("completed")],
    nextCursor: null,
  };
  assert.equal(parseAiDiagnosticList(list, s, PROC).items.length, 1);
});
test("cross-scope, signatures, fabricated Pass, numeric risk and wrong provenance fail closed", () => {
  for (const change of [
    { binding: { ...binding, registry: `0x${"2".repeat(40)}` } },
    { binding: { ...binding, runId: "other" } },
    { procurementId: PROJECT },
    { signingEnabled: true },
    { stage: 1 },
    { purpose: "assessment" },
    { syntheticInput: false },
    { modelReal: false },
    { realAI: false },
    {
      summary: {
        text: "Pass",
        outcome: 0,
        riskScoreBps: 0,
        completeness: "complete",
        findings: [],
        missingInputs: [],
      },
    },
    { reportHash: null },
    { provenance: null },
  ])
    assert.throws(() =>
      parseAiDiagnostic(
        { ...diagnostic("completed"), ...change },
        state(),
        PROC,
        PO,
      ),
    );
  assert.throws(() => parseAiDiagnostic(diagnostic(), state(), PROC, PROJECT));
});
test("four exact private routes never prove chain confirmation or permit URL overrides", () => {
  assert.equal(AI_DIAGNOSTIC_ROUTES.length, 4);
  for (const route of AI_DIAGNOSTIC_ROUTES) {
    assert.equal(route.provesChainConfirmation, false);
    assert.equal(route.provesExecution, false);
    assert.equal(route.roles.includes("donor"), false);
    assert.equal(route.roles.includes("recipient"), false);
  }
  assert.equal(
    buildDemoRoute("ai.diagnostic.report", { diagnosticId: ID })?.path,
    `ai-diagnostics/${ID}/report`,
  );
  for (const path of [
    `ai-diagnostics/${ID}/report/other`,
    `ai-diagnostics/${ID}%2freport`,
    `ai-diagnostics/http://example.com/report`,
    `procurements/${PROC}/ai-diagnostics/execute`,
  ])
    assert.equal(matchDemoRoute("GET", path.split("/")), null);
  assert.equal(matchDemoRoute("POST", ["ai-diagnostics", ID, "report"]), null);
});
const env = {
  POG_A2_INTEGRATION: "true",
  POG_FULL_DEMO: "true",
  POG_AI_DIAGNOSTICS: "true",
  POG_A2_API_URL: "http://127.0.0.1:18081",
};
function req(
  path: string,
  method = "GET",
  body?: string,
  extra: Record<string, string> = {},
) {
  return new Request(`http://127.0.0.1:3102/api/a2/${path}`, {
    method,
    body,
    headers: {
      Host: "127.0.0.1:3102",
      Origin: "http://127.0.0.1:3102",
      Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
      ...(body
        ? {
            "Content-Type": "application/json",
            "Idempotency-Key": "same-diagnostic-key",
          }
        : {}),
      ...extra,
    },
  });
}
test("new routes require explicit opt-in, authentication and same-origin consent POST", async () => {
  const path = `procurements/${PROC}/ai-diagnostics`;
  const forbiddenFetch: typeof fetch = async () =>
    assert.fail("must not forward");
  for (const [request, settings, expected] of [
    [req(path), { ...env, POG_AI_DIAGNOSTICS: "false" }, 404],
    [req(path, "GET", undefined, { Cookie: "" }), env, 401],
    [req(path, "POST", "{}", { Origin: "http://attacker.example" }), env, 403],
    [req(`${path}?url=http://example.com`), env, 404],
  ] as const) {
    const response = await handleA2Proxy(request, path.split("/"), {
      env: settings,
      fetch: forbiddenFetch,
    });
    assert.equal(response.status, expected);
  }
});
test("canonical AI download preserves every Unicode and fractional byte instead of reserializing", async () => {
  const bytes = new TextEncoder().encode(
    '{"schemaVersion":"pog.ai.report/0.2-candidate","summary":"原文","findings":[{"amount":1.2300}]}',
  );
  const sha = `0x${createHash("sha256").update(bytes).digest("hex")}`;
  assert.equal(validAiDiagnosticReport(bytes, TOKEN, sha, HASH), true);
  const path = `ai-diagnostics/${ID}/report`;
  const response = await handleA2Proxy(req(path), path.split("/"), {
    env,
    fetch: async (url, init) => {
      assert.equal(String(url), `http://127.0.0.1:18081/v2/${path}`);
      assert.equal(
        new Headers(init?.headers).get("authorization"),
        `Bearer ${TOKEN}`,
      );
      return new Response(bytes, {
        headers: {
          "Content-Type": "application/json",
          "X-Report-SHA256": sha,
          "X-Report-Hash": HASH,
        },
      });
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
  assert.equal(response.headers.get("X-Report-SHA256"), sha);
  assert.match(response.headers.get("Content-Disposition")!, /attachment/);
  assert.equal(validAiDiagnosticReport(bytes, TOKEN, HASH, HASH), false);
  const unsafe = new TextEncoder().encode(
    '{"schemaVersion":"pog.ai.report/0.2-candidate","token":"private"}',
  );
  assert.equal(
    validAiDiagnosticReport(
      unsafe,
      TOKEN,
      `0x${createHash("sha256").update(unsafe).digest("hex")}`,
      HASH,
    ),
    false,
  );
});
