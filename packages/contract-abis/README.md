# PoG contract ABIs

Package version: `0.1.0-m1`

Generated from compiled Solidity with Foundry 1.8.4 and Solc 0.8.24:

- `MockHKD.json`
- `PoGRegistry.json`

Regenerate after a successful build:

```sh
forge inspect contracts/src/MockHKD.sol:MockHKD abi --json \
  > packages/contract-abis/MockHKD.json
forge inspect contracts/src/PoGRegistry.sol:PoGRegistry abi --json \
  > packages/contract-abis/PoGRegistry.json
```

These files are generated ABI arrays, not hand-maintained copies.
`ProcurementEscrow.json` is intentionally absent because Escrow is outside M1.

