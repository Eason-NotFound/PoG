/** Validate only this private report download. Never reserialize hash-committed bytes. */
import { createHash } from "node:crypto";

const SECRET =
  /^(?:token|access_?token|refresh_?token|session_?token|password(?:_?hash)?|private_?key|secret|authorization|cookie|set-cookie|(?:raw_?)?signature|(?:raw|signed)_?transaction)$/i;
const HASH = /^0x[0-9a-fA-F]{64}$/;
function safe(value: unknown, token: string | null, depth = 0): boolean {
  if (depth > 30) return false;
  if (typeof value === "string")
    return (
      (!token || !value.includes(token)) &&
      !/0x[0-9a-fA-F]{130}(?![0-9a-fA-F])|Bearer\s|https?:\/\/|\/(?:Users|private|tmp|var|home|etc)\//i.test(
        value,
      )
    );
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value))
    return value.every((item) => safe(item, token, depth + 1));
  return (
    Boolean(value) &&
    typeof value === "object" &&
    Object.entries(value as Record<string, unknown>).every(
      ([key, item]) => !SECRET.test(key) && safe(item, token, depth + 1),
    )
  );
}
export function validAiDiagnosticReport(
  bytes: Uint8Array,
  token: string | null,
  sha256: string | null,
  reportHash: string | null,
): boolean {
  try {
    if (
      !sha256 ||
      !reportHash ||
      !HASH.test(sha256) ||
      !HASH.test(reportHash) ||
      `0x${createHash("sha256").update(bytes).digest("hex")}` !==
        sha256.toLowerCase()
    )
      return false;
    const data: unknown = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    );
    return (
      Boolean(data) &&
      typeof data === "object" &&
      !Array.isArray(data) &&
      (data as Record<string, unknown>).schemaVersion ===
        "pog.ai.report/0.2-candidate" &&
      safe(data, token)
    );
  } catch {
    return false;
  }
}
