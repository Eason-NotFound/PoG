import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEMO_A2_ROUTES,
  FULL_DEMO_ROUTES,
  DEMO_ACTION_CATALOG,
  LOCAL_DEMO_ACTIONS,
  PLANNED_DEMO_ACTIONS,
  DemoActionCatalogError,
  buildDemoRoute,
  isDemoProxyMethod,
  matchDemoRoute,
} from "../src/lib/demo-action-catalog";
import { A2_SESSION_COOKIE, handleA2Proxy } from "../src/lib/a2-proxy";

const ID = "12345678-9abc-def0-1234-56789abcdef0";
const UPPER_ID = ID.toUpperCase();
const TOKEN = "a".repeat(43);
const ENV = {
  POG_A2_INTEGRATION: "true",
  POG_A2_API_URL: "http://127.0.0.1:8765",
};

const EXPECTED_ROUTES = [
  "DELETE sessions/current",
  "GET deployment-config",
  "GET documents/{documentId}",
  "GET documents/{documentId}/content",
  "GET me",
  "GET operations/{operationId}",
  "GET procurements",
  "GET procurements/{procurementId}",
  "GET projects",
  "GET projects/{projectId}",
  "GET projects/{projectId}/ledger",
  "GET signing-requests/{requestId}",
  "POST documents",
  "POST procurements",
  "POST procurements/{procurementId}/chain/create",
  "POST procurements/{procurementId}/chain/invoice-and-goods",
  "POST procurements/{procurementId}/chain/purchase-order",
  "POST procurements/{procurementId}/chain/reserve",
  "POST procurements/{procurementId}/signing-requests",
  "POST projects",
  "POST projects/{projectId}/chain/create",
  "POST projects/{projectId}/donations",
  "POST sessions",
  "POST signing-requests/{requestId}/sign-demo",
  "POST signing-requests/{requestId}/submit",
  "POST signing-requests/{requestId}/submit-signed",
].sort();

function parameters(template: string, id = ID): Record<string, string> {
  return Object.fromEntries(
    [...template.matchAll(/\{([a-zA-Z][a-zA-Z0-9]*)\}/g)].map((item) => [
      item[1],
      id,
    ]),
  );
}
function catalogError(code: DemoActionCatalogError["code"]) {
  return (problem: unknown) =>
    problem instanceof DemoActionCatalogError && problem.code === code;
}
function req(path: string, method = "GET", body?: string) {
  return new Request(`http://localhost:3000/api/a2/${path}`, {
    method,
    body,
    headers: {
      Host: "localhost:3000",
      Origin: "http://localhost:3000",
      Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
      "Content-Type": "application/json",
      "Idempotency-Key": "catalog-test-intent",
    },
  });
}
const forbiddenFetch: typeof fetch = async () => {
  assert.fail("Unavailable route must not reach the upstream");
};

test("catalog preserves exactly the existing 26 BFF method/template pairs", () => {
  assert.equal(DEMO_A2_ROUTES.length, 26);
  assert.deepEqual(
    DEMO_A2_ROUTES.map((item) => `${item.method} ${item.template}`).sort(),
    EXPECTED_ROUTES,
  );
  assert.equal(
    new Set(DEMO_ACTION_CATALOG.map((item) => item.id)).size,
    DEMO_ACTION_CATALOG.length,
  );
});

test("every declared action builds only the fixed relative backend/browser route", () => {
  for (const item of DEMO_A2_ROUTES) {
    const built = buildDemoRoute(item.id, parameters(item.template));
    assert.equal(built.actionId, item.id);
    assert.equal(built.method, item.method);
    assert.equal(built.path, item.template.replace(/\{[^}]+\}/g, ID));
    assert.equal(built.proxyPath, `/api/a2/${built.path}`);
    assert.equal(built.backendPath, `/v2/${built.path}`);
    assert.equal(matchDemoRoute(built.method, built.path.split("/")), item);
  }
});

test("UUID case is accepted and preserved without lossy parsing or rewriting", () => {
  for (const item of DEMO_A2_ROUTES.filter((entry) =>
    entry.template.includes("{"),
  )) {
    const built = buildDemoRoute(item.id, parameters(item.template, UPPER_ID));
    assert.ok(built.path.includes(UPPER_ID));
    assert.equal(matchDemoRoute(item.method, built.path.split("/")), item);
  }
});

test("UUID-only builder rejects encoding, path escapes, queries and URL overrides", () => {
  for (const value of [
    "current",
    "",
    ID.slice(1),
    `${ID}%2fcontent`,
    `${ID}/content`,
    `${ID}\\content`,
    `%31${ID.slice(1)}`,
    `${ID}?role=foundation`,
    `${ID}#fragment`,
    `${ID}\u0000`,
    ` ${ID}`,
    `${ID} `,
    "http://127.0.0.1:8545",
    "https://attacker.example",
    "../me",
  ]) {
    assert.throws(
      () => buildDemoRoute("project.read", { projectId: value }),
      catalogError("invalid_route_parameters"),
    );
  }
});

test("builder requires exactly its own UUID parameter names and rejects extra context", () => {
  for (const input of [
    {},
    { procurementId: ID },
    { projectId: ID, rpcUrl: "http://127.0.0.1:8545" },
    { projectId: ID, role: "foundation" },
    { projectId: ID, baseUrl: "https://attacker.example" },
    Object.create({ projectId: ID }) as Record<string, string>,
    { projectId: 1 } as unknown as Record<string, string>,
    null as unknown as Record<string, string>,
    [ID] as unknown as Record<string, string>,
  ]) {
    assert.throws(
      () => buildDemoRoute("project.read", input),
      catalogError("invalid_route_parameters"),
    );
  }
  assert.throws(
    () => buildDemoRoute("identity.read", { role: "donor" }),
    catalogError("invalid_route_parameters"),
  );
});

test("unknown action and unknown or context-prefixed paths never gain a route", () => {
  for (const action of [
    "",
    "__proto__",
    "constructor",
    "release",
    "https://attacker.example",
  ])
    assert.throws(() => buildDemoRoute(action), catalogError("unknown_action"));
  for (const path of [
    ["v2", "me"],
    ["api", "a2", "me"],
    ["projects", "current"],
    ["projects", ID, "anything"],
    ["signing-requests", ID, "automatic-sign"],
    ["sessions", "current", "anything"],
    ["me", ""],
    [],
  ])
    assert.equal(matchDemoRoute("GET", path), null);
});

test("matcher accepts only exact supported methods, not cross-method or case variants", () => {
  assert.deepEqual(["GET", "POST", "DELETE"].map(isDemoProxyMethod), [
    true,
    true,
    true,
  ]);
  for (const method of ["get", "post", "PATCH", "PUT", "HEAD", "OPTIONS", ""])
    assert.equal(isDemoProxyMethod(method), false);
  assert.equal(matchDemoRoute("POST", ["me"]), null);
  assert.equal(matchDemoRoute("GET", ["sessions"]), null);
  assert.equal(matchDemoRoute("DELETE", ["projects"]), null);
});

test("matcher rejects path encoding and arbitrary source types before matching", () => {
  for (const path of [
    ["%6de"],
    ["projects", `${ID}%2Fcontent`],
    ["projects", "..", "me"],
    ["projects", ID, "http://127.0.0.1:8545"],
    ["me\u0000"],
    [""],
    ["projects", 1] as unknown as string[],
    null as unknown as string[],
  ])
    assert.equal(matchDemoRoute("GET", path), null);
});

test("unfrozen actual AI and standalone human action aliases have no executable paths", () => {
  assert.deepEqual(
    PLANNED_DEMO_ACTIONS.map((item) => item.id),
    [
      "ai.pre.detect",
      "ai.final.detect",
      "release.human.approve",
      "settlement.human.approve",
    ],
  );
  for (const item of PLANNED_DEMO_ACTIONS) {
    assert.equal(item.status, "unavailable");
    assert.equal(item.method, null);
    assert.equal(item.template, null);
    assert.throws(
      () => buildDemoRoute(item.id, { procurementId: ID }),
      catalogError("action_unavailable"),
    );
  }
});

test("navigation, locale, input and consent remain local-only, not fake business requests", () => {
  assert.deepEqual(
    LOCAL_DEMO_ACTIONS.map((item) => item.id),
    [
      "ui.navigation",
      "ui.language",
      "ui.selection",
      "ui.input",
      "ui.expand",
      "ui.clipboard",
      "ui.dismiss",
      "ui.consent",
    ],
  );
  for (const item of LOCAL_DEMO_ACTIONS) {
    assert.equal(item.status, "local_only");
    assert.equal(item.method, null);
    assert.equal(item.template, null);
    assert.throws(
      () => buildDemoRoute(item.id),
      catalogError("action_unavailable"),
    );
  }
});

test("declarations are immutable descriptive facts, never available or confirmed capabilities", () => {
  assert.ok(Object.isFrozen(DEMO_ACTION_CATALOG));
  for (const item of DEMO_ACTION_CATALOG) {
    assert.ok(Object.isFrozen(item));
    assert.equal(item.provesExecution, false);
    assert.equal(item.provesChainConfirmation, false);
    for (const key of [
      "available",
      "approved",
      "confirmed",
      "transactionHash",
      "balance",
    ])
      assert.equal(Object.hasOwn(item, key), false);
    if ("roles" in item) assert.ok(Object.isFrozen(item.roles));
  }
  const built = buildDemoRoute("procurement.reserve.execute", {
    procurementId: ID,
  });
  assert.equal(built.status, "declared");
  assert.equal(built.provesExecution, false);
  assert.equal(built.provesChainConfirmation, false);
  assert.ok(Object.isFrozen(built));
});

test("transport declaration for submit-signed explicitly retains pending backend status", () => {
  const built = buildDemoRoute("authorization.saved.submit", { requestId: ID });
  assert.equal(built.backendStatus, "pending_backend_review");
  assert.equal(built.path, `signing-requests/${ID}/submit-signed`);
  assert.equal(built.provesExecution, false);
  assert.equal(built.provesChainConfirmation, false);
});

test("role metadata documents fixed actors without implementing authorization", () => {
  const create = DEMO_A2_ROUTES.find(
    (item) => item.id === "procurement.draft.create",
  )!;
  assert.deepEqual(create.roles, ["foundation"]);
  assert.deepEqual(
    DEMO_A2_ROUTES.find((item) => item.id === "donation.deposit")!.roles,
    ["donor"],
  );
  assert.deepEqual(
    DEMO_A2_ROUTES.find((item) => item.id === "authorization.prepare")!.roles,
    ["human_approver", "recipient", "service_ai"],
  );
  // Matcher has no selected-role parameter: backend owns the actor decision.
  assert.equal(matchDemoRoute("POST", ["procurements"]), create);
  assert.equal(Object.hasOwn(create, "authorizationGranted"), false);
});

test("BFF still forwards exact declared body and preserves backend role refusal", async () => {
  const body =
    '{"projectId":"' + ID + '","title":"synthetic","role":"foundation"}';
  let calls = 0;
  const response = await handleA2Proxy(
    req("procurements", "POST", body),
    ["procurements"],
    {
      env: ENV,
      fetch: async (url, input) => {
        calls += 1;
        assert.equal(String(url), `${ENV.POG_A2_API_URL}/v2/procurements`);
        assert.equal(new TextDecoder().decode(input?.body as Uint8Array), body);
        assert.equal(
          new Headers(input?.headers).get("Authorization"),
          `Bearer ${TOKEN}`,
        );
        return new Response(
          JSON.stringify({
            error: {
              code: "role_forbidden",
              message: "Only Foundation may create procurement drafts",
            },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        );
      },
    },
  );
  assert.equal(calls, 1);
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error.code, "role_forbidden");
});

test("BFF cannot forward unfrozen endpoints or claim a declared route exists", async () => {
  for (const path of [
    `procurements/${ID}/chain/release-all`,
    `procurements/${ID}/chain/settlement`,
    `procurements/${ID}/ai-final`,
    `projects/${ID}/funding`,
    "redemptions",
    "payments",
  ]) {
    const response = await handleA2Proxy(
      req(path, "POST", "{}"),
      path.split("/"),
      {
        env: ENV,
        fetch: forbiddenFetch,
      },
    );
    assert.equal(response.status, 404);
    assert.equal((await response.json()).error.code, "a2_route_not_allowed");
  }
  const path = `signing-requests/${ID}/submit-signed`;
  const response = await handleA2Proxy(
    req(path, "POST", '{"confirm":true}'),
    path.split("/"),
    {
      env: ENV,
      fetch: async () =>
        new Response(JSON.stringify({ detail: "Not Found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
    },
  );
  assert.equal(response.status, 404);
  assert.notEqual((await response.json()).status, "confirmed");
});

test("frozen full-demo routes are declarations only and retain strict UUID-only building", () => {
  assert.equal(FULL_DEMO_ROUTES.length, 21);
  for (const declaration of FULL_DEMO_ROUTES) {
    const built = buildDemoRoute(
      declaration.id,
      parameters(declaration.template),
    );
    assert.equal(
      matchDemoRoute(built.method, built.path.split("/")),
      declaration,
    );
    assert.equal(built.backendStatus, "pending_backend_review");
    assert.equal(built.provesExecution, false);
    assert.equal(built.provesChainConfirmation, false);
  }
});

test("full-demo opt-in refuses faucet donations while default A2 stays compatible", async () => {
  for (const declaration of FULL_DEMO_ROUTES) {
    const built = buildDemoRoute(
      declaration.id,
      parameters(declaration.template),
    );
    const disabled = await handleA2Proxy(
      req(built.path, built.method, built.method === "POST" ? "{}" : undefined),
      built.path.split("/"),
      { env: ENV, fetch: forbiddenFetch },
    );
    assert.equal(disabled.status, 404);
    assert.equal((await disabled.json()).error.code, "full_demo_disabled");
  }
  const path = `projects/${ID}/donations`;
  const res = await handleA2Proxy(
    req(path, "POST", '{"amountAtomic":"10000"}'),
    path.split("/"),
    {
      env: { ...ENV, POG_FULL_DEMO: "true" },
      fetch: forbiddenFetch,
    },
  );
  assert.equal(res.status, 404);
  assert.equal((await res.json()).error.code, "funded_donation_required");
  let called = false;
  const legacy = await handleA2Proxy(
    req(path, "POST", '{"amountAtomic":"10000"}'),
    path.split("/"),
    {
      env: ENV,
      fetch: async () => {
        called = true;
        return new Response('{"operation":{"status":"queued"}}', {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      },
    },
  );
  assert.equal(called, true);
  assert.equal(legacy.status, 202);
});

test("queued HTTP 202 remains queued, never catalog-based chain confirmation", async () => {
  const path = `projects/${ID}/chain/create`;
  const response = await handleA2Proxy(
    req(path, "POST", "{}"),
    path.split("/"),
    {
      env: ENV,
      fetch: async () =>
        new Response(
          JSON.stringify({
            operation: {
              operationId: ID,
              status: "queued",
              chainVerified: false,
            },
          }),
          { status: 202, headers: { "Content-Type": "application/json" } },
        ),
    },
  );
  assert.equal(response.status, 202);
  const body = await response.json();
  assert.equal(body.operation.status, "queued");
  assert.equal(body.operation.chainVerified, false);
  assert.equal(Object.hasOwn(body.operation, "transactionHash"), false);
});
