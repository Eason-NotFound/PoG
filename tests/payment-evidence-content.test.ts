import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { A2_SESSION_COOKIE, handleA2Proxy } from "../src/lib/a2-proxy";
const ID = "00000000-0000-0000-0000-000000000001";
const TOKEN = "fixture-session-token-aaaaaaaaaaaaaaaaaaaaaa";
const HASH = `0x${"a".repeat(64)}`;
const ADDRESS = `0x${"b".repeat(40)}`;
const ENV = {
  POG_A2_INTEGRATION: "true",
  POG_FULL_DEMO: "true",
  POG_A2_API_URL: "http://127.0.0.1:18081",
};
const path = `payment-evidence/${ID}/content`;
const proof = {
  operationId: ID,
  transactionId: ID,
  txHash: HASH,
  receiptStatus: "1",
  blockNumber: "2",
  blockHash: HASH,
  emitter: ADDRESS,
  logIndex: "0",
  caller: ADDRESS,
  target: ADDRESS,
};
// Exact fields from payment_domain.canonical_evidence_bytes, not an economic
// success fixture. The production backend supplies these only after reconciliation.
const evidence = {
  schemaVersion: "pog-conversion-evidence-v1",
  kind: "conversion",
  mode: "simulation",
  binding: {
    namespaceId: ID,
    runId: "run-1",
    instanceId: "instance-1",
    chainId: "31337",
    registry: ADDRESS,
    escrow: ADDRESS,
    token: ADDRESS,
  },
  projectId: ID,
  projectChainId: HASH,
  procurementId: ID,
  procurementChainId: HASH,
  foundation: ADDRESS,
  treasury: ADDRESS,
  vendor: ADDRESS,
  token: ADDRESS,
  amountAtomic: "72000000",
  hkdCents: "7200",
  invoiceDocumentVersionId: ID,
  invoiceHash: HASH,
  releaseOperationId: ID,
  releaseProof: proof,
  redemptionResourceId: ID,
  redemptionOperationId: ID,
  redemptionProof: proof,
  redemptionJournalId: ID,
};
function canonical(value: unknown): string {
  function sorted(item: unknown): unknown {
    return item && typeof item === "object"
      ? Object.fromEntries(
          Object.entries(item)
            .sort(([a], [b]) => a.localeCompare(b, "en"))
            .map(([key, child]) => [key, sorted(child)]),
        )
      : item;
  }
  return JSON.stringify(sorted(value));
}
function request(route = path, method = "GET", body?: BodyInit): Request {
  return new Request(`http://127.0.0.1:3101/api/a2/${route}`, {
    method,
    body,
    ...(body instanceof ReadableStream ? { duplex: "half" as const } : {}),
    headers: {
      Host: "127.0.0.1:3101",
      Origin: "http://127.0.0.1:3101",
      Cookie: `${A2_SESSION_COOKIE}=${TOKEN}`,
    },
  });
}
test("actual canonical payment evidence schema reaches the browser byte-for-byte and hash-for-hash", async () => {
  for (const data of [
    evidence,
    {
      ...evidence,
      schemaVersion: "pog-supplier-payment-evidence-v1",
      kind: "supplier_payment",
      paymentResourceId: ID,
      paymentOperationId: ID,
      paymentJournalId: ID,
    },
  ]) {
    const raw = new TextEncoder().encode(canonical(data));
    const response = await handleA2Proxy(request(), path.split("/"), {
      env: ENV,
      fetch: async (url, input) => {
        assert.equal(String(url), `${ENV.POG_A2_API_URL}/v2/${path}`);
        assert.equal(
          new Headers(input?.headers).get("Authorization"),
          `Bearer ${TOKEN}`,
        );
        return new Response(raw, {
          headers: {
            "Content-Type": "application/json",
            "Set-Cookie": "forbidden=1",
          },
        });
      },
    });
    assert.equal(response.status, 200);
    const delivered = new Uint8Array(await response.arrayBuffer());
    assert.deepEqual(delivered, raw);
    assert.equal(
      createHash("sha256").update(delivered).digest("hex"),
      createHash("sha256").update(raw).digest("hex"),
    );
    assert.equal(response.headers.get("Content-Type"), "application/json");
    assert.equal(
      response.headers.get("Content-Disposition"),
      `attachment; filename="${ID}.json"`,
    );
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(response.headers.get("Set-Cookie"), null);
  }
});
test("unsafe, noncanonical or oversized committed JSON is refused rather than rewritten", async () => {
  const signature = `0x${"c".repeat(130)}`;
  for (const raw of [
    canonical({ ...evidence, signature }),
    canonical({
      ...evidence,
      binding: { ...evidence.binding, runId: signature },
    }),
    canonical({ ...evidence, amountAtomic: 72000000 }),
    canonical({ ...evidence, hkdCents: "7201" }),
    ` ${canonical(evidence)}`,
    canonical(evidence).replace(
      '"kind":"conversion"',
      '"kind":"conversion","kind":"conversion"',
    ),
  ]) {
    const response = await handleA2Proxy(request(), path.split("/"), {
      env: ENV,
      fetch: async () =>
        new Response(raw, { headers: { "Content-Type": "application/json" } }),
    });
    assert.equal(response.status, 502);
    const result = await response.json();
    assert.equal(result.error.code, "a2_invalid_payment_evidence");
    assert.doesNotMatch(JSON.stringify(result), /0xc{130}/);
  }
  const invalidHeaders: Record<string, string>[] = [
    { "Content-Type": "text/html" },
    {
      "Content-Type": "application/json",
      "Content-Length": String(2 * 1024 * 1024 + 1),
    },
  ];
  for (const headers of invalidHeaders) {
    const response = await handleA2Proxy(request(), path.split("/"), {
      env: ENV,
      fetch: async () => new Response("not evidence", { headers }),
    });
    assert.equal(response.status, 502);
  }
});
test("bodyless browser DELETE with a Next-style empty stream revokes and clears its real session", async () => {
  const route = "sessions/current";
  const empty = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.close();
    },
  });
  const response = await handleA2Proxy(
    request(route, "DELETE", empty),
    route.split("/"),
    {
      env: ENV,
      fetch: async (url, input) => {
        assert.equal(String(url), `${ENV.POG_A2_API_URL}/v2/${route}`);
        assert.equal(input?.method, "DELETE");
        assert.equal(input?.body, undefined);
        assert.equal(new Headers(input?.headers).get("Content-Type"), null);
        assert.equal(
          new Headers(input?.headers).get("Authorization"),
          `Bearer ${TOKEN}`,
        );
        return new Response('{"loggedOut":true}', {
          headers: { "Content-Type": "application/json" },
        });
      },
    },
  );
  assert.equal(response.status, 200);
  assert.equal((await response.json()).loggedOut, true);
  assert.match(response.headers.get("Set-Cookie")!, /Max-Age=0$/);
  const rejected = await handleA2Proxy(
    request(route, "DELETE", "{}"),
    route.split("/"),
    {
      env: ENV,
      fetch: async () => {
        assert.fail("Nonempty DELETE must not reach backend");
      },
    },
  );
  assert.equal(rejected.status, 400);
  assert.equal((await rejected.json()).error.code, "a2_unexpected_body");
});
