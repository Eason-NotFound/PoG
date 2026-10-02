# Blockchain Build and Test Runbook

## Toolchain

- Foundry `1.8.4`
- Solidity `0.8.24`
- OpenZeppelin Contracts `v5.7.0`
- forge-std `v1.16.2`

If Foundry is installed under the default user location but is not on `PATH`:

```sh
export PATH="$HOME/.foundry/bin:$PATH"
```

## Install pinned libraries

The repository vendors exact stable tags under `contracts/lib`. To reproduce
them in a clean checkout:

```sh
forge install --no-git --shallow \
  OpenZeppelin/openzeppelin-contracts@tag=v5.7.0 \
  foundry-rs/forge-std@tag=v1.16.2
```

Expected tag commits are recorded in `contracts/lib/README.md`.

## Format, build, and test

```sh
forge fmt --check
forge build
forge test -vv
git diff --check
```

For a clean compiler run, use `forge build --force`. M1 expects smoke,
MockHKD, and PoGRegistry test suites.

## Regenerate ABIs

```sh
forge inspect contracts/src/MockHKD.sol:MockHKD abi --json \
  > packages/contract-abis/MockHKD.json
forge inspect contracts/src/PoGRegistry.sol:PoGRegistry abi --json \
  > packages/contract-abis/PoGRegistry.json
python3 -m json.tool packages/contract-abis/MockHKD.json >/dev/null
python3 -m json.tool packages/contract-abis/PoGRegistry.json >/dev/null
```

Regenerate whenever either public contract surface changes.

## Scope guard

M1 has no deployment command. Do not deploy or implement ProcurementEscrow until
the CEO approves the next milestone.

