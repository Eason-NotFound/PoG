import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readDB, publicUser } from "../src/lib/store";
import { canAct, type AccessUser } from "../src/lib/access";
import {
  execute,
  uploadEvidence,
  evidenceFor,
  pageData,
  ApiError,
  lookupHash,
} from "../src/lib/service";
let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pog-review-"));
  process.env.POG_DATA_DIR = dir;
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));
const u = (id: string) => publicUser(readDB().users.find((u) => u.id === id)!);
const fails = (fn: () => unknown, status: number) =>
  assert.throws(
    fn,
    (e: unknown) => e instanceof ApiError && e.status === status,
  );
const pdf = Buffer.from("%PDF-1.4\nevidence").toString("base64");
const photo = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]).toString(
  "base64",
);
const upload = (
  who: AccessUser,
  type: string,
  content = type === "photo" ? photo : pdf,
) =>
  uploadEvidence(who, {
    procurementId: "PR-002",
    name: type + (type === "photo" ? ".png" : ".pdf"),
    type,
    content,
  });
function review(decision: string, procurementId = "PR-003") {
  const r = readDB()
    .reviews.filter((r) => r.procurementId === procurementId)
    .at(-1)!;
  return execute(
    u("admin"),
    "reviewCase",
    { reviewId: r.id, decision, reason: "Manual review notes" },
    "review-" + r.id,
  );
}

test("human audit is admin-only, version-bound, one-time and does not move funds", () => {
  const c = readDB().procurements.find((c) => c.id === "PR-003")!;
  const r = readDB().reviews.find((r) => r.procurementId === c.id)!;
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePurchase",
        { procurementId: c.id },
        "before-admin-review",
      ),
    409,
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "reviewCase",
        { reviewId: r.id, decision: "approved", reason: "x" },
        "not-admin-review",
      ),
    403,
  );
  const before = readDB().projects[0];
  review("approved");
  assert.deepEqual(readDB().projects[0], before);
  fails(
    () =>
      execute(
        u("admin"),
        "reviewCase",
        { reviewId: r.id, decision: "rejected", reason: "changed" },
        "duplicate-review",
      ),
    409,
  );
  execute(
    u("foundation"),
    "approvePurchase",
    { procurementId: c.id },
    "after-admin-review",
  );
  assert.equal(readDB().projects[0].reserved, before.reserved + c.amount);
  assert.equal(canAct(u("admin"), "approvePayment"), false);
  assert.equal(
    canAct(
      { ...u("maintainer"), grants: ["admin.audit", "admin.appeals"] },
      "reviewCase",
    ),
    false,
  );
});
test("foundation and recipient submit different evidence; both are required in either order", () => {
  fails(() => upload(u("recipient"), "invoice"), 403);
  fails(() => upload(u("foundation"), "photo"), 403);
  fails(() => upload({ ...u("foundation"), orgId: "foreign" }, "invoice"), 404);
  fails(() => upload(u("recipient"), "photo", pdf), 400);
  upload(u("recipient"), "photo");
  execute(
    u("recipient"),
    "deliver",
    { procurementId: "PR-002", quantity: 12, note: "received" },
    "receipt-first",
  );
  assert.equal(
    readDB().procurements.find((c) => c.id === "PR-002")!.status,
    "reserved",
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "submitFoundationProof",
        { procurementId: "PR-002", note: "invoice" },
        "missing-invoice",
      ),
    400,
  );
  const evidence = upload(u("foundation"), "invoice");
  assert.equal(evidenceFor(u("recipient"), evidence.id).name, "invoice.pdf");
  fails(() => evidenceFor(u("donor"), evidence.id), 404);
  assert.equal(
    "content" in pageData(u("foundation"), "foundation.evidence").evidence![0],
    false,
  );
  const before = readDB().procurements.find((c) => c.id === "PR-002")!.hash;
  execute(
    u("foundation"),
    "submitFoundationProof",
    { procurementId: "PR-002", note: "purchased and dispatched" },
    "foundation-second",
  );
  const c = readDB().procurements.find((c) => c.id === "PR-002")!;
  assert.equal(c.status, "payment_review");
  assert.notEqual(c.hash, before);
  assert.ok(lookupHash(u("recipient"), "recipient.hash", before).length);
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePayment",
        { procurementId: c.id },
        "without-final-audit",
      ),
    409,
  );
  review("approved", c.id);
  execute(
    u("foundation"),
    "approvePayment",
    { procurementId: c.id },
    "with-final-audit",
  );
  assert.equal(
    readDB().procurements.find((x) => x.id === c.id)!.status,
    "paid",
  );
  fails(() => upload(u("foundation"), "invoice"), 409);
});
test("appeals are scoped, prevent duplicates, resolve once and reopen without automatic approval", () => {
  review("rejected");
  const r = readDB().reviews.find((r) => r.procurementId === "PR-003")!;
  fails(
    () =>
      execute(
        { ...u("recipient"), orgId: "foreign" },
        "recipientAppeal",
        { reviewId: r.id, reason: "claim" },
        "foreign-appeal",
      ),
    403,
  );
  const a = execute(
    u("foundation"),
    "foundationAppeal",
    { reviewId: r.id, reason: "Please reconsider the documents" },
    "appeal-one",
  );
  fails(
    () =>
      execute(
        u("recipient"),
        "recipientAppeal",
        { reviewId: r.id, reason: "duplicate" },
        "appeal-duplicate",
      ),
    409,
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "resolveAppeal",
        { appealId: a.id, resolution: "accepted", response: "ok" },
        "self-resolve",
      ),
    403,
  );
  const before = readDB().projects[0];
  execute(
    u("admin"),
    "resolveAppeal",
    {
      appealId: a.id,
      resolution: "accepted",
      response: "A second review is needed",
    },
    "resolve-one",
  );
  assert.deepEqual(readDB().projects[0], before);
  assert.equal(readDB().appeals[0].status, "resolved");
  assert.equal(
    readDB()
      .reviews.filter((x) => x.procurementId === "PR-003")
      .at(-1)!.status,
    "pending",
  );
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePurchase",
        { procurementId: "PR-003" },
        "appeal-not-approval",
      ),
    409,
  );
  fails(
    () =>
      execute(
        u("admin"),
        "resolveAppeal",
        { appealId: a.id, resolution: "rejected", response: "twice" },
        "resolve-twice",
      ),
    409,
  );
  assert.equal(
    pageData(u("recipient"), "recipient.appeals").appeals?.length,
    0,
  );
});
test("frozen AI results require supplementation, retain history and undergo a new audit", () => {
  fails(() => review("approved", "PR-004"), 409);
  review("more_info", "PR-004");
  const old = readDB().procurements.find((c) => c.id === "PR-004")!.hash;
  execute(
    u("recipient"),
    "resubmitProcurement",
    { procurementId: "PR-004", note: "Corrected invoice explanation" },
    "correct-frozen-case",
  );
  const c = readDB().procurements.find((c) => c.id === "PR-004")!;
  assert.notEqual(c.hash, old);
  assert.equal(c.status, "human_review");
  assert.equal(c.risk, 18);
  const rs = readDB().reviews.filter((r) => r.procurementId === c.id);
  assert.equal(rs.length, 2);
  assert.equal(rs[0].decision, "more_info");
  assert.equal(rs[1].status, "pending");
  fails(
    () =>
      execute(
        u("foundation"),
        "approvePurchase",
        { procurementId: c.id },
        "new-version-needs-review",
      ),
    409,
  );
});
test("migration preserves old money, accounts, evidence and hashes, and makes a backup", () => {
  const db = readDB();
  db.procurements[1].status = "payment_review";
  db.procurements[1].finalRisk = 12;
  const legacy = { ...db, version: 1, reviews: undefined, appeals: undefined };
  writeFileSync(join(dir, "pog-demo.json"), JSON.stringify(legacy));
  const next = readDB();
  assert.equal(next.version, 2);
  assert.equal(next.procurements[1].status, "reserved");
  assert.deepEqual(next.records, db.records);
  assert.deepEqual(next.projects, db.projects);
  assert.deepEqual(next.users, db.users);
  assert.ok(next.reviews.some((r) => r.status === "pending"));
  assert.ok(next.reviews.some((r) => r.legacy));
  const backup = join(dir, "pog-demo.json.v1-backup");
  assert.ok(existsSync(backup));
  assert.equal(JSON.parse(readFileSync(backup, "utf8")).version, 1);
});

test("foundation-created purchases can be supplemented after an admin request", () => {
  const result = execute(
    u("foundation"),
    "foundationProcurement",
    {
      projectId: "P-001",
      vendorId: "V-001",
      name: "Foundation supplies",
      quantity: 2,
      unitPrice: 1000,
    },
    "foundation-create",
  );
  const c = readDB().procurements.find((c) => c.id === result.id)!;
  assert.equal(c.recipientId, "org-recipient");
  review("more_info", c.id);
  uploadEvidence(u("foundation"), {
    procurementId: c.id,
    name: "quote.pdf",
    type: "quotation",
    content: pdf,
  });
  execute(
    u("foundation"),
    "resubmitFoundationProcurement",
    { procurementId: c.id, note: "Updated quote" },
    "foundation-supplement",
  );
  assert.equal(
    readDB().procurements.find((x) => x.id === c.id)!.status,
    "human_review",
  );
  assert.equal(
    readDB()
      .reviews.filter((r) => r.procurementId === c.id)
      .at(-1)!.status,
    "pending",
  );
});
