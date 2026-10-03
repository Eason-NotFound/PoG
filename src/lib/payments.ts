import type { AccessUser } from "./access";
import type { Database } from "./store";
import type { Project, Procurement } from "./types";
import type { FundsView, PaymentBook, ProjectFunds } from "./payment-types";
import { audit, id, mutate, now, readDB, sha, snapshot } from "./store";
import { freshFunds } from "./payment-state";

export class PaymentError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
function ensure(ok: unknown, message: string, status = 409): asserts ok {
  if (!ok) throw new PaymentError(status, message);
}
const book = (db: Database) => db.payment!;
const n = (v: string) => BigInt(v);
const add = (a: string, b: string) => String(n(a) + n(b));
const sub = (a: string, b: string) => String(n(a) - n(b));
const units = (cents: number) => String(BigInt(cents) * 10000n);
const cents = (v: string) => Number(n(v)) / 10000;
const reason = (v: unknown) => {
  ensure(
    typeof v === "string" && v.trim() && v.length <= 500,
    "请填写原因或凭证编号",
    400,
  );
  return v.trim();
};
const money = (v: unknown) => {
  ensure(
    typeof v === "string" && /^(0|[1-9]\d{0,6})(\.\d{1,2})?$/.test(v),
    "金额须为最多两位小数的字符串",
    400,
  );
  const [a, b = ""] = v.split(".");
  const c = Number(a) * 100 + Number(b.padEnd(2, "0"));
  ensure(c > 0, "金额必须大于零", 400);
  return c;
};
const role = (u: AccessUser, ...roles: string[]) =>
  ensure(u.active && roles.includes(u.role), "没有此资金操作的权限", 403);
const managed = (u: AccessUser, p: Project) => {
  role(u, "foundation");
  ensure(p.foundationId === u.orgId, "不能操作其他基金会项目", 403);
};
const active = (f: ProjectFunds) =>
  ensure(
    f.state === "Active" && !f.paused,
    "项目已暂停或正在关闭，不能发起新资金操作",
  );
const wallet = (db: Database, userId: string) => {
  const w = book(db).wallets[userId];
  ensure(w, "此用户未配置资金账户", 403);
  return w;
};
const held = (b: PaymentBook, userId: string) =>
  b.exchanges
    .filter((o) => o.userId === userId && o.status === "frozen")
    .reduce((a, o) => a + o.cents, 0);
const locked = (f: ProjectFunds) =>
  n(f.deposited) - n(f.released) + n(f.returned) - n(f.refunded);
const unresolved = (db: Database, projectId: string) =>
  db.procurements.filter(
    (c) =>
      c.projectId === projectId &&
      c.status !== "cancelled" &&
      !(
        c.status === "funds_released" &&
        book(db).releases.some(
          (r) => r.procurementId === c.id && r.externalReference,
        )
      ),
  ).length;

function event(
  db: Database,
  u: AccessUser,
  projectId: string,
  kind: string,
  amount: string,
  from: string,
  to: string,
  reference?: string,
) {
  const e = {
    id: id("FLOW"),
    projectId,
    kind,
    actorId: u.id,
    actorRole: u.role,
    amount,
    from,
    to,
    at: now(),
    reference,
  };
  book(db).events.push({ ...e, receiptHash: sha(JSON.stringify(e)) });
  audit(db, u, "payment:" + kind, projectId);
}
function syncProject(db: Database, p: Project) {
  const f = book(db).projects[p.id];
  p.deposited = cents(f.deposited);
  p.available = cents(String(locked(f) - n(f.reserved)));
  p.reserved = cents(f.reserved);
  p.released = cents(sub(f.released, f.returned));
  p.refunded = cents(f.refunded);
}
export function assertPaymentBook(db: Database) {
  const b = book(db);
  let total = n(b.redemptionTokens);
  for (const [user, w] of Object.entries(b.wallets)) {
    ensure(
      n(w.tokens) >= 0n &&
        Number.isSafeInteger(w.hkdCents) &&
        w.hkdCents >= held(b, user),
      "资金账户状态不一致",
    );
    total += n(w.tokens);
  }
  for (const [pid, f] of Object.entries(b.projects)) {
    const custody = locked(f);
    ensure(
      custody >= 0n && n(f.reserved) >= 0n && n(f.reserved) <= custody,
      "项目锁款状态不一致",
    );
    const releases = b.releases.filter((r) => r.projectId === pid);
    ensure(
      releases.reduce((s, r) => s + n(r.amount), 0n) === n(f.released),
      "拨款总额不一致",
    );
    for (const r of releases)
      ensure(
        n(r.redeemed) + n(r.returned) <= n(r.amount),
        "兑付或退回超过拨款",
      );
    total += custody;
    const p = db.projects.find((p) => p.id === pid)!;
    syncProject(db, p);
  }
  ensure(total === n(b.minted), "模拟币总量不守恒，操作已取消");
}
export function trackProject(db: Database, p: Project) {
  p.paymentTracked = true;
  book(db).projects[p.id] = freshFunds();
}

// These hooks run INSIDE the website's existing atomic mutate transaction.
export function donateTokens(
  db: Database,
  u: AccessUser,
  p: Project,
  amount: number,
) {
  role(u, "donor");
  const f = book(db).projects[p.id];
  active(f);
  ensure(Number.isSafeInteger(amount) && amount > 0, "捐款金额不合法", 400);
  const w = wallet(db, u.id),
    a = units(amount);
  ensure(n(w.tokens) >= n(a), "模拟币余额不足，请先在资金链中兑换");
  ensure(
    Object.hasOwn(f.donors, u.id) || Object.keys(f.donors).length < 64,
    "最多支持64位捐款人",
  );
  w.tokens = sub(w.tokens, a);
  f.deposited = add(f.deposited, a);
  f.donors[u.id] = add(f.donors[u.id] || "0", a);
  const r = snapshot(db, {
    id: id("DON"),
    kind: "donation",
    title: p.name,
    ownerId: u.id,
    projectId: p.id,
    amount,
    unit: "mHKD",
    snapshot: {
      amount,
      projectId: p.id,
      ownerId: u.id,
      mode: "integrated-demo",
    },
  });
  db.donations.push({
    id: r.id,
    ownerId: u.id,
    projectId: p.id,
    amount,
    allocated: 0,
    hash: r.hash,
    createdAt: r.createdAt,
  });
  event(db, u, p.id, "donated", a, "Donor 钱包", "项目锁定池", r.id);
  assertPaymentBook(db);
  return {
    id: r.id,
    hash: r.hash,
    message: "模拟币已从 Donor 钱包转入项目锁定池",
  };
}
export function reserveTokens(
  db: Database,
  u: AccessUser,
  p: Project,
  c: Procurement,
) {
  managed(u, p);
  const f = book(db).projects[p.id];
  active(f);
  const a = units(c.amount);
  ensure(locked(f) - n(f.reserved) >= n(a), "项目可预留模拟币不足");
  f.reserved = add(f.reserved, a);
  c.status = "reserved";
  event(db, u, p.id, "reserved", a, "项目锁定池", "采购预留（仍在池内）", c.id);
  assertPaymentBook(db);
}
export function releaseTokens(
  db: Database,
  u: AccessUser,
  p: Project,
  c: Procurement,
) {
  managed(u, p);
  const b = book(db),
    f = b.projects[p.id];
  active(f);
  const a = units(c.amount);
  ensure(
    n(f.reserved) >= n(a) && !b.releases.some((r) => r.procurementId === c.id),
    "预留不足或此采购已拨款",
  );
  f.reserved = sub(f.reserved, a);
  f.released = add(f.released, a);
  const w = wallet(db, u.id);
  w.tokens = add(w.tokens, a);
  b.releases.push({
    id: id("REL"),
    projectId: p.id,
    procurementId: c.id,
    foundationId: u.id,
    amount: a,
    redeemed: "0",
    returned: "0",
  });
  c.status = "funds_released";
  event(
    db,
    u,
    p.id,
    "released",
    a,
    "项目锁定池",
    "Foundation 模拟币钱包",
    c.id,
  );
  assertPaymentBook(db);
}

export function paymentState(u: AccessUser): FundsView {
  role(u, "donor", "foundation", "recipient", "admin");
  const db = readDB(),
    b = book(db);
  const w = b.wallets[u.id] || { hkdCents: 0, tokens: "0" },
    holdCents = held(b, u.id);
  const donorIds = db.users.filter((u) => u.role === "donor").map((u) => u.id);
  return {
    mode: "integrated-demo",
    updatedAt: now(),
    user: {
      id: u.id,
      role: u.role,
      ...w,
      holdCents,
      availableHkdCents: w.hkdCents - holdCents,
    },
    globalDonors: {
      tokens: String(
        donorIds.reduce((s, id) => s + n(b.wallets[id]?.tokens || "0"), 0n),
      ),
      frozenHkdCents: donorIds.reduce((s, id) => s + held(b, id), 0),
    },
    exchanges: b.exchanges.filter((o) => o.userId === u.id),
    projects: db.projects
      .filter(
        (p) =>
          p.paymentTracked &&
          (u.role === "donor" ||
            u.role === "admin" ||
            p.foundationId === u.orgId ||
            p.recipientId === u.orgId),
      )
      .map((p) => {
        const f = b.projects[p.id],
          releases = b.releases.filter((r) => r.projectId === p.id);
        const redeemed = releases.reduce((s, r) => s + n(r.redeemed), 0n),
          foundationTokens = n(f.released) - n(f.returned) - redeemed;
        return {
          id: p.id,
          name: p.name,
          state: f.state,
          paused: f.paused,
          deposited: f.deposited,
          locked: String(locked(f)),
          reserved: f.reserved,
          free: String(locked(f) - n(f.reserved)),
          foundationTokens: String(foundationTokens),
          redeemed: String(redeemed),
          refunded: f.refunded,
          returnable: String(foundationTokens),
          donorCount: Object.keys(f.donors).length,
          myDonation: f.donors[u.id] || "0",
          myRefund: f.entitlements[u.id] || "0",
          myClaimed: !!f.claimed[u.id],
          balanced:
            locked(f) + foundationTokens + redeemed + n(f.refunded) ===
            n(f.deposited),
          unresolved: unresolved(db, p.id),
          canManage: u.role === "foundation" && p.foundationId === u.orgId,
          events: b.events
            .filter((e) => e.projectId === p.id)
            .map(({ actorId, ...e }) => e),
          releases:
            u.role === "foundation" || u.role === "admin" ? releases : [],
        };
      }),
  };
}

export function paymentCommand(
  u: AccessUser,
  kind: string,
  body: Record<string, unknown>,
  key: string,
) {
  role(u, "donor", "foundation", "admin");
  ensure(/^[A-Za-z0-9_-]{8,100}$/.test(key), "缺少有效幂等键", 400);
  return mutate((db) => {
    const b = book(db),
      dedupe = u.id + ":" + key,
      fingerprint = sha(
        JSON.stringify([
          kind,
          Object.fromEntries(
            Object.entries(body).sort(([a], [b]) => a.localeCompare(b)),
          ),
        ]),
      );
    const prior = b.intents[dedupe];
    if (prior) {
      ensure(prior.fingerprint === fingerprint, "幂等键已用于不同请求");
      return prior.result;
    }
    let p = db.projects.find(
      (p) => p.id === body.projectId && p.paymentTracked,
    );
    const order = b.exchanges.find((o) => o.id === body.orderId);
    const release = b.releases.find((r) => r.id === body.releaseId);
    if (order) p = db.projects.find((p) => p.id === order.projectId);
    if (release) p = db.projects.find((p) => p.id === release.projectId);
    ensure(p, "请选择资金链项目", 404);
    const f = b.projects[p.id];
    let result: Record<string, unknown> = {
      message: "资金状态已更新",
      status: "completed",
    };
    if (kind === "exchange") {
      role(u, "donor");
      active(f);
      const c = money(body.amount),
        w = wallet(db, u.id);
      ensure(w.hkdCents - held(b, u.id) >= c, "模拟 HKD 可用余额不足");
      const o = {
        id: id("EX"),
        userId: u.id,
        projectId: p.id,
        cents: c,
        status: "frozen" as const,
        createdAt: now(),
      };
      b.exchanges.push(o);
      event(
        db,
        u,
        p.id,
        "exchange-frozen",
        units(c),
        "Donor 可用 HKD",
        "Donor 冻结 HKD",
        o.id,
      );
      result = {
        id: o.id,
        status: "frozen",
        message: "模拟 HKD 已冻结，确认到账后才扣账并发放模拟币",
      };
    } else if (kind === "confirm-exchange" || kind === "cancel-exchange") {
      role(u, "donor");
      ensure(order && order.userId === u.id, "只能处理自己的兑换", 403);
      ensure(order.status === "frozen", "此兑换已经处理");
      if (kind === "confirm-exchange") {
        active(f);
        const w = wallet(db, u.id),
          a = units(order.cents);
        w.hkdCents -= order.cents;
        w.tokens = add(w.tokens, a);
        b.minted = add(b.minted, a);
        order.status = "completed";
        event(
          db,
          u,
          p.id,
          "exchanged",
          a,
          "Donor 冻结 HKD",
          "Donor 模拟币钱包",
          order.id,
        );
      } else {
        order.status = "cancelled";
        event(
          db,
          u,
          p.id,
          "exchange-cancelled",
          units(order.cents),
          "Donor 冻结 HKD",
          "Donor 可用 HKD",
          order.id,
        );
      }
    } else if (kind === "donate") {
      result = donateTokens(db, u, p, money(body.amount));
    } else if (kind === "redeem" || kind === "return") {
      managed(u, p);
      ensure(
        release && release.foundationId === u.id,
        "请选择本基金会拨款",
        403,
      );
      const c = money(body.amount),
        a = units(c),
        w = wallet(db, u.id);
      ensure(
        n(a) <= n(release.amount) - n(release.redeemed) - n(release.returned) &&
          n(w.tokens) >= n(a),
        "超出本笔未兑付的拨款余额",
      );
      if (kind === "redeem") {
        active(f);
        release.redeemed = add(release.redeemed, a);
        b.redemptionTokens = add(b.redemptionTokens, a);
        w.hkdCents += c;
        event(
          db,
          u,
          p.id,
          "redeemed",
          a,
          "Foundation 模拟币钱包",
          "模拟兑付钱包 / HKD 入账",
          release.id,
        );
      } else {
        ensure(f.state === "Closing", "先申请关闭，再退回尚未兑付的拨款");
        release.returned = add(release.returned, a);
        f.returned = add(f.returned, a);
        event(
          db,
          u,
          p.id,
          "returned",
          a,
          "Foundation 模拟币钱包",
          "项目锁定池",
          release.id,
        );
      }
      w.tokens = sub(w.tokens, a);
    } else if (kind === "cashout") {
      role(u, "donor");
      const c = money(body.amount),
        a = units(c),
        w = wallet(db, u.id);
      ensure(n(w.tokens) >= n(a), "模拟币余额不足");
      w.tokens = sub(w.tokens, a);
      w.hkdCents += c;
      b.redemptionTokens = add(b.redemptionTokens, a);
      event(
        db,
        u,
        p.id,
        "cashout",
        a,
        "Donor 模拟币钱包",
        "模拟兑付钱包 / HKD 入账",
      );
    } else if (kind === "pause" || kind === "resume") {
      managed(u, p);
      ensure(f.state === "Active", "只有进行中项目可暂停/恢复");
      f.paused = kind === "pause";
      event(db, u, p.id, kind, "0", "项目", "项目", reason(body.reason));
    } else if (kind === "closing") {
      managed(u, p);
      ensure(f.state === "Active", "项目已申请关闭");
      f.state = "Closing";
      event(
        db,
        u,
        p.id,
        "closing",
        "0",
        "进行中",
        "关闭核账中",
        reason(body.reason),
      );
      for (const o of b.exchanges.filter(
        (o) => o.projectId === p!.id && o.status === "frozen",
      )) {
        o.status = "cancelled";
        event(
          db,
          u,
          p.id,
          "exchange-cancelled",
          units(o.cents),
          "Donor 冻结 HKD",
          "Donor 可用 HKD",
          o.id,
        );
      }
    } else if (kind === "external-reconciliation") {
      role(u, "admin");
      ensure(release, "请选择拨款");
      ensure(
        !release.externalReference &&
          release.returned === "0" &&
          release.redeemed === release.amount,
        "兑付未完成、已核账或存在退回，不能确认",
      );
      release.externalReference = reason(body.reference);
      event(
        db,
        u,
        p.id,
        "external-reconciled",
        release.amount,
        "Foundation",
        "外部凭证核对",
        release.externalReference,
      );
    } else if (kind === "close") {
      role(u, "admin");
      ensure(
        f.state === "Closing" &&
          f.reserved === "0" &&
          unresolved(db, p.id) === 0,
        "关闭核账尚未完成：检查预留与未结采购",
      );
      const pool = locked(f),
        total = n(f.deposited);
      let cursor = 0n;
      for (const [donor, credit] of Object.entries(f.donors)) {
        const before = cursor;
        cursor += n(credit);
        f.entitlements[donor] = String(
          (pool * cursor) / total - (pool * before) / total,
        );
      }
      f.state = pool === 0n ? "Closed" : "Refundable";
      event(
        db,
        u,
        p.id,
        "refund-ready",
        String(pool),
        "项目锁定池",
        "待原 Donor 领取",
      );
    } else if (kind === "claim") {
      role(u, "donor");
      ensure(
        f.state === "Refundable" && n(f.donors[u.id] || "0") > 0n,
        "只有原 Donor 可在核账后领取",
      );
      ensure(!f.claimed[u.id], "已经领取过退款");
      const a = f.entitlements[u.id],
        w = wallet(db, u.id);
      f.claimed[u.id] = true;
      f.refunded = add(f.refunded, a);
      w.tokens = add(w.tokens, a);
      if (Object.keys(f.donors).every((d) => f.claimed[d])) f.state = "Closed";
      event(db, u, p.id, "refunded", a, "项目锁定池", "原 Donor 模拟币钱包");
    } else if (kind === "cancel-procurement") {
      role(u, "admin");
      const c = db.procurements.find(
        (c) => c.id === body.procurementId && c.projectId === p!.id,
      );
      ensure(
        c &&
          ["human_review", "needs_info", "frozen", "reserved"].includes(
            c.status,
          ) &&
          !c.recipientProof,
        "此采购不可取消；收货后必须继续核账",
      );
      if (c.status === "reserved")
        f.reserved = sub(f.reserved, units(c.amount));
      c.status = "cancelled";
      event(
        db,
        u,
        p.id,
        "procurement-cancelled",
        units(c.amount),
        "采购预留",
        "项目锁定池",
        reason(body.reason),
      );
    } else throw new PaymentError(404, "资金操作不存在");
    assertPaymentBook(db);
    b.intents[dedupe] = { fingerprint, result };
    return result;
  });
}
