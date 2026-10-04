/**
 * Narrow server-side bridge to the A2 API. It never imports the demo store,
 * talks to an RPC endpoint, or treats an HTTP 202 as a chain confirmation.
 * Response integer tokens beyond JS's safe range are exact decimal-string
 * presentations, including typedData uints. The backend's persisted typedData
 * remains authoritative; this UI must not generate signatures from its DTO.
 * Request bytes are never converted by this response-only presentation rule.
 */
import {
  FULL_DEMO_ROUTES,
  AI_DIAGNOSTIC_ROUTES,
  OFFLINE_AI_ROUTES,
  isDemoProxyMethod,
  matchDemoRoute,
} from "./demo-action-catalog";
import { validCanonicalPaymentEvidence } from "./payment-evidence-content";
import { validAiDiagnosticReport } from "./ai-diagnostic-content";

export const A2_SESSION_COOKIE = "pog_a2_session";
export const A2_JSON_BODY_LIMIT = 64 * 1024;
export const A2_UPLOAD_BODY_LIMIT = 10 * 1024 * 1024 + 64 * 1024;
const RESPONSE_JSON_LIMIT = 2 * 1024 * 1024;
const UUID =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const TOKEN = /^[A-Za-z0-9_-]{32,128}$/;
const ROLES = new Set([
  "foundation",
  "recipient",
  "donor",
  "human_approver",
  "service_ai",
]);
const SECRET_KEY =
  /^(?:token|access_?token|refresh_?token|session_?token|password(?:_?hash)?|private_?key|secret|authorization|cookie|set-cookie|(?:raw_?)?signature|(?:raw|signed)_?transaction)$/i;
const SAFE_HEADERS = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin",
};

type Environment = Record<string, string | undefined>;
export type A2ProxyOptions = {
  env?: Environment;
  fetch?: typeof fetch;
  /** Injection for deterministic offline tests, not a client-supplied option. */
  timeoutMs?: number;
  now?: () => number;
};

class ProxyFailure extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

function json(data: unknown, status = 200, cookie?: string) {
  const headers: Record<string, string> = {
    ...SAFE_HEADERS,
    "Content-Type": "application/json; charset=utf-8",
  };
  if (cookie) headers["Set-Cookie"] = cookie;
  return new Response(JSON.stringify(data), { status, headers });
}

function failure(error: ProxyFailure, cookie?: string) {
  return json(
    {
      error: {
        code: error.code,
        message: error.message,
        operationId: null,
        details: null,
      },
    },
    error.status,
    cookie,
  );
}

function upstreamBase(env: Environment): string {
  if (env.POG_A2_INTEGRATION !== "true")
    throw new ProxyFailure(
      503,
      "a2_integration_disabled",
      "A2 integration is not enabled; no demo fallback is used.",
    );
  const raw = env.POG_A2_API_URL;
  // Numeric loopback, an explicit port and no prefix prevent DNS, userinfo,
  // protocol and path tricks. Only the server environment chooses this target.
  if (!raw || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}\/?$/.test(raw))
    throw new ProxyFailure(
      503,
      "a2_upstream_not_configured",
      "A fixed loopback A2 API endpoint is required.",
    );
  const port = Number(raw.split(":")[2].replace(/\/$/, ""));
  if (port > 65535 || port === 80)
    throw new ProxyFailure(
      503,
      "a2_upstream_not_configured",
      "A fixed loopback A2 API endpoint is required.",
    );
  const url = new URL(raw);
  return url.origin;
}

function routeFor(
  method: string,
  path: string[],
): { route: string; login: boolean; logout: boolean; content: boolean } {
  if (
    !Array.isArray(path) ||
    !path.length ||
    path.some((part) => !/^[A-Za-z0-9-]+$/.test(part))
  )
    throw new ProxyFailure(
      404,
      "a2_route_not_allowed",
      "This A2 route is not available.",
    );
  const route = path.join("/");
  if (!isDemoProxyMethod(method))
    throw new ProxyFailure(
      405,
      "method_not_allowed",
      "Only allowlisted GET, POST and DELETE requests are supported.",
    );
  const declaration = matchDemoRoute(method, path);
  if (!declaration)
    throw new ProxyFailure(
      404,
      "a2_route_not_allowed",
      "This A2 route is not available.",
    );
  return {
    route,
    login: declaration.behavior === "login",
    logout: declaration.behavior === "logout",
    content: declaration.behavior === "document_content",
  };
}

function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  try {
    if (!origin || !host) throw new Error();
    const source = new URL(origin);
    const requested = new URL(request.url);
    const expected = new URL(`${requested.protocol}//${host}`);
    if (
      !/^https?:$/.test(source.protocol) ||
      source.origin !== origin ||
      expected.host !== host ||
      expected.pathname !== "/" ||
      source.protocol !== requested.protocol ||
      source.host !== host
    )
      throw new Error();
  } catch {
    throw new ProxyFailure(
      403,
      "a2_origin_mismatch",
      "A same-origin request is required.",
    );
  }
}

function cookieToken(request: Request): string | null {
  const matches = (request.headers.get("cookie") ?? "")
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${A2_SESSION_COOKIE}=`));
  if (matches.length !== 1) return null;
  const value = matches[0].slice(A2_SESSION_COOKIE.length + 1);
  return TOKEN.test(value) ? value : null;
}

function sessionCookie(value: string, maxAge: number, secure: boolean) {
  return `${A2_SESSION_COOKIE}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure ? "; Secure" : ""}`;
}

async function limitedBytes(
  body: ReadableStream<Uint8Array> | null,
  length: string | null,
  limit: number,
  status: number,
) {
  if (
    length !== null &&
    (!/^[0-9]+$/.test(length) || BigInt(length) > BigInt(limit))
  )
    throw new ProxyFailure(
      status,
      "a2_body_too_large",
      "The A2 message exceeds its size limit.",
    );
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new ProxyFailure(
          status,
          "a2_body_too_large",
          "The A2 message exceeds its size limit.",
        );
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

/**
 * Small response-only JSON parser. Numeric tokens are examined lexically before
 * any Number conversion, so uint256 precision cannot already have been lost.
 * Fractions/exponents are outside A2's integer-only numeric surface and fail
 * closed rather than inventing a float-derived canonical decimal value.
 */
function losslessResponseJson(text: string): unknown {
  let index = 0;
  const maxSafe = "9007199254740991";
  const numberToken = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;
  const invalid = (): never => {
    throw new Error("Invalid A2 JSON syntax");
  };
  const whitespace = () => {
    while (index < text.length && /[\t\n\r ]/.test(text[index])) index++;
  };
  const string = (): string => {
    if (text[index] !== '"') return invalid();
    const start = index++;
    let ended = false;
    while (index < text.length) {
      if (text[index] === "\\") {
        index += 2;
        continue;
      }
      if (text[index++] === '"') {
        ended = true;
        break;
      }
    }
    if (!ended) return invalid();
    // JSON.parse is used only for a quoted string, never for a numeric token.
    const result = JSON.parse(text.slice(start, index)) as string;
    for (let offset = 0; offset < result.length; offset++) {
      const unit = result.charCodeAt(offset);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const next = result.charCodeAt(++offset);
        if (!(next >= 0xdc00 && next <= 0xdfff)) return invalid();
      } else if (unit >= 0xdc00 && unit <= 0xdfff) return invalid();
    }
    return result;
  };
  const value = (depth: number): unknown => {
    if (depth > 100) return invalid();
    whitespace();
    if (text[index] === '"') return string();
    if (text[index] === "{") {
      index++;
      const object: Record<string, unknown> = Object.create(null);
      whitespace();
      if (text[index] === "}") {
        index++;
        return object;
      }
      while (true) {
        whitespace();
        const key = string();
        whitespace();
        if (text[index++] !== ":") return invalid();
        object[key] = value(depth + 1);
        whitespace();
        const separator = text[index++];
        if (separator === "}") return object;
        if (separator !== ",") return invalid();
      }
    }
    if (text[index] === "[") {
      index++;
      const array: unknown[] = [];
      whitespace();
      if (text[index] === "]") {
        index++;
        return array;
      }
      while (true) {
        array.push(value(depth + 1));
        whitespace();
        const separator = text[index++];
        if (separator === "]") return array;
        if (separator !== ",") return invalid();
      }
    }
    for (const [literal, parsed] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ] as const) {
      if (text.startsWith(literal, index)) {
        index += literal.length;
        return parsed;
      }
    }
    numberToken.lastIndex = index;
    const match = numberToken.exec(text);
    if (!match) return invalid();
    const token = match[0];
    index += token.length;
    if (/[.eE]/.test(token) || token === "-0")
      throw new ProxyFailure(
        502,
        "a2_noncanonical_json_number",
        "A2 response numbers must be canonical integers; no float reconstruction is performed.",
      );
    const magnitude = token.startsWith("-") ? token.slice(1) : token;
    const unsafe =
      magnitude.length > maxSafe.length ||
      (magnitude.length === maxSafe.length && magnitude > maxSafe);
    return unsafe ? token : Number(token);
  };
  const parsed = value(0);
  whitespace();
  if (index !== text.length) return invalid();
  return parsed;
}

function parseObject(bytes: Uint8Array, status: number, responseOnly = false) {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const value: unknown = responseOnly
      ? losslessResponseJson(text)
      : JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new Error();
    return value as Record<string, unknown>;
  } catch (error) {
    if (error instanceof ProxyFailure) throw error;
    throw new ProxyFailure(
      status,
      "a2_invalid_json",
      "A valid JSON object is required.",
    );
  }
}

function sanitize(
  value: unknown,
  token: string | null,
  isError = false,
  depth = 0,
  diagnostic = false,
): unknown {
  if (depth > 30) return "[omitted]";
  if (typeof value === "string") {
    // Do not substring-rewrite typed hashes, signatures or transaction facts.
    // Tokens have no place in successful A2 DTOs outside secret-key fields;
    // exact accidental echoes are still removed, while errors are scrubbed.
    // Diagnostic contexts may carry public hashes alongside a message; never
    // substring-redact a valid hash/address into a different commitment.
    if (/^0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$/.test(value)) return value;
    let text = value;
    if (diagnostic)
      text = text
        // A 65-byte signature is reusable authorization, not a public hash.
        // Restrict this rule to diagnostics; ordinary business/typedData text
        // is never reinterpreted as authorization material.
        .replace(
          /(?<![0-9a-fA-F])0x[0-9a-fA-F]{130}(?![0-9a-fA-F])/g,
          "[signature redacted]",
        )
        .replace(/Bearer\s+[^\s"']+/gi, "Bearer [redacted]")
        .replace(/https?:\/\/[^\s"']+/gi, "[endpoint]")
        .replace(
          /\/(?:Users|private|tmp|var|home|etc)\/[^\s"']+/g,
          "[internal path]",
        );
    if (token && diagnostic)
      text = text
        .split(/(0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})(?![0-9a-fA-F]))/g)
        .map((part) =>
          /^0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$/.test(part)
            ? part
            : part.split(token).join("[redacted]"),
        )
        .join("");
    else if (token && text === token) text = "[redacted]";
    return text;
  }
  if (Array.isArray(value))
    return value.map((item) =>
      sanitize(item, token, isError, depth + 1, diagnostic),
    );
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    // A polling operation can be HTTP 200 while its worker outcome is failed
    // or requires_attention. Scrub its known diagnostics, not all business
    // fields or typedData merely because the operation itself failed.
    const errorContext =
      isError ||
      typeof record.errorCode === "string" ||
      (record.error !== undefined && record.error !== null) ||
      record.status === "failed" ||
      record.status === "requires_attention";
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !SECRET_KEY.test(key))
        .map(([key, item]) => {
          const childDiagnostic =
            diagnostic ||
            /^(?:error|error_?message)$/i.test(key) ||
            (errorContext && /^(?:message|detail|details)$/i.test(key));
          return [
            key,
            sanitize(item, token, childDiagnostic, depth + 1, childDiagnostic),
          ];
        }),
    );
  }
  return value;
}

function safeUser(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ProxyFailure(
      502,
      "a2_invalid_session_response",
      "The A2 identity response is invalid.",
    );
  const source = value as Record<string, unknown>;
  if (
    typeof source.id !== "string" ||
    !new RegExp(`^${UUID}$`).test(source.id) ||
    typeof source.username !== "string" ||
    typeof source.role !== "string" ||
    !ROLES.has(source.role) ||
    typeof source.walletAddress !== "string" ||
    !/^0x[0-9a-fA-F]{40}$/.test(source.walletAddress)
  )
    throw new ProxyFailure(
      502,
      "a2_invalid_session_response",
      "The A2 identity response is invalid.",
    );
  const user: Record<string, string> = {
    id: source.id,
    username: source.username,
    role: source.role,
    walletAddress: source.walletAddress,
  };
  if (typeof source.displayName === "string")
    user.displayName = source.displayName;
  return user;
}

export async function handleA2Proxy(
  request: Request,
  path: string[],
  options: A2ProxyOptions = {},
): Promise<Response> {
  const env = options.env ?? process.env;
  const secure =
    env.POG_COOKIE_SECURE === "true" ||
    new URL(request.url).protocol === "https:";
  let logout = false;
  const clearCookie = () => sessionCookie("", 0, secure);
  try {
    const base = upstreamBase(env);
    const route = routeFor(request.method, path);
    const declaration = matchDemoRoute(request.method, path);
    if (
      OFFLINE_AI_ROUTES.some((item) => item.id === declaration?.id) &&
      (env.POG_FULL_DEMO !== "true" || env.POG_OFFLINE_DEMO_ENABLED !== "true")
    )
      throw new ProxyFailure(
        404,
        "offline_demo_disabled",
        "Offline AI fixtures require an explicit local simulation opt-in; no live inference or automatic approval is performed.",
      );
    if (
      AI_DIAGNOSTIC_ROUTES.some((item) => item.id === declaration?.id) &&
      (env.POG_FULL_DEMO !== "true" || env.POG_AI_DIAGNOSTICS !== "true")
    )
      throw new ProxyFailure(
        404,
        "ai_diagnostics_disabled",
        "Live AI diagnostics require an explicit server opt-in; no assessment or approval is generated.",
      );
    if (
      env.POG_FULL_DEMO !== "true" &&
      FULL_DEMO_ROUTES.some((item) => item.id === declaration?.id)
    )
      throw new ProxyFailure(
        404,
        "full_demo_disabled",
        "Full-demo routes require an explicit server opt-in.",
      );
    if (
      env.POG_FULL_DEMO === "true" &&
      request.method === "POST" &&
      declaration?.id === "donation.deposit"
    )
      throw new ProxyFailure(
        404,
        "funded_donation_required",
        "Full demo requires a reconciled project funding claim; use funded-donations.",
      );
    const requested = new URL(request.url);
    if (
      requested.pathname !== `/api/a2/${route.route}` ||
      requested.search ||
      requested.hash
    )
      throw new ProxyFailure(
        404,
        "a2_route_not_allowed",
        "Encoded paths and query overrides are not supported.",
      );
    if (request.method !== "GET") sameOrigin(request);
    logout = route.logout;
    const token = cookieToken(request);
    if (!route.login && !token)
      throw new ProxyFailure(
        401,
        "authentication_required",
        "An A2 session is required; the demo session is not accepted.",
      );
    const headers = new Headers({
      Accept: route.content
        ? "application/pdf, image/jpeg, image/png, application/json"
        : "application/json",
    });
    if (!route.login && token) headers.set("Authorization", `Bearer ${token}`);
    let body: Uint8Array | undefined;
    if (request.method === "POST") {
      if (!route.login) {
        const key = request.headers.get("idempotency-key");
        if (!key || !/^[ -~]{1,128}$/.test(key))
          throw new ProxyFailure(
            400,
            "idempotency_key_required",
            "A printable 1..128-character Idempotency-Key is required.",
          );
        headers.set("Idempotency-Key", key);
      }
      const contentType = request.headers.get("content-type") ?? "";
      if (route.route === "documents") {
        if (
          !/^multipart\/form-data;\s*boundary=(?:"[A-Za-z0-9'()+_,./:=?-]{1,70}"|[A-Za-z0-9'()+_,./:=?-]{1,70})$/i.test(
            contentType,
          )
        )
          throw new ProxyFailure(
            415,
            "a2_content_type_required",
            "Document upload requires multipart/form-data.",
          );
        body = await limitedBytes(
          request.body,
          request.headers.get("content-length"),
          A2_UPLOAD_BODY_LIMIT,
          413,
        );
      } else {
        if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(contentType))
          throw new ProxyFailure(
            415,
            "a2_content_type_required",
            "This A2 request requires application/json.",
          );
        body = await limitedBytes(
          request.body,
          request.headers.get("content-length"),
          A2_JSON_BODY_LIMIT,
          413,
        );
        parseObject(body, 400);
      }
      headers.set("Content-Type", contentType);
    } else if (request.body) {
      // Next can provide a non-null empty stream for a bodyless DELETE.
      // Validate zero actual bytes; never accept a non-empty payload.
      try {
        await limitedBytes(
          request.body,
          request.headers.get("content-length"),
          0,
          400,
        );
      } catch {
        throw new ProxyFailure(
          400,
          "a2_unexpected_body",
          "This A2 request does not accept a body.",
        );
      }
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timedOut = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(
          new ProxyFailure(
            504,
            "a2_upstream_timeout",
            "The A2 API did not respond in time; the operation outcome must be checked before retrying.",
          ),
        );
      }, options.timeoutMs ?? 8000);
    });
    try {
      return await Promise.race([
        timedOut,
        (async () => {
          const upstream = await (options.fetch ?? fetch)(
            `${base}/v2/${route.route}`,
            {
              method: request.method,
              headers,
              body: body as BodyInit | undefined,
              redirect: "manual",
              cache: "no-store",
              signal: controller.signal,
            },
          );
          if (upstream.status >= 300 && upstream.status < 400) {
            await upstream.body?.cancel();
            throw new ProxyFailure(
              502,
              "a2_upstream_redirect",
              "A2 redirects are not followed.",
            );
          }
          const contentType = upstream.headers.get("content-type") ?? "";
          const mime = contentType.split(";")[0].toLowerCase().trim();
          if (declaration?.id === "ai.diagnostic.report" && upstream.ok) {
            if (mime !== "application/json") {
              await upstream.body?.cancel();
              throw new ProxyFailure(
                502,
                "invalid_ai_diagnostic_report",
                "An AI diagnostic report must be canonical JSON.",
              );
            }
            const bytes = await limitedBytes(
              upstream.body,
              upstream.headers.get("content-length"),
              RESPONSE_JSON_LIMIT,
              502,
            );
            const sha256 = upstream.headers.get("x-report-sha256");
            const reportHash = upstream.headers.get("x-report-hash");
            if (!validAiDiagnosticReport(bytes, token, sha256, reportHash))
              throw new ProxyFailure(
                502,
                "invalid_ai_diagnostic_report",
                "AI report bytes failed validation; no rewritten report is returned.",
              );
            return new Response(bytes as BodyInit, {
              status: upstream.status,
              headers: {
                ...SAFE_HEADERS,
                "Content-Type": "application/json",
                "Content-Disposition": `attachment; filename="ai-diagnostic-${path[1]}.json"`,
                "X-Report-SHA256": sha256!,
                "X-Report-Hash": reportHash!,
              },
            });
          }
          if (declaration?.id === "payment.evidence.content" && upstream.ok) {
            if (mime !== "application/json") {
              await upstream.body?.cancel();
              throw new ProxyFailure(
                502,
                "a2_invalid_payment_evidence",
                "Payment evidence must use its fixed canonical JSON schema.",
              );
            }
            const bytes = await limitedBytes(
              upstream.body,
              upstream.headers.get("content-length"),
              RESPONSE_JSON_LIMIT,
              502,
            );
            if (!validCanonicalPaymentEvidence(bytes, token))
              throw new ProxyFailure(
                502,
                "a2_invalid_payment_evidence",
                "Canonical payment evidence failed validation; no rewritten substitute is returned.",
              );
            return new Response(bytes as BodyInit, {
              status: upstream.status,
              headers: {
                ...SAFE_HEADERS,
                "Content-Type": "application/json",
                "Content-Disposition": `attachment; filename="${path[1]}.json"`,
              },
            });
          }
          if (
            route.content &&
            upstream.ok &&
            ["application/pdf", "image/jpeg", "image/png"].includes(mime)
          ) {
            const bytes = await limitedBytes(
              upstream.body,
              upstream.headers.get("content-length"),
              A2_UPLOAD_BODY_LIMIT - 64 * 1024,
              502,
            );
            return new Response(bytes as BodyInit, {
              status: upstream.status,
              headers: {
                ...SAFE_HEADERS,
                "Content-Type": mime,
                "Content-Disposition": `attachment; filename="${path[1]}"`,
              },
            });
          }
          if (mime !== "application/json") {
            await upstream.body?.cancel();
            throw new ProxyFailure(
              502,
              "a2_invalid_upstream_response",
              "The A2 API returned an unsupported response.",
            );
          }
          const bytes = await limitedBytes(
            upstream.body,
            upstream.headers.get("content-length"),
            RESPONSE_JSON_LIMIT,
            502,
          );
          const data = parseObject(bytes, 502, true);
          if (route.login && upstream.ok) {
            const issued = data.token;
            const expiresAt = data.expiresAt;
            const expiry =
              typeof expiresAt === "string" ? Date.parse(expiresAt) : NaN;
            const remaining = Math.floor(
              (expiry - (options.now ?? Date.now)()) / 1000,
            );
            if (
              typeof issued !== "string" ||
              !TOKEN.test(issued) ||
              !Number.isFinite(remaining) ||
              remaining < 1
            )
              throw new ProxyFailure(
                502,
                "a2_invalid_session_response",
                "The A2 session response is invalid.",
              );
            // Deliberately return a projection, not the upstream object/token.
            return json(
              { user: safeUser(data.user), expiresAt },
              upstream.status,
              sessionCookie(issued, Math.min(remaining, 86400), secure),
            );
          }
          const response =
            route.route === "me" && upstream.ok
              ? safeUser(data)
              : sanitize(data, token, !upstream.ok);
          return json(
            response,
            upstream.status,
            route.logout || (!route.login && upstream.status === 401)
              ? clearCookie()
              : undefined,
          );
        })(),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  } catch (error) {
    return failure(
      error instanceof ProxyFailure
        ? error
        : new ProxyFailure(
            502,
            "a2_upstream_unavailable",
            "The A2 API is unavailable; no demo fallback is used. Check the operation outcome before retrying.",
          ),
      logout ? clearCookie() : undefined,
    );
  }
}
