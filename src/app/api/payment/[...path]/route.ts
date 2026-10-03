import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth";
import { getUserByToken } from "@/lib/store";
import { paymentCommand, paymentState, PaymentError } from "@/lib/payments";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
async function handler(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const json = (data: unknown, status = 200) =>
    NextResponse.json(data, {
      status,
      headers: { "Cache-Control": "no-store" },
    });
  try {
    if (process.env.POG_DEMO_MODE !== "true")
      throw new PaymentError(503, "模拟资金服务未启用");
    const u = getUserByToken(req.cookies.get(SESSION_COOKIE)?.value);
    if (!u) throw new PaymentError(401, "请先登录");
    const route = (await context.params).path.join("/");
    if (req.method === "GET" && route === "state") return json(paymentState(u));
    if (req.method === "POST" && /^operations\/[a-z-]+$/.test(route)) {
      let validOrigin = false;
      try {
        const origin = new URL(req.headers.get("origin") || "");
        validOrigin =
          ["http:", "https:"].includes(origin.protocol) &&
          origin.host === req.headers.get("host");
      } catch {}
      if (!validOrigin) throw new PaymentError(403, "请求来源不匹配");
      if (!req.headers.get("content-type")?.startsWith("application/json"))
        throw new PaymentError(415, "需要 JSON");
      const reader = req.body?.getReader();
      if (!reader) throw new PaymentError(400, "请求为空");
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.length;
        if (size > 16384) {
          await reader.cancel();
          throw new PaymentError(413, "请求过大");
        }
        chunks.push(part.value);
      }
      let body: Record<string, unknown>;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (!body || Array.isArray(body) || typeof body !== "object")
          throw new Error();
      } catch {
        throw new PaymentError(400, "无效 JSON");
      }
      return json(
        paymentCommand(
          u,
          route.slice(11),
          body,
          req.headers.get("idempotency-key") || "",
        ),
      );
    }
    throw new PaymentError(404, "接口不存在");
  } catch (e) {
    if (e instanceof PaymentError) return json({ error: e.message }, e.status);
    console.error(e);
    return json({ error: "资金服务异常，请使用原幂等键重试" }, 500);
  }
}
export const GET = handler;
export const POST = handler;
