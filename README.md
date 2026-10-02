# Proof of Giving (PoG)

HacKU 2026 FinTech MVP. The team repository is
[Eason-NotFound/PoG](https://github.com/Eason-NotFound/PoG).

Current blockchain milestone: **M1 technically verified; awaiting user acceptance**.
`MockHKD` is a valueless 6-decimal demo ERC-20. `PoGRegistry` records project,
procurement and evidence state and verifies signed AI assessments. Escrow in the
tests is a mock: deposits, human approvals and vendor payments are not implemented.

## Build and verify

Requires Foundry 1.8.4, Solidity 0.8.24 and Python 3 (standard library only).
OpenZeppelin Contracts v5.7.0 and forge-std v1.16.2 are vendored under `contracts/lib`.

```sh
bash scripts/check-blockchain.sh
```

The booth will have Foundation, Recipient and Donor views. The integration
contract for the frontend, API/database, AI evidence and payment teams is in
[docs/INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md).

## Project references

- [Blockchain status](docs/STATUS.md)
- [Contract specification](docs/BLOCKCHAIN_SPEC.md)
- [Build runbook](docs/BLOCKCHAIN_RUNBOOK.md)
- [Milestones](docs/MILESTONES.md)
- [Version control and user acceptance](docs/VERSION_CONTROL.md)

Work proceeds one approved milestone at a time. Preserve accepted versions with
Git history and annotated tags; submit changes through reviewed feature branches.
