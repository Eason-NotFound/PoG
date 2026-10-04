import { test } from "node:test";
import assert from "node:assert/strict";
import {
  A2_JSON_BODY_LIMIT,
  A2_SESSION_COOKIE,
  A2_UPLOAD_BODY_LIMIT,
  handleA2Proxy,
  type A2ProxyOptions,
} from "../src/lib/a2-proxy";

const ID = "00000000-0000-0000-0000-000000000001";
const TOKEN = "a".repeat(43);
const USER = {
  id: ID,
  username: "foundation",
  role: "foundation",
  walletAddress: `0x${"a".repeat(40)}`,
};
const ENV = {
  POG_A2_INTEGRATION: "true",
  POG_A2_API_URL: "http://127.0.0.1:8080",
};
const options = (
  fetcher: typeof fetch,
  extra: A2ProxyOptions = {},
): A2ProxyOptions => ({ env: ENV, fetch: fetcher, ...extra });
const json = (
  data: unknown,
  status = 200,
  headers: Record<string, string> = {},
) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
function request(
  path: string,
  method = "GET",
  body?: string | FormData,
  headers: Record<string, string> = {},
) {
  return new Request(`http://localhost:3000/api/a2/${path}`, {
    method,
    body,
    headers: {
      Host: "localhost:3000",
      Origin: "http://localhost:3000",
      Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
      ...(typeof body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
      ...headers,
    },
  });
}
const forbiddenFetch: typeof fetch = async () => {
  assert.fail("Rejected request must not reach an upstream");
};
async function reject(
  req: Request,
  path: string[],
  status: number,
  extra: A2ProxyOptions = {},
) {
  const res = await handleA2Proxy(req, path, options(forbiddenFetch, extra));
  assert.equal(res.status, status);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  return res;
}

test("integration is opt-in and cannot fall back to the demo store", async () => {
  const res = await reject(request("me"), ["me"], 503, { env: {} });
  assert.equal((await res.json()).error.code, "a2_integration_disabled");
});

test("enabled integration without a fixed endpoint fails closed", async () => {
  await reject(request("me"), ["me"], 503, {
    env: { POG_A2_INTEGRATION: "true" },
  });
});

test("only an explicit numeric HTTP loopback endpoint is accepted", async () => {
  for (const endpoint of [
    "https://127.0.0.1:8080",
    "http://localhost:8080",
    "http://example.com:8080",
    "http://127.1:8080",
    "http://2130706433:8080",
    "http://127.0.0.1:80",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:8080/v2",
    "http://127.0.0.1:8080?rpc=x",
    "http://user:password@127.0.0.1:8080",
    "http://127.0.0.1:8080#fragment",
    " http://127.0.0.1:8080",
  ]) {
    await reject(request("me"), ["me"], 503, {
      env: { ...ENV, POG_A2_API_URL: endpoint },
    });
  }
});

test("legacy cookie and a client Authorization header cannot create A2 authority", async () => {
  await reject(
    request("me", "GET", undefined, {
      Cookie: `pog_session=${TOKEN}`,
      Authorization: `Bearer ${TOKEN}`,
    }),
    ["me"],
    401,
  );
});

test("duplicate or malformed A2 cookies are rejected without forwarding", async () => {
  for (const cookie of [
    `${A2_SESSION_COOKIE}=short`,
    `${A2_SESSION_COOKIE}=${TOKEN}; ${A2_SESSION_COOKIE}=${TOKEN}`,
    `${A2_SESSION_COOKIE}=${TOKEN}%0A`,
  ])
    await reject(
      request("projects", "GET", undefined, { Cookie: cookie }),
      ["projects"],
      401,
    );
});

test("POST requires a valid Origin and Host even for session login", async () => {
  const variants: Record<string, string>[] = [
    { Origin: "" },
    { Host: "" },
    { Origin: "http://attacker.example" },
    { Origin: "null" },
    { Origin: "https://localhost:3000" },
    { Origin: "http://localhost:3000/" },
    { Origin: "http://localhost:3000@attacker.example" },
    { Host: "localhost:3000,attacker.example" },
  ];
  for (const headers of variants)
    await reject(request("sessions", "POST", "{}", headers), ["sessions"], 403);
});

test("DELETE CSRF rejection cannot clear the legitimate cookie", async () => {
  const res = await reject(
    request("sessions/current", "DELETE", undefined, {
      Origin: "http://attacker.example",
    }),
    ["sessions", "current"],
    403,
  );
  assert.equal(res.headers.get("set-cookie"), null);
});

test("forwarded-host spoofing cannot bypass the Origin guard", async () => {
  await reject(
    request("projects", "POST", "{}", {
      Origin: "http://attacker.example",
      "X-Forwarded-Host": "attacker.example",
      "Idempotency-Key": "key",
    }),
    ["projects"],
    403,
  );
});

test("the exact route/method allowlist excludes admin, RPC, payment and release", async () => {
  for (const path of [
    "rpc",
    "admin",
    "reset",
    "health",
    "ready",
    "v2/me",
    "projects/current",
    `projects/${ID}/close`,
    `procurements/${ID}/chain/release`,
    `procurements/${ID}/chain/settlement`,
    `procurements/${ID}/payment`,
    "payments",
    "sessions/current/anything",
  ])
    await reject(request(path), path.split("/"), 404);
  await reject(request("projects", "PATCH", "{}"), ["projects"], 405);
  await reject(request("me", "POST", "{}"), ["me"], 404);
});

test("path encoding, path escape and parameter/context mismatch are rejected", async () => {
  for (const parts of [
    ["projects", "..", "me"],
    ["projects", "%2e%2e", "me"],
    ["projects", `${ID}%2Fcontent`],
    ["projects", "http://127.0.0.1:8545"],
    ["projects", `${ID}\\content`],
    ["me\u0000"],
    [""],
  ])
    await reject(request("me"), parts, 404);
  await reject(request("projects"), ["me"], 404);
  await reject(request("%6de"), ["me"], 404);
});

test("query and URL overrides are never forwarded", async () => {
  for (const query of [
    "?rpcUrl=http://127.0.0.1:8545",
    "?url=https://attacker.example",
    "?role=foundation",
    "?chainId=1",
  ])
    await reject(request(`projects${query}`), ["projects"], 404);
});

test("successful login sends credentials only to the configured session route and stores token in HttpOnly cookie", async () => {
  const issued = "b".repeat(43);
  const now = Date.parse("2026-10-03T10:00:00Z");
  const res = await handleA2Proxy(
    request(
      "sessions",
      "POST",
      '{"username":"foundation","password":"synthetic-password"}',
      { Cookie: "" },
    ),
    ["sessions"],
    options(
      async (url, init) => {
        assert.equal(String(url), "http://127.0.0.1:8080/v2/sessions");
        assert.equal(new Headers(init?.headers).get("authorization"), null);
        assert.match(
          new TextDecoder().decode(init?.body as Uint8Array),
          /synthetic-password/,
        );
        return json({
          token: issued,
          expiresAt: "2026-10-03T11:00:00Z",
          user: {
            ...USER,
            password: "do-not-return",
            privateKey: "do-not-return",
          },
        });
      },
      { now: () => now },
    ),
  );
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), {
    user: USER,
    expiresAt: "2026-10-03T11:00:00Z",
  });
  assert.equal(
    res.headers.get("set-cookie"),
    `${A2_SESSION_COOKIE}=${issued}; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600`,
  );
});

test("HTTPS/configured secure cookie is server-selected and session max-age is bounded", async () => {
  const res = await handleA2Proxy(
    request("sessions", "POST", "{}"),
    ["sessions"],
    options(
      async () =>
        json({ token: TOKEN, expiresAt: "2026-11-03T10:00:00Z", user: USER }),
      {
        env: { ...ENV, POG_COOKIE_SECURE: "true" },
        now: () => Date.parse("2026-10-03T10:00:00Z"),
      },
    ),
  );
  assert.match(res.headers.get("set-cookie")!, /Max-Age=86400; Secure$/);
});

test("malformed or expired login response cannot set a session", async () => {
  for (const data of [
    { user: USER },
    { token: "short", expiresAt: "2026-10-03T11:00:00Z", user: USER },
    { token: TOKEN, expiresAt: "2026-10-03T09:00:00Z", user: USER },
    {
      token: TOKEN,
      expiresAt: "2026-10-03T11:00:00Z",
      user: { ...USER, role: "admin" },
    },
  ]) {
    const res = await handleA2Proxy(
      request("sessions", "POST", "{}"),
      ["sessions"],
      options(async () => json(data), {
        now: () => Date.parse("2026-10-03T10:00:00Z"),
      }),
    );
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("set-cookie"), null);
  }
});

test("me uses upstream identity, forwards only the isolated cookie token and strips extra credentials", async () => {
  const res = await handleA2Proxy(
    request("me", "GET", undefined, {
      Authorization: "Bearer spoofed",
      "X-Role": "admin",
    }),
    ["me"],
    options(async (_url, init) => {
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("authorization"), `Bearer ${TOKEN}`);
      assert.equal(headers.get("cookie"), null);
      assert.equal(headers.get("x-role"), null);
      return json({ ...USER, password: "hidden", token: "hidden" }, 200, {
        "Set-Cookie": "attacker=1",
      });
    }),
  );
  assert.deepEqual(await res.json(), USER);
  assert.equal(res.headers.get("set-cookie"), null);
});

test("upstream 401 remains unauthorized and clears stale A2 session only", async () => {
  const res = await handleA2Proxy(
    request("me"),
    ["me"],
    options(async () =>
      json(
        { error: { code: "invalid_session", message: "Session invalid" } },
        401,
      ),
    ),
  );
  assert.equal(res.status, 401);
  assert.match(res.headers.get("set-cookie")!, /^pog_a2_session=;.*Max-Age=0$/);
  assert.equal((await res.json()).error.code, "invalid_session");
});

test("logout revokes the API session and clears cookie without claiming chain changes", async () => {
  const res = await handleA2Proxy(
    request("sessions/current", "DELETE"),
    ["sessions", "current"],
    options(async (url, init) => {
      assert.equal(String(url), "http://127.0.0.1:8080/v2/sessions/current");
      assert.equal(init?.method, "DELETE");
      assert.equal(
        new Headers(init?.headers).get("authorization"),
        `Bearer ${TOKEN}`,
      );
      return json({ loggedOut: true, alreadyLoggedOut: false });
    }),
  );
  assert.deepEqual(await res.json(), {
    loggedOut: true,
    alreadyLoggedOut: false,
  });
  assert.match(res.headers.get("set-cookie")!, /Max-Age=0$/);
});

test("failed logout clears local cookie but explicitly reports inability to reach API", async () => {
  const res = await handleA2Proxy(
    request("sessions/current", "DELETE"),
    ["sessions", "current"],
    options(async () => {
      throw new Error("internal password=do-not-print");
    }),
  );
  assert.equal(res.status, 502);
  assert.match(res.headers.get("set-cookie")!, /Max-Age=0$/);
  assert.doesNotMatch(await res.text(), /do-not-print/);
});

test("mutations require valid idempotency keys and never generate a replacement", async () => {
  for (const key of ["", "a".repeat(129), "é"])
    await reject(
      request("projects", "POST", "{}", { "Idempotency-Key": key }),
      ["projects"],
      400,
    );
});

test("idempotency key and atomic amounts are forwarded unchanged; 202 is never upgraded to confirmed", async () => {
  const payload = '{"amountAtomic":"100000000"}';
  const data = {
    operation: { operationId: ID, status: "queued", chainVerified: false },
  };
  const res = await handleA2Proxy(
    request(`projects/${ID}/donations`, "POST", payload, {
      "Idempotency-Key": "donation-original-key",
    }),
    ["projects", ID, "donations"],
    options(async (_url, init) => {
      assert.equal(
        new Headers(init?.headers).get("idempotency-key"),
        "donation-original-key",
      );
      assert.equal(new TextDecoder().decode(init?.body as Uint8Array), payload);
      assert.equal(init?.redirect, "manual");
      assert.equal(init?.cache, "no-store");
      return json(data, 202);
    }),
  );
  assert.equal(res.status, 202);
  assert.deepEqual(await res.json(), data);
});

test("every allowed A2 mutation maps to precisely its /v2 endpoint", async () => {
  for (const path of [
    "projects",
    "procurements",
    `projects/${ID}/chain/create`,
    `procurements/${ID}/chain/create`,
    `procurements/${ID}/chain/purchase-order`,
    `procurements/${ID}/chain/invoice-and-goods`,
    `procurements/${ID}/chain/reserve`,
    `procurements/${ID}/signing-requests`,
    `signing-requests/${ID}/sign-demo`,
    `signing-requests/${ID}/submit`,
    `signing-requests/${ID}/submit-signed`,
  ]) {
    const res = await handleA2Proxy(
      request(path, "POST", "{}", { "Idempotency-Key": "original" }),
      path.split("/"),
      options(async (url) => {
        assert.equal(String(url), `http://127.0.0.1:8080/v2/${path}`);
        return json({ operation: { status: "queued" } }, 202);
      }),
    );
    assert.equal(res.status, 202);
  }
});

test("unimplemented submit-signed remains an API error, never a mock success", async () => {
  const path = `signing-requests/${ID}/submit-signed`;
  const res = await handleA2Proxy(
    request(path, "POST", '{"confirm":true}', {
      "Idempotency-Key": "original",
    }),
    path.split("/"),
    options(async () =>
      json(
        {
          error: {
            code: "route_not_found",
            message: "Request could not be processed",
          },
        },
        404,
      ),
    ),
  );
  assert.equal(res.status, 404);
  assert.equal((await res.json()).error.code, "route_not_found");
});

test("JSON content type, syntax, object shape and bounded UTF-8 are validated before forwarding", async () => {
  await reject(
    request("projects", "POST", "{}", {
      "Idempotency-Key": "key",
      "Content-Type": "text/plain",
    }),
    ["projects"],
    415,
  );
  for (const body of ["broken", "[]", "null", '"string"'])
    await reject(
      request("projects", "POST", body, { "Idempotency-Key": "key" }),
      ["projects"],
      400,
    );
  await reject(
    request(
      "projects",
      "POST",
      JSON.stringify({ title: "x".repeat(A2_JSON_BODY_LIMIT) }),
      { "Idempotency-Key": "key" },
    ),
    ["projects"],
    413,
  );
});

test("announced oversized or invalid content-length is refused", async () => {
  for (const length of [String(A2_JSON_BODY_LIMIT + 1), "-1", "not-a-number"])
    await reject(
      request("projects", "POST", "{}", {
        "Idempotency-Key": "key",
        "Content-Length": length,
      }),
      ["projects"],
      413,
    );
});

test("invalid UTF-8 JSON is rejected before the API receives a request", async () => {
  const req = new Request("http://localhost:3000/api/a2/projects", {
    method: "POST",
    headers: {
      Host: "localhost:3000",
      Origin: "http://localhost:3000",
      Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
      "Idempotency-Key": "key",
      "Content-Type": "application/json",
    },
    body: new Uint8Array([123, 34, 120, 34, 58, 34, 255, 34, 125]),
  });
  await reject(req, ["projects"], 400);
});

test("signed authorization material is never returned by a successful BFF DTO", async () => {
  const path = `signing-requests/${ID}`;
  const digest = `0x${"a".repeat(64)}`;
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(async () =>
      json({
        id: ID,
        digest,
        signature: "sensitive-signature",
        nested: {
          rawSignature: "sensitive-signature",
          signedTransaction: "sensitive-transaction",
          passwordHash: "sensitive-password",
          deadline: "100",
        },
      }),
    ),
  );
  assert.deepEqual(await res.json(), {
    id: ID,
    digest,
    nested: { deadline: "100" },
  });
});

test("multipart upload preserves original bytes, boundary and document-version response", async () => {
  const form = new FormData();
  form.set("procurementId", ID);
  form.set("category", "receipt_evidence");
  form.set(
    "file",
    new Blob(["synthetic-pdf-bytes"], { type: "application/pdf" }),
    "synthetic.pdf",
  );
  const req = request("documents", "POST", form, {
    "Idempotency-Key": "immutable-doc-key",
  });
  const original = new Uint8Array(await req.clone().arrayBuffer());
  const data = {
    document: {
      id: ID,
      versionId: ID,
      version: 1,
      keccak256: `0x${"a".repeat(64)}`,
    },
    operation: { status: "awaiting_authorization" },
  };
  const res = await handleA2Proxy(
    req,
    ["documents"],
    options(async (_url, init) => {
      assert.deepEqual(init?.body, original);
      assert.equal(
        new Headers(init?.headers).get("content-type"),
        req.headers.get("content-type"),
      );
      return json(data, 202);
    }),
  );
  assert.equal(res.status, 202);
  assert.deepEqual(await res.json(), data);
});

test("documents require multipart and honor the API file-plus-overhead cap", async () => {
  await reject(
    request("documents", "POST", "{}", { "Idempotency-Key": "key" }),
    ["documents"],
    415,
  );
  await reject(
    request("documents", "POST", "small", {
      "Idempotency-Key": "key",
      "Content-Type": "multipart/form-data; boundary=fixture",
      "Content-Length": String(A2_UPLOAD_BODY_LIMIT + 1),
    }),
    ["documents"],
    413,
  );
});

test("private document download forwards cookie authority and forces safe attachment headers", async () => {
  const content = new Uint8Array([1, 2, 3]);
  const path = `documents/${ID}/content`;
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(
      async () =>
        new Response(content, {
          headers: {
            "Content-Type": "application/pdf",
            "Set-Cookie": "leak=1",
            "Content-Disposition": 'inline; filename="unsafe.html"',
            "Access-Control-Allow-Origin": "*",
          },
        }),
    ),
  );
  assert.deepEqual(new Uint8Array(await res.arrayBuffer()), content);
  assert.equal(
    res.headers.get("Content-Disposition"),
    `attachment; filename="${ID}"`,
  );
  assert.equal(res.headers.get("X-Content-Type-Options"), "nosniff");
  assert.equal(res.headers.get("set-cookie"), null);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), null);
});

test("document content refuses HTML and oversized upstream files", async () => {
  const path = `documents/${ID}/content`;
  for (const response of [
    new Response("<script>bad()</script>", {
      headers: { "Content-Type": "text/html" },
    }),
    new Response("small", {
      headers: {
        "Content-Type": "application/pdf",
        "Content-Length": String(10 * 1024 * 1024 + 1),
      },
    }),
  ]) {
    const res = await handleA2Proxy(
      request(path),
      path.split("/"),
      options(async () => response),
    );
    assert.equal(res.status, 502);
  }
});

test("confirmed operation facts remain exact, not inferred from a transaction hash", async () => {
  const path = `operations/${ID}`;
  const data = {
    operationId: ID,
    status: "confirmed",
    chainVerified: true,
    steps: [
      {
        status: "confirmed",
        transaction: {
          transactionHash: `0x${"a".repeat(64)}`,
          receiptStatus: 1,
          canonical: true,
          blockNumber: 21,
        },
      },
    ],
  };
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(async () => json(data)),
  );
  assert.deepEqual(await res.json(), data);
});

test("safe upstream error codes, operation IDs and details survive while secrets/internal paths are removed", async () => {
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(async () =>
      json(
        {
          error: {
            code: "signing_nonce_in_use",
            operationId: ID,
            message: `Cannot contact http://127.0.0.1:8545 /Users/private/file Bearer ${TOKEN}`,
            details: {
              nonceFamily: "human",
              expected: "1",
              token: TOKEN,
              password: "secret",
            },
          },
        },
        409,
      ),
    ),
  );
  const body = await res.json();
  assert.equal(res.status, 409);
  assert.equal(body.error.code, "signing_nonce_in_use");
  assert.equal(body.error.operationId, ID);
  assert.deepEqual(body.error.details, { nonceFamily: "human", expected: "1" });
  assert.doesNotMatch(JSON.stringify(body), /8545|\/Users\/|Bearer a|secret/);
});

test("HTTP 200 failed and requires_attention operation diagnostics redact worker secrets while preserving identifiers and typedData", async () => {
  const hash = `0x${"a".repeat(64)}`;
  const businessText =
    "Ordinary delivery note /Users/warehouse/example and Bearer public-business-note";
  const path = `operations/${ID}`;
  for (const status of ["failed", "requires_attention"]) {
    const data = {
      operationId: ID,
      status,
      chainVerified: false,
      errorCode: "chain_unavailable",
      errorMessage: `Worker failed Bearer different-fixture-token /Users/operator/private/evidence.pdf ${TOKEN} http://127.0.0.1:8545`,
      transactionHash: hash,
      title: businessText,
      typedData: {
        message: {
          reportHash: hash,
          notice: businessText,
          nonce: "9007199254740993",
        },
      },
    };
    const res = await handleA2Proxy(
      request(path),
      path.split("/"),
      options(async () => json(data)),
    );
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, status);
    assert.equal(body.operationId, ID);
    assert.equal(body.errorCode, "chain_unavailable");
    assert.equal(body.transactionHash, hash);
    assert.equal(body.title, businessText);
    assert.deepEqual(body.typedData, data.typedData);
    assert.doesNotMatch(
      body.errorMessage,
      /different-fixture-token|\/Users\/|8545|a{43}/,
    );
    assert.match(body.errorMessage, /\[redacted\]/);
  }
});

test("HTTP 200 error/detail contexts are scrubbed without rewriting safe nested hashes, codes or ordinary detail", async () => {
  const path = `operations/${ID}`;
  const hash = `0x${"a".repeat(64)}`;
  const ordinary =
    "Business detail /private/tmp/customer-note Bearer public-note";
  const data = {
    operationId: ID,
    detail: ordinary,
    error: {
      code: "chain_nonce_history_conflict",
      operationId: ID,
      message: "Bearer fixture-token /private/tmp/worker-state",
      detail: "Bearer another-fixture-token /Users/operator/private/chain.json",
      details: {
        hash,
        expectedNonce: "1",
        reason: "Bearer nested-token /tmp/raw.log",
      },
    },
  };
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(async () => json(data)),
  );
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.operationId, ID);
  // A detail next to an actual error is diagnostic; arbitrary business fields
  // below unrelated objects remain ordinary text.
  assert.doesNotMatch(body.detail, /private\/tmp|public-note/);
  assert.equal(body.error.code, "chain_nonce_history_conflict");
  assert.equal(body.error.operationId, ID);
  assert.equal(body.error.details.hash, hash);
  assert.equal(body.error.details.expectedNonce, "1");
  assert.doesNotMatch(
    JSON.stringify(body.error),
    /fixture-token|nested-token|\/Users\/|\/tmp\//,
  );
  const ordinaryRes = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(async () =>
      json({ operationId: ID, status: "queued", detail: ordinary }),
    ),
  );
  assert.deepEqual(await ordinaryRes.json(), {
    operationId: ID,
    status: "queued",
    detail: ordinary,
  });
});

test("HTTP 200 known diagnostic fields handle nulls and primitive error strings safely", async () => {
  const path = `operations/${ID}`;
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(async () =>
      json({
        operationId: ID,
        errorMessage: null,
        error: "Bearer primitive-token /Users/operator/raw.log",
      }),
    ),
  );
  const body = await res.json();
  assert.equal(body.errorMessage, null);
  assert.equal(body.operationId, ID);
  assert.doesNotMatch(body.error, /primitive-token|\/Users\//);
});

test("HTTP 200 diagnostic raw signatures are redacted without treating addresses/hashes or typedData text as signatures", async () => {
  const path = `operations/${ID}`;
  const signature = `0x${"c".repeat(130)}`;
  const address = `0x${"a".repeat(40)}`;
  const hash = `0x${"a".repeat(64)}`;
  for (const errorMessage of [
    signature,
    `Signature ${signature}; address ${address}; commitment ${hash}`,
  ]) {
    const res = await handleA2Proxy(
      request(path),
      path.split("/"),
      options(async () =>
        json({
          operationId: ID,
          status: "requires_attention",
          errorCode: "signature_invalid",
          errorMessage,
          walletAddress: address,
          transactionHash: hash,
          typedData: {
            message: { bytesPresentation: signature, reportHash: hash },
          },
        }),
      ),
    );
    const body = await res.json();
    assert.equal(res.status, 200);
    assert.equal(body.operationId, ID);
    assert.equal(body.errorCode, "signature_invalid");
    assert.equal(body.walletAddress, address);
    assert.equal(body.transactionHash, hash);
    assert.deepEqual(body.typedData, {
      message: { bytesPresentation: signature, reportHash: hash },
    });
    assert.doesNotMatch(body.errorMessage, /c{130}/);
    assert.match(body.errorMessage, /\[signature redacted\]/);
    if (errorMessage !== signature) {
      assert.ok(body.errorMessage.includes(address));
      assert.ok(body.errorMessage.includes(hash));
    }
  }
});

test("HTTP error status does not make unrelated typedData or business text diagnostic", async () => {
  const ordinary = "Business note /Users/warehouse/info Bearer public-note";
  const data = {
    error: {
      code: "validation_error",
      message: "Worker path /Users/operator/private Bearer secret-fixture",
    },
    title: ordinary,
    typedData: {
      message: { notice: ordinary, reportHash: `0x${"a".repeat(64)}` },
    },
  };
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(async () => json(data, 409)),
  );
  const body = await res.json();
  assert.equal(body.title, ordinary);
  assert.deepEqual(body.typedData, data.typedData);
  assert.equal(body.error.code, "validation_error");
  assert.doesNotMatch(body.error.message, /secret-fixture|\/Users\//);
});

test("API transport failure returns a sanitized unavailable error and never imports legacy data", async () => {
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(async () => {
      throw new Error(`connect privateKey=secret ${TOKEN}`);
    }),
  );
  assert.equal(res.status, 502);
  assert.equal((await res.json()).error.code, "a2_upstream_unavailable");
});

test("redirects cannot move tokens or uploads to another endpoint", async () => {
  let calls = 0;
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(async (_url, init) => {
      calls++;
      assert.equal(init?.redirect, "manual");
      return new Response(null, {
        status: 307,
        headers: {
          Location: "http://attacker.example",
          "Set-Cookie": "leak=1",
        },
      });
    }),
  );
  assert.equal(calls, 1);
  assert.equal(res.status, 502);
  assert.equal(res.headers.get("location"), null);
  assert.equal(res.headers.get("set-cookie"), null);
});

test("unresponsive upstream fetch times out and aborts without a blind retry", async () => {
  let signal: AbortSignal | null | undefined;
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(
      async (_url, init) => {
        signal = init?.signal;
        return new Promise<Response>(() => {});
      },
      { timeoutMs: 10 },
    ),
  );
  assert.equal(res.status, 504);
  assert.equal(signal?.aborted, true);
  assert.match((await res.json()).error.message, /outcome.*checked/);
});

test("timeout covers a stalled response body, not only receipt of headers", async () => {
  let signal: AbortSignal | null | undefined;
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(
      async (_url, init) => {
        signal = init?.signal;
        return new Response(new ReadableStream<Uint8Array>({ start() {} }), {
          headers: { "Content-Type": "application/json" },
        });
      },
      { timeoutMs: 10 },
    ),
  );
  assert.equal(res.status, 504);
  assert.equal(signal?.aborted, true);
});

test("non-JSON, malformed JSON and oversized JSON upstream responses fail closed", async () => {
  for (const response of [
    new Response("not-json", { headers: { "Content-Type": "text/plain" } }),
    new Response("broken", { headers: { "Content-Type": "application/json" } }),
    json({ unexpected: "x".repeat(2 * 1024 * 1024) }),
  ]) {
    const res = await handleA2Proxy(
      request("projects"),
      ["projects"],
      options(async () => response),
    );
    assert.equal(res.status, 502);
  }
});

test("response uint256 maximum and unsafe typedData integers are exact decimal-text presentations", async () => {
  const maximum =
    "115792089237316195423570985008687907853269984665640564039457584007913129639935";
  const raw = `{"nonce":"9007199254740993","typedData":{"domain":{"chainId":31337},"message":{"invoiceAmount":${maximum},"nonce":9007199254740993,"safe":9007199254740991,"zero":0}},"values":[9007199254740992,-9007199254740993,-9007199254740991]}`;
  const path = `signing-requests/${ID}`;
  const res = await handleA2Proxy(
    request(path),
    path.split("/"),
    options(
      async () =>
        new Response(raw, { headers: { "Content-Type": "application/json" } }),
    ),
  );
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), {
    nonce: "9007199254740993",
    typedData: {
      domain: { chainId: 31337 },
      message: {
        invoiceAmount: maximum,
        nonce: "9007199254740993",
        safe: 9007199254740991,
        zero: 0,
      },
    },
    values: ["9007199254740992", "-9007199254740993", -9007199254740991],
  });
});

test("integer substrings, escaped quotes, backslashes and Unicode in quoted strings are never number tokens", async () => {
  const text = '9007199254740993 / 1e999 / 0.125 / "quoted" / \\ / 中文 / 😀';
  const data = { note: text, nested: [text], safe: 7 };
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(async () => json(data)),
  );
  assert.deepEqual(await res.json(), data);
});

test("escaped Unicode quote and surrogate-pair boundaries cannot expose string contents to numeric parsing", async () => {
  const raw =
    '{"note":"\\u00229007199254740993\\u0022","emoji":"\\ud83d\\ude00","backslash":"\\u005c","unsafe":9007199254740993}';
  const res = await handleA2Proxy(
    request("projects"),
    ["projects"],
    options(
      async () =>
        new Response(raw, { headers: { "Content-Type": "application/json" } }),
    ),
  );
  assert.deepEqual(await res.json(), {
    note: '"9007199254740993"',
    emoji: "😀",
    backslash: "\\",
    unsafe: "9007199254740993",
  });
});

test("fractional, exponential and negative-zero response tokens fail closed instead of float reconstruction", async () => {
  for (const number of [
    "0.5",
    "1.0",
    "1e0",
    "9007199254740993e0",
    "1e999",
    "-1.25",
    "-0",
  ]) {
    const raw = `{"numeric":${number}}`;
    const res = await handleA2Proxy(
      request("projects"),
      ["projects"],
      options(
        async () =>
          new Response(raw, {
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    assert.equal(res.status, 502, number);
    assert.equal(
      (await res.json()).error.code,
      "a2_noncanonical_json_number",
      number,
    );
  }
});

test("unsafe-number handling cannot repair malformed JSON or an unquoted object key", async () => {
  for (const raw of [
    '{"value":09007199254740993}',
    "{9007199254740993:1}",
    '{"safe":1,9007199254740993:2}',
    '{"value":+9007199254740993}',
    '{"value":9007199254740993,}',
    '{"value":[9007199254740993,]}',
    '{"value":9007199254740993} trailing',
    '{"value":"unterminated 9007199254740993}',
    '{"value":--9007199254740993}',
  ]) {
    const res = await handleA2Proxy(
      request("projects"),
      ["projects"],
      options(
        async () =>
          new Response(raw, {
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    assert.equal(res.status, 502, raw);
    assert.equal((await res.json()).error.code, "a2_invalid_json", raw);
  }
});

test("isolated escaped surrogates in response strings fail closed", async () => {
  for (const raw of [
    '{"note":"\\ud800"}',
    '{"note":"\\udc00"}',
    '{"note":"\\ud800x"}',
  ]) {
    const res = await handleA2Proxy(
      request("projects"),
      ["projects"],
      options(
        async () =>
          new Response(raw, {
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    assert.equal(res.status, 502);
    assert.equal((await res.json()).error.code, "a2_invalid_json");
  }
});

test("response presentation never converts invalid client amount-number bytes into accepted atomic strings", async () => {
  for (const amount of [
    "9007199254740993",
    "-9007199254740993",
    "1e3",
    "1.25",
  ]) {
    const raw = `{"amountAtomic":${amount}}`;
    const path = `projects/${ID}/donations`;
    const rejected = {
      error: {
        code: "validation_error",
        message: "Request validation failed",
        details: [{ location: ["body", "amountAtomic"], type: "string_type" }],
      },
    };
    const res = await handleA2Proxy(
      request(path, "POST", raw, { "Idempotency-Key": "original-key" }),
      path.split("/"),
      options(async (_url, init) => {
        assert.equal(new TextDecoder().decode(init?.body as Uint8Array), raw);
        assert.equal(
          new Headers(init?.headers).get("idempotency-key"),
          "original-key",
        );
        return json(rejected, 422);
      }),
    );
    assert.equal(res.status, 422);
    assert.deepEqual(await res.json(), rejected);
  }
});
