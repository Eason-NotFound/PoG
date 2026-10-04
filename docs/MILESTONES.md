# Blockchain Milestones

Updated: 2026-10-04. Published source baseline:
`d74a7a0ef53abe32640f019ce1551bf5c2432826`.

This file distinguishes blockchain milestones from current cross-module work.
The AI project's data/model milestones are a separate sequence; their M2/M3
names are not the blockchain M2/M3 below. Start work only within the latest
explicit user task and review gates; this document grants no new scope.

## Current lifecycle summary

| Area | Published state | Remaining work |
| --- | --- | --- |
| M1 / M2 V2 | Accepted and archived contracts | Preserve source, ABIs, accepted tags and historical evidence |
| M3.1 | Accepted local deployment | Check the actual manifest/instance when starting elsewhere |
| API A2 | Published at `api-v0.2.0-a2` / merge `4c9f1a4` | Existing HTTP flow stops at `ReceiptConfirmed` |
| M3.2 tools | Published local API/chain verification code / merge `0014f37` | Broader frontend/model/payment integration is a separate scope |
| AI interface | Frozen R2 format and A2 mapping / merge `d74a7a0` | Runtime source, trusted report storage, scoring policy and signing integration |
| Portal / service loop | Portal demo published; scoped local model work is separate | Shared API adapter, final release/settlement and Allocation/Merkle integration |
| Full prototype / booth | End-to-end acceptance not established | Actual UI/API/database/model/chain trace, failure/retry/role checks |

Historical instructions below record what each milestone authorized at the
time. Later A2/M3.2 publications supersede the old blanket onboarding-only stop
within their approved scopes; neither they nor this update automatically approve
all later work. See [current status](STATUS.md) and
[project overview](PROJECT_OVERVIEW.md) for the cross-module boundary.

## M0/M0.1 — specification and Foundry skeleton (accepted)

- Freeze decisions, data structures, state machine, EIP-712 types, invariants,
  events, integration boundaries, security checklist, and status.
- Create dependency-free Foundry layout and smoke test.
- M0.1 freezes one-time Registry/Escrow binding, counterpart-only call directions,
  split ownership, exact AI evidence hashes, and safe policy update authority.
- At M0 exit, do not implement `MockHKD`, `PoGRegistry`, or
  `ProcurementEscrow`.

Exit: documents agree, directories exist, and `forge build` plus smoke test pass,
or missing local tooling is captured with exact reproducible evidence.

## M1 — test token and registry (user accepted)

Implemented `MockHKD`, Registry metadata/evidence/AI assessment/lifecycle paths,
minimum Escrow interface and test doubles, unit/fuzz tests, pinned dependencies,
runbook, and generated ABIs. Escrow custody/payment is not implemented.

Exit: formatting/build/full tests/diff checks pass, CEO reports technical review,
and the user accepts M1. Passing this exit does not automatically authorize M2.

## M2 V2 — Foundation release, settlement attestation and refunds (user accepted and archived)

User authorization: 2026-10-03, after M1 archival and explicit approval of the
revised Foundation settlement architecture, invoice-limited release, continued
locking, and proportional refunds following closure/reconciliation. The frozen
current scope is `M2_V2_SPEC.md`; flow is `FOUNDATION_SETTLEMENT_FLOW.md`.

Implement independent RegistryV2/EscrowV2 with donor custody, PO-first AI/human
reservation, Invoice/goods and signed Recipient receipt, final AI/human approval,
exact invoice stablecoin release to Foundation, and distinct full mock supplier
payment evidence with human reconciliation. Add Closing restrictions, unsettled
obligation gates, actual stablecoin returns and bounded proportional refunds.
Preserve all M1 source, tests, ABIs and frozen specifications.

Exit: V2 custody/lifecycle/signatures/refunds pass unit, integration, adversarial,
fuzz/invariant and independent CEO checks; M1 regression and baseline hashes
pass; generated V2 ABIs and exact integration instructions are reviewed. CEO
publishes the technically verified candidate, reports results and stops.
No M2 acceptance/merge or M3 authorization is implied by passing tests.

Acceptance recorded on 2026-10-03: the user explicitly accepted AI assessments
as risk evidence, human approval as the final fund decision, MockHKD, simulated
supplier payment and no live AI/API/database deployments. The user authorized
merge and formal archival only. PR #2 merged as `61aa673`; immutable accepted
tag `blockchain-v0.2.0-m2` points to that merge. Main and tag CI passed.
Preserve the RC technical snapshot at `810a54e`. Report and stop; M3 has not been
authorized by this acceptance.

### Historical superseded M2 direct-vendor plan

The original implementation was stopped and its untested draft preserved in a
local ignored archive. The original scope below is historical context only.

User authorization: 2026-10-02, after explicit M1 acceptance and confirmation of
Hank's offline review. Begin only after M1 is merged and formally tagged.

Implement deposits, human approval aggregation, reservation, payment,
cancellation, invariants, and adversarial/replay tests. Add reserveAmount to the
approval submission ABI while preserving ApprovalIntent typed-data fields;
recompute exact terms at submission. Execution counts only unexpired approvals;
an expired signer renews using a fresh nonce without duplicate counting.

Exit: real Escrow unit/integration/invariant checks and full M1 regression pass,
ABI/interface changes are documented, the CEO reports to the user, and work
stops. No M2 acceptance/merge or M3 authorization is implied by passing tests.

## M3 — local integration

Split into explicit sub-gates. M2 acceptance did not itself authorize M3; the
user's subsequent request on 2026-10-03 conditionally authorized M3.1. No blocking
product choice is needed for the local-only defaults in `M3_1_SPEC.md`.

### M3.1 — local Anvil deployment (user accepted and formally tagged)

Implement startup/deploy/status/verify/stop/explicit-reset tools, separate demo
role addresses, RegistryV2/EscrowV2 binding and an instance-bound deployment
manifest. Preserve accepted Solidity, tests, dependencies, ABIs and technical
baselines. RPC is loopback only, chain ID 31337. Test balances are a faucet,
not fiat conversion. Exit: local/live isolated tests and full regression pass;
verified feature PR/CI is reported to the user, then STOP for acceptance.

M3.1 received user acceptance and GitHub upload authorization on 2026-10-03.
PR #4 merged as `977ea62`; new annotated formal version
`blockchain-v0.3.1-m3.1` points to that merge. Its tree equals accepted head
`e6aebae`; RC `6295409` and all previous versions remain unchanged. Archival
recheck: 32/32 local unit/live cases (36.843 seconds), 81/81 Solidity regression,
M1 15/15, M2 18/18, five ABI checks and push guard 12/12 passed. Status records
use a separate PR/CI. Report and STOP; this does not authorize M3.2.

### M3.2 — local API/chain tools (published; broader integration pending)

Later authorized work published the isolated M3.2 verifier and related tests in
merge `0014f37`. The accepted API A2 baseline is `api-v0.2.0-a2` at `4c9f1a4`.
It includes ABI/typed-data/nonce/deadline integration, idempotent submission,
confirmed-event read models and deployment-instance/cache invalidation, limited
to the local synthetic workflow ending at Recipient `ReceiptConfirmed`.
The published tools do not establish real Qwen, final release/payment or portal
integration. Use [A2 status](api/A2_STATUS.md) for the API capability boundary.

### M3.3 — AI/human/payment service loop (scoped work; integration pending)

Integrate actual off-chain risk reports, Recipient signature, final human
decisions, Foundation release and separate simulated supplier-payment attestation
with the AI/API/payment teams. The frozen AI interface is published; a limited
local stage-0 Qwen diagnostic path is separate and not yet published on main.
Actual trusted reports, scoring/signing gates and the final API payment tail
remain to be connected. Do not claim M3.1 or the offline AI tools run these
services; further implementation follows the specific current user task.

### M3.4 — full prototype / multi-client acceptance (not yet established)

Foundation/Recipient/Donor interfaces share one API/chain; normal/failure/refund
paths, cold start and supervised reset are rehearsed with the other teams.
Published preparation documents are plans, not a record of that acceptance.
Require an exact integrated version and actual frontend input, API operations,
database changes, model execution, receipts/events and final Donor output.
Any mock or unsupported step must remain explicit.

## M4 — Base Sepolia demo readiness

Planned only: testnet deployment after explicit approval, address verification,
end-to-end rehearsal, monitoring, and demo runbook. No mainnet or real-value asset.
