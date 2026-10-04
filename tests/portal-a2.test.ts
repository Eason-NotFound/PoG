import { test } from "node:test";
import assert from "node:assert/strict";
import {
  loadPortalA2Page,
  portalA2Action,
  portalA2Allowed,
  portalA2AtomicToCents,
  portalA2CentsToAtomic,
  portalA2Login,
  portalA2ProcurementFacts,
  portalA2Upload,
  portalA2User,
  projectPortalA2Page,
  type PortalA2State,
  type PortalA2Json,
} from "../src/lib/portal-a2";
import { FULL_DEMO_INTERFACE } from "../src/lib/full-demo-ui";
import type { A2SigningRequest } from "../src/lib/a2-workbench";

const ID = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";
const PROC = "33333333-3333-4333-8333-333333333333";
const VERSION = "44444444-4444-4444-8444-444444444444";
const REQUEST = "55555555-5555-4555-8555-555555555555";
const WALLET = `0x${"1".repeat(40)}`;
const HASH = `0x${"a".repeat(64)}`;
const binding = {
  namespaceId: ID,
  runId: "run",
  instanceId: "instance",
  chainId: "31337",
};
const envelope = (value: PortalA2Json) => ({
  interfaceId: FULL_DEMO_INTERFACE,
  mode: "simulation",
  binding,
  ...value,
});
const capability = { implemented: true, enabled: true, reasonCode: null };
function state(role = "foundation"): PortalA2State {
  return {
    currentUser: {
      id: ID,
      username: role === "human_approver" ? "admin" : role,
      role,
      walletAddress: WALLET,
    },
    deployment: {
      runId: "run",
      instanceId: "instance",
      chainId: "31337",
      verified: true,
    },
    context: {
      interfaceId: FULL_DEMO_INTERFACE,
      mode: "simulation",
      binding,
      capabilities: Object.fromEntries(
        [
          "project.draft.create",
          "project.chain.create",
          "procurement.draft.create",
          "procurement.chain.create",
          "procurement.po.record",
          "procurement.reserve.execute",
          "authorization.prepare",
          "authorization.demo.sign",
          "authorization.saved.submit",
          "release.execute",
          "donor.funding.convert",
          "donation.funded.deposit",
        ].map((id) => [id, capability]),
      ),
      defaults: {
        recipientUserId: ID,
        humanApproverUserId: ID,
        supplierWallet: WALLET,
      },
    },
    rawProjects: [
      {
        id: PROJECT,
        businessId: HASH,
        title: "真实项目",
        publicSummary: "真实 API 摘要",
        foundationWallet: WALLET,
        recipientWallet: WALLET,
        chainState: { status: "active", verified: true },
      },
    ],
    rawProcurements: [
      {
        id: PROC,
        projectId: PROJECT,
        businessId: HASH,
        title: "实际采购",
        vendorWallet: WALLET,
        budgetCapAtomic: "100000000",
        chainState: { status: "funds_released", verified: true },
      },
    ],
    workspaces: {
      [PROC]: {
        ...envelope({
          procurementId: PROC,
          chainState: "funds_released",
          amounts: {
            invoiceAmountAtomic: "50000000",
            reservedAmountAtomic: "100000000",
          },
          release: { confirmed: true },
          settlement: { confirmed: false, settlementHash: HASH },
          supplierPayment: null,
        }),
        documentVersions: [],
        allowedActions: { "release.execute": capability },
      },
    },
    ledgers: {
      [PROJECT]: {
        projectId: PROJECT,
        businessId: HASH,
        asset: WALLET,
        depositsAtomic: "200000000",
        reservedAtomic: "0",
        releasedAtomic: "50000000",
        returnedAtomic: "0",
        refundedAtomic: "0",
        refundPoolAtomic: "0",
        currentCallerDonorCreditAtomic: role === "donor" ? "200000000" : null,
        chainVerified: true,
      },
    },
    exchanges: [],
    signingRequests: [],
    operations: [],
    account: null,
    progressByProject: {},
    paymentStatuses: {},
    procurementFacts: {},
  };
}
const payment = (amountAtomic = "50000000") => ({
  id: VERSION,
  operationId: REQUEST,
  kind: "supplier_payment",
  status: "reconciled",
  reconciled: true,
  projectId: PROJECT,
  procurementId: PROC,
  amountAtomic,
});
function signedRequest(status: string): A2SigningRequest {
  return {
    id: REQUEST,
    procurementId: PROC,
    kind: "release",
    status,
    signer: WALLET,
    nonce: "0",
    deadline: "9999999999",
    digest: HASH,
    policyEpoch: 1,
    synthetic: false,
    typedData: {},
  };
}

test("portal money preserves exact cents and rejects subcent, noncanonical and unsafe values", () => {
  assert.equal(portalA2AtomicToCents("1230000"), 123);
  assert.equal(portalA2CentsToAtomic(123), "1230000");
  assert.equal(
    portalA2AtomicToCents("90071992547409910000"),
    Number.MAX_SAFE_INTEGER,
  );
  assert.equal(
    portalA2CentsToAtomic("9007199254740993"),
    "90071992547409930000",
  );
  for (const value of ["1", "90071992547409920000", "01", "-10000", "1e4"])
    assert.throws(() => portalA2AtomicToCents(value));
  for (const value of [0.1, -1, NaN, Number.MAX_SAFE_INTEGER + 1, "01", "1.1"])
    assert.throws(() => portalA2CentsToAtomic(value));
});

test("admin username only maps the API human_approver role, never grants wallet authority", () => {
  const me = { id: ID, username: "admin", walletAddress: WALLET };
  assert.equal(
    portalA2User({ ...me, role: "human_approver" }).role,
    "human_approver",
  );
  assert.throws(() => portalA2User({ ...me, role: "admin" }));
  assert.throws(() => portalA2User({ ...me, role: "owner" }));
  assert.throws(() => portalA2User({ ...me, role: "service_ai" }));
  assert.throws(() =>
    portalA2User({ ...me, role: "foundation", walletAddress: "0x0" }),
  );
  assert.throws(() =>
    projectPortalA2Page("foundation.projects", state("human_approver")),
  );
});

test("Released never becomes Paid; exact reconciled supplier payment AND settlement are required", () => {
  const s = state();
  const proc = s.rawProcurements[0];
  const ws = s.workspaces[PROC];
  assert.equal(portalA2ProcurementFacts(proc, ws).releaseConfirmed, true);
  assert.equal(
    projectPortalA2Page("foundation.payment", s).data.procurements?.[0].status,
    "funds_released",
  );
  const project = projectPortalA2Page("foundation.payment", s).data
    .projects![0];
  assert.equal(project.paymentTracked, true);
  assert.equal(project.released, 5000);
  assert.equal(project.refunded, 0);
  assert.equal(project.paid, 0, "released funds are not paid supplier cost");
  const confirmed = {
    ...proc,
    chainState: { status: "payment_confirmed", verified: true },
  };
  const complete = {
    ...ws,
    chainState: "payment_confirmed",
    supplierPayment: payment(),
    settlement: { confirmed: true, settlementHash: HASH },
  };
  assert.equal(
    portalA2ProcurementFacts(confirmed, complete).supplierPaymentReconciled,
    true,
  );
  assert.equal(
    portalA2ProcurementFacts(confirmed, complete).settlementConfirmed,
    true,
  );
  s.rawProcurements = [confirmed];
  s.workspaces[PROC] = complete;
  assert.equal(
    projectPortalA2Page("foundation.payment", s).data.procurements?.[0].status,
    "paid",
  );
  for (const patch of [
    { status: "queued" },
    { reconciled: false },
    { procurementId: ID },
    { projectId: ID },
    { amountAtomic: "40000000" },
  ]) {
    s.workspaces[PROC] = {
      ...complete,
      supplierPayment: { ...payment(), ...patch },
    };
    assert.notEqual(
      projectPortalA2Page("foundation.payment", s).data.procurements?.[0]
        .status,
      "paid",
    );
  }
  s.workspaces[PROC] = {
    ...complete,
    settlement: { confirmed: true, settlementHash: `0x${"0".repeat(64)}` },
  };
  assert.notEqual(
    projectPortalA2Page("foundation.payment", s).data.procurements?.[0].status,
    "paid",
  );
});

test("missing AI, quantities, unit price and target remain unknown; no fake decisions or audit logs", () => {
  const s = state();
  const before = JSON.stringify(s);
  const projected = projectPortalA2Page("foundation.overview", s);
  assert.equal(
    JSON.stringify(s),
    before,
    "pure projection does not mutate its source",
  );
  const proc = projected.data.procurements![0];
  assert.ok(Number.isNaN(proc.risk));
  assert.ok(Number.isNaN(proc.quantity));
  assert.ok(Number.isNaN(proc.unitPrice));
  assert.ok(Number.isNaN(projected.data.projects![0].target));
  assert.equal(projected.integration.procurementFacts[PROC].finalRisk, null);
  assert.deepEqual(projected.integration.procurementFacts[PROC].reviewPassed, {
    purchase: false,
    delivery: false,
  });
  assert.deepEqual(
    projected.data.reviews,
    [],
    "missing assessments do not manufacture future review cases",
  );
  assert.deepEqual(projected.data.logs, []);
  assert.deepEqual(projected.data.ledger, []);
});

test("synthetic risk is labeled and does not create a human approval record", () => {
  const s = state();
  s.rawProcurements[0].chainState.status = "final_assessed";
  s.workspaces[PROC].chainState = "final_assessed";
  s.workspaces[PROC].finalAssessment = {
    assessmentId: HASH,
    stage: "1",
    outcome: "0",
    riskScoreBps: "100",
    evidenceHash: HASH,
    reportHash: HASH,
    deadline: "9999999999",
    synthetic: true,
    realAI: false,
  };
  const page = projectPortalA2Page("foundation.payment", s);
  assert.equal(
    page.integration.procurementFacts[PROC].finalRisk,
    null,
    "synthetic fixtures do not populate real AI risk fields",
  );
  assert.equal(
    page.integration.procurementFacts[PROC].finalAssessment?.riskScoreBps,
    "100",
  );
  assert.equal(
    page.integration.procurementFacts[PROC].finalAssessment?.synthetic,
    true,
  );
  assert.equal(
    page.integration.procurementFacts[PROC].finalAssessment?.realAI,
    false,
  );
  assert.equal(page.data.reviews![0].decision, undefined);
  s.workspaces[PROC].finalAssessment = {
    ...(s.workspaces[PROC].finalAssessment as object),
    realAI: true,
  };
  assert.equal(
    projectPortalA2Page("foundation.payment", s).integration.procurementFacts[
      PROC
    ].finalRisk,
    null,
  );
});

test("donation projection comes only from exact caller chain ledger, never mint or pending allocations", () => {
  const s = state("donor");
  const page = projectPortalA2Page("donor.donations", s);
  assert.equal(page.data.donations!.length, 1);
  assert.equal(page.data.donations![0].amount, 20000);
  assert.equal(page.data.donations![0].hash, "");
  assert.equal(page.data.donations![0].createdAt, "");
  assert.ok(Number.isNaN(page.data.donations![0].allocated));
  assert.deepEqual(
    page.data.procurements,
    [],
    "Donor projection discards any stale private-role state",
  );
  assert.deepEqual(page.integration.rawProcurements, []);
  assert.deepEqual(page.data.evidence, []);
  s.ledgers[PROJECT].currentCallerDonorCreditAtomic = "0";
  assert.deepEqual(
    projectPortalA2Page("donor.donations", s).data.donations,
    [],
  );
  s.ledgers[PROJECT].currentCallerDonorCreditAtomic = "200000000";
  s.ledgers[PROJECT].chainVerified = false;
  assert.deepEqual(
    projectPortalA2Page("donor.donations", s).data.donations,
    [],
  );
});

test("unknown or queued raw states stay fail-closed and capabilities are role scoped", () => {
  const s = state();
  s.rawProcurements[0].chainState = {
    status: "unknown_new_state",
    verified: true,
  };
  assert.equal(
    projectPortalA2Page("foundation.overview", s).data.procurements![0].status,
    "needs_info",
  );
  assert.equal(portalA2Allowed(s, "reviewCase").enabled, false);
  assert.equal(portalA2Allowed(s, "ai.pre.detect").enabled, false);
  assert.equal(
    portalA2Allowed(s, "authorization.prepare", {
      procurementId: PROC,
      kind: "release",
    }).enabled,
    false,
  );
  assert.equal(
    portalA2Allowed(s, "procurement.po.record", {
      procurementId: PROC,
      projectId: ID,
    }).enabled,
    false,
  );
  const donor = state("donor");
  assert.equal(
    portalA2Allowed(donor, "release.execute", { procurementId: PROC }).enabled,
    false,
  );
  donor.rawProjects[0].chainState = {
    status: "creation_queued",
    verified: false,
  };
  assert.equal(
    portalA2Allowed(donor, "donor.funding.convert", { projectId: PROJECT })
      .enabled,
    false,
  );
});

test("prepare, sign and submit do not confer each other's permission", () => {
  const s = state("human_approver");
  s.signingRequests = [signedRequest("prepared")];
  assert.equal(
    portalA2Allowed(s, "authorization.demo.sign", {
      procurementId: PROC,
      requestId: REQUEST,
    }).enabled,
    false,
    "missing scoped capability fails closed",
  );
  s.workspaces[PROC].allowedActions["authorization.demo.sign"] = capability;
  s.workspaces[PROC].allowedActions["authorization.saved.submit"] = capability;
  assert.equal(
    portalA2Allowed(s, "authorization.demo.sign", {
      procurementId: PROC,
      requestId: REQUEST,
    }).enabled,
    true,
  );
  assert.equal(
    portalA2Allowed(s, "authorization.saved.submit", {
      procurementId: PROC,
      requestId: REQUEST,
    }).enabled,
    false,
  );
  s.signingRequests = [signedRequest("signed")];
  assert.equal(
    portalA2Allowed(s, "authorization.saved.submit", {
      procurementId: PROC,
      requestId: REQUEST,
    }).enabled,
    true,
  );
  s.signingRequests[0].signer = `0x${"2".repeat(40)}`;
  assert.equal(
    portalA2Allowed(s, "authorization.saved.submit", {
      procurementId: PROC,
      requestId: REQUEST,
    }).enabled,
    false,
  );
});

test("new-instance supplier uses API defaults and unsent procurement needs no invented workspace", () => {
  const s = state();
  s.rawProcurements = [];
  const page = projectPortalA2Page("foundation.projects", s);
  assert.deepEqual(
    page.data.vendors?.map((v) => v.wallet),
    [WALLET],
  );
  assert.equal(page.data.vendors?.[0].verified, false);
  s.context.defaults = null;
  assert.equal(portalA2Allowed(s, "project.draft.create").enabled, false);
  s.context.defaults = {
    recipientUserId: ID,
    humanApproverUserId: ID,
    supplierWallet: WALLET,
  };
  s.rawProcurements = [
    {
      id: PROC,
      projectId: PROJECT,
      businessId: HASH,
      title: "draft",
      vendorWallet: WALLET,
      budgetCapAtomic: "10000",
      chainState: { status: "off_chain_draft", verified: false },
    },
  ];
  s.workspaces = {};
  assert.equal(
    portalA2Allowed(s, "procurement.chain.create", { procurementId: PROC })
      .enabled,
    true,
  );
  assert.equal(
    portalA2Allowed(s, "procurement.po.record", { procurementId: PROC })
      .enabled,
    false,
  );
});

test("one fixed action means one same-origin request and retains exact retry key and payload", async () => {
  const calls: { path: string; options: RequestInit }[] = [];
  const fake = async (input: RequestInfo | URL, options?: RequestInit) => {
    calls.push({ path: String(input), options: options ?? {} });
    return Response.json({ operation: { status: "queued" } }, { status: 202 });
  };
  const result = await portalA2Action(
    "authorization.saved.submit",
    { requestId: REQUEST, confirm: true },
    "exact-retry-key",
    { fetch: fake as typeof fetch },
  );
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].path,
    `/api/a2/signing-requests/${REQUEST}/submit-signed`,
  );
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.body, '{"confirm":true}');
  assert.equal(
    new Headers(calls[0].options.headers).get("Idempotency-Key"),
    "exact-retry-key",
  );
  assert.equal(result.operation?.status, "queued", "HTTP 202 remains queued");
  assert.equal(
    new Headers(calls[0].options.headers).has("Authorization"),
    false,
  );
});

test("unsupported aliases, fake AI, path tricks and implicit confirmation never issue a request", async () => {
  let count = 0;
  const fake = (async () => {
    count++;
    return Response.json({});
  }) as typeof fetch;
  for (const [action, body] of [
    ["reviewCase", {}],
    ["ai.pre.detect", {}],
    ["authorization.prepare", { procurementId: PROC, kind: "ai_final" }],
    ["authorization.demo.sign", { requestId: REQUEST, confirm: "true" }],
    ["donation.funded.deposit", { projectId: PROJECT, confirm: false }],
    ["project.chain.create", { projectId: "../../projects" }],
    [
      "procurement.draft.create",
      { projectId: PROJECT, budgetCapAtomic: 10000 },
    ],
  ] as [string, PortalA2Json][])
    await assert.rejects(portalA2Action(action, body, "k", { fetch: fake }));
  assert.equal(count, 0);
});

test("quotes preserve executed=false and carry the required BFF request key", async () => {
  let headers: Headers | null = null;
  const fake = (async (_input: RequestInfo | URL, options?: RequestInit) => {
    headers = new Headers(options?.headers);
    return Response.json({ executed: false, quote: {} });
  }) as typeof fetch;
  assert.equal(
    (
      await portalA2Action(
        "mock.exchange.quote",
        { direction: "hkd_to_mock", projectId: PROJECT, hkdCents: "500" },
        "quote-key",
        { fetch: fake },
      )
    ).executed,
    false,
  );
  assert.equal(
    (headers as unknown as Headers).get("Idempotency-Key"),
    "quote-key",
  );
  await assert.rejects(
    portalA2Action("mock.exchange.quote", {}, "q", {
      fetch: (async () => Response.json({ executed: true })) as typeof fetch,
    }),
  );
});

test("transport failures and upstream timeout stay unknown and never trigger another action", async () => {
  let count = 0;
  await assert.rejects(
    portalA2Action("project.chain.create", { projectId: PROJECT }, "saved", {
      fetch: (async () => {
        count++;
        throw new Error("offline");
      }) as typeof fetch,
    }),
    (error: unknown) =>
      Boolean(
        error &&
        typeof error === "object" &&
        "status" in error &&
        error.status === 0,
      ),
  );
  assert.equal(count, 1);
  await assert.rejects(
    portalA2Action("project.chain.create", { projectId: PROJECT }, "saved", {
      fetch: (async () =>
        Response.json(
          {
            error: {
              code: "a2_upstream_timeout",
              message: "Outcome unknown",
              operationId: REQUEST,
            },
          },
          { status: 504 },
        )) as typeof fetch,
    }),
    (error: unknown) =>
      Boolean(
        error &&
        typeof error === "object" &&
        "status" in error &&
        error.status === 504 &&
        "operationId" in error &&
        error.operationId === REQUEST,
      ),
  );
});

test("upload keeps each original category/version separate and rejects the old demo photo alias", async () => {
  const calls: RequestInit[] = [];
  const fake = (async (_input: RequestInfo | URL, options?: RequestInit) => {
    calls.push(options ?? {});
    return Response.json(
      {
        document: {
          id: ID,
          versionId: VERSION,
          procurementId: PROC,
          category: "request",
          originalFilename: "request.pdf",
          keccak256: HASH,
          sha256: "a".repeat(64),
          referenced: false,
        },
      },
      { status: 202 },
    );
  }) as typeof fetch;
  const file = new File(["%PDF-1.4"], "request.pdf", {
    type: "application/pdf",
  });
  const doc = await portalA2Upload(PROC, "request", file, { fetch: fake });
  assert.equal(doc.versionId, VERSION);
  assert.equal(doc.category, "request");
  assert.equal((calls[0].body as FormData).get("category"), "request");
  assert.equal(
    new Headers(calls[0].headers).has("Content-Type"),
    false,
    "browser chooses multipart boundary",
  );
  await assert.rejects(portalA2Upload(PROC, "photo", file, { fetch: fake }));
  assert.equal(calls.length, 1);
});

test("login relies on authenticated API identity rather than typed username", async () => {
  const result = await portalA2Login("admin", "test-only", {
    fetch: (async () =>
      Response.json({
        user: {
          id: ID,
          username: "admin",
          role: "human_approver",
          walletAddress: WALLET,
        },
      })) as typeof fetch,
  });
  assert.deepEqual(result, { redirect: "/admin/overview" });
});

test("Donor page loading never fetches private procurement, signing or evidence data", async () => {
  const s = state("donor");
  const paths: string[] = [];
  const replies: Record<string, PortalA2Json> = {
    "/api/a2/me": s.currentUser as unknown as PortalA2Json,
    "/api/a2/deployment-config": s.deployment,
    "/api/a2/integration-context": s.context as unknown as PortalA2Json,
    "/api/a2/projects": { items: s.rawProjects },
    "/api/a2/operations": envelope({ items: [] }),
    "/api/a2/mock-exchanges": envelope({ items: [] }),
    "/api/a2/mock-hkd/accounts/me": envelope({
      account: { availableHkdCents: "10000" },
    }),
    [`/api/a2/projects/${PROJECT}/ledger`]: s.ledgers[
      PROJECT
    ] as unknown as PortalA2Json,
    [`/api/a2/projects/${PROJECT}/progress`]: envelope({ projectId: PROJECT }),
  };
  const fake = (async (input: RequestInfo | URL) => {
    const path = String(input);
    paths.push(path);
    assert.ok(replies[path], `unexpected request ${path}`);
    return Response.json(replies[path]);
  }) as typeof fetch;
  const page = await loadPortalA2Page("donor.overview", { fetch: fake });
  assert.equal(page.user.role, "donor");
  assert.equal(page.data.donations![0].amount, 20000);
  assert.equal(
    paths.some((path) => /procurements|signing-requests|documents/.test(path)),
    false,
  );
  replies[`/api/a2/projects/${PROJECT}/progress`] = {
    ...envelope({ projectId: PROJECT }),
    binding: { ...binding, instanceId: "reset" },
  };
  await assert.rejects(
    loadPortalA2Page("donor.overview", { fetch: fake }),
    /different deployment/,
  );
});

test("private page retains off-chain drafts without unavailable Registry reads", async () => {
  const s = state();
  s.rawProcurements[0].chainState = {
    status: "off_chain_draft",
    verified: false,
  };
  const paths: string[] = [];
  const replies: Record<string, PortalA2Json> = {
    "/api/a2/me": s.currentUser as unknown as PortalA2Json,
    "/api/a2/deployment-config": s.deployment,
    "/api/a2/integration-context": s.context as unknown as PortalA2Json,
    "/api/a2/projects": { items: s.rawProjects },
    "/api/a2/procurements": { items: s.rawProcurements },
    "/api/a2/operations": envelope({ items: [] }),
    "/api/a2/mock-exchanges": envelope({ items: [] }),
    "/api/a2/mock-hkd/accounts/me": envelope({
      account: { availableHkdCents: "10000" },
    }),
    [`/api/a2/projects/${PROJECT}/ledger`]: s.ledgers[
      PROJECT
    ] as unknown as PortalA2Json,
  };
  const fake = (async (input: RequestInfo | URL) => {
    const path = String(input);
    paths.push(path);
    assert.ok(replies[path], `must not query nonexistent chain record ${path}`);
    return Response.json(replies[path]);
  }) as typeof fetch;
  const page = await loadPortalA2Page("foundation.overview", { fetch: fake });
  assert.equal(
    page.integration.rawProcurements[0].chainState.status,
    "off_chain_draft",
  );
  assert.equal(page.integration.workspaces[PROC], undefined);
  assert.equal(
    paths.some((path) => /workspace|payment-status|progress/.test(path)),
    false,
  );
  assert.equal(
    portalA2Allowed(page.integration, "procurement.chain.create", {
      procurementId: PROC,
    }).enabled,
    true,
  );
});

test("changed identity or deployment blocks the displayed action before any POST", async () => {
  const expected = state();
  let posts = 0;
  const fake = (async (input: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method === "POST") {
      posts++;
      return Response.json({});
    }
    const path = String(input);
    if (path.endsWith("/me"))
      return Response.json({ ...expected.currentUser, id: REQUEST });
    if (path.endsWith("/deployment-config"))
      return Response.json(expected.deployment);
    return Response.json(expected.context);
  }) as typeof fetch;
  await assert.rejects(
    portalA2Action(
      "project.chain.create",
      { projectId: PROJECT },
      "same-intent",
      { fetch: fake, expectedState: expected },
    ),
    (error: unknown) =>
      Boolean(
        error &&
        typeof error === "object" &&
        "status" in error &&
        error.status === 409,
      ),
  );
  assert.equal(posts, 0);
});
