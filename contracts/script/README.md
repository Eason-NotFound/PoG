# Deployment scripts

Reserved for later milestones. M0 does not deploy or implement business contracts.

M3.1 uses `scripts/local-chain.py` (Python standard library plus pinned Foundry
`forge create`/`cast`) for local deployment. A separate Solidity deployment
contract is not required: the accepted business contracts and artifacts remain
unchanged. No public-network deployment or private signing key is supplied here.
