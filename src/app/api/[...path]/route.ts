import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth";
import { firstPath, type Action } from "@/lib/access";
import { getUserByToken, login, logout, readDB } from "@/lib/store";
import {
  ApiError,
  canReadRecord,
  evidenceFor,
  execute,
  lookupHash,
  pageData,
  uploadEvidence,
} from "@/lib/service";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const loginAttempts = new Map<string, { count: number; until: number }>();
const json = (data: unknown, status = 200) =>
  NextResponse.json(data, { status, headers: { "Cache-Control": "no-store" } });
async function readBody(req: NextRequest) {
  if (!req.headers.get("content-type")?.startsWith("application/json"))
    throw new ApiError(415, "请求需要 JSON");
  const reader = req.body?.getReader();
  if (!reader) throw new ApiError(400, "请求为空");
  let n = 0;
  const chunks: Uint8Array[] = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    n += value.length;
    if (n > 1_600_000) {
      await reader.cancel();
      throw new ApiError(413, "请求过大");
    }
    chunks.push(value);
  }
  try {
    const v = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!v || Array.isArray(v) || typeof v !== "object") throw new Error();
    return v as Record<string, unknown>;
  } catch {
    throw new ApiError(400, "无效的 JSON 请求");
  }
}
async function handler(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  try {
    if (process.env.POG_DEMO_MODE !== "true")
      throw new ApiError(
        503,
        "演示适配器未启用；请配置 .env.local 或接入真实 API",
      );
    const route = (await context.params).path.join("/");
    const token = req.cookies.get(SESSION_COOKIE)?.value;
    if (req.method === "POST") {
      const origin = req.headers.get("origin");
      // Next's internal URL can use the bind address (0.0.0.0); compare with
      // the actual request Host, never a client-supplied forwarded-host header.
      if (
        !origin ||
        !["http:", "https:"].includes(new URL(origin).protocol) ||
        new URL(origin).host !== req.headers.get("host")
      )
        throw new ApiError(403, "请求来源不匹配");
      const body = await readBody(req);
      if (route === "auth/login") {
        const username =
          typeof body.username === "string" ? body.username.trim() : "";
        const password = typeof body.password === "string" ? body.password : "";
        if (
          username.length > 80 ||
          password.length > 200 ||
          !username ||
          !password
        )
          throw new ApiError(400, "请输入账号与密码");
        const attempt = loginAttempts.get(username);
        if (attempt && attempt.until > Date.now() && attempt.count >= 10)
          throw new ApiError(429, "尝试次数过多，请 1 分钟后重试");
        const result = login(username, password);
        if (!result) {
          loginAttempts.set(username, {
            count:
              attempt && attempt.until > Date.now() ? attempt.count + 1 : 1,
            until: Date.now() + 60000,
          });
          throw new ApiError(401, "账号、密码错误或账户已停用");
        }
        loginAttempts.delete(username);
        if (token) logout(token);
        const res = json({
          user: result.user,
          redirect: firstPath(result.user),
        });
        res.cookies.set(SESSION_COOKIE, result.token, {
          httpOnly: true,
          sameSite: "strict",
          secure: process.env.POG_COOKIE_SECURE === "true",
          path: "/",
          maxAge: 8 * 3600,
        });
        return res;
      }
      if (route === "auth/logout") {
        logout(token);
        const res = json({ ok: true });
        res.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
        return res;
      }
      const u = getUserByToken(token);
      if (!u) throw new ApiError(401, "请先登录");
      if (route === "evidence") return json(uploadEvidence(u, body), 201);
      if (route.startsWith("actions/"))
        return json(
          execute(
            u,
            route.slice(8) as Action,
            body,
            req.headers.get("idempotency-key") || "",
          ),
        );
    } else if (req.method === "GET") {
      const u = getUserByToken(token);
      if (!u) throw new ApiError(401, "请先登录");
      if (route === "session") return json({ user: u });
      if (route === "page")
        return json({
          user: u,
          pageId: req.nextUrl.searchParams.get("id"),
          data: pageData(u, req.nextUrl.searchParams.get("id") || ""),
          updatedAt: new Date().toISOString(),
        });
      if (route === "records/by-hash")
        return json({
          records: lookupHash(
            u,
            req.nextUrl.searchParams.get("page") || "",
            req.nextUrl.searchParams.get("hash") || "",
          ),
        });
      if (route.startsWith("evidence/")) {
        const e = evidenceFor(u, route.slice(9));
        return new Response(Buffer.from(e.content, "base64"), {
          headers: {
            "Content-Type": e.mime,
            "Content-Disposition": `attachment; filename="${e.id}"; filename*=UTF-8''${encodeURIComponent(e.name)}`,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
          },
        });
      }
      if (route.startsWith("records/") && route.endsWith("/export")) {
        const ref = route.split("/")[1];
        const r = readDB()
          .records.slice()
          .reverse()
          .find((r) => r.id === ref);
        if (!r || !canReadRecord(u, r))
          throw new ApiError(404, "未找到可访问记录");
        return new Response(
          JSON.stringify(
            { notice: "PoG 演示凭证，不是税务收据，无真实付款", ...r },
            null,
            2,
          ),
          {
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "Content-Disposition": `attachment; filename="${r.id}.json"`,
              "Cache-Control": "no-store",
            },
          },
        );
      }
    }
    throw new ApiError(404, "接口不存在");
  } catch (error) {
    if (error instanceof ApiError)
      return json({ error: error.message }, error.status);
    console.error(error);
    return json({ error: "服务暂时不可用，请稍后重试" }, 500);
  }
}
export const GET = handler;
export const POST = handler;
