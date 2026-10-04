import {
  guardPortalA2ExpectedState,
  portalA2Read,
  PortalA2Error,
  type PortalA2Options,
  type PortalA2State,
} from "./portal-a2";

export type ProjectLookupResult = {
  projectId: string;
  businessId: string;
  title: string;
  publicSummary: string;
  status: string;
  chainVerified: true;
  amounts: {
    donatedAtomic: string;
    lockedAtomic: string;
    reservedAtomic: string;
    releasedAtomic: string;
    returnedAtomic: string;
    refundedAtomic: string;
  };
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const BYTES32 = /^0x[0-9a-f]{64}$/i;
const UINT256_MAX = (1n << 256n) - 1n;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function invalid(message: string): never {
  throw new PortalA2Error("project_lookup_invalid_response", message, 502);
}

function atomic(value: unknown): bigint {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value))
    invalid("Project ledger returned an invalid atomic amount.");
  const amount = BigInt(value);
  if (amount > UINT256_MAX)
    invalid("Project ledger amount exceeds the token integer range.");
  return amount;
}

async function guard(dependencies: PortalA2Options): Promise<void> {
  try {
    await guardPortalA2ExpectedState(dependencies);
  } catch (error) {
    if (error instanceof PortalA2Error && error.code === "stale_portal_context")
      throw new PortalA2Error(
        error.code,
        "Your identity or demo instance changed. Reload before searching again.",
        409,
      );
    throw error;
  }
}

/** Public project projection only. Never reads private evidence or writes state.
 * All token amounts remain exact 6-decimal atomic integers; reserved funds are
 * included in locked funds, and cumulative release is not supplier payment.
 */
export async function lookupProjectRecord(
  input: string,
  dependencies: PortalA2Options & { expectedState: PortalA2State },
): Promise<ProjectLookupResult> {
  const identifier =
    typeof input === "string" ? input.trim().toLowerCase() : "";
  const byHash = BYTES32.test(identifier);
  if (!byHash && !UUID.test(identifier))
    throw new PortalA2Error(
      "invalid_project_identifier",
      "Enter a complete project UUID or 0x-prefixed 32-byte project ID.",
      400,
    );
  if (!dependencies?.expectedState)
    throw new PortalA2Error(
      "project_lookup_context_required",
      "Reload your workspace before searching for a project.",
      409,
    );

  await guard(dependencies);
  let projectId = identifier;
  if (byHash) {
    const list = await portalA2Read("project.list", {}, dependencies);
    if (!Array.isArray(list.items))
      invalid("Project list response has no visible project items.");
    const matches = new Set<string>();
    for (const item of list.items) {
      if (
        !isRecord(item) ||
        typeof item.businessId !== "string" ||
        item.businessId.toLowerCase() !== identifier
      )
        continue;
      if (typeof item.id !== "string" || !UUID.test(item.id))
        invalid("A matching project returned an invalid project UUID.");
      matches.add(item.id.toLowerCase());
    }
    if (!matches.size)
      throw new PortalA2Error(
        "project_not_found",
        "No project with this ID is visible to your account in this demo instance.",
        404,
      );
    if (matches.size !== 1)
      throw new PortalA2Error(
        "project_lookup_ambiguous",
        "The visible project ID is not unique. Reload and verify the exact project UUID.",
        409,
      );
    projectId = [...matches][0];
  }

  let project: Record<string, unknown>;
  try {
    project = await portalA2Read("project.read", { projectId }, dependencies);
  } catch (error) {
    if (error instanceof PortalA2Error && error.status === 404)
      throw new PortalA2Error(
        "project_not_found",
        "No project with this ID is visible to your account in this demo instance.",
        404,
      );
    throw error;
  }
  if (
    typeof project.id !== "string" ||
    project.id.toLowerCase() !== projectId ||
    typeof project.businessId !== "string" ||
    !BYTES32.test(project.businessId) ||
    (byHash && project.businessId.toLowerCase() !== identifier) ||
    typeof project.title !== "string" ||
    typeof project.publicSummary !== "string" ||
    !isRecord(project.chainState) ||
    typeof project.chainState.status !== "string"
  )
    invalid("Project response does not match the requested project.");
  if (project.chainState.verified !== true)
    throw new PortalA2Error(
      "project_unverified",
      "This project has not been confirmed on the current local blockchain.",
      409,
    );
  const businessId = project.businessId.toLowerCase();
  const ledger = await portalA2Read(
    "project.ledger.read",
    { projectId },
    dependencies,
  );
  if (
    typeof ledger.projectId !== "string" ||
    ledger.projectId.toLowerCase() !== projectId ||
    typeof ledger.businessId !== "string" ||
    ledger.businessId.toLowerCase() !== businessId ||
    ledger.chainVerified !== true
  )
    invalid("Project ledger is not verified for this exact project.");

  const donated = atomic(ledger.depositsAtomic);
  const returned = atomic(ledger.returnedAtomic);
  const released = atomic(ledger.releasedAtomic);
  const refunded = atomic(ledger.refundedAtomic);
  const reserved = atomic(ledger.reservedAtomic);
  const locked = donated + returned - released - refunded;
  if (locked < 0n || locked > UINT256_MAX || reserved > locked)
    invalid(
      "Project ledger amounts are inconsistent; no balance was inferred.",
    );

  await guard(dependencies);
  return {
    projectId,
    businessId,
    title: project.title,
    publicSummary: project.publicSummary,
    status: project.chainState.status,
    chainVerified: true,
    amounts: {
      donatedAtomic: donated.toString(),
      lockedAtomic: locked.toString(),
      reservedAtomic: reserved.toString(),
      releasedAtomic: released.toString(),
      returnedAtomic: returned.toString(),
      refundedAtomic: refunded.toString(),
    },
  };
}
