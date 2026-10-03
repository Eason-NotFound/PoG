import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { NextRequest } from "next/server";
import { GET, POST } from "../src/app/api/payment/[...path]/route";
import { login, readDB } from "../src/lib/store";
import { SESSION_COOKIE } from "../src/lib/auth";
let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pog-api-funds-"));
  process.env.POG_DATA_DIR = dir;
  process.env.POG_DEMO_MODE = "true";
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));
const cookie = (who: string) =>
  `${SESSION_COOKIE}=${login(who, "PoG-demo-2026")!.token}`;
const context = (path: string) => ({
  params: Promise.resolve({ path: path.split("/") }),
});
test("native fund endpoint authenticates, blocks recipient writes and enforces Origin", async () => {
  assert.equal(
    (
      await GET(
        new NextRequest("http://localhost:3000/api/payment/state"),
        context("state"),
      )
    ).status,
    401,
  );
  const c = cookie("recipient");
  const r = await GET(
    new NextRequest("http://localhost:3000/api/payment/state", {
      headers: { cookie: c },
    }),
    context("state"),
  );
  assert.equal(r.status, 200);
  assert.equal((await r.json()).user.role, "recipient");
  const post = (origin: string) =>
    new NextRequest("http://localhost:3000/api/payment/operations/exchange", {
      method: "POST",
      headers: {
        cookie: c,
        host: "localhost:3000",
        origin,
        "Content-Type": "application/json",
        "Idempotency-Key": "api-test-request",
      },
      body: JSON.stringify({ projectId: "P-FLOW", amount: "60" }),
    });
  assert.equal(
    (await POST(post("http://evil.test"), context("operations/exchange")))
      .status,
    403,
  );
  assert.equal(
    (await POST(post("http://localhost:3000"), context("operations/exchange")))
      .status,
    403,
  );
  assert.equal(readDB().payment!.exchanges.length, 0);
});
test("native route freezes exactly once, rejects altered retries, and is no-store", async () => {
  const c = cookie("donor");
  const req = (amount: string) =>
    new NextRequest("http://localhost:3000/api/payment/operations/exchange", {
      method: "POST",
      headers: {
        cookie: c,
        host: "localhost:3000",
        origin: "http://localhost:3000",
        "Content-Type": "application/json",
        "Idempotency-Key": "api-test-exchange",
      },
      body: JSON.stringify({ projectId: "P-FLOW", amount }),
    });
  const one = await POST(req("60"), context("operations/exchange"));
  assert.equal(one.status, 200);
  assert.equal(one.headers.get("cache-control"), "no-store");
  const a = await one.json();
  const two = await POST(req("60"), context("operations/exchange"));
  assert.equal((await two.json()).id, a.id);
  assert.equal(
    (await POST(req("61"), context("operations/exchange"))).status,
    409,
  );
  assert.equal(readDB().payment!.exchanges.length, 1);
});
