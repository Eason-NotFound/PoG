import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { publicUser, readDB } from "../src/lib/store";
import {
  paymentCommand,
  paymentState,
  assertPaymentBook,
} from "../src/lib/payments";
import { execute, uploadEvidence } from "../src/lib/service";
let dir: string,
  serial = 0;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pog-integrated-"));
  process.env.POG_DATA_DIR = dir;
  serial = 0;
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));
const u = (id: string) => publicUser(readDB().users.find((x) => x.id === id)!);
const key = () => `request-key-${++serial}`;
const pid = () => readDB().projects.find((p) => p.paymentTracked)!.id;
const pay = (
  who: string,
  kind: string,
  body: Record<string, unknown> = {},
  k = key(),
) => paymentCommand(u(who), kind, { projectId: pid(), ...body }, k);
function exchange(who: string, amount: string) {
  const r = pay(who, "exchange", { amount });
  pay(who, "confirm-exchange", { orderId: r.id });
}
function fund() {
  exchange("donor", "60");
  exchange("donor2", "40");
  pay("donor", "donate", { amount: "60" });
  pay("donor2", "donate", { amount: "40" });
}
function review(procurementId: string, stage: "purchase" | "delivery") {
  const r = readDB()
    .reviews.filter(
      (r) => r.procurementId === procurementId && r.stage === stage,
    )
    .at(-1)!;
  execute(
    u("admin"),
    "reviewCase",
    { reviewId: r.id, decision: "approved", reason: "已核对所有证明" },
    key(),
  );
}
function release72() {
  const created = execute(
    u("foundation"),
    "foundationProcurement",
    {
      projectId: pid(),
      name: "社区学习用品",
      quantity: 1,
      unitPrice: 7200,
      vendorId: "V-001",
      note: "测试",
    },
    key(),
  );
  const cid = created.id as string;
  review(cid, "purchase");
  execute(u("foundation"), "approvePurchase", { procurementId: cid }, key());
  assert.equal(paymentState(u("recipient")).projects[0].reserved, "72000000");
  assert.throws(() =>
    execute(u("foundation"), "approvePayment", { procurementId: cid }, key()),
  );
  uploadEvidence(u("foundation"), {
    name: "invoice.pdf",
    procurementId: cid,
    type: "invoice",
    content: Buffer.from("%PDF-1.4\ntest invoice\n%%EOF").toString("base64"),
  });
  uploadEvidence(u("recipient"), {
    name: "receipt.png",
    procurementId: cid,
    type: "photo",
    content: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]).toString(
      "base64",
    ),
  });
  execute(
    u("foundation"),
    "submitFoundationProof",
    { procurementId: cid, note: "发票已核对" },
    key(),
  );
  execute(
    u("recipient"),
    "deliver",
    { procurementId: cid, quantity: 1, note: "已收齐验收" },
    key(),
  );
  review(cid, "delivery");
  execute(u("foundation"), "approvePayment", { procurementId: cid }, key());
  return readDB().payment!.releases[0];
}
test("visible HKD freeze → confirm → tokens; retry is idempotent and balances preserved", () => {
  const k = key(),
    r = pay("donor", "exchange", { amount: "60" }, k);
  assert.equal(pay("donor", "exchange", { amount: "60" }, k).id, r.id);
  let view = paymentState(u("donor"));
  assert.equal(view.user.holdCents, 6000);
  assert.equal(view.user.availableHkdCents, 94000);
  assert.equal(view.user.tokens, "0");
  assert.equal(paymentState(u("recipient")).globalDonors.frozenHkdCents, 6000);
  pay("donor", "confirm-exchange", { orderId: r.id });
  view = paymentState(u("donor"));
  assert.equal(view.user.holdCents, 0);
  assert.equal(view.user.hkdCents, 94000);
  assert.equal(view.user.tokens, "60000000");
  assert.throws(() => pay("donor", "confirm-exchange", { orderId: r.id }));
  assert.equal(paymentState(u("donor")).user.tokens, "60000000");
  const cancel = pay("donor", "exchange", { amount: "10" });
  pay("donor", "cancel-exchange", { orderId: cancel.id });
  assert.equal(paymentState(u("donor")).user.availableHkdCents, 94000);
});
test("website donation API and Payment API use one pool and record", () => {
  exchange("donor", "60");
  const k = key();
  const body = { projectId: pid(), amount: 6000 };
  const a = execute(u("donor"), "donate", body, k);
  assert.equal(execute(u("donor"), "donate", body, k).id, a.id);
  const db = readDB(),
    p = db.projects.find((p) => p.id === pid())!;
  assert.equal(p.available, 6000);
  assert.equal(paymentState(u("recipient")).projects[0].locked, "60000000");
  assert.equal(paymentState(u("donor")).user.tokens, "0");
  assert.equal(db.donations.filter((d) => d.projectId === pid()).length, 1);
  assertPaymentBook(db);
});
test("full original review/evidence flow → release72 → redeem72 → refund16.8/11.2", () => {
  fund();
  const r = release72();
  let v = paymentState(u("recipient")).projects[0];
  assert.equal(v.locked, "28000000");
  assert.equal(v.foundationTokens, "72000000");
  assert(v.balanced);
  assert.equal(
    readDB().procurements.find((c) => c.id === r.procurementId)!.status,
    "funds_released",
  );
  assert.equal(
    readDB().projects.find((p) => p.id === pid())!.paid,
    0,
    "No supplier payment",
  );
  pay("foundation", "redeem", { releaseId: r.id, amount: "72" });
  v = paymentState(u("recipient")).projects[0];
  assert.equal(v.foundationTokens, "0");
  assert.equal(v.redeemed, "72000000");
  assert.equal(paymentState(u("foundation")).user.hkdCents, 7200);
  assert.throws(() =>
    pay("foundation", "redeem", { releaseId: r.id, amount: "72" }),
  );
  pay("foundation", "closing", { reason: "项目结束" });
  assert.throws(() => pay("admin", "close"));
  pay("admin", "external-reconciliation", {
    releaseId: r.id,
    reference: "FOUNDATION-EXTERNAL-72",
  });
  pay("admin", "close");
  assert.equal(paymentState(u("donor")).projects[0].myRefund, "16800000");
  pay("donor", "claim");
  pay("donor2", "claim");
  assert.equal(paymentState(u("donor2")).user.tokens, "11200000");
  assert.equal(paymentState(u("recipient")).projects[0].state, "Closed");
  assert.throws(() => pay("donor", "claim"));
  assertPaymentBook(readDB());
});
test("pause blocks donation, closing cancels frozen HKD and full refund returns to original wallets", () => {
  fund();
  const freeze = pay("donor", "exchange", { amount: "5" });
  pay("foundation", "pause", { reason: "检查" });
  assert.throws(() => pay("donor", "donate", { amount: "1" }));
  assert.throws(() => pay("donor", "confirm-exchange", { orderId: freeze.id }));
  pay("foundation", "closing", { reason: "取消" });
  assert.equal(paymentState(u("donor")).user.holdCents, 0);
  pay("admin", "close");
  pay("donor", "claim");
  pay("donor2", "claim");
  assert.equal(paymentState(u("donor")).user.tokens, "60000000");
  assert.equal(paymentState(u("donor2")).user.tokens, "40000000");
  assertPaymentBook(readDB());
});
test("recipient reads shared evidence but cannot move funds or read donor private balances", () => {
  fund();
  const recipient = paymentState(u("recipient"));
  assert.equal(recipient.exchanges.length, 0);
  assert.equal(recipient.user.hkdCents, 0);
  assert.equal(recipient.projects[0].releases.length, 0);
  assert(recipient.projects[0].events.every((e) => !("actorId" in e)));
  for (const kind of [
    "exchange",
    "donate",
    "redeem",
    "pause",
    "close",
    "claim",
  ])
    assert.throws(() => pay("recipient", kind, { amount: "1" }));
  assert.throws(() => paymentState(u("maintainer")));
  assert.equal(
    paymentState({ ...u("recipient"), orgId: "foreign" }).projects.length,
    0,
  );
  assert.throws(() =>
    paymentCommand(
      { ...u("foundation"), orgId: "foreign" },
      "pause",
      { projectId: pid(), reason: "x" },
      key(),
    ),
  );
});
test("migration keeps historical money intact; new projects enter integrated ledger", () => {
  const before = readDB().projects[0];
  assert.equal(before.deposited, 100000);
  assert.equal(before.paid, 20000);
  const r = execute(
    u("foundation"),
    "createProject",
    { name: "另一个项目", description: "第二轮演示", target: 10000 },
    key(),
  );
  assert(readDB().payment!.projects[r.id as string]);
  assert.equal(readDB().projects[0].deposited, before.deposited);
  assert.throws(() =>
    execute(
      u("foundation"),
      "deposit",
      { projectId: pid(), amount: 10000 },
      key(),
    ),
  );
});
test("invalid inputs and duplicate-key mismatches cannot change ledger", () => {
  for (const amount of ["0", "-1", "0.001", 60])
    assert.throws(() => pay("donor", "exchange", { amount }));
  const k = key();
  pay("donor", "exchange", { amount: "60" }, k);
  assert.throws(() => pay("donor", "exchange", { amount: "61" }, k));
  assert.throws(() =>
    pay("donor2", "cancel-exchange", {
      orderId: readDB().payment!.exchanges[0].id,
    }),
  );
  assert.equal(paymentState(u("donor")).user.holdCents, 6000);
});
