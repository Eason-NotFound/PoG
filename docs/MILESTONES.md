# Blockchain Milestones

Each milestone begins only after the CEO reports review results and the user
explicitly authorizes the next milestone. This file
describes sequencing; it authorizes no work beyond the current accepted scope.

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

Planned only: Anvil deployment scripts/manifests, API/relayer integration,
event-confirmation flow, and three booth views using preset test wallets.

## M4 — Base Sepolia demo readiness

Planned only: testnet deployment after explicit approval, address verification,
end-to-end rehearsal, monitoring, and demo runbook. No mainnet or real-value asset.
