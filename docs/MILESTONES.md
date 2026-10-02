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

## M1 — test token and registry (current; awaiting review)

Implemented `MockHKD`, Registry metadata/evidence/AI assessment/lifecycle paths,
minimum Escrow interface and test doubles, unit/fuzz tests, pinned dependencies,
runbook, and generated ABIs. Escrow custody/payment is not implemented.

Exit: formatting/build/full tests/diff checks pass, CEO reports technical review,
and the user accepts M1. Passing this exit does not automatically authorize M2.

## M2 — escrow and approval gates

Planned only: implement deposits, human approval aggregation, reservation,
payment, cancellation, invariants, and adversarial/replay tests.

## M3 — local integration

Planned only: Anvil deployment scripts/manifests, API/relayer integration,
event-confirmation flow, and three booth views using preset test wallets.

## M4 — Base Sepolia demo readiness

Planned only: testnet deployment after explicit approval, address verification,
end-to-end rehearsal, monitoring, and demo runbook. No mainnet or real-value asset.
