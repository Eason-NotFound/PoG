"""Local simulation payment domain, sharing the API's PostgreSQL transaction.

No endpoint or signer lives here. Reservations require the original authenticated
actor; workers finalize only server-persisted, canonical chain dependencies.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID

from eth_utils import keccak
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from web3 import Web3

from .amounts import UINT256_MAX, validate_uint256_string
from .errors import APIError
from .idempotency import audit
from .models import (ChainEvent, ChainTransaction, DeploymentInstance, Document, DocumentVersion,
                     Operation, OperationStep, Procurement, Project, User, WalletAuthorization)
from .payment_models import (CanonicalOutflow, FundedClaim, HKDJournal, PaymentEvidence,
                             PaymentResource, SimHKDAccount)


FIXTURE_VERSION = "pog-sim-hkd-opening-v1"
ATOMIC_PER_CENT = 10_000
ZERO_ADDRESS = "0x" + "0" * 40
PROOF_FIELDS = frozenset(("operationId", "transactionId", "txHash", "receiptStatus", "blockNumber",
                          "blockHash", "emitter", "logIndex", "caller", "target"))
BINDING_FIELDS = frozenset(("namespaceId", "runId", "instanceId", "chainId", "registry", "escrow", "token"))
EVIDENCE_BASE_FIELDS = frozenset((
    "schemaVersion", "kind", "mode", "binding", "projectId", "projectChainId", "procurementId",
    "procurementChainId", "foundation", "treasury", "vendor", "token", "amountAtomic", "hkdCents",
    "invoiceDocumentVersionId", "invoiceHash", "releaseOperationId", "releaseProof", "redemptionResourceId",
    "redemptionOperationId", "redemptionProof", "redemptionJournalId",
))
PAYMENT_EVIDENCE_FIELDS = EVIDENCE_BASE_FIELDS | frozenset(("paymentResourceId", "paymentOperationId", "paymentJournalId"))


def _error(code: str, message: str, status: int = 409):
    raise APIError(status, code, message)


def positive_uint(value: str | int | Decimal) -> int:
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            _error("payment_amount_invalid", "Amount must be an unsigned integer", 422)
        value = str(int(value))
    elif type(value) is int:
        value = str(value)
    try:
        validate_uint256_string(value)
    except (TypeError, ValueError):
        _error("payment_amount_invalid", "Amount must be canonical uint256 decimal text", 422)
    amount = int(value)
    if amount == 0:
        _error("payment_amount_invalid", "Amount must be positive", 422)
    return amount


def cents_to_atomic(value) -> tuple[int, int]:
    cents = positive_uint(value)
    if cents > UINT256_MAX // ATOMIC_PER_CENT:
        _error("payment_amount_invalid", "Converted amount exceeds uint256", 422)
    return cents, cents * ATOMIC_PER_CENT


def atomic_to_cents(value) -> tuple[int, int]:
    amount = positive_uint(value)
    if amount % ATOMIC_PER_CENT:
        _error("payment_subcent_amount", "Full Invoice amount must be an exact HKD cent", 422)
    return amount // ATOMIC_PER_CENT, amount


def binding(ns: DeploymentInstance, gate) -> dict:
    return {"namespaceId": str(ns.id), "runId": ns.run_id, "instanceId": ns.instance_id,
            "chainId": "31337", "registry": gate.contract_address("PoGRegistryV2"),
            "escrow": gate.contract_address("ProcurementEscrowV2"), "token": gate.contract_address("MockHKD")}


def _namespace(ns, gate):
    gate.verify()
    if not ns.active or not ns.verified or ns.mode != "verified" or ns.chain_id != 31337:
        _error("deployment_instance_inactive", "Payment requires the current verified local instance")
    if ns.run_id != gate.run_id or ns.instance_id != gate.instance_id or ns.manifest_sha256 != gate.manifest_sha256:
        _error("deployment_instance_changed", "Payment deployment binding changed")


def _lock(session, namespace_id, label):
    key = int.from_bytes(hashlib.sha256(f"pog-payment:{namespace_id}:{label}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _actor(session, ns, principal, role, gate):
    _namespace(ns, gate)
    if principal.role != role:
        _error("role_forbidden", "This payment action is not available to the current role", 403)
    user = session.get(User, principal.user_id)
    auths = session.scalars(select(WalletAuthorization).where(
        WalletAuthorization.user_id == principal.user_id,
        WalletAuthorization.role_name == role, WalletAuthorization.active.is_(True),
    )).all()
    if user is None or not user.active or len(auths) != 1 or auths[0].wallet_address.lower() != principal.wallet_address.lower():
        _error("signer_mismatch", "Payment actor no longer has its original active role wallet", 403)
    allowed = [gate.roles["donorA"], gate.roles["donorB"]] if role == "donor" else [gate.roles["foundation"]]
    if principal.wallet_address.lower() not in [value.lower() for value in allowed]:
        _error("manifest_role_mismatch", "Payment actor must use its independent manifest role wallet")


def _project(ns, project, gate, *, active=True):
    if project.namespace_id != ns.id:
        _error("resource_not_found", "Project not found in current namespace", 404)
    view = gate.call("PoGRegistryV2", "getProject", project.business_id)
    if str(view[1]).lower() != project.foundation_wallet.lower() or str(view[3]).lower() != gate.contract_address("MockHKD").lower():
        _error("payment_project_binding", "Project parties or simulated token changed")
    if active and int(view[7]) != 0:
        _error("project_not_active", "Funding and funded donations require an active project")


def _foundation(session, ns, principal, procurement, project, gate):
    _actor(session, ns, principal, "foundation", gate)
    if (procurement.namespace_id != ns.id or procurement.project_id != project.id
            or project.foundation_user_id != principal.user_id
            or procurement.foundation_user_id != principal.user_id
            or project.foundation_wallet.lower() != principal.wallet_address.lower()):
        _error("resource_forbidden", "Only this procurement's owning Foundation may perform payment", 403)
    _project(ns, project, gate, active=False)


def _account(session, ns, party_key, *, lock=False):
    query = select(SimHKDAccount).where(SimHKDAccount.namespace_id == ns.id, SimHKDAccount.party_key == party_key.lower())
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None or row.fixture_version != FIXTURE_VERSION:
        _error("simulation_account_unprovisioned", "Explicit simulation-account provisioning is required")
    return row


def _owner_account(session, ns, principal, *, lock=False):
    row = _account(session, ns, principal.wallet_address, lock=lock)
    if row.owner_user_id != principal.user_id or row.role != principal.role:
        _error("simulation_account_binding", "Simulation account does not belong to the current role wallet", 403)
    return row


def _locked_accounts(session, ns, debit_key, credit_key):
    keys = sorted({debit_key.lower(), credit_key.lower()})
    rows = session.scalars(select(SimHKDAccount).where(
        SimHKDAccount.namespace_id == ns.id, SimHKDAccount.party_key.in_(keys),
    ).order_by(SimHKDAccount.party_key).with_for_update()).all()
    if len(rows) != 2 or any(row.fixture_version != FIXTURE_VERSION for row in rows):
        _error("simulation_account_unprovisioned", "Both simulated payment parties must be explicitly provisioned")
    mapping = {row.party_key: row for row in rows}
    return mapping[debit_key.lower()], mapping[credit_key.lower()]


def _journal(session, ns, *, debit_key, credit_key, cents, effect, operation=None, fixture_key=None, from_hold=False):
    cents = positive_uint(cents)
    existing = session.scalar(select(HKDJournal).where(
        HKDJournal.namespace_id == ns.id,
        HKDJournal.fixture_key == fixture_key if fixture_key is not None else
        (HKDJournal.operation_id == operation.id) & (HKDJournal.effect_kind == effect),
    ))
    if existing is not None:
        _error("payment_effect_conflict", "Economic effect is already recorded; inspect its original resource")
    debit, credit = _locked_accounts(session, ns, debit_key, credit_key)
    source_balance = int(debit.held_cents if from_hold else debit.available_cents)
    if debit.role != "fixture_equity" and source_balance < cents:
        _error("sim_hkd_insufficient", "Insufficient available simulated HKD")
    if int(credit.available_cents) + int(credit.held_cents) > UINT256_MAX - cents:
        _error("payment_amount_invalid", "Simulated account capacity would overflow", 422)
    if from_hold:
        debit.held_cents = Decimal(source_balance - cents)
    else:
        debit.available_cents = Decimal(source_balance - cents)
    credit.available_cents = Decimal(int(credit.available_cents) + cents)
    journal = HKDJournal(namespace_id=ns.id, operation_id=operation.id if operation else None,
                         effect_kind=effect, fixture_key=fixture_key, debit_account_id=debit.id,
                         credit_account_id=credit.id, cents=Decimal(cents))
    session.add(journal)
    session.flush()
    return journal


def provision_simulation(session, ns, gate, donor_opening_cents=100000):
    """Explicit technical CLI fixture. No login, refresh or worker auto-top-up."""
    _namespace(ns, gate)
    cents, _ = cents_to_atomic(donor_opening_cents)
    _lock(session, ns.id, "provision")
    definitions = [("clearing", "clearing", None, None), ("fixture_equity", "fixture_equity", None, None),
                   (gate.roles["vendor"].lower(), "supplier", None, gate.roles["vendor"])]
    auths = session.execute(select(WalletAuthorization, User).join(User, User.id == WalletAuthorization.user_id).where(
        WalletAuthorization.active.is_(True), User.active.is_(True),
        WalletAuthorization.role_name.in_(("donor", "foundation")),
    )).all()
    for authorization, user in auths:
        role = authorization.role_name
        allowed = (gate.roles["donorA"], gate.roles["donorB"]) if role == "donor" else (gate.roles["foundation"],)
        if authorization.wallet_address.lower() not in {wallet.lower() for wallet in allowed}:
            continue
        definitions.append((authorization.wallet_address.lower(), role, user.id, authorization.wallet_address))
    for key, role, owner, wallet in definitions:
        row = session.scalar(select(SimHKDAccount).where(SimHKDAccount.namespace_id == ns.id, SimHKDAccount.party_key == key))
        if row is None:
            row = SimHKDAccount(namespace_id=ns.id, party_key=key, owner_user_id=owner,
                                wallet_address=wallet, role=role, available_cents=0,
                                held_cents=0, fixture_version=FIXTURE_VERSION)
            session.add(row)
            session.flush()
        elif row.role != role or row.owner_user_id != owner or row.fixture_version != FIXTURE_VERSION:
            _error("simulation_account_binding", "Provisioning conflicts with an existing account")
        if role == "donor":
            fixture_key = f"{FIXTURE_VERSION}:{row.id}"
            old = session.scalar(select(HKDJournal).where(HKDJournal.namespace_id == ns.id, HKDJournal.fixture_key == fixture_key))
            if old is None:
                _journal(session, ns, debit_key="fixture_equity", credit_key=key, cents=cents,
                         effect="opening", fixture_key=fixture_key)
                audit(session, principal_id=owner, action="payment.fixture_provision", outcome="confirmed",
                      resource_type="sim_hkd_account", resource_id=row.id,
                      metadata={"mode": "simulation", "fixtureVersion": FIXTURE_VERSION, "hkdCents": str(cents)})
            elif int(old.cents) != cents:
                _error("simulation_fixture_conflict", "Opening fixture amount is immutable")
    return {"mode": "simulation", "namespaceId": str(ns.id), "fixtureVersion": FIXTURE_VERSION,
            "accountCount": len(definitions)}


def account_dto(session, ns, principal):
    if principal.role not in ("donor", "foundation"):
        _error("role_forbidden", "Only Donor and Foundation have own simulated HKD accounts", 403)
    row = _owner_account(session, ns, principal)
    return {"id": str(row.id), "namespaceId": str(ns.id), "mode": "simulation",
            "availableHkdCents": str(int(row.available_cents)), "heldHkdCents": str(int(row.held_cents)),
            "fixtureVersion": row.fixture_version}


def _resource(session, ns, principal, project, operation, kind, cents, amount, gate, *, procurement=None, source=None, invoice=None, material=None):
    if operation.namespace_id != ns.id or operation.principal_id != principal.user_id:
        _error("payment_operation_binding", "Payment operation does not belong to this actor and namespace")
    row = PaymentResource(namespace_id=ns.id, operation_id=operation.id, source_operation_id=source,
                          actor_user_id=principal.user_id, project_id=project.id,
                          procurement_id=procurement.id if procurement else None,
                          kind=kind, status="held" if kind == "funding" else "queued",
                          hkd_cents=Decimal(cents), amount_atomic=Decimal(amount), allocated_atomic=0,
                          actor_wallet=principal.wallet_address,
                          counterparty_wallet=(gate.roles["relayer"] if kind == "funding" else
                                               gate.roles["mockRedemption"] if kind == "redemption" else procurement.vendor_wallet),
                          token_address=gate.contract_address("MockHKD"), invoice_version_id=invoice,
                          source_material=material or {}, journal_ids=[])
    session.add(row)
    session.flush()
    operation.result_resource_type = "payment_resource"
    operation.result_resource_id = row.id
    audit(session, principal_id=principal.user_id, operation_id=operation.id, action=f"payment.{kind}.reserve",
          outcome="queued", resource_type="payment_resource", resource_id=row.id,
          metadata={"mode": "simulation", "hkdCents": str(cents), "amountAtomic": str(amount)})
    return row


def reserve_funding(session, ns, principal, project, operation, hkd_cents, gate):
    _actor(session, ns, principal, "donor", gate)
    _project(ns, project, gate)
    cents, amount = cents_to_atomic(hkd_cents)
    _lock(session, ns.id, f"donor:{principal.wallet_address.lower()}")
    account = _owner_account(session, ns, principal, lock=True)
    _account(session, ns, "clearing")
    if int(account.available_cents) < cents:
        _error("sim_hkd_insufficient", "Insufficient available simulated HKD")
    if not reconcile_donor_outflows(session, ns, principal.wallet_address, gate):
        _error("funding_requires_attention", "Donor token movements require reconciliation")
    account.available_cents = Decimal(int(account.available_cents) - cents)
    account.held_cents = Decimal(int(account.held_cents) + cents)
    before = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(principal.wallet_address)))
    return _resource(session, ns, principal, project, operation, "funding", cents, amount, gate,
                     material={"balanceBeforeAtomic": str(before)})


def reserve_funded_donation(session, ns, principal, project, operation, funding_operation_id, amount_atomic, gate):
    _actor(session, ns, principal, "donor", gate)
    _project(ns, project, gate)
    amount = positive_uint(amount_atomic)
    _lock(session, ns.id, f"donor:{principal.wallet_address.lower()}")
    resource = session.scalar(select(PaymentResource).where(
        PaymentResource.namespace_id == ns.id, PaymentResource.operation_id == funding_operation_id,
        PaymentResource.kind == "funding",
    ).with_for_update())
    if (resource is None or resource.actor_user_id != principal.user_id or resource.project_id != project.id
            or resource.actor_wallet.lower() != principal.wallet_address.lower()):
        _error("funding_source_forbidden", "Funding must belong to this Donor, project and instance", 403)
    if resource.status != "reconciled":
        _error("funding_not_reconciled", "Only canonically reconciled funding has usable credit")
    assert_resource_canonical(session, ns, resource, gate)
    if not reconcile_donor_outflows(session, ns, principal.wallet_address, gate):
        _error("funding_requires_attention", "Donor token movements require reconciliation")
    if int(resource.allocated_atomic) > int(resource.amount_atomic) - amount:
        _error("funded_credit_insufficient", "Funding credit is already claimed or insufficient")
    if operation.namespace_id != ns.id or operation.principal_id != principal.user_id:
        _error("payment_operation_binding", "Donation operation binding is invalid")
    resource.allocated_atomic = Decimal(int(resource.allocated_atomic) + amount)
    claim = FundedClaim(namespace_id=ns.id, operation_id=operation.id, funding_resource_id=resource.id,
                        actor_user_id=principal.user_id, project_id=project.id,
                        donor_wallet=principal.wallet_address, amount_atomic=Decimal(amount), status="reserved")
    session.add(claim)
    session.flush()
    operation.result_resource_type = "funded_claim"
    operation.result_resource_id = claim.id
    audit(session, principal_id=principal.user_id, operation_id=operation.id, action="payment.funded_credit.reserve",
          outcome="queued", resource_type="funded_claim", resource_id=claim.id,
          metadata={"fundingResourceId": str(resource.id), "amountAtomic": str(amount)})
    return claim


def _hex(value):
    if isinstance(value, (bytes, bytearray)) or hasattr(value, "hex") and not isinstance(value, str):
        result = value.hex()
        return (result if result.startswith("0x") else "0x" + result).lower()
    return str(value).lower()


def chain_proof(session, ns, operation_id, action, gate, *, expected_caller=None, expected_args=None, expected_event=None, exact_event=None):
    """Revalidate persisted envelope, canonical status-1 receipt, and stored event."""
    rows = session.execute(select(ChainTransaction, OperationStep).join(
        OperationStep, OperationStep.id == ChainTransaction.step_id,
    ).where(ChainTransaction.namespace_id == ns.id, ChainTransaction.operation_id == operation_id,
            OperationStep.detail["action"].astext == action)).all()
    if len(rows) != 1:
        _error("payment_chain_source_missing", "Exact original chain dependency is not persisted")
    tx, step = rows[0]
    detail = step.detail
    target_name = "MockHKD" if action in ("payment.mint", "payment.redeem") else "ProcurementEscrowV2"
    caller = expected_caller or detail.get("caller")
    args = expected_args if expected_args is not None else detail.get("args")
    event_name = expected_event or detail.get("expectedEvent")
    target = gate.contract_address(target_name)
    if (tx.status != "confirmed" or not tx.canonical or step.status != "confirmed" or tx.chain_id != 31337
            or not tx.tx_hash or not tx.receipt_json or int(tx.receipt_json.get("status", -1)) != 1
            or _hex(tx.caller_address) != _hex(caller) or _hex(tx.to_address) != _hex(target)
            or str(tx.value_text) != "0" or detail.get("args") != args
            or detail.get("expectedEvent") != event_name or _hex(detail.get("caller")) != _hex(caller)):
        _error("payment_chain_source_invalid", "Original chain dependency is not an exact confirmed envelope")
    envelope = gate.prepare(action, caller, args, event_name)
    if _hex(tx.calldata) != _hex(envelope.data) or _hex(envelope.to) != _hex(target):
        _error("payment_chain_source_invalid", "Persisted chain calldata differs from the fixed action")
    receipt, current_events = gate.receipt_with_events(tx.tx_hash, event_name)
    stored = tx.receipt_json
    if (receipt is None or int(receipt.get("status", -1)) != 1
            or _hex(receipt.get("transactionHash")) != _hex(tx.tx_hash)
            or _hex(receipt.get("blockHash")) != _hex(tx.block_hash)
            or _hex(receipt.get("blockHash")) != _hex(stored.get("blockHash"))
            or int(receipt.get("blockNumber", -1)) != int(stored.get("blockNumber", -2))
            or _hex(receipt.get("from")) != _hex(caller) or _hex(receipt.get("to")) != _hex(target)
            or not gate.canonical(int(receipt["blockNumber"]), receipt["blockHash"])):
        _error("payment_chain_reorganization", "Original payment dependency is no longer canonical")
    events = session.scalars(select(ChainEvent).where(
        ChainEvent.namespace_id == ns.id, ChainEvent.tx_hash == tx.tx_hash,
        ChainEvent.block_hash == tx.block_hash, ChainEvent.event_name == event_name,
        func.lower(ChainEvent.contract_address) == target.lower(), ChainEvent.canonical.is_(True),
    )).all()
    matches = []
    for event in events:
        if exact_event and not all(_hex(event.payload.get(name)) == _hex(value) for name, value in exact_event.items()):
            continue
        if any(int(fresh["logIndex"]) == event.log_index and _hex(fresh["address"]) == _hex(target)
               and _hex(fresh["blockHash"]) == _hex(tx.block_hash)
               and all(_hex(fresh["args"].get(name)) == _hex(value) for name, value in event.payload.items())
               for fresh in current_events):
            matches.append(event)
    if len(matches) != 1:
        _error("payment_chain_source_invalid", "Exact canonical source event is absent or ambiguous")
    event = matches[0]
    return {"operationId": str(operation_id), "transactionId": str(tx.id), "txHash": tx.tx_hash,
            "receiptStatus": "1", "blockNumber": str(receipt["blockNumber"]), "blockHash": tx.block_hash,
            "emitter": target, "logIndex": str(event.log_index), "caller": caller, "target": target}


def _proof_still_canonical(proof, gate):
    if not isinstance(proof, dict) or set(proof) != PROOF_FIELDS or proof.get("receiptStatus") != "1":
        _error("payment_chain_source_invalid", "Stored payment proof schema is invalid")
    if not gate.canonical(int(proof["blockNumber"]), proof["blockHash"]):
        _error("payment_chain_reorganization", "Original payment proof is no longer canonical")


def _invoice_source(session, ns, procurement):
    # Shared full-demo original PO/request/goods/Invoice provenance guard.
    # Lazy import avoids making model/module initialization circular.
    from .a2 import validate_original_sources
    validate_original_sources(session, procurement)
    material = procurement.source_versions_json or {}
    try:
        identifier = UUID(material["invoiceDocumentVersionId"])
    except (KeyError, ValueError, TypeError, AttributeError):
        _error("payment_invoice_source_missing", "Full demo requires the original immutable Invoice version")
    row = session.get(DocumentVersion, identifier)
    doc = session.get(Document, row.document_id) if row is not None else None
    if (row is None or doc is None or doc.namespace_id != ns.id or doc.procurement_id != procurement.id
            or doc.category != "invoice" or row.uploaded_by_user_id != procurement.foundation_user_id
            or not row.referenced or _hex("0x" + row.keccak256_hex) != _hex(procurement.invoice_hash)):
        _error("payment_invoice_source_invalid", "Original Invoice scope, uploader or commitment is invalid")
    return row


def _released(session, ns, procurement, project, gate):
    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
    cents, amount = atomic_to_cents(procurement.invoice_amount_atomic)
    if (int(view[-1]) not in (9, 10, 11, 12) or int(view[11]) != amount or int(view[20]) != 0
            or _hex(view[10]) != _hex(procurement.invoice_hash) or _hex(view[1]) != _hex(project.business_id)
            or _hex(view[2]) != _hex(procurement.vendor_wallet)):
        _error("payment_release_required", "Procurement requires its full canonical Invoice release with no returned funds")
    invoice = _invoice_source(session, ns, procurement)
    rows = session.scalars(select(OperationStep).join(Operation, Operation.id == OperationStep.operation_id).where(
        Operation.namespace_id == ns.id, OperationStep.detail["action"].astext == "release.execute",
        OperationStep.detail["procurementUuid"].astext == str(procurement.id), OperationStep.status == "confirmed",
    )).all()
    if len(rows) != 1:
        _error("payment_release_source_missing", "Original confirmed release operation is not uniquely bound")
    step = rows[0]
    if _hex(step.detail.get("caller")) not in {_hex(project.foundation_wallet), _hex(gate.roles["humanApprover"])}:
        _error("payment_release_source_invalid", "Release caller is not a fixed allowed project actor")
    proof = chain_proof(session, ns, step.operation_id, "release.execute", gate,
                        expected_args=[procurement.business_id], expected_event="FundsReleasedToFoundation",
                        exact_event={"procurementId": procurement.business_id, "projectId": project.business_id,
                                     "foundation": project.foundation_wallet, "invoiceAmount": amount})
    return cents, amount, invoice, step.operation_id, proof


def assert_project_backing(session, ns, project, gate, *, claim_amount=0, exclude_resource_id=None):
    """Only this project's integrated, canonical funded donations back兑回.

    Legacy faucet donations remain valid A2 donations, but create no simulated
    HKD backing. Pending/unknown/attention redemption claims never regain quota.
    """
    _lock(session, ns.id, f"project-backing:{project.id}")
    claims = session.scalars(select(FundedClaim).where(FundedClaim.namespace_id == ns.id,
        FundedClaim.project_id == project.id, FundedClaim.status == "consumed").order_by(FundedClaim.id)).all()
    funded = 0
    for claim in claims:
        funding = session.get(PaymentResource, claim.funding_resource_id)
        if (funding is None or funding.namespace_id != ns.id or funding.project_id != project.id
                or funding.kind != "funding" or funding.actor_user_id != claim.actor_user_id
                or _hex(funding.actor_wallet) != _hex(claim.donor_wallet)):
            _error("payment_project_backing_invalid", "Original project-funded allocation binding changed")
        assert_resource_canonical(session, ns, funding, gate)
        _proof_still_canonical(claim.chain_proof, gate)
        proof = chain_proof(session, ns, claim.operation_id, "donation.deposit", gate,
            expected_caller=claim.donor_wallet, expected_args=[project.business_id, str(int(claim.amount_atomic))],
            expected_event="Donated", exact_event={"projectId": project.business_id,
                                                  "donor": claim.donor_wallet, "amount": int(claim.amount_atomic)})
        if proof != claim.chain_proof:
            _error("payment_project_backing_invalid", "Original canonical funded donation proof changed")
        funded += int(claim.amount_atomic)
    # Every persisted redemption has a once claim, including failed/unknown
    # outcomes. No automatic quota recovery is supported in this checkpoint.
    redemptions = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id == ns.id,
        PaymentResource.project_id == project.id, PaymentResource.kind == "redemption")).all()
    claimed = sum(int(row.amount_atomic) for row in redemptions if row.id != exclude_resource_id)
    if claimed + int(claim_amount) > funded:
        _error("payment_project_backing_insufficient", "This project lacks unclaimed canonical integrated HKD backing")
    return {"fundedAtomic": str(funded), "claimedAtomic": str(claimed), "availableAtomic": str(funded - claimed)}


def reserve_redemption(session, ns, principal, procurement, project, operation, gate):
    _foundation(session, ns, principal, procurement, project, gate)
    _lock(session, ns.id, f"project-backing:{project.id}")
    _lock(session, ns.id, f"procurement:{procurement.id}")
    cents, amount, invoice, source, proof = _released(session, ns, procurement, project, gate)
    if session.scalar(select(PaymentResource.id).where(PaymentResource.namespace_id == ns.id,
                                                      PaymentResource.procurement_id == procurement.id,
                                                      PaymentResource.kind == "redemption")) is not None:
        _error("redemption_already_claimed", "This procurement already has an immutable redemption claim")
    assert_project_backing(session, ns, project, gate, claim_amount=amount)
    _owner_account(session, ns, principal)
    _account(session, ns, "clearing")
    token_balance = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(project.foundation_wallet)))
    if token_balance < amount:
        _error("redemption_token_insufficient", "Foundation lacks the full released simulated token amount")
    return _resource(session, ns, principal, project, operation, "redemption", cents, amount, gate,
                     procurement=procurement, source=source, invoice=invoice.id,
                     material={"releaseProof": proof, "invoiceHash": procurement.invoice_hash})


def reserve_supplier_payment(session, ns, principal, procurement, project, operation, gate):
    _foundation(session, ns, principal, procurement, project, gate)
    _lock(session, ns.id, f"project-backing:{project.id}")
    _lock(session, ns.id, f"procurement:{procurement.id}")
    _released(session, ns, procurement, project, gate)
    redemption = session.scalar(select(PaymentResource).where(PaymentResource.namespace_id == ns.id,
                                                             PaymentResource.procurement_id == procurement.id,
                                                             PaymentResource.kind == "redemption").with_for_update())
    if redemption is None or redemption.status != "reconciled":
        _error("redemption_not_reconciled", "Supplier payment requires this procurement's reconciled redemption")
    assert_resource_canonical(session, ns, redemption, gate)
    assert_project_backing(session, ns, project, gate)
    if session.scalar(select(PaymentResource.id).where(PaymentResource.namespace_id == ns.id,
                                                      PaymentResource.procurement_id == procurement.id,
                                                      PaymentResource.kind == "supplier_payment")) is not None:
        _error("supplier_payment_already_claimed", "This procurement already has a full-Invoice supplier payment claim")
    payee = _account(session, ns, procurement.vendor_wallet)
    if payee.role != "supplier" or _hex(payee.wallet_address) != _hex(procurement.vendor_wallet):
        _error("supplier_account_binding", "Fixed confirmed vendor is not a provisioned simulated Supplier")
    account = _owner_account(session, ns, principal)
    cents, amount = atomic_to_cents(procurement.invoice_amount_atomic)
    if int(account.available_cents) < cents:
        _error("sim_hkd_insufficient", "Foundation lacks the full Invoice simulated HKD amount")
    return _resource(session, ns, principal, project, operation, "supplier_payment", cents, amount, gate,
                     procurement=procurement, source=redemption.operation_id, invoice=redemption.invoice_version_id,
                     material={"redemptionResourceId": str(redemption.id), "invoiceHash": procurement.invoice_hash})


def assert_resource_canonical(session, ns, resource, gate):
    _namespace(ns, gate)
    if resource.namespace_id != ns.id or resource.status != "reconciled":
        _error("payment_resource_not_reconciled", "Payment resource is not reconciled in this instance")
    if resource.kind in ("funding", "redemption"):
        _proof_still_canonical(resource.chain_proof, gate)
        if resource.kind == "funding":
            proof = chain_proof(session, ns, resource.operation_id, "payment.mint", gate,
                                expected_caller=gate.roles["relayer"],
                                expected_args=[resource.actor_wallet, str(int(resource.amount_atomic))],
                                expected_event="Transfer", exact_event={"from": ZERO_ADDRESS,
                                    "to": resource.actor_wallet, "value": int(resource.amount_atomic)})
        else:
            proof = chain_proof(session, ns, resource.operation_id, "payment.redeem", gate,
                                expected_caller=resource.actor_wallet,
                                expected_args=[gate.roles["mockRedemption"], str(int(resource.amount_atomic))],
                                expected_event="Transfer", exact_event={"from": resource.actor_wallet,
                                    "to": gate.roles["mockRedemption"], "value": int(resource.amount_atomic)})
        if proof != resource.chain_proof:
            _error("payment_source_changed", "Original canonical payment proof binding changed")
    if resource.kind == "redemption":
        _proof_still_canonical(resource.source_material.get("releaseProof"), gate)
        procurement = session.get(Procurement, resource.procurement_id)
        project = session.get(Project, resource.project_id)
        cents, amount, invoice, release_id, proof = _released(session, ns, procurement, project, gate)
        if (release_id != resource.source_operation_id or invoice.id != resource.invoice_version_id
                or cents != int(resource.hkd_cents) or amount != int(resource.amount_atomic)
                or proof != resource.source_material.get("releaseProof")):
            _error("payment_source_changed", "Original released Invoice dependency changed")
    elif resource.kind == "supplier_payment":
        try:
            source_id = UUID(resource.source_material["redemptionResourceId"])
        except (KeyError, ValueError, TypeError):
            _error("payment_source_changed", "Supplier payment has no original redemption source")
        redemption = session.get(PaymentResource, source_id)
        if redemption is None or redemption.operation_id != resource.source_operation_id:
            _error("payment_source_changed", "Original supplier payment redemption source changed")
        assert_resource_canonical(session, ns, redemption, gate)


def reconcile_donor_outflows(session, ns, donor_wallet, gate) -> bool:
    """Fail closed on mixed ERC20 outflows, not physical coin tracing.

    The caller owns this transaction. A worker commits attention freezes; HTTP
    refusal still fails closed if its enclosing transaction rolls back.
    """
    resources = session.scalars(select(PaymentResource).where(
        PaymentResource.namespace_id == ns.id, PaymentResource.kind == "funding",
        func.lower(PaymentResource.actor_wallet) == donor_wallet.lower(),
        PaymentResource.chain_proof.is_not(None),
    ).order_by(PaymentResource.id).with_for_update()).all()
    if not resources:
        return True
    if any(resource.status == "requires_attention" for resource in resources):
        return False
    start = min(int(resource.chain_proof["blockNumber"]) for resource in resources)
    try:
        tip = int(gate.w3.eth.block_number)
    except Exception as exc:
        from .chain import ChainUnavailable
        raise ChainUnavailable("Donor outflow reconciliation tip is unavailable") from exc
    token = gate.contract_address("MockHKD")
    escrow = gate.contract_address("ProcurementEscrowV2")
    events = gate.events_in_range(start, tip)
    claims = session.scalars(select(FundedClaim).where(
        FundedClaim.namespace_id == ns.id, func.lower(FundedClaim.donor_wallet) == donor_wallet.lower(),
    )).all()
    safe = True
    for resource in resources:
        try:
            _proof_still_canonical(resource.chain_proof, gate)
        except APIError:
            safe = False
    for event in events:
        values = event["args"]
        if (event["event"] != "Transfer" or _hex(event["address"]) != _hex(token)
                or _hex(values.get("from")) != _hex(donor_wallet) or int(values.get("value", 0)) == 0):
            continue
        if not gate.canonical(int(event["blockNumber"]), event["blockHash"]):
            safe = False
            continue
        # Outflows preceding the first mint in its block cannot consume this credit.
        first = min(resources, key=lambda row: (int(row.chain_proof["blockNumber"]), int(row.chain_proof["logIndex"])))
        if (int(event["blockNumber"]), int(event["logIndex"])) < (int(first.chain_proof["blockNumber"]), int(first.chain_proof["logIndex"])):
            continue
        matched = None
        for claim in claims:
            tx = session.scalar(select(ChainTransaction).join(OperationStep, OperationStep.id == ChainTransaction.step_id).where(
                ChainTransaction.namespace_id == ns.id, ChainTransaction.operation_id == claim.operation_id,
                ChainTransaction.tx_hash == event["transactionHash"], ChainTransaction.block_hash == event["blockHash"],
                ChainTransaction.status == "confirmed", ChainTransaction.canonical.is_(True),
                OperationStep.detail["action"].astext == "donation.deposit", OperationStep.status == "confirmed",
            ))
            if (tx is not None and _hex(values.get("to")) == _hex(escrow)
                    and int(values["value"]) == int(claim.amount_atomic)
                    and _hex(tx.caller_address) == _hex(donor_wallet)):
                project = session.get(Project, claim.project_id)
                try:
                    proof = chain_proof(session, ns, claim.operation_id, "donation.deposit", gate,
                        expected_caller=claim.donor_wallet, expected_args=[project.business_id, str(int(claim.amount_atomic))],
                        expected_event="Donated", exact_event={"projectId": project.business_id,
                                                              "donor": claim.donor_wallet, "amount": int(claim.amount_atomic)})
                except APIError:
                    continue
                if proof["txHash"].lower() == str(event["transactionHash"]).lower():
                    matched = claim
                    break
        session.execute(insert(CanonicalOutflow).values(namespace_id=ns.id, donor_wallet=donor_wallet,
            tx_hash=event["transactionHash"], log_index=int(event["logIndex"]), block_hash=event["blockHash"],
            block_number=int(event["blockNumber"]), amount_atomic=int(values["value"]),
            claim_id=matched.id if matched else None, matched=matched is not None,
        ).on_conflict_do_nothing(constraint="uq_payment_outflow_identity"))
        if matched is None:
            safe = False
    remaining = sum(int(row.amount_atomic) - int(row.allocated_atomic) for row in resources)
    balance = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(donor_wallet), block_identifier=tip))
    if balance < remaining:
        safe = False
    if not safe:
        for resource in resources:
            resource.status = "requires_attention"
            resource.error_code = "funding_external_outflow"
        audit(session, principal_id=None, action="payment.donor_outflow_freeze", outcome="requires_attention",
              metadata={"namespaceId": str(ns.id), "donorWallet": donor_wallet, "reasonCode": "funding_external_outflow"})
    return safe


def freeze_namespace(session, ns, reason="chain_reorganization", *, invalidated=False):
    """Preserve every journal/evidence; never rewind caches after reorg/reset."""
    namespace_id = ns.id if hasattr(ns, "id") else ns
    resources = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id == namespace_id).with_for_update()).all()
    for row in resources:
        row.status = "invalidated_instance" if invalidated else "requires_attention"
        row.error_code = reason
    claims = session.scalars(select(FundedClaim).where(FundedClaim.namespace_id == namespace_id).with_for_update()).all()
    for row in claims:
        row.status = "invalidated_instance" if invalidated else "requires_attention"
    operation_ids = {row.operation_id for row in resources} | {row.operation_id for row in claims}
    if operation_ids:
        operations = session.scalars(select(Operation).where(Operation.namespace_id == namespace_id,
            Operation.id.in_(operation_ids), Operation.status.in_(("awaiting_authorization", "queued", "submitted", "requires_attention"))).with_for_update()).all()
        for operation in operations:
            operation.status = "invalidated_instance" if invalidated else "requires_attention"
            operation.error_code, operation.error_status = reason, 409
            operation.error_detail = "Original simulation dependencies changed; economic history and unknown envelopes are preserved"
        # An observed/broadcast chain step or envelope is never turned into
        # not_broadcast by a source freeze. Its durable nonce remains occupied.
        for step in session.scalars(select(OperationStep).where(OperationStep.operation_id.in_(operation_ids),
            OperationStep.status == "queued").with_for_update()):
            step.status = "invalidated_instance" if invalidated else "requires_attention"
    if resources or claims:
        audit(session, principal_id=None, action="payment.namespace_freeze", outcome="requires_attention",
              metadata={"namespaceId": str(namespace_id), "reasonCode": reason, "journalHistoryPreserved": True})


def validate_pending_chain(session, ns, step, operation, gate):
    """Money-source gate immediately before preparation and actual broadcast.

    Unlike a PG finalizer this prevents an orphan funding/release from borrowing
    unrelated faucet or another procurement's fungible wallet balance.
    """
    detail = step.detail
    action = detail.get("action")
    if action not in ("payment.mint", "payment.redeem", "donation.approve", "donation.deposit"):
        return
    if action.startswith("donation.") and "fundedClaimUuid" not in detail:
        return  # Default historical A2 donation remains compatible.
    _namespace(ns, gate)
    if (operation.namespace_id != ns.id or step.operation_id != operation.id
            or step.executor != "chain" or step.kind != action
            or operation.status not in ("queued", "submitted")):
        _error("payment_chain_preflight_invalid", "Economic chain step is not a current pending operation")
    if action.startswith("donation."):
        try:
            identifier = UUID(detail["fundedClaimUuid"])
        except (ValueError, TypeError, KeyError):
            _error("payment_chain_preflight_invalid", "Funded donation has no original allocation claim")
        claim = session.get(FundedClaim, identifier, with_for_update=True)
        if (claim is None or claim.namespace_id != ns.id or claim.operation_id != operation.id
                or claim.actor_user_id != operation.principal_id or claim.status != "reserved"
                or operation.operation_kind != "payment.funded_donation"
                or operation.result_resource_type != "funded_claim" or operation.result_resource_id != claim.id):
            _error("payment_chain_preflight_invalid", "Original funded donation claim is frozen or not bound")
        resource = session.get(PaymentResource, claim.funding_resource_id, with_for_update=True)
        if (resource is None or resource.kind != "funding" or resource.namespace_id != ns.id
                or resource.project_id != claim.project_id or resource.actor_user_id != claim.actor_user_id
                or _hex(resource.actor_wallet) != _hex(claim.donor_wallet)
                or int(resource.allocated_atomic) < int(claim.amount_atomic)):
            _error("payment_chain_preflight_invalid", "Original funded allocation source binding changed")
        project = session.get(Project, claim.project_id)
        _actor(session, ns, SimplePaymentPrincipal(claim.actor_user_id, claim.donor_wallet, "donor"), "donor", gate)
        _project(ns, project, gate)
        assert_resource_canonical(session, ns, resource, gate)
        if not reconcile_donor_outflows(session, ns, claim.donor_wallet, gate):
            _error("funding_requires_attention", "Original funded-credit token movements require reconciliation")
        args = ([gate.contract_address("ProcurementEscrowV2"), str(int(claim.amount_atomic))]
                if action == "donation.approve" else [project.business_id, str(int(claim.amount_atomic))])
        event = "Approval" if action == "donation.approve" else "Donated"
        if (detail.get("args") != args or _hex(detail.get("caller")) != _hex(claim.donor_wallet)
                or detail.get("projectUuid") != str(project.id) or detail.get("expectedEvent") != event):
            _error("payment_chain_preflight_invalid", "Funded donation caller, amount or target differs from its immutable claim")
        return
    try:
        identifier = UUID(detail["paymentResourceUuid"])
    except (ValueError, TypeError, KeyError):
        _error("payment_chain_preflight_invalid", "Economic chain step has no original payment resource")
    resource = session.get(PaymentResource, identifier)
    if resource is not None and action == "payment.redeem":
        _lock(session, ns.id, f"project-backing:{resource.project_id}")
    # Session factories disable autoflush. Preserve an in-transaction freeze
    # before refreshing the locked row; never overwrite attention with old DB
    # state while deciding whether a broadcast is authorized.
    session.flush()
    resource = session.get(PaymentResource, identifier, with_for_update=True, populate_existing=True)
    if (resource is None or resource.namespace_id != ns.id or resource.operation_id != operation.id
            or resource.actor_user_id != operation.principal_id or operation.result_resource_type != "payment_resource"
            or operation.result_resource_id != resource.id or detail.get("projectUuid") != str(resource.project_id)
            or resource.journal_ids or resource.status not in ("held", "queued", "chain_submitted")):
        _error("payment_chain_preflight_invalid", "Original economic resource is frozen, completed or not bound")
    project = session.get(Project, resource.project_id)
    if action == "payment.mint":
        if (resource.kind != "funding" or resource.procurement_id is not None or resource.source_operation_id is not None
                or operation.operation_kind != "payment.funding"):
            _error("payment_chain_preflight_invalid", "Mint resource is not a project-bound funding hold")
        principal = SimplePaymentPrincipal(resource.actor_user_id, resource.actor_wallet, "donor")
        _actor(session, ns, principal, "donor", gate)
        _project(ns, project, gate)
        account = _owner_account(session, ns, principal, lock=True)
        held_resources = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id == ns.id,
            PaymentResource.kind == "funding", PaymentResource.actor_user_id == resource.actor_user_id,
            PaymentResource.journal_ids == []).with_for_update()).all()
        if int(account.held_cents) < sum(int(row.hkd_cents) for row in held_resources):
            _error("payment_chain_preflight_invalid", "Mint has no complete original simulated HKD hold")
        if session.scalar(select(HKDJournal.id).where(HKDJournal.operation_id == operation.id)) is not None:
            _error("payment_chain_preflight_invalid", "Funding economic effect is already journaled")
        if not reconcile_donor_outflows(session, ns, resource.actor_wallet, gate):
            _error("funding_requires_attention", "Donor token movements require reconciliation")
        args, caller = [resource.actor_wallet, str(int(resource.amount_atomic))], gate.roles["relayer"]
    else:
        if resource.kind != "redemption" or resource.procurement_id is None or operation.operation_kind != "payment.redemption":
            _error("payment_chain_preflight_invalid", "Treasury transfer has no original redemption claim")
        procurement = session.get(Procurement, resource.procurement_id)
        _foundation(session, ns, SimplePaymentPrincipal(resource.actor_user_id, resource.actor_wallet, "foundation"), procurement, project, gate)
        _lock(session, ns.id, f"project-backing:{project.id}")
        cents, amount, invoice, source, proof = _released(session, ns, procurement, project, gate)
        assert_project_backing(session, ns, project, gate, claim_amount=amount, exclude_resource_id=resource.id)
        if (resource.source_operation_id != source or resource.invoice_version_id != invoice.id
                or int(resource.amount_atomic) != amount or int(resource.hkd_cents) != cents
                or resource.source_material.get("releaseProof") != proof
                or detail.get("procurementUuid") != str(procurement.id)
                or _hex(resource.counterparty_wallet) != _hex(gate.roles["mockRedemption"])):
            _error("payment_chain_preflight_invalid", "Original full-Invoice release dependency changed")
        args, caller = [gate.roles["mockRedemption"], str(int(resource.amount_atomic))], resource.actor_wallet
    if (detail.get("args") != args or _hex(detail.get("caller")) != _hex(caller)
            or detail.get("expectedEvent") != "Transfer" or _hex(resource.token_address) != _hex(gate.contract_address("MockHKD"))):
        _error("payment_chain_preflight_invalid", "Economic caller, target or amount differs from immutable payment scope")


class SimplePaymentPrincipal:
    """Internal persisted actor adapter; never a session or role escalation."""
    def __init__(self, user_id, wallet_address, role):
        self.user_id, self.wallet_address, self.role = user_id, wallet_address, role


def canonical_evidence_bytes(material: dict) -> bytes:
    schema = material.get("schemaVersion")
    expected = EVIDENCE_BASE_FIELDS if schema == "pog-conversion-evidence-v1" else PAYMENT_EVIDENCE_FIELDS if schema == "pog-supplier-payment-evidence-v1" else None
    if expected is None or set(material) != expected:
        raise ValueError("Payment evidence must use its exact fixed whitelist")
    if material.get("mode") != "simulation" or material.get("kind") != ("conversion" if schema == "pog-conversion-evidence-v1" else "supplier_payment"):
        raise ValueError("Payment evidence simulation/schema binding is invalid")
    def validate(value):
        if type(value) is str:
            if not value.isascii() or any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise ValueError("Evidence strings must be printable ASCII")
        elif type(value) is dict:
            for key, item in value.items():
                if type(key) is not str or not key.isascii():
                    raise ValueError("Evidence keys must be ASCII")
                validate(item)
        else:
            raise ValueError("Evidence contains only fixed maps and ASCII strings")
    validate(material)
    if set(material["binding"]) != BINDING_FIELDS or material["binding"]["chainId"] != "31337":
        raise ValueError("Evidence deployment binding must use the fixed local schema")
    for name in ("amountAtomic", "hkdCents"):
        validate_uint256_string(material[name])
    if int(material["hkdCents"]) <= 0 or int(material["amountAtomic"]) != int(material["hkdCents"]) * ATOMIC_PER_CENT:
        raise ValueError("Evidence full Invoice amount must match its exact positive HKD cents")
    for name in ("releaseProof", "redemptionProof"):
        if set(material[name]) != PROOF_FIELDS or material[name]["receiptStatus"] != "1":
            raise ValueError("Evidence requires exact canonical proof fields")
        for field in ("blockNumber", "logIndex"):
            validate_uint256_string(material[name][field])
    return json.dumps(material, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def create_evidence(session, ns, resource, gate):
    if resource.kind not in ("redemption", "supplier_payment") or resource.status != "reconciled":
        _error("payment_evidence_dependency", "Evidence requires an already reconciled conversion or payment")
    existing = session.scalar(select(PaymentEvidence).where(PaymentEvidence.resource_id == resource.id))
    if existing is not None:
        return existing
    procurement = session.get(Procurement, resource.procurement_id)
    project = session.get(Project, resource.project_id)
    redemption = resource if resource.kind == "redemption" else session.get(PaymentResource, UUID(resource.source_material["redemptionResourceId"]))
    if redemption is None or redemption.namespace_id != ns.id or redemption.status != "reconciled":
        _error("payment_evidence_dependency", "Original reconciled redemption is unavailable")
    invoice = _invoice_source(session, ns, procurement)
    if invoice.id != resource.invoice_version_id or len(redemption.journal_ids) != 1 or not redemption.chain_proof:
        _error("payment_evidence_dependency", "Original Invoice, transfer or balanced journal is unavailable")
    material = {"schemaVersion": "pog-conversion-evidence-v1" if resource.kind == "redemption" else "pog-supplier-payment-evidence-v1",
                "kind": "conversion" if resource.kind == "redemption" else "supplier_payment", "mode": "simulation",
                "binding": binding(ns, gate), "projectId": str(project.id), "projectChainId": project.business_id,
                "procurementId": str(procurement.id), "procurementChainId": procurement.business_id,
                "foundation": project.foundation_wallet, "treasury": gate.roles["mockRedemption"],
                "vendor": procurement.vendor_wallet, "token": gate.contract_address("MockHKD"),
                "amountAtomic": str(int(resource.amount_atomic)), "hkdCents": str(int(resource.hkd_cents)),
                "invoiceDocumentVersionId": str(invoice.id), "invoiceHash": procurement.invoice_hash,
                "releaseOperationId": str(redemption.source_operation_id), "releaseProof": redemption.source_material["releaseProof"],
                "redemptionResourceId": str(redemption.id), "redemptionOperationId": str(redemption.operation_id),
                "redemptionProof": redemption.chain_proof, "redemptionJournalId": redemption.journal_ids[0]}
    if resource.kind == "supplier_payment":
        if len(resource.journal_ids) != 1:
            _error("payment_evidence_dependency", "Original supplier double-entry journal is unavailable")
        material.update(paymentResourceId=str(resource.id), paymentOperationId=str(resource.operation_id), paymentJournalId=resource.journal_ids[0])
    raw = canonical_evidence_bytes(material)
    row = PaymentEvidence(namespace_id=ns.id, resource_id=resource.id, procurement_id=resource.procurement_id,
                          kind=material["kind"], schema_version=material["schemaVersion"], canonical_bytes=raw,
                          sha256_hex=hashlib.sha256(raw).hexdigest(), keccak256_hex=keccak(raw).hex())
    session.add(row)
    session.flush()
    return row


def resource_dto(session, resource):
    evidence = session.scalar(select(PaymentEvidence).where(PaymentEvidence.resource_id == resource.id))
    return {"id": str(resource.id), "operationId": str(resource.operation_id), "kind": resource.kind,
            "status": resource.status, "reconciled": resource.status == "reconciled", "projectId": str(resource.project_id),
            "procurementId": str(resource.procurement_id) if resource.procurement_id else None,
            "hkdCents": str(int(resource.hkd_cents)), "amountAtomic": str(int(resource.amount_atomic)),
            "sourceOperationId": str(resource.source_operation_id) if resource.source_operation_id else None,
            "evidenceId": str(evidence.id) if evidence else None, "journalIds": resource.journal_ids,
            "chainProof": resource.chain_proof if resource.kind != "supplier_payment" else None,
            "actorWallet": resource.actor_wallet, "counterpartyWallet": resource.counterparty_wallet,
            "tokenAddress": resource.token_address, "allocatedAtomic": str(int(resource.allocated_atomic)),
            "errorCode": resource.error_code}


def payment_status(session, ns, procurement):
    rows = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id == ns.id,
                                                        PaymentResource.procurement_id == procurement.id)).all()
    by_kind = {row.kind: row for row in rows}
    return {"redemption": resource_dto(session, by_kind["redemption"]) if "redemption" in by_kind else None,
            "supplierPayment": resource_dto(session, by_kind["supplier_payment"]) if "supplier_payment" in by_kind else None}
