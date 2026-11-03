import { handleA2Proxy } from "@/lib/a2-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function handler(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return handleA2Proxy(request, (await context.params).path);
}

export const GET = handler;
export const POST = handler;
export const DELETE = handler;
