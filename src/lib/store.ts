import {
  createHash,
  randomBytes,
  scryptSync,
  timingSafeEqual,
} from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import type { AccessUser } from "./access";
import type {
  AuditLog,
  ReviewCase,
  Appeal,
  Donation,
  Evidence,
  LedgerEntry,
  Procurement,
  Project,
  RecordSnapshot,
  Vendor,
} from "./types";

export type StoredUser = AccessUser & {
  username: string;
  passwordHash: string;
};
export type Database = {
  version: number;
  reviews: ReviewCase[];
  appeals: Appeal[];
  users: StoredUser[];
  sessions: { tokenHash: string; userId: string; expires: number }[];
  projects: Project[];
  donations: Donation[];
  procurements: Procurement[];
  vendors: Vendor[];
  evidence: Evidence[];
  records: RecordSnapshot[];
  logs: AuditLog[];
  ledger: LedgerEntry[];
  reimbursements: {
    id: string;
    orgId: string;
    name: string;
    amount: number;
    status: string;
    createdAt: string;
  }[];
  intents: Record<
    string,
    { bodyHash: string; result: Record<string, unknown> }
  >;
};
export const now = () => new Date().toISOString();
export const id = (prefix: string) =>
  `${prefix}-${randomBytes(5).toString("hex").toUpperCase()}`;
export const sha = (text: string | Buffer) =>
  "0x" + createHash("sha256").update(text).digest("hex");
export const publicUser = (u: StoredUser): AccessUser => ({
  id: u.id,
  role: u.role,
  name: u.name,
  orgId: u.orgId,
  grants: u.grants,
  active: u.active,
});
export function passwordHash(password: string) {
  const salt = randomBytes(16).toString("hex");
  return `${salt}:${scryptSync(password, salt, 32).toString("hex")}`;
}
export function passwordMatches(password: string, encoded: string) {
  const [salt, hash] = encoded.split(":");
  if (!salt || !hash) return false;
  const actual = scryptSync(password, salt, 32);
  const expected = Buffer.from(hash, "hex");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
export function audit(
  db: Database,
  u: AccessUser,
  action: string,
  target: string,
) {
  db.logs.unshift({
    id: id("LOG"),
    actorId: u.id,
    actorName: u.name,
    action,
    target,
    at: now(),
  });
}
export function snapshot(
  db: Database,
  r: Omit<RecordSnapshot, "hash" | "createdAt" | "version">,
) {
  const createdAt = now(),
    version =
      Math.max(
        0,
        ...db.records.filter((x) => x.id === r.id).map((x) => x.version),
      ) + 1;
  const hash = sha(
    JSON.stringify({
      id: r.id,
      kind: r.kind,
      version,
      createdAt,
      content: r.snapshot,
    }),
  );
  const record = { ...r, hash, createdAt, version };
  db.records.push(record);
  return record;
}

function seed(): Database {
  const password = process.env.POG_DEMO_PASSWORD || "PoG-demo-2026";
  const accounts: [string, StoredUser["role"], string, string, string[]][] = [
    ["donor", "donor", "捐款人 A", "personal-a", []],
    ["foundation", "foundation", "晨光基金会", "org-foundation", []],
    ["recipient", "recipient", "同心社区学习中心", "org-recipient", []],
    ["admin", "admin", "PoG 管理员", "platform", []],
    [
      "maintainer",
      "maintainer",
      "页面维护员",
      "platform",
      ["donor.projects", "admin.connections"],
    ],
    ["donor2", "donor", "捐款人 B", "personal-b", []],
  ];
  const db: Database = {
    version: 2,
    reviews: [],
    appeals: [],
    users: accounts.map(([username, role, name, orgId, grants]) => ({
      id: username,
      username,
      role,
      name,
      orgId,
      grants,
      active: true,
      passwordHash: passwordHash(password),
    })),
    sessions: [],
    projects: [],
    donations: [],
    procurements: [],
    vendors: [],
    evidence: [],
    records: [],
    logs: [],
    ledger: [],
    reimbursements: [],
    intents: {},
  };
  db.projects.push({
    id: "P-001",
    name: "让学习资源走进社区",
    description:
      "为社区学习中心提供阅读教材与教学用品，让每一笔支持都对应清晰的用途和交付。",
    foundationId: "org-foundation",
    recipientId: "org-recipient",
    target: 200000,
    deposited: 100000,
    available: 68000,
    reserved: 12000,
    paid: 20000,
    rulesHash: sha("PoG demo rules v1"),
    categories: ["教育物资", "设备"],
  });
  db.vendors.push({
    id: "V-001",
    name: "知行文具",
    verified: true,
    wallet: "0x0000000000000000000000000000000000000101",
  });
  for (const d of [
    { id: "DON-001", ownerId: "donor", amount: 60000, allocated: 12000 },
    { id: "DON-002", ownerId: "donor2", amount: 30000, allocated: 6000 },
    { id: "DON-003", ownerId: "seed-donor-c", amount: 10000, allocated: 2000 },
  ]) {
    const r = snapshot(db, {
      id: d.id,
      kind: "donation",
      title: "社区学习资源捐款",
      ownerId: d.ownerId,
      projectId: "P-001",
      amount: d.amount,
      unit: "HKD",
      snapshot: { ...d, projectId: "P-001", paymentMode: "simulation" },
    });
    db.donations.push({
      ...d,
      projectId: "P-001",
      hash: r.hash,
      createdAt: r.createdAt,
    });
  }
  const seeded: [string, string, number, Procurement["status"], number][] = [
    ["PR-001", "20 本阅读练习册", 20000, "paid", 15],
    ["PR-002", "12 套补充学习材料", 12000, "reserved", 18],
    ["PR-003", "8 套阅读教具", 8000, "human_review", 18],
    ["PR-004", "重复票据演示", 5000, "frozen", 92],
  ];
  for (const [ref, name, amount, status, risk] of seeded) {
    const record = snapshot(db, {
      id: ref,
      kind: "procurement",
      title: name,
      orgId: "org-recipient",
      projectId: "P-001",
      amount,
      unit: "mHKD",
      snapshot: { name, amount, vendorId: "V-001", projectId: "P-001" },
    });
    db.procurements.push({
      id: ref,
      projectId: "P-001",
      recipientId: "org-recipient",
      vendorId: "V-001",
      name,
      quantity:
        ref === "PR-001"
          ? 20
          : ref === "PR-002"
            ? 12
            : ref === "PR-003"
              ? 8
              : 1,
      unitPrice:
        ref === "PR-001"
          ? 1000
          : ref === "PR-002"
            ? 1000
            : ref === "PR-003"
              ? 1000
              : amount,
      amount,
      status,
      risk,
      hash: record.hash,
      createdAt: record.createdAt,
      note: status === "frozen" ? "演示：疑似重复票据，禁止预留和付款。" : "",
      evidenceIds: [],
    });
  }
  db.ledger = [
    {
      id: "LED-001",
      projectId: "P-001",
      kind: "已注资（演示）",
      amount: 100000,
      reference: "SEED",
      at: now(),
    },
    {
      id: "LED-002",
      projectId: "P-001",
      kind: "供应商已收款（演示）",
      amount: 20000,
      reference: "PR-001",
      at: now(),
    },
    {
      id: "LED-003",
      projectId: "P-001",
      kind: "预算已预留（演示）",
      amount: 12000,
      reference: "PR-002",
      at: now(),
    },
  ];
  migrate(db);
  return db;
}
function dbPath() {
  const dir = path.resolve(process.env.POG_DATA_DIR || ".data");
  mkdirSync(dir, { recursive: true });
  return path.join(dir, "pog-demo.json");
}
export function readDB(): Database {
  const file = dbPath();
  if (!existsSync(file)) writeDB(seed());
  const db: Database = JSON.parse(readFileSync(file, "utf8"));
  const original = JSON.stringify(db, null, 2);
  if (migrate(db)) {
    if (!existsSync(file + ".v1-backup"))
      writeFileSync(file + ".v1-backup", original, { mode: 0o600 });
    writeDB(db);
  }
  return db;
}
function writeDB(db: Database) {
  const file = dbPath();
  const temp = file + ".tmp";
  writeFileSync(temp, JSON.stringify(db, null, 2), { mode: 0o600 });
  renameSync(temp, file);
}
// Synchronous read/modify/rename is atomic within this single Node demo process.
// Replace with a transactional PostgreSQL repository before multi-process deployment.
export function mutate<T>(fn: (db: Database) => T): T {
  const db = readDB();
  const result = fn(db);
  writeDB(db);
  return result;
}
export function getUserByToken(token: string | undefined): AccessUser | null {
  if (!token) return null;
  const db = readDB();
  const s = db.sessions.find(
    (s) => s.tokenHash === sha(token) && s.expires > Date.now(),
  );
  const u = s && db.users.find((u) => u.id === s.userId && u.active);
  return u ? publicUser(u) : null;
}
export function login(
  username: string,
  password: string,
): { token: string; user: AccessUser } | null {
  return mutate((db) => {
    const u = db.users.find((u) => u.username === username && u.active);
    if (!u || !passwordMatches(password, u.passwordHash)) return null;
    const token = randomBytes(32).toString("hex");
    db.sessions = db.sessions.filter((s) => s.expires > Date.now());
    db.sessions.push({
      tokenHash: sha(token),
      userId: u.id,
      expires: Date.now() + 8 * 3600_000,
    });
    audit(db, u, "登录", "session");
    return { token, user: publicUser(u) };
  });
}
export function logout(token: string | undefined) {
  if (token)
    mutate((db) => {
      db.sessions = db.sessions.filter((s) => s.tokenHash !== sha(token));
    });
}

/** Additive migration: preserve existing records, evidence, balances and hashes. */
function migrate(db: Database) {
  const needsMigration = db.version < 2 || !db.reviews || !db.appeals;
  db.reviews ??= [];
  db.appeals ??= [];
  for (const c of db.procurements) {
    // Old delivery submissions may only contain recipient invoices. Collect both
    // parties' evidence again without changing money or immutable snapshots.
    if (
      needsMigration &&
      c.status === "payment_review" &&
      (!c.foundationProof || !c.recipientProof)
    ) {
      c.status = "reserved";
      c.finalRisk = undefined;
    }
    if (db.reviews.some((r) => r.procurementId === c.id)) continue;
    const completed = ["reserved", "payment_review", "paid"].includes(c.status);
    db.reviews.push({
      id: id("AUD"),
      procurementId: c.id,
      projectId: c.projectId,
      stage: "purchase",
      status: completed ? "reviewed" : "pending",
      sourceHash: c.hash,
      evidenceIds: [...c.evidenceIds],
      risk: c.risk,
      createdAt: c.createdAt,
      ...(completed
        ? {
            decision: "approved" as const,
            reason: "旧版演示记录，未重新执行人工审计",
            legacy: true,
            reviewedAt: c.createdAt,
          }
        : {}),
    });
  }
  db.version = 2;
  return needsMigration;
}
