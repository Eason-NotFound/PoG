import { test } from "node:test";
import assert from "node:assert/strict";
import {
  OFFLINE_AI_INTERFACE,
  offlineDefaultStage,
  offlineScope,
  offlineStageReady,
  parseOfflineAi,
  submitOfflineAi,
} from "../src/lib/offline-ai";
import {
  OFFLINE_AI_ROUTES,
  buildDemoRoute,
  matchDemoRoute,
} from "../src/lib/demo-action-catalog";
import { A2_SESSION_COOKIE, handleA2Proxy } from "../src/lib/a2-proxy";
import type { PortalA2State } from "../src/lib/portal-a2";

const PROC = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";
const REQUEST = "33333333-3333-4333-8333-333333333333";
const OP = "44444444-4444-4444-8444-444444444444";
const HASH = `0x${"1".repeat(64)}`;
const TOKEN = "a".repeat(43);
const binding = {
  namespaceId: PROJECT,
  runId: "run",
  instanceId: HASH,
  chainId: "31337",
  registry: `0x${"1".repeat(40)}`,
};
function state(role = "foundation", status = "po_recorded"): PortalA2State {
  return {
    currentUser: { id: OP, role, walletAddress: binding.registry },
    context: { binding },
    rawProcurements: [
      {
        id: PROC,
        projectId: PROJECT,
        businessId: HASH,
        chainState: { verified: true, status },
      },
    ],
    workspaces: { [PROC]: { procurementId: PROC, chainState: status } },
  } as unknown as PortalA2State;
}
function payload() {
  const sample = (stage: "PRE" | "FINAL") => ({
    title: `${stage} office-chair sample`,
    sampleRiskScoreBps: stage === "PRE" ? 1600 : 1000,
    markdown: "MOCK DEMO — historical fictional report. NOT_RUN.",
    provenance: "mock_demo",
    sourceSha256: HASH,
    context: "historical_reference_not_current_evidence",
  });
  return {
    interfaceId: OFFLINE_AI_INTERFACE,
    binding: { ...binding },
    availability: { enabled: true, reasonCode: null },
    procurement: {
      id: PROC,
      projectId: PROJECT,
      businessId: HASH,
      sourceVersionIds: {},
    },
    provenance: "mock_demo",
    actualModelExecuted: false,
    reports: { PRE: sample("PRE"), FINAL: sample("FINAL") },
    items: [
      {
        stage: "PRE",
        requestId: REQUEST,
        status: "queued",
        synthetic: true,
        technicalRiskScoreBps: 100,
        technicalReportHash: HASH,
        evidenceHash: HASH,
        nonce: "0",
        deadline: "2000000000",
        operation: {
          operationId: OP,
          status: "queued",
          operationKind: "ai_pre",
          chainVerified: false,
        },
      },
    ],
  };
}
test("offline DTO keeps 16/10 historical scores separate from 100 bps technical fixture", () => {
  const value = parseOfflineAi(payload(), state(), PROC);
  assert.equal(value.reports.PRE.sampleRiskScoreBps, 1600);
  assert.equal(value.reports.FINAL.sampleRiskScoreBps, 1000);
  assert.equal(value.items[0].technicalRiskScoreBps, 100);
  assert.match(value.reports.PRE.markdown, /NOT_RUN/);
  assert.equal(value.actualModelExecuted, false);
  assert.equal(value.items[0].operation?.chainVerified, false);
});
test("different deployment, current-evidence relabelling and real-model claims fail closed", () => {
  for (const change of [
    { binding: { ...binding, runId: "different" } },
    { binding: { ...binding, registry: `0x${"2".repeat(40)}` } },
    { actualModelExecuted: true },
    { provenance: "real_qwen" },
    { procurement: { ...payload().procurement, id: PROJECT } },
    {
      reports: {
        ...payload().reports,
        PRE: { ...payload().reports.PRE, context: "current_evidence" },
      },
    },
    {
      reports: {
        ...payload().reports,
        FINAL: { ...payload().reports.FINAL, sampleRiskScoreBps: 100 },
      },
    },
    { items: [{ ...payload().items[0], synthetic: false }] },
    { items: [{ ...payload().items[0], technicalRiskScoreBps: 1600 }] },
  ])
    assert.throws(() =>
      parseOfflineAi({ ...payload(), ...change }, state(), PROC),
    );
});
test("only owning Foundation at confirmed PRE/receipt stages may request fixtures; Admin defaults FINAL", () => {
  assert.equal(offlineStageReady(state(), PROC, "PRE"), true);
  assert.equal(offlineStageReady(state(), PROC, "FINAL"), false);
  assert.equal(
    offlineStageReady(state("foundation", "receipt_confirmed"), PROC, "FINAL"),
    true,
  );
  assert.equal(offlineStageReady(state("human_approver"), PROC, "PRE"), false);
  assert.equal(
    offlineStageReady(state("foundation", "pre_assessed"), PROC, "PRE"),
    false,
  );
  assert.equal(
    offlineDefaultStage(state("human_approver", "final_assessed"), PROC),
    "FINAL",
  );
  assert.equal(
    offlineDefaultStage(state("human_approver", "payment_confirmed"), PROC),
    "FINAL",
  );
  assert.notEqual(
    offlineScope(state(), PROC),
    offlineScope(state("human_approver"), PROC),
  );
});
test("confirmation and stage guards make no network calls without explicit new intent", async () => {
  const options = { fetch: async () => assert.fail("must not fetch") };
  await assert.rejects(
    submitOfflineAi(state(), PROC, "PRE", false, "key", options),
    /Explicit confirmation/,
  );
  await assert.rejects(
    submitOfflineAi(state("human_approver"), PROC, "PRE", true, "key", options),
    /not ready/,
  );
  await assert.rejects(
    submitOfflineAi(state(), PROC, "FINAL", true, "key", options),
    /not ready/,
  );
});
test("two offline routes are fixed UUID-only paths and never human/funds execution", () => {
  assert.equal(OFFLINE_AI_ROUTES.length, 2);
  assert.equal(
    buildDemoRoute("ai.offline.submit", { procurementId: PROC }).path,
    `procurements/${PROC}/offline-demo-ai`,
  );
  assert.equal(
    matchDemoRoute("POST", [
      "procurements",
      PROC,
      "offline-demo-ai",
      "release",
    ]),
    null,
  );
  assert.equal(
    matchDemoRoute("POST", [
      "procurements",
      `${PROC}%2fother`,
      "offline-demo-ai",
    ]),
    null,
  );
  assert.deepEqual(OFFLINE_AI_ROUTES[1].roles, ["foundation"]);
  assert.equal(
    OFFLINE_AI_ROUTES.every(
      (item) => !item.provesExecution && !item.provesChainConfirmation,
    ),
    true,
  );
});
test("BFF requires separate local opt-in, authentication and same origin", async () => {
  const path = `procurements/${PROC}/offline-demo-ai`;
  const env = {
    POG_A2_INTEGRATION: "true",
    POG_FULL_DEMO: "true",
    POG_OFFLINE_DEMO_ENABLED: "true",
    POG_A2_API_URL: "http://127.0.0.1:18081",
  };
  function req(post = false, headers: Record<string, string> = {}) {
    return new Request(`http://127.0.0.1:3102/api/a2/${path}`, {
      method: post ? "POST" : "GET",
      body: post ? "{}" : undefined,
      headers: {
        Host: "127.0.0.1:3102",
        Origin: "http://127.0.0.1:3102",
        Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
        "Content-Type": "application/json",
        "Idempotency-Key": "same-offline-key",
        ...headers,
      },
    });
  }
  const forbidden: typeof fetch = async () => assert.fail("must not forward");
  for (const [request, settings, status] of [
    [req(), { ...env, POG_OFFLINE_DEMO_ENABLED: "false" }, 404],
    [req(), { ...env, POG_FULL_DEMO: "false" }, 404],
    [req(false, { Cookie: "" }), env, 401],
    [req(true, { Origin: "http://attacker.example" }), env, 403],
  ] as const) {
    const result = await handleA2Proxy(request, path.split("/"), {
      env: settings,
      fetch: forbidden,
    });
    assert.equal(result.status, status);
  }
  const response = await handleA2Proxy(req(), path.split("/"), {
    env,
    fetch: async (url) => {
      assert.equal(String(url), `http://127.0.0.1:18081/v2/${path}`);
      return new Response(JSON.stringify(payload()), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).actualModelExecuted, false);
});
