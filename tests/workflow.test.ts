import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { publicUser, readDB, sha } from "../src/lib/store";
import {
  ApiError,
  evidenceFor,
  execute,
  uploadEvidence,
} from "../src/lib/service";
let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pog-flow-"));
  process.env.POG_DATA_DIR = dir;
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));
const u = (id: string) => publicUser(readDB().users.find((x) => x.id === id)!);
const fails = (f: () => unknown, status: number) =>
  assert.throws(
    f,
    (e: unknown) => e instanceof ApiError && e.status === status,
  );
function approveAudit(procurementId: string, stage: "purchase" | "delivery") {
  const review = readDB()
    .reviews.filter(
      (r) => r.procurementId === procurementId && r.stage === stage,
    )
    .at(-1)!;
  execute(
    u("admin"),
    "reviewCase",
    { reviewId: review.id, decision: "approved", reason: "已核对本次材料" },
    "audit-" + review.id,
  );
}
function invariant() {
  const p = readDB().projects[0];
  assert.equal(p.deposited, p.available + p.reserved + p.paid);
  assert.ok(p.available >= 0 && p.reserved >= 0);
}
test("donation creates an immutable content hash but does not fabricate escrow funding", () => {
  const before = readDB().projects[0];
  const r = execute(
    u("donor"),
    "donate",
    { projectId: "P-001", amount: 30000 },
    "donate-once-01",
  );
  const db = readDB();
  assert.equal(db.projects[0].available, before.available);
  assert.equal(db.donations.length, 4);
  const record = db.records.find((x) => x.id === r.id)!;
  assert.equal(
    record.hash,
    sha(
      JSON.stringify({
        id: record.id,
        kind: record.kind,
        version: record.version,
        createdAt: record.createdAt,
        content: record.snapshot,
      }),
    ),
  );
});
test("budget reservation is idempotent and rejects repeated or altered requests", () => {
  const args = { procurementId: "PR-003" };
  approveAudit("PR-003", "purchase");
  const result = execute(
    u("foundation"),
    "approvePurchase",
    args,
    "reserve-unique",
  );
  assert.deepEqual(
    execute(u("foundation"), "approvePurchase", args, "reserve-unique"),
    result,
  );
  assert.equal(readDB().projects[0].reserved, 20000);
  invariant();
  fails(
    () => execute(u("foundation"), "approvePurchase", args, "reserve-again"),
    409,
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePurchase",
        { procurementId: "PR-002" },
        "reserve-unique",
      ),
    409,
  );
  invariant();
});
test("recipient cannot approve and another foundation cannot operate a foreign project", () => {
  fails(
    () =>
      execute(
        u("recipient"),
        "approvePurchase",
        { procurementId: "PR-003" },
        "unauthorized",
      ),
    403,
  );
  fails(
    () =>
      execute(
        { ...u("foundation"), orgId: "different-org" },
        "approvePurchase",
        { procurementId: "PR-003" },
        "foreign-scope",
      ),
    403,
  );
});
test("frozen and incomplete claims cannot trigger payment", () => {
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePurchase",
        { procurementId: "PR-004" },
        "frozen-reserve",
      ),
    409,
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePayment",
        { procurementId: "PR-002" },
        "without-delivery",
      ),
    409,
  );
  invariant();
});
test("uploads are byte-hashed, private and validated; final payment requires evidence and is one-time", () => {
  const recipient = u("recipient"),
    c = Buffer.from("%PDF-1.4\nPoG demo evidence\n%%EOF").toString("base64");
  fails(
    () =>
      uploadEvidence(u("foundation"), {
        name: "fake.pdf",
        procurementId: "PR-002",
        type: "invoice",
        content: Buffer.from("<script>bad</script>").toString("base64"),
      }),
    400,
  );
  fails(
    () =>
      execute(
        recipient,
        "deliver",
        { procurementId: "PR-002", quantity: 12, note: "验收完成" },
        "no-evidence-yet",
      ),
    400,
  );
  const invoice = uploadEvidence(u("foundation"), {
    name: "invoice.pdf",
    procurementId: "PR-002",
    type: "invoice",
    content: c,
  });
  uploadEvidence(recipient, {
    name: "grn.pdf",
    procurementId: "PR-002",
    type: "grn",
    content: c,
  });
  uploadEvidence(recipient, {
    name: "receipt.png",
    procurementId: "PR-002",
    type: "photo",
    content: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]).toString(
      "base64",
    ),
  });
  execute(
    u("foundation"),
    "submitFoundationProof",
    { procurementId: "PR-002", note: "采购发票已核对" },
    "foundation-proof",
  );
  assert.equal(invoice.hash, sha(Buffer.from(c, "base64")));
  assert.equal(evidenceFor(recipient, invoice.id).name, "invoice.pdf");
  fails(() => evidenceFor(u("donor"), invoice.id), 404);
  fails(() => evidenceFor(u("maintainer"), invoice.id), 404);
  execute(
    recipient,
    "deliver",
    { procurementId: "PR-002", quantity: 12, note: "全部到货验收完成" },
    "submit-delivery",
  );
  approveAudit("PR-002", "delivery");
  execute(
    u("foundation"),
    "approvePayment",
    { procurementId: "PR-002" },
    "pay-vendor-once",
  );
  assert.equal(readDB().projects[0].paid, 32000);
  assert.equal(readDB().projects[0].reserved, 0);
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePayment",
        { procurementId: "PR-002" },
        "pay-vendor-twice",
      ),
    409,
  );
  invariant();
});
test("amount validation prevents negative or floating minor-unit amounts", () => {
  for (const amount of [-1, 0, 1.1, NaN])
    fails(
      () =>
        execute(
          u("donor"),
          "donate",
          { projectId: "P-001", amount },
          "invalid-amount",
        ),
      400,
    );
});
test("supplementing a claim keeps the old snapshot and creates a new queryable hash", () => {
  const before = readDB().procurements.find((c) => c.id === "PR-003")!.hash;
  execute(
    u("foundation"),
    "requestInfo",
    { procurementId: "PR-003", note: "请说明选定报价的理由" },
    "request-more-info",
  );
  execute(
    u("recipient"),
    "resubmitProcurement",
    { procurementId: "PR-003", note: "补充：该供应商报价最低且符合规格" },
    "submit-new-version",
  );
  const db = readDB(),
    records = db.records.filter((r) => r.id === "PR-003");
  assert.equal(records.length, 2);
  assert.equal(records[0].hash, before);
  assert.equal(records[1].version, 2);
  assert.notEqual(records[1].hash, before);
  assert.equal(
    db.procurements.find((c) => c.id === "PR-003")!.status,
    "human_review",
  );
});
