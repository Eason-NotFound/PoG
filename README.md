# Proof of Giving (PoG)

HacKU 2026 FinTech MVP. The team repository is
[Eason-NotFound/PoG](https://github.com/Eason-NotFound/PoG).

Current blockchain milestone: **M1 accepted and archived; M2 architecture revised**.
`MockHKD` is a valueless 6-decimal demo ERC-20. `PoGRegistry` records project,
procurement and evidence state and verifies signed AI assessments. Escrow in the
tests is a mock: deposits, human approvals and payments are not verified as a
real system. The old direct-vendor Escrow draft is superseded, untested and
uncommitted; it is not the current architecture's implementation.

The revised [Foundation settlement flow](docs/FOUNDATION_SETTLEMENT_FLOW.md)
releases approved stablecoins to Foundation after receipt verification, then
separately tracks platform conversion to HKD and supplier payment. Each Donor first
converts simulated HKD to MockHKD, then donates directly to a specific project's
locked Escrow. Recipient confirms receipt. The release-amount rule remains to be
confirmed before revised implementation dispatch.

## Build and verify

Requires Foundry 1.8.4, Solidity 0.8.24 and Python 3 (standard library only).
OpenZeppelin Contracts v5.7.0 and forge-std v1.16.2 are vendored under `contracts/lib`.

```sh
bash scripts/check-blockchain.sh
```

The booth will have Foundation, Recipient and Donor views. The integration
contract for the frontend, API/database, AI evidence and payment teams is in
[docs/INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md).
It is the historical M0.1/M1 interface baseline; revised Foundation-settlement
interfaces are not yet frozen and must not be inferred from the old payment API.

## Project references

- [Blockchain status](docs/STATUS.md)
- [Contract specification](docs/BLOCKCHAIN_SPEC.md)
- [Build runbook](docs/BLOCKCHAIN_RUNBOOK.md)
- [Milestones](docs/MILESTONES.md)
- [Version control and user acceptance](docs/VERSION_CONTROL.md)

Work proceeds one approved milestone at a time. Preserve accepted versions with
Git history and annotated tags; submit changes through reviewed feature branches.
