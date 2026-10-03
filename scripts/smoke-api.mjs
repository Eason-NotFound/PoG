import assert from "node:assert/strict";
const base = process.env.POG_TEST_URL || "http://localhost:3000";
let checks = 0;
async function request(
  path,
  { cookie, method = "GET", body, origin = base } = {},
) {
  const headers = {};
  if (cookie) headers.Cookie = cookie;
  if (method === "POST") {
    headers["Content-Type"] = "application/json";
    headers.Origin = origin;
  }
  return fetch(base + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
assert.equal((await request("/api/page?id=donor.overview")).status, 401);
checks++;
for (const [username, allowed, denied] of [
  ["donor", "donor.overview", "foundation.review"],
  ["foundation", "foundation.overview", "recipient.delivery"],
  ["recipient", "recipient.overview", "donor.projects"],
  ["maintainer", "donor.projects", "admin.users"],
  ["admin", "admin.users", null],
]) {
  const login = await request("/api/auth/login", {
    method: "POST",
    body: {
      username,
      password: process.env.POG_DEMO_PASSWORD || "PoG-demo-2026",
    },
  });
  assert.equal(login.status, 200, username + " login");
  const cookie = login.headers.get("set-cookie")?.split(";")[0];
  assert.ok(cookie);
  checks++;
  try {
    const ok = await request("/api/page?id=" + allowed, { cookie });
    assert.equal(ok.status, 200);
    const data = await ok.json();
    assert.ok(!JSON.stringify(data).includes("passwordHash"));
    checks++;
    if (denied) {
      assert.equal(
        (await request("/api/page?id=" + denied, { cookie })).status,
        403,
      );
      checks++;
    }
    if (username === "donor") {
      assert.deepEqual(
        data.data.donations.map((x) => x.ownerId),
        data.data.donations.map(() => "donor"),
      );
      const bad = await request("/api/actions/updatePermissions", {
        cookie,
        method: "POST",
        body: { userId: "maintainer", grants: ["admin.users"] },
      });
      assert.equal(bad.status, 403);
      checks++;
    }
    const csrf = await request("/api/auth/logout", {
      cookie,
      method: "POST",
      body: {},
      origin: "http://example.invalid",
    });
    assert.equal(csrf.status, 403);
    checks++;
  } finally {
    await request("/api/auth/logout", { cookie, method: "POST", body: {} });
  }
}
console.log("HTTP smoke checks passed:", checks);
