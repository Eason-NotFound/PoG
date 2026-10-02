# Proof of Giving (PoG)

HacKU 2026 FinTech MVP. The team repository is
[Eason-NotFound/PoG](https://github.com/Eason-NotFound/PoG).

Current blockchain milestone: **M2 V2 accepted, merged and formally archived; M3 not authorized**.
The accepted implementation is unchanged `MockHKD` plus independent `PoGRegistryV2` and
`ProcurementEscrowV2`. MockHKD is a freely mintable, valueless 6-decimal demo token.
The V2 contracts verify project custody, AI/Recipient/human signatures, exact
Foundation release, separate mock supplier settlement and closure/refunds.
Accepted M1 source/tests/ABIs and tags remain unchanged; its historical Escrow
test double and the locally archived obsolete direct-vendor draft are not V2.

The revised [Foundation settlement flow](docs/FOUNDATION_SETTLEMENT_FLOW.md)
releases approved stablecoins to Foundation after receipt verification, then
separately tracks platform conversion to HKD and supplier payment. Each Donor first
converts simulated HKD to MockHKD, then donates directly to a specific project's
locked Escrow. Recipient signs receipt; release is exactly the approved Invoice
amount. Active-project remainder stays locked; following termination and human
reconciliation it is refunded proportionally to original donor wallets.

M2 adds independent `PoGRegistryV2` / `ProcurementEscrowV2` and tests while keeping
all accepted M1 source/ABIs unchanged. Scope is frozen in
[M2 V2 specification](docs/M2_V2_SPEC.md). Independent verification passed 81/81
tests, with all five fuzz cases repeated at 2,000 runs, strict source lint, ABI
consistency, runtime-size limits, M1 baseline 15/15 and push-guard 12/12.
This does not imply Anvil deployment, actual AI service or real fiat payment.

Verified source snapshot: `blockchain-v0.2.0-m2-rc.1` at `810a54e`.
The user accepted M2 V2 on 2026-10-03, including AI as risk evidence, human
approval as the final fund decision, and the deliberate mock/no-live-service limits.
[PR #2](https://github.com/Eason-NotFound/PoG/pull/2) is merged as `61aa673`;
the new immutable accepted tag is
[`blockchain-v0.2.0-m2`](https://github.com/Eason-NotFound/PoG/tree/blockchain-v0.2.0-m2).
Main and accepted-tag CI passed. M1 and the M2 RC remain unchanged.
The frozen ABI package retains its original `0.2.0-m2-rc.1` artifact identifier;
acceptance is recorded by the formal Git tag and [current status](docs/STATUS.md),
not by rewriting verified artifacts. Stop after archival; M3 needs a separate authorization.

## Build and verify

Requires Foundry 1.8.4, Solidity 0.8.24 and Python 3 (standard library only).
OpenZeppelin Contracts v5.7.0 and forge-std v1.16.2 are vendored under `contracts/lib`.

```sh
bash scripts/check-blockchain.sh
```

The booth will have Foundation, Recipient and Donor views. The integration
plan for frontend, API/database, AI evidence and payment teams is in
[V2 integration hand-off](docs/M2_V2_INTEGRATION.md). API paths in that file are
proposed resources, not running endpoints. The
[exact V2 implementation interface](docs/M2_V2_INTERFACE_IMPLEMENTED.md) includes
call prototypes, enum ordinals, signing types and hash formulas.
[Historical M1 integration](docs/INTEGRATION_CONTRACT.md) is preserved for M1;
do not apply its direct-vendor payment API or version-1 signatures to V2.

## Project references

- [Blockchain status](docs/STATUS.md)
- [Current V2 specification](docs/M2_V2_SPEC.md)
- [Historical M1 specification](docs/BLOCKCHAIN_SPEC.md)
- [Build runbook](docs/BLOCKCHAIN_RUNBOOK.md)
- [V2 verification and acceptance](docs/M2_V2_RUNBOOK.md)
- [Milestones](docs/MILESTONES.md)
- [Version control and user acceptance](docs/VERSION_CONTROL.md)

Work proceeds one approved milestone at a time. Preserve accepted versions with
Git history and annotated tags; submit changes through reviewed feature branches.
