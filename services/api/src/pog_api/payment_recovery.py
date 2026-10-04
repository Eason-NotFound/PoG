"""Explicit maintenance of a positively proven, never-prepared funding attempt.

The caller owns the transaction and explicit operator confirmation. This is not
a payment endpoint, an unknown-transaction resend, or an account replenishment.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select

from .amounts import UINT256_MAX
from .chain import ChainUnavailable
from .errors import APIError
from .hashing import payload_sha256
from .idempotency import audit
from .models import AuditLog, ChainTransaction, DeploymentInstance, Operation, OperationStep, Project
from .payment_domain import (_actor, _namespace, _owner_account, _project,
                             SimplePaymentPrincipal, validate_pending_chain)
from .payment_models import FundedClaim, HKDJournal, PaymentEvidence, PaymentResource


FAILURE_CODE = "chain_preparation_rejected"
RECOVERY_ACTION = "payment.funding.retry_preflight"


def _reject(message: str, code: str = "payment_preflight_recovery_not_allowed"):
    raise APIError(409, code, message)


def _failure_matches(row, operation, resource):
    return (row.action == "chain_worker.not_broadcast" and row.outcome == "failed"
            and row.principal_id == operation.principal_id
            and row.resource_type == "payment_resource" and row.resource_id == resource.id
            and row.metadata_json == {"errorCode": FAILURE_CODE,
                                      "transactionId": None, "envelopeHash": None})


def _scope_digest(operation, resource, steps):
    return payload_sha256({"namespaceId": str(operation.namespace_id),
        "operationId": str(operation.id), "principalId": str(operation.principal_id),
        "payloadHash": operation.payload_hash, "idempotencyKey": operation.idempotency_key,
        "paymentResourceId": str(resource.id), "projectId": str(resource.project_id),
        "actorWallet": resource.actor_wallet, "hkdCents": str(int(resource.hkd_cents)),
        "amountAtomic": str(int(resource.amount_atomic)),
        "steps": [{"id": str(step.id), "executor": step.executor, "kind": step.kind,
                   "stepIndex": step.step_index, "detail": step.detail} for step in steps]})


def retry_funding_preflight(session, ns, operation_id: UUID, gate) -> dict:
    """Requeue only the original failed Mint; no send, envelope, debit, or new hold.

Requires an enclosing transaction. A nested transaction rolls back temporary
queued statuses and any donor-reconciliation writes when a guard rejects.
"""
    namespace = session.get(DeploymentInstance, ns.id, with_for_update=True, populate_existing=True)
    if namespace is None:
        _reject("Original verified deployment is unavailable")
    _namespace(namespace, gate)
    operation = session.scalar(select(Operation).where(Operation.id == operation_id,
        Operation.namespace_id == namespace.id).with_for_update())
    if operation is None or operation.operation_kind != "payment.funding":
        _reject("Only an original funding operation in this namespace can be maintained")
    steps = session.scalars(select(OperationStep).where(OperationStep.operation_id == operation.id)
        .order_by(OperationStep.step_index).with_for_update()).all()
    resource = session.scalar(select(PaymentResource).where(PaymentResource.operation_id == operation.id)
        .with_for_update())
    if (resource is None or len(steps) != 2 or resource.namespace_id != namespace.id
            or resource.kind != "funding" or resource.actor_user_id != operation.principal_id
            or resource.procurement_id is not None or resource.source_operation_id is not None
            or resource.invoice_version_id is not None or operation.result_resource_type != "payment_resource"
            or operation.result_resource_id != resource.id or steps[0].step_index != 0
            or steps[1].step_index != 1 or steps[0].executor != "chain"
            or steps[0].kind != "payment.mint" or steps[1].executor != "payment"
            or steps[1].kind != "payment.finalize_funding"):
        _reject("Original funding resource and two-step scope must match exactly")
    common = {"projectUuid": str(resource.project_id), "paymentResourceUuid": str(resource.id)}
    expected_mint = {**common, "action": "payment.mint", "caller": gate.roles["relayer"],
                     "args": [resource.actor_wallet, str(int(resource.amount_atomic))], "expectedEvent": "Transfer"}
    if (steps[0].detail != expected_mint or steps[1].detail != {**common, "action": "payment.finalize_funding"}
            or resource.counterparty_wallet.lower() != gate.roles["relayer"].lower()
            or resource.token_address.lower() != gate.contract_address("MockHKD").lower()
            or operation.payload_hash != payload_sha256({"confirm": True,
                "projectId": str(resource.project_id), "hkdCents": str(int(resource.hkd_cents))})):
        _reject("Original payload, fixed Mint arguments or finalizer bindings changed")
    logs = session.scalars(select(AuditLog).where(AuditLog.operation_id == operation.id)
        .order_by(AuditLog.created_at, AuditLog.id)).all()
    failures = [row for row in logs if _failure_matches(row, operation, resource)]
    recoveries = [row for row in logs if row.action == RECOVERY_ACTION]
    digest = _scope_digest(operation, resource, steps)
    if recoveries:
        row = recoveries[0]
        metadata = row.metadata_json
        original = next((item for item in failures if str(item.id) == metadata.get("originalFailureAuditId")), None)
        if (len(recoveries) != 1 or original is None or row.outcome != "queued"
                or row.principal_id != operation.principal_id or row.resource_type != "payment_resource"
                or row.resource_id != resource.id or metadata != {
                    "originalFailureAuditId": str(original.id), "originalFailureCode": FAILURE_CODE,
                    "stepId": str(steps[0].id), "heldPreserved": True, "zeroAttempt": True,
                    "newAction": RECOVERY_ACTION, "namespaceId": str(namespace.id),
                    "heldHkdCents": str(int(resource.hkd_cents)), "scopeHash": digest}
                or operation.status not in ("queued", "submitted", "confirmed")):
            _reject("Recovery history or current operation requires independent attention")
        return {"operationId": str(operation.id), "paymentResourceId": str(resource.id),
                "status": operation.status, "replayed": True}
    if (operation.status != "failed" or operation.error_code != FAILURE_CODE
            or operation.error_status != 409 or steps[0].status != "failed" or steps[1].status != "queued"
            or resource.status != "held" or resource.error_code is not None
            or int(resource.allocated_atomic) != 0 or resource.chain_proof is not None or resource.journal_ids
            or len(failures) != 1):
        _reject("Funding is not a positively proven preparation-only failure with its original hold")
    if (session.scalar(select(ChainTransaction.id).where(or_(ChainTransaction.operation_id == operation.id,
            ChainTransaction.step_id.in_([step.id for step in steps]))).limit(1)) is not None
            or session.scalar(select(HKDJournal.id).where(HKDJournal.operation_id == operation.id).limit(1)) is not None
            or session.scalar(select(FundedClaim.id).where(or_(FundedClaim.operation_id == operation.id,
                FundedClaim.funding_resource_id == resource.id)).limit(1)) is not None
            or session.scalar(select(PaymentEvidence.id).where(PaymentEvidence.resource_id == resource.id).limit(1)) is not None):
        _reject("Any durable transaction attempt, allocation or accounting effect forbids this recovery")
    principal = SimplePaymentPrincipal(resource.actor_user_id, resource.actor_wallet, "donor")
    _actor(session, namespace, principal, "donor", gate)
    project = session.get(Project, resource.project_id)
    if project is None:
        _reject("Original project is unavailable")
    _project(namespace, project, gate)
    account = _owner_account(session, namespace, principal, lock=True)
    held = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id == namespace.id,
        PaymentResource.kind == "funding", PaymentResource.actor_user_id == operation.principal_id,
        PaymentResource.journal_ids == []).with_for_update()).all()
    if (int(account.held_cents) != sum(int(row.hkd_cents) for row in held)
            or resource not in held or set(resource.source_material) != {"balanceBeforeAtomic"}
            or not isinstance(resource.source_material["balanceBeforeAtomic"], str)
            or not resource.source_material["balanceBeforeAtomic"].isascii()
            or not resource.source_material["balanceBeforeAtomic"].isdecimal()
            or str(int(resource.source_material["balanceBeforeAtomic"])) != resource.source_material["balanceBeforeAtomic"]
            or int(resource.source_material["balanceBeforeAtomic"]) > UINT256_MAX):
        _reject("The complete original simulated HKD hold and source material must remain intact")
    from .worker import ChainWorker
    if not ChainWorker._lock_caller(session, namespace.id, expected_mint["caller"]):
        _reject("Original caller lane is busy", "payment_preflight_recovery_lane_busy")
    lane = select(ChainTransaction.id).where(ChainTransaction.namespace_id == namespace.id,
        func.lower(ChainTransaction.caller_address) == expected_mint["caller"].lower())
    if session.scalar(lane.where(ChainTransaction.status.in_(
            ("prepared", "sending", "submitted", "requires_attention", "invalidated_instance"))).limit(1)) is not None:
        _reject("Original caller has an unresolved envelope", "payment_preflight_recovery_lane_busy")
    with session.begin_nested():
        operation.status = steps[0].status = "queued"
        validate_pending_chain(session, namespace, steps[0], operation, gate)
        try:
            envelope = gate.prepare(expected_mint["action"], expected_mint["caller"],
                                    expected_mint["args"], expected_mint["expectedEvent"])
        except ChainUnavailable:
            raise
        except Exception as exc:
            raise APIError(409, "payment_preflight_recovery_prepare_rejected",
                           "Original fixed Mint cannot currently be prepared") from exc
        if (envelope.caller.lower() != expected_mint["caller"].lower()
                or envelope.to.lower() != resource.token_address.lower() or envelope.chain_id != 31337
                or envelope.action != "payment.mint" or envelope.expected_event != "Transfer"
                or type(envelope.nonce) is not int or not 0 <= envelope.nonce <= UINT256_MAX
                or envelope.value != 0 or not isinstance(envelope.data, str)
                or not envelope.data.startswith("0x") or len(envelope.data) < 10
                or len(envelope.data) % 2 or any(value not in "0123456789abcdefABCDEF" for value in envelope.data[2:])):
            _reject("Prepared envelope is not the exact local fixed Mint")
        if session.scalar(lane.where(ChainTransaction.evm_nonce_text == str(envelope.nonce)).limit(1)) is not None:
            _reject("Prepared nonce already exists in durable history", "payment_preflight_recovery_nonce_occupied")
        operation.error_code = operation.error_status = operation.error_detail = None
        audit(session, principal_id=operation.principal_id, operation_id=operation.id,
            action=RECOVERY_ACTION, outcome="queued", resource_type="payment_resource", resource_id=resource.id,
            metadata={"originalFailureAuditId": str(failures[0].id), "originalFailureCode": FAILURE_CODE,
                "stepId": str(steps[0].id), "heldPreserved": True, "zeroAttempt": True,
                "newAction": RECOVERY_ACTION, "namespaceId": str(namespace.id),
                "heldHkdCents": str(int(resource.hkd_cents)), "scopeHash": digest})
        session.flush()
    return {"operationId": str(operation.id), "paymentResourceId": str(resource.id),
            "status": "queued", "replayed": False}
