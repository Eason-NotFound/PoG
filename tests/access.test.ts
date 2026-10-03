import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  allowedPages,
  canAct,
  canRead,
  firstPath,
  type AccessUser,
} from "../src/lib/access";
import { getUserByToken, login, publicUser, readDB } from "../src/lib/store";
import { ApiError, execute, lookupHash, pageData } from "../src/lib/service";
let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pog-access-"));
  process.env.POG_DATA_DIR = dir;
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));
const user = (id: string) =>
  publicUser(readDB().users.find((u) => u.id === id)!);
const rejected = (fn: () => unknown, status = 403) =>
  assert.throws(
    fn,
    (e: unknown) => e instanceof ApiError && e.status === status,
  );
test("donor, foundation, recipient and admin follow the requested workspace matrix", () => {
  const expected = {
    donor: ["donor"],
    foundation: ["donor", "foundation"],
    recipient: ["recipient"],
    admin: ["donor", "foundation", "recipient", "admin"],
  };
  for (const [id, portals] of Object.entries(expected))
    assert.deepEqual(
      [...new Set(allowedPages(user(id)).map((p) => p.portal))],
      portals,
    );
  assert.equal(canRead(user("donor"), "foundation.review"), false);
  assert.equal(canRead(user("recipient"), "donor.projects"), false);
  assert.equal(canRead(user("admin"), "invented.page"), false);
  assert.equal(firstPath(user("admin")), "/admin/overview");
  assert.equal(firstPath(user("foundation")), "/foundation/overview");
});
test("maintainer sees only assigned pages and cannot mutate even an assigned admin page", () => {
  const u = user("maintainer");
  assert.deepEqual(
    allowedPages(u).map((p) => p.id),
    ["donor.projects", "admin.connections"],
  );
  assert.equal(firstPath(u), "/donor/projects");
  assert.equal(canRead(u, "donor.overview"), false);
  assert.equal(
    canAct({ ...u, grants: ["admin.users"] }, "updatePermissions"),
    false,
  );
  assert.equal(canAct(u, "donate"), false);
  rejected(() =>
    execute(
      u,
      "donate",
      { projectId: "P-001", amount: 100 },
      "maintainer-attempt",
    ),
  );
  rejected(() => pageData(u, "admin.users"));
});
test("admin visibility does not imply business approval powers", () => {
  const u = user("admin");
  assert.equal(canRead(u, "foundation.payment"), true);
  assert.equal(canAct(u, "approvePayment"), false);
  assert.equal(canAct(u, "updatePermissions"), true);
});
test("revoking page grants applies to an existing session immediately", () => {
  const session = login("maintainer", "PoG-demo-2026")!;
  assert.ok(session);
  execute(
    user("admin"),
    "updatePermissions",
    { userId: "maintainer", grants: ["recipient.delivery"], active: true },
    "grant-recipient",
  );
  const updated = getUserByToken(session.token)!;
  assert.equal(canRead(updated, "donor.projects"), false);
  assert.equal(canRead(updated, "recipient.delivery"), true);
  rejected(() => pageData(updated, "donor.projects"));
  assert.ok(pageData(updated, "recipient.delivery"));
  execute(
    user("admin"),
    "updatePermissions",
    { userId: "maintainer", grants: [], active: false },
    "disable-staff",
  );
  assert.equal(getUserByToken(session.token), null);
});
test("non-admin cannot change grants; invalid page IDs cannot be stored", () => {
  rejected(() =>
    execute(
      user("foundation"),
      "updatePermissions",
      { userId: "maintainer", grants: ["admin.users"] },
      "not-admin-grant",
    ),
  );
  rejected(
    () =>
      execute(
        user("admin"),
        "updatePermissions",
        { userId: "maintainer", grants: ["fake.page"] },
        "invalid-page-id",
      ),
    400,
  );
});
test("new maintainers start with zero pages and can receive individual grants", () => {
  const result = execute(
    user("admin"),
    "createMaintainer",
    { name: "测试工作人员", username: "staff2", password: "Local-test-123" },
    "create-new-staff",
  );
  const u = result.user as AccessUser;
  assert.equal(firstPath(u), "/no-access");
  assert.deepEqual(u.grants, []);
  execute(
    user("admin"),
    "updatePermissions",
    { userId: u.id, grants: ["foundation.ledger"], active: true },
    "grant-ledger-only",
  );
  const session = login("staff2", "Local-test-123")!;
  assert.equal(firstPath(session.user), "/foundation/ledger");
});
test("each donor only receives their own records and cannot look up someone else by hash", () => {
  const a = user("donor"),
    b = user("donor2"),
    all = readDB().donations;
  assert.deepEqual(
    pageData(a, "donor.donations").donations?.map((x) => x.id),
    ["DON-001"],
  );
  assert.deepEqual(
    pageData(b, "donor.donations").donations?.map((x) => x.id),
    ["DON-002"],
  );
  assert.equal(
    lookupHash(a, "donor.hash", all.find((x) => x.id === "DON-002")!.hash)
      .length,
    0,
  );
  assert.equal(
    lookupHash(a, "donor.hash", all.find((x) => x.id === "DON-001")!.hash)
      .length,
    1,
  );
});
test("page endpoints project only the data required for that page", () => {
  const staff = user("maintainer");
  const data = pageData(staff, "donor.projects");
  assert.ok(data.projects);
  assert.equal(data.users, undefined);
  assert.equal(data.donations, undefined);
  assert.equal(data.procurements, undefined);
  assert.deepEqual(pageData(staff, "admin.connections"), {});
});
test("forged or expired session token does not authenticate", () => {
  assert.equal(getUserByToken("invented"), null);
  assert.equal(login("donor", "bad-password"), null);
});
