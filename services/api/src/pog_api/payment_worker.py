"""Atomic PostgreSQL executor for explicit local-simulation payment operations."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased
from web3 import Web3

from .db import build_engine, build_session_factory
from .errors import APIError
from .idempotency import audit, ensure_verified_namespace
from .models import DeploymentInstance, Operation, OperationStep, Procurement, Project
from .payment_domain import (_actor, _foundation, _journal, _lock, _namespace, _project, _released,
                             _proof_still_canonical, assert_resource_canonical, chain_proof,
                             create_evidence, freeze_namespace, reconcile_donor_outflows, assert_project_backing, ZERO_ADDRESS)
from .payment_models import FundedClaim, PaymentResource


class PaymentWorker:
    def __init__(self, database_url: str, gateway):
        self.engine = build_engine(database_url)
        self.factory = build_session_factory(self.engine)
        self.gateway = gateway

    def close(self):
        self.engine.dispose()

    @staticmethod
    def _principal(resource, role):
        return SimpleNamespace(user_id=resource.actor_user_id, wallet_address=resource.actor_wallet, role=role)

    def _funding(self, session, ns, operation, resource):
        gate = self.gateway
        principal = self._principal(resource, "donor")
        _actor(session, ns, principal, "donor", gate)
        project = session.get(Project, resource.project_id)
        _project(ns, project, gate, active=False)
        _lock(session, ns.id, f"donor:{resource.actor_wallet.lower()}")
        amount = int(resource.amount_atomic)
        proof = chain_proof(session, ns, operation.id, "payment.mint", gate,
                            expected_caller=gate.roles["relayer"], expected_args=[resource.actor_wallet, str(amount)],
                            expected_event="Transfer", exact_event={"from": ZERO_ADDRESS, "to": resource.actor_wallet, "value": amount})
        # The exact mint is necessary but not alone sufficient to reconcile its credit.
        block = int(proof["blockNumber"])
        after = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(resource.actor_wallet), block_identifier=block))
        before = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(resource.actor_wallet), block_identifier=block - 1))
        if after < before + amount:
            raise APIError(409, "funding_balance_mismatch", "Canonical mint does not reconcile the Donor balance change")
        resource.chain_proof = proof
        session.flush()
        if not reconcile_donor_outflows(session, ns, resource.actor_wallet, gate):
            raise APIError(409, "funding_external_outflow", "Donor token movements require reconciliation")
        journal = _journal(session, ns, debit_key=resource.actor_wallet, credit_key="clearing",
                           cents=int(resource.hkd_cents), effect="funding", operation=operation, from_hold=True)
        resource.journal_ids = [str(journal.id)]
        resource.status = "reconciled"
        _proof_still_canonical(proof, gate)

    def _donation(self, session, ns, operation, claim):
        gate = self.gateway
        resource = session.get(PaymentResource, claim.funding_resource_id, with_for_update=True)
        if resource is None or claim.status != "reserved" or claim.namespace_id != ns.id:
            raise APIError(409, "funded_claim_invalid", "Original funded donation claim is not reserved")
        _actor(session, ns, self._principal(resource, "donor"), "donor", gate)
        project = session.get(Project, claim.project_id)
        _project(ns, project, gate, active=False)
        _lock(session, ns.id, f"donor:{claim.donor_wallet.lower()}")
        assert_resource_canonical(session, ns, resource, gate)
        amount = int(claim.amount_atomic)
        proof = chain_proof(session, ns, operation.id, "donation.deposit", gate,
                            expected_caller=claim.donor_wallet, expected_args=[project.business_id, str(amount)],
                            expected_event="Donated", exact_event={"projectId": project.business_id,
                                                                   "donor": claim.donor_wallet, "amount": amount})
        credit = int(gate.call("ProcurementEscrowV2", "donorCredit", project.business_id,
                               Web3.to_checksum_address(claim.donor_wallet), block_identifier=int(proof["blockNumber"])))
        ledger = gate.call("ProcurementEscrowV2", "getLedger", project.business_id, block_identifier=int(proof["blockNumber"]))
        if credit < amount or int(ledger[1]) < amount:
            raise APIError(409, "funded_donation_mismatch", "Canonical donor credit and project ledger do not reconcile")
        if not reconcile_donor_outflows(session, ns, claim.donor_wallet, gate):
            raise APIError(409, "funding_external_outflow", "Donor token movements require reconciliation")
        claim.chain_proof = proof
        claim.status = "consumed"
        _proof_still_canonical(proof, gate)

    def _redemption(self, session, ns, operation, resource):
        gate = self.gateway
        procurement = session.get(Procurement, resource.procurement_id)
        project = session.get(Project, resource.project_id)
        principal = self._principal(resource, "foundation")
        _foundation(session, ns, principal, procurement, project, gate)
        _lock(session, ns.id, f"project-backing:{project.id}")
        _lock(session, ns.id, f"procurement:{procurement.id}")
        cents, amount, invoice, release_id, release_proof = _released(session, ns, procurement, project, gate)
        assert_project_backing(session, ns, project, gate, claim_amount=amount, exclude_resource_id=resource.id)
        if (release_id != resource.source_operation_id or invoice.id != resource.invoice_version_id
                or cents != int(resource.hkd_cents) or amount != int(resource.amount_atomic)
                or release_proof != resource.source_material.get("releaseProof")):
            raise APIError(409, "redemption_source_changed", "Original redemption release and Invoice dependency changed")
        proof = chain_proof(session, ns, operation.id, "payment.redeem", gate,
                            expected_caller=resource.actor_wallet, expected_args=[gate.roles["mockRedemption"], str(amount)],
                            expected_event="Transfer", exact_event={"from": resource.actor_wallet,
                                                                    "to": gate.roles["mockRedemption"], "value": amount})
        block = int(proof["blockNumber"])
        foundation_after = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(resource.actor_wallet), block_identifier=block))
        foundation_before = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(resource.actor_wallet), block_identifier=block - 1))
        treasury_after = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(gate.roles["mockRedemption"]), block_identifier=block))
        treasury_before = int(gate.call("MockHKD", "balanceOf", Web3.to_checksum_address(gate.roles["mockRedemption"]), block_identifier=block - 1))
        if foundation_before - foundation_after != amount or treasury_after - treasury_before != amount:
            raise APIError(409, "redemption_balance_mismatch", "Canonical treasury transfer does not reconcile both token balances")
        journal = _journal(session, ns, debit_key="clearing", credit_key=resource.actor_wallet,
                           cents=cents, effect="redemption", operation=operation)
        resource.chain_proof = proof
        resource.journal_ids = [str(journal.id)]
        resource.status = "reconciled"
        session.flush()
        create_evidence(session, ns, resource, gate)
        _proof_still_canonical(release_proof, gate)
        _proof_still_canonical(proof, gate)

    def _supplier(self, session, ns, operation, resource):
        gate = self.gateway
        procurement = session.get(Procurement, resource.procurement_id)
        project = session.get(Project, resource.project_id)
        _foundation(session, ns, self._principal(resource, "foundation"), procurement, project, gate)
        _lock(session, ns.id, f"project-backing:{project.id}")
        _lock(session, ns.id, f"procurement:{procurement.id}")
        cents, amount, invoice, release_id, release_proof = _released(session, ns, procurement, project, gate)
        redemption = session.get(PaymentResource, UUID(resource.source_material["redemptionResourceId"]), with_for_update=True)
        if (redemption is None or redemption.kind != "redemption" or redemption.procurement_id != procurement.id
                or redemption.project_id != project.id or redemption.namespace_id != ns.id
                or redemption.operation_id != resource.source_operation_id or redemption.invoice_version_id != invoice.id
                or int(resource.hkd_cents) != cents or int(resource.amount_atomic) != amount
                or resource.counterparty_wallet.lower() != procurement.vendor_wallet.lower()):
            raise APIError(409, "supplier_payment_source_changed", "Original fixed-vendor redemption source changed")
        assert_resource_canonical(session, ns, redemption, gate)
        assert_project_backing(session, ns, project, gate)
        journal = _journal(session, ns, debit_key=resource.actor_wallet, credit_key=procurement.vendor_wallet,
                           cents=cents, effect="supplier_payment", operation=operation)
        resource.journal_ids = [str(journal.id)]
        resource.chain_proof = None  # PG accounting never manufactures a chain receipt.
        resource.status = "reconciled"
        session.flush()
        create_evidence(session, ns, resource, gate)
        _proof_still_canonical(release_proof, gate)
        _proof_still_canonical(redemption.chain_proof, gate)

    def _attention(self, operation_id, step_id, resource_id, claim_id, exc):
        # The economic transaction rolled back first: no partially-applied journal.
        with self.factory() as session, session.begin():
            original = session.get(Operation, operation_id)
            if original is None:
                return
            # Reset/reorg and all economic executors use the same outer lane:
            # namespace first, then operation/step/resources. This attention
            # transaction must not invert that ordering after rollback.
            session.get(DeploymentInstance, original.namespace_id, with_for_update=True)
            operation = session.get(Operation, operation_id, with_for_update=True, populate_existing=True)
            step = session.get(OperationStep, step_id, with_for_update=True)
            if operation is None or step is None or step.status == "confirmed" or operation.status == "invalidated_instance":
                return
            operation.status = step.status = "requires_attention"
            operation.error_code, operation.error_status = exc.code, exc.status_code
            operation.error_detail = exc.message
            resource = session.get(PaymentResource, resource_id, with_for_update=True) if resource_id else None
            if resource is not None:
                resource.status, resource.error_code = "requires_attention", exc.code
            claim = session.get(FundedClaim, claim_id, with_for_update=True) if claim_id else None
            if claim is not None:
                claim.status = "requires_attention"
                resource = session.get(PaymentResource, claim.funding_resource_id, with_for_update=True)
                if resource is not None:
                    resource.status, resource.error_code = "requires_attention", exc.code
            if exc.code == "funding_external_outflow" and resource is not None:
                # The failed economic transaction also rolled back its broad
                # Donor freeze. Persist that freeze here without committing any
                # journal/cache changes from the failed transaction.
                for funding in session.scalars(select(PaymentResource).where(
                    PaymentResource.namespace_id == operation.namespace_id,
                    PaymentResource.kind == "funding",
                    func.lower(PaymentResource.actor_wallet) == resource.actor_wallet.lower(),
                ).with_for_update()):
                    funding.status, funding.error_code = "requires_attention", exc.code
            if exc.code in ("payment_chain_reorganization", "payment_source_changed", "payment_release_required"):
                freeze_namespace(session, operation.namespace_id, exc.code)
            audit(session, principal_id=operation.principal_id, operation_id=operation.id,
                  action="payment.worker_attention", outcome="requires_attention",
                  resource_type=operation.result_resource_type, resource_id=operation.result_resource_id,
                  metadata={"reasonCode": exc.code, "economicTransactionRolledBack": True})

    def once(self) -> bool:
        operation_id = step_id = resource_id = claim_id = None
        try:
            with self.factory() as session, session.begin():
                ns = ensure_verified_namespace(session, self.gateway)
                ns = session.scalar(select(DeploymentInstance).where(DeploymentInstance.id == ns.id)
                    .with_for_update().execution_options(populate_existing=True))
                _namespace(ns, self.gateway)
                previous = aliased(OperationStep)
                prerequisite = exists().where(previous.operation_id == OperationStep.operation_id,
                                               previous.step_index < OperationStep.step_index,
                                               previous.status != "confirmed")
                step = session.scalar(select(OperationStep).join(Operation, Operation.id == OperationStep.operation_id).where(
                    Operation.namespace_id == ns.id, Operation.status.in_(("queued", "submitted")),
                    OperationStep.executor == "payment", OperationStep.status == "queued", ~prerequisite,
                ).order_by(OperationStep.created_at, OperationStep.step_index).with_for_update(skip_locked=True, of=OperationStep).limit(1))
                if step is None:
                    # An idle worker can still detect canonical dependency drift.
                    resources = session.scalars(select(PaymentResource).where(
                        PaymentResource.namespace_id == ns.id, PaymentResource.status == "reconciled",
                    ).order_by(PaymentResource.id)).all()
                    for resource in resources:
                        if resource.kind in ("funding", "redemption"):
                            proofs = [resource.chain_proof]
                            if resource.kind == "redemption":
                                proofs.append(resource.source_material.get("releaseProof"))
                            for proof in proofs:
                                if proof and not self.gateway.canonical(int(proof["blockNumber"]), proof["blockHash"]):
                                    freeze_namespace(session, ns, "payment_chain_reorganization")
                                    return True
                    return False
                operation = session.get(Operation, step.operation_id, with_for_update=True)
                operation_id, step_id = operation.id, step.id
                if step.kind != step.detail.get("action"):
                    raise APIError(409, "payment_step_invalid", "Persisted payment step kind differs from its action")
                if step.kind == "payment.finalize_donation":
                    claim_id = UUID(step.detail["fundedClaimUuid"])
                    claim = session.get(FundedClaim, claim_id, with_for_update=True)
                    if claim is None or claim.operation_id != operation.id:
                        raise APIError(409, "payment_step_invalid", "Payment step is not bound to its original claim")
                    self._donation(session, ns, operation, claim)
                else:
                    resource_id = UUID(step.detail["paymentResourceUuid"])
                    resource = session.get(PaymentResource, resource_id)
                    if resource is not None and resource.kind in ("redemption", "supplier_payment"):
                        _lock(session, ns.id, f"project-backing:{resource.project_id}")
                    resource = session.get(PaymentResource, resource_id, with_for_update=True, populate_existing=True)
                    if resource is None or resource.operation_id != operation.id or resource.namespace_id != ns.id:
                        raise APIError(409, "payment_step_invalid", "Payment step is not bound to its original resource")
                    expected = {"payment.finalize_funding": "funding", "payment.finalize_redemption": "redemption",
                                "payment.supplier_pay": "supplier_payment"}.get(step.kind)
                    if resource.kind != expected or resource.status not in ("queued", "held", "chain_submitted", "chain_confirmed"):
                        raise APIError(409, "payment_step_invalid", "Payment resource is not a pending original action")
                    dispatch = {"funding": self._funding, "redemption": self._redemption, "supplier_payment": self._supplier}
                    dispatch[resource.kind](session, ns, operation, resource)
                step.status = "confirmed"
                session.flush()
                remaining = session.scalar(select(func.count()).select_from(OperationStep).where(
                    OperationStep.operation_id == operation.id, OperationStep.status != "confirmed"))
                operation.status = "confirmed" if remaining == 0 else "queued"
                operation.error_code = operation.error_status = operation.error_detail = None
                audit(session, principal_id=operation.principal_id, operation_id=operation.id,
                      action=step.kind, outcome="confirmed", resource_type=operation.result_resource_type,
                      resource_id=operation.result_resource_id, metadata={"mode": "simulation", "executor": "payment", "chainVerified": False})
            return True
        except APIError as exc:
            if operation_id is None:
                raise
            self._attention(operation_id, step_id, resource_id, claim_id, exc)
            return True
