# CEO Decisions — M0 Freeze

Last updated: 2026-10-02 (Asia/Hong_Kong)

This document is the product-authority baseline for the PoG blockchain work. A
later milestone may refine implementation details, but changing a frozen decision
requires an explicit CEO decision and a coordinated update to every affected
interface document.

## Product scope

- PoG is a 48-hour hackathon MVP. It does not create a blockchain.
- Local development targets Anvil. The intended public test deployment is Base
  Sepolia; no production-network deployment is authorized.
- The eventual contract set is exactly `MockHKD.sol`, `PoGRegistry.sol`, and
  `ProcurementEscrow.sol`.
- `MockHKD` is a test token with no monetary value. It is not RedCoin and must
  never be presented as a real stablecoin.
- Payment asset addresses are configuration/deployment inputs. No contract,
  service, or UI may hard-code a RedCoin address.
- Deploy Registry first and Escrow second with Registry immutable in Escrow.
  Protocol admin binds Escrow into Registry exactly once before any project; the
  binding can never change.
- Registry owns public lifecycle state. Escrow owns public custody, approvals,
  reservation, and payment. Their internal hooks/callbacks accept only the exact
  paired contract and have no admin bypass.
- M0 is specification plus tooling scaffolding only. It must not implement the
  three business contracts.

## Authority and user experience

- Booth interfaces are Foundation, Recipient, and Donor. A vendor does not get a
  separate booth interface in the MVP; demos use a preset vendor test wallet.
- AI signs structured risk assessments. AI cannot approve a procurement, reserve
  funds, approve payment, or submit a human signature.
- Human approvers sign EIP-712 `ApprovalIntent` messages. A relayer submits those
  messages and later execution transactions. Success is displayed only after a
  matching canonical-chain event is confirmed.
- The MVP approval threshold is 1-of-1. The policy and signed intent bind a policy
  epoch and the on-chain accounting supports a threshold greater than one later.
- A procurement's vendor is fixed when the procurement is created. Changing the
  vendor requires cancelling it before funds are paid and creating a new
  procurement ID.

## Two approval gates

1. Before procurement: request and quote-bundle commitments are recorded into an
   exact pre-evidence hash; an AI pre-procurement assessment signs that hash;
   humans approve exact reserve terms; then `reserveBudget` may execute.
2. After delivery: GRN and final invoice commitments are recorded, an AI final
   assessment signs their exact final-evidence hash; humans approve exact payment
   terms; then `executePayment` may execute.

Neither gate treats an AI outcome as an approval. A valid, non-expired assessment
is required evidence; the human remains accountable for proceeding when a report
shows elevated risk.

## Explicit non-goals

M0 and the MVP exclude upgradeable proxies, NFTs, DAOs, real stablecoins,
cross-chain messaging, Chainlink, zkML, custody of private keys, and autonomous AI
transactions. Donor refunds, project closure, and administrative withdrawal are
also outside the hackathon MVP.
