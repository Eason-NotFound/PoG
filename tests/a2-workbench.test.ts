import { test } from "node:test";
import assert from "node:assert/strict";
import {
  A2RequestGeneration,
  StaleA2Response,
  commitA2Response,
  guardA2Response,
  decimalToAtomic,
  deploymentIdentity,
  formatAtomic,
  freeLockedAtomic,
  isChainConfirmed,
  mayPrepareSigning,
  maySignRequest,
  publicJson,
  type A2Ledger,
  type A2Operation,
  type A2SigningRequest,
  type A2AsyncContext,
} from "../src/lib/a2-workbench";

test("six-decimal conversion never loses atomic units or uses float rounding", () => {
  assert.equal(decimalToAtomic("0.000001"), "1");
  assert.equal(decimalToAtomic("100.000001"), "100000001");
  assert.equal(
    decimalToAtomic("9007199254740993.123456"),
    "9007199254740993123456",
  );
  assert.equal(
    formatAtomic("9007199254740993123456"),
    "9007199254740993.123456",
  );
  assert.equal(formatAtomic("100000000"), "100");
});

test("zero, excess precision, exponent, negative and noncanonical amounts fail closed", () => {
  for (const value of [
    "0",
    "0.000000",
    "1.0000001",
    "1e3",
    "-1",
    "NaN",
    ".1",
    "01",
    " 1",
    "1.",
  ]) {
    assert.throws(() => decimalToAtomic(value));
  }
  assert.throws(() => decimalToAtomic((1n << 256n).toString()));
  assert.throws(() => formatAtomic("01"));
  assert.throws(() => formatAtomic("-1"));
});

const ledger: A2Ledger = {
  projectId: "p",
  businessId: "b",
  asset: "mHKD",
  depositsAtomic: "100000000",
  reservedAtomic: "80000000",
  releasedAtomic: "0",
  returnedAtomic: "0",
  refundedAtomic: "0",
  refundPoolAtomic: "0",
  currentCallerDonorCreditAtomic: null,
  chainVerified: true,
};

test("derived free funds use verified ledger only, not demo store or Foundation release assumptions", () => {
  assert.equal(freeLockedAtomic(ledger), "20000000");
  assert.throws(() => freeLockedAtomic({ ...ledger, chainVerified: false }));
  assert.throws(() =>
    freeLockedAtomic({ ...ledger, reservedAtomic: "100000001" }),
  );
});

const confirmed: A2Operation = {
  operationId: "op",
  status: "confirmed",
  operationKind: "donation",
  chainVerified: true,
  steps: [
    {
      stepIndex: 0,
      kind: "deposit",
      status: "confirmed",
      transaction: {
        status: "confirmed",
        transactionHash: "0x" + "a".repeat(64),
        receiptStatus: 1,
        blockNumber: 21,
        blockHash: "0x" + "b".repeat(64),
        canonical: true,
      },
    },
  ],
};

test("202, hashes alone, noncanonical receipts and offchain drafts are not chain confirmation", () => {
  assert.equal(isChainConfirmed(confirmed), true);
  assert.equal(isChainConfirmed({ ...confirmed, status: "queued" }), false);
  assert.equal(isChainConfirmed({ ...confirmed, chainVerified: false }), false);
  assert.equal(isChainConfirmed({ ...confirmed, steps: [] }), false);
  for (const patch of [
    { canonical: false },
    { receiptStatus: 0 },
    { blockHash: undefined },
    { blockNumber: undefined },
  ]) {
    assert.equal(
      isChainConfirmed({
        ...confirmed,
        steps: [
          {
            ...confirmed.steps![0],
            transaction: {
              ...confirmed.steps![0].transaction!,
              ...patch,
            },
          },
        ],
      }),
      false,
    );
  }
});

test("page-admin, Foundation and Recipient do not gain each other's signing authority", () => {
  assert.equal(mayPrepareSigning("human_approver", "reserve"), true);
  assert.equal(mayPrepareSigning("admin", "reserve"), false);
  assert.equal(mayPrepareSigning("foundation", "receipt"), false);
  assert.equal(mayPrepareSigning("recipient", "ai_pre"), false);
  const request: A2SigningRequest = {
    id: "r",
    procurementId: "p",
    kind: "reserve",
    status: "prepared",
    signer: "0xAb",
    nonce: "0",
    deadline: "1",
    digest: "h",
    policyEpoch: 1,
    synthetic: false,
    typedData: null,
  };
  assert.equal(
    maySignRequest(
      {
        id: "u",
        username: "admin",
        role: "human_approver",
        walletAddress: "0xaB",
      },
      request,
    ),
    true,
  );
  assert.equal(
    maySignRequest(
      {
        id: "u",
        username: "admin",
        role: "human_approver",
        walletAddress: "0xcd",
      },
      request,
    ),
    false,
  );
});

test("rendered diagnostic JSON does not disclose tokens, signatures or passwords", () => {
  const rendered = publicJson({
    token: "SECRET_TOKEN",
    nested: { signature: "SECRET_SIG", password: "SECRET_PASS" },
    digest: "public",
  });
  assert.equal(rendered.includes("SECRET"), false);
  assert.equal(rendered.includes("public"), true);
});

test("deployment identity requires actual run/instance and distinguishes a chain reset", () => {
  assert.equal(deploymentIdentity({}), null);
  assert.equal(deploymentIdentity({ runId: "r", instanceId: null }), null);
  assert.notEqual(
    deploymentIdentity({ runId: "r1", instanceId: "i1" }),
    deploymentIdentity({ runId: "r2", instanceId: "i2" }),
  );
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("late list/signing response is discarded after switching identity", async () => {
  const gate = new A2RequestGeneration();
  let context: A2AsyncContext = {
    identity: "foundation:user1:wallet1",
    deployment: "run1:instance1",
  };
  const scope = gate.capture(context);
  const source = deferred<string>();
  const pending = guardA2Response(
    gate,
    scope,
    () => context,
    () => source.promise,
  );
  const rejected = assert.rejects(pending, StaleA2Response);
  context = { ...context, identity: "recipient:user2:wallet2" };
  gate.invalidate();
  source.resolve("old private signing/list data");
  await rejected;
  let committed = false;
  assert.equal(
    commitA2Response(
      gate,
      scope,
      () => context,
      () => {
        committed = true;
      },
    ),
    false,
  );
  assert.equal(committed, false);
});

test("same-user deployment reset discards a pending mutation response", async () => {
  const gate = new A2RequestGeneration();
  let context: A2AsyncContext = {
    identity: "foundation:user1:wallet1",
    deployment: "run1:instance1",
  };
  const scope = gate.capture(context);
  const source = deferred<object>();
  const pending = guardA2Response(
    gate,
    scope,
    () => context,
    () => source.promise,
  );
  const rejected = assert.rejects(pending, StaleA2Response);
  context = { ...context, deployment: "run2:instance2" };
  gate.invalidate();
  source.resolve({
    operationId: "old-operation",
    signingRequest: "old-request",
  });
  await rejected;
  assert.equal(gate.current(scope, context), false);
  assert.equal(gate.current(gate.capture(context), context), true);
});

test("logging out and back to the same identity still invalidates older generation", () => {
  const gate = new A2RequestGeneration();
  const context = {
    identity: "foundation:user1:wallet1",
    deployment: "run1:instance1",
  };
  const old = gate.capture(context);
  gate.invalidate(); // logout clears all private state
  gate.invalidate(); // same user logs in again
  assert.equal(gate.current(old, context), false);
  assert.equal(gate.current(gate.capture(context), context), true);
});

test("late 401/error cannot clear new identity, show old errors or finish new busy state", async () => {
  const gate = new A2RequestGeneration();
  let context: A2AsyncContext = {
    identity: "old-user",
    deployment: "run1:instance1",
  };
  const scope = gate.capture(context);
  const source = deferred<unknown>();
  const state = { identity: "new-user", error: "new-error", busy: true };
  const pending = (async () => {
    try {
      await guardA2Response(
        gate,
        scope,
        () => context,
        () => source.promise,
      );
    } catch (problem) {
      commitA2Response(
        gate,
        scope,
        () => context,
        () => {
          state.identity = "logged-out";
          state.error = String(problem);
        },
      );
    } finally {
      commitA2Response(
        gate,
        scope,
        () => context,
        () => {
          state.busy = false;
        },
      );
    }
  })();
  context = { ...context, identity: "new-user" };
  gate.invalidate();
  source.reject({ status: 401, code: "invalid_session" });
  await pending;
  assert.deepEqual(state, {
    identity: "new-user",
    error: "new-error",
    busy: true,
  });
});

test("operation lookup/error recovery cannot resurrect old data after reset", async () => {
  const gate = new A2RequestGeneration();
  let context: A2AsyncContext = {
    identity: "same-user",
    deployment: "run1:instance1",
  };
  const scope = gate.capture(context);
  const source = deferred<string>();
  const operations: string[] = [];
  const pending = (async () => {
    const operation = await guardA2Response(
      gate,
      scope,
      () => context,
      () => source.promise,
    ).catch(() => null);
    if (operation)
      commitA2Response(
        gate,
        scope,
        () => context,
        () => {
          operations.push(operation);
        },
      );
  })();
  context = { ...context, deployment: "run2:instance2" };
  gate.invalidate();
  source.resolve("old-operation");
  await pending;
  assert.deepEqual(operations, []);
});

test("recheck at state commit closes the post-resolution continuation window", async () => {
  const gate = new A2RequestGeneration();
  const context = { identity: "same-user", deployment: "same-deployment" };
  const scope = gate.capture(context);
  const result = await guardA2Response(
    gate,
    scope,
    () => context,
    async () => "old-result",
  );
  gate.invalidate();
  let rendered = "new state";
  assert.equal(
    commitA2Response(
      gate,
      scope,
      () => context,
      () => {
        rendered = result;
      },
    ),
    false,
  );
  assert.equal(rendered, "new state");
});

test("an invalidated retry scope cannot start a new network request", async () => {
  const gate = new A2RequestGeneration();
  const context = { identity: "same-user", deployment: "same-deployment" };
  const scope = gate.capture(context);
  gate.invalidate();
  let requests = 0;
  await assert.rejects(
    guardA2Response(
      gate,
      scope,
      () => context,
      async () => {
        requests += 1;
        return {};
      },
    ),
    StaleA2Response,
  );
  assert.equal(requests, 0);
});
