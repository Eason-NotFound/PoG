import { test } from "node:test";
import assert from "node:assert/strict";
import { lookupProjectRecord } from "../src/lib/project-lookup";
import { PortalA2Error, type PortalA2State } from "../src/lib/portal-a2";
import { FULL_DEMO_INTERFACE } from "../src/lib/full-demo-ui";

const PROJECT = "a2222222-2222-4222-8222-222222222222";
const OTHER = "b3333333-3333-4333-8333-333333333333";
const HASH = `0x${"ab".repeat(32)}`;
const binding = {
  namespaceId: "11111111-1111-4111-8111-111111111111",
  runId: "test-run",
  instanceId: "test-instance",
  chainId: "31337",
};
const user = {
  id: "44444444-4444-4444-8444-444444444444",
  username: "donor",
  role: "donor",
  walletAddress: `0x${"1".repeat(40)}`,
};
const deployment = {
  runId: binding.runId,
  instanceId: binding.instanceId,
  chainId: "31337",
  verified: true,
};
const context = {
  interfaceId: FULL_DEMO_INTERFACE,
  mode: "simulation" as const,
  binding,
  capabilities: {},
  defaults: {
    recipientUserId: user.id,
    humanApproverUserId: user.id,
    supplierWallet: user.walletAddress,
  },
};
const expectedState = {
  currentUser: user,
  deployment,
  context,
  rawProjects: [],
  rawProcurements: [],
  workspaces: {},
  ledgers: {},
  exchanges: [],
  signingRequests: [],
  operations: [],
  account: null,
  progressByProject: {},
  paymentStatuses: {},
  procurementFacts: {},
} as PortalA2State;
const project = {
  id: PROJECT,
  businessId: HASH,
  title: "Breakfast",
  publicSummary: "40 breakfast packs",
  chainState: { status: "active", verified: true },
  privateField: "must not be projected",
};
const ledger = {
  projectId: PROJECT,
  businessId: HASH,
  chainVerified: true,
  depositsAtomic: "100000000",
  returnedAtomic: "0",
  releasedAtomic: "72000000",
  refundedAtomic: "0",
  reservedAtomic: "0",
  currentCallerDonorCreditAtomic: "100000000",
};

type Overrides = {
  project?: Record<string, unknown>;
  ledger?: Record<string, unknown>;
  list?: unknown[];
  projectStatus?: number;
  identityChangeAt?: number;
  namespaceChangeAt?: number;
};
function harness(overrides: Overrides = {}) {
  const calls: { path: string; method?: string }[] = [];
  let meCalls = 0;
  let contextCalls = 0;
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    calls.push({ path, method: init?.method });
    assert.equal(init?.method, "GET");
    assert.equal(init?.cache, "no-store");
    if (path === "/api/a2/me") {
      meCalls++;
      return Response.json(
        meCalls === overrides.identityChangeAt
          ? { ...user, role: "foundation" }
          : user,
      );
    }
    if (path === "/api/a2/deployment-config") return Response.json(deployment);
    if (path === "/api/a2/integration-context") {
      contextCalls++;
      return Response.json(
        contextCalls === overrides.namespaceChangeAt
          ? { ...context, binding: { ...binding, namespaceId: OTHER } }
          : context,
      );
    }
    if (path === "/api/a2/projects")
      return Response.json({ items: overrides.list ?? [project] });
    if (path === `/api/a2/projects/${PROJECT}`)
      return overrides.projectStatus
        ? Response.json(
            { error: { code: "not_found", message: "not found" } },
            { status: overrides.projectStatus },
          )
        : Response.json(overrides.project ?? project);
    if (path === `/api/a2/projects/${PROJECT}/ledger`)
      return Response.json(overrides.ledger ?? ledger);
    throw new Error(`Unexpected read: ${path}`);
  }) as typeof globalThis.fetch;
  return { calls, dependencies: { fetch, expectedState } };
}
const code = (expected: string) => (error: unknown) =>
  error instanceof PortalA2Error && error.code === expected;

test("UUID resolves exact public project and ledger with GET-only reads", async () => {
  const h = harness();
  const result = await lookupProjectRecord(
    `  ${PROJECT.toUpperCase()}  `,
    h.dependencies,
  );
  assert.deepEqual(result, {
    projectId: PROJECT,
    businessId: HASH,
    title: "Breakfast",
    publicSummary: "40 breakfast packs",
    status: "active",
    chainVerified: true,
    amounts: {
      donatedAtomic: "100000000",
      lockedAtomic: "28000000",
      reservedAtomic: "0",
      releasedAtomic: "72000000",
      returnedAtomic: "0",
      refundedAtomic: "0",
    },
  });
  assert.equal(
    h.calls.filter((call) => call.path === "/api/a2/projects").length,
    0,
  );
  assert.equal(h.calls.filter((call) => call.path === "/api/a2/me").length, 2);
  assert.ok(
    h.calls.every(
      (call) => !/documents|procurements|sign|ai|payments/.test(call.path),
    ),
  );
  assert.equal("privateField" in result, false);
  assert.equal("currentCallerDonorCreditAtomic" in result.amounts, false);
});

test("bytes32 resolves only current-role visible list then verifies exact project", async () => {
  const h = harness();
  const result = await lookupProjectRecord(
    ` ${HASH.toUpperCase()} `,
    h.dependencies,
  );
  assert.equal(result.projectId, PROJECT);
  assert.equal(result.businessId, HASH);
  assert.equal(
    h.calls.filter((call) => call.path === "/api/a2/projects").length,
    1,
  );
});

test("malformed identifiers are rejected without any request or fuzzy lookup", async () => {
  for (const input of [
    "Breakfast",
    "0x12",
    HASH.slice(2),
    `${PROJECT}/ledger`,
    "",
    "11111111-1111-1111-1111-11111111111g",
  ]) {
    const h = harness();
    await assert.rejects(
      lookupProjectRecord(input, h.dependencies),
      code("invalid_project_identifier"),
    );
    assert.equal(h.calls.length, 0);
  }
});

test("no visible bytes32 match or UUID 404 produces explicit not-found", async () => {
  const invisible = harness({ list: [] });
  await assert.rejects(
    lookupProjectRecord(HASH, invisible.dependencies),
    code("project_not_found"),
  );
  assert.ok(!invisible.calls.some((call) => call.path.endsWith("/ledger")));
  const missing = harness({ projectStatus: 404 });
  await assert.rejects(
    lookupProjectRecord(PROJECT, missing.dependencies),
    code("project_not_found"),
  );
});

test("non-unique visible bytes32 is not silently resolved", async () => {
  const h = harness({ list: [project, { ...project, id: OTHER }] });
  await assert.rejects(
    lookupProjectRecord(HASH, h.dependencies),
    code("project_lookup_ambiguous"),
  );
});

test("project and ledger identity mismatch fail closed", async () => {
  for (const overrides of [
    { project: { ...project, id: OTHER } },
    { ledger: { ...ledger, projectId: OTHER } },
    { ledger: { ...ledger, businessId: `0x${"c".repeat(64)}` } },
    { ledger: { ...ledger, chainVerified: false } },
  ]) {
    const h = harness(overrides);
    await assert.rejects(
      lookupProjectRecord(PROJECT, h.dependencies),
      code("project_lookup_invalid_response"),
    );
  }
  const h = harness({
    project: { ...project, businessId: `0x${"c".repeat(64)}` },
  });
  await assert.rejects(
    lookupProjectRecord(HASH, h.dependencies),
    code("project_lookup_invalid_response"),
  );
});

test("unverified project does not request or display a confirmed ledger", async () => {
  const h = harness({
    project: {
      ...project,
      chainState: { status: "off_chain_draft", verified: false },
    },
  });
  await assert.rejects(
    lookupProjectRecord(PROJECT, h.dependencies),
    code("project_unverified"),
  );
  assert.ok(!h.calls.some((call) => call.path.endsWith("/ledger")));
});

test("changed identity before reads and changed namespace after reads reject stale context", async () => {
  const before = harness({ identityChangeAt: 1 });
  await assert.rejects(
    lookupProjectRecord(PROJECT, before.dependencies),
    code("stale_portal_context"),
  );
  assert.ok(!before.calls.some((call) => call.path.includes("/projects")));
  const after = harness({ namespaceChangeAt: 2 });
  await assert.rejects(
    lookupProjectRecord(PROJECT, after.dependencies),
    code("stale_portal_context"),
  );
  assert.ok(after.calls.some((call) => call.path.endsWith("/ledger")));
});

test("exact six-decimal atoms and integers above Number precision are never rounded", async () => {
  const h = harness({
    ledger: {
      ...ledger,
      depositsAtomic: "9007199254740993123456",
      releasedAtomic: "1",
      reservedAtomic: "1000001",
      returnedAtomic: "2",
      refundedAtomic: "1",
    },
  });
  const result = await lookupProjectRecord(PROJECT, h.dependencies);
  assert.equal(result.amounts.donatedAtomic, "9007199254740993123456");
  assert.equal(result.amounts.lockedAtomic, "9007199254740993123456");
  assert.equal(result.amounts.reservedAtomic, "1000001");
  assert.equal(result.amounts.releasedAtomic, "1");
});

test("invalid or inconsistent atomic balances never manufacture locked funds", async () => {
  for (const changes of [
    { depositsAtomic: "01" },
    { depositsAtomic: "1.0" },
    { depositsAtomic: "-1" },
    { releasedAtomic: "100000001" },
    { reservedAtomic: "28000001" },
    { depositsAtomic: (1n << 256n).toString() },
    { donatedAtomic: "100000000", depositsAtomic: undefined },
  ]) {
    const h = harness({ ledger: { ...ledger, ...changes } });
    await assert.rejects(
      lookupProjectRecord(PROJECT, h.dependencies),
      code("project_lookup_invalid_response"),
    );
  }
});
