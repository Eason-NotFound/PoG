import type { Database } from "./store";
import type { ProjectFunds } from "./payment-types";
export const freshFunds = (): ProjectFunds => ({
  state: "Active",
  paused: false,
  deposited: "0",
  reserved: "0",
  released: "0",
  returned: "0",
  refunded: "0",
  donors: {},
  entitlements: {},
  claimed: {},
});

// Additive migration: legacy example balances/receipts remain untouched.
export function initializePayment(db: Database) {
  if (db.payment) return false;
  db.payment = {
    version: 1,
    wallets: {},
    exchanges: [],
    projects: {},
    releases: [],
    events: [],
    minted: "0",
    redemptionTokens: "0",
    intents: {},
  };
  for (const u of db.users)
    if (["donor", "foundation"].includes(u.role))
      db.payment.wallets[u.id] = {
        hkdCents: u.role === "donor" ? 100000 : 0,
        tokens: "0",
      };
  let projectId = "P-FLOW";
  while (db.projects.some((p) => p.id === projectId)) projectId += "-NEW";
  db.projects.push({
    id: projectId,
    name: "透明资金链 · 社区学习计划",
    description:
      "从模拟 HKD 兑换、捐款锁定，到验收拨款、基金会兑付和剩余资金退款。三方共享同一笔资金记录。",
    foundationId: "org-foundation",
    recipientId: "org-recipient",
    target: 10000,
    deposited: 0,
    available: 0,
    reserved: 0,
    paid: 0,
    released: 0,
    refunded: 0,
    paymentTracked: true,
    rulesHash: "",
    categories: ["教育物资", "设备"],
  });
  db.payment.projects[projectId] = freshFunds();
  return true;
}
