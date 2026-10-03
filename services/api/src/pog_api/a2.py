from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from eth_utils import keccak
from fastapi import Depends, Header
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from web3 import Web3

from .amounts import decimal_to_uint_string
from .errors import APIError
from .idempotency import audit, begin_operation, validate_idempotency_key, find_operation, lock_operation_key
from .hashing import payload_sha256
from .models import (
    Document, DocumentVersion, DonorCreditProjection, LedgerProjection, Operation, ChainTransaction,
    OperationStep, Procurement, Project, SigningRequest, WalletAuthorization,
)
from .schemas import (
    DemoSignRequest, DonationCreate, EmptyMutation, InvoiceAndGoodsCreate,
    PurchaseOrderCreate, ReserveCreate, SignatureSubmit, SigningRequestCreate,
)
from .typed_data import (
    ai_assessment_typed, digest, human_intent_typed, recipient_receipt_typed, recover,
)

IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def _operation(operation: Operation, replayed: bool) -> dict[str, Any]:
    return {
        "operationId": str(operation.id), "status": operation.status,
        "operationKind": operation.operation_kind,
        "resourceType": operation.result_resource_type,
        "resourceId": str(operation.result_resource_id) if operation.result_resource_id else None,
        "replayed": replayed, "chainVerified": False,
        "errorCode": operation.error_code, "errorStatus": operation.error_status,
        "errorMessage": operation.error_detail,
    }


def _require_gateway(gateway, gateway_error):
    if gateway is None:
        raise APIError(503, "chain_unavailable", gateway_error or "A2 chain mode is disabled")
    try:
        gateway.verify()
    except Exception as exc:
        raise APIError(503, "chain_gate_failed", str(exc)) from exc
    return gateway


def _wallet(session: Session, user_id: UUID, role: str) -> str:
    rows = session.scalars(select(WalletAuthorization).where(
        WalletAuthorization.user_id == user_id,
        WalletAuthorization.role_name == role,
        WalletAuthorization.active.is_(True),
    )).all()
    if len(rows) != 1:
        raise APIError(409, "ambiguous_role_wallet", f"Expected exactly one active {role} wallet")
    return Web3.to_checksum_address(rows[0].wallet_address)


def _must_match(value: str, expected: str, role: str) -> None:
    if value.lower() != expected.lower():
        raise APIError(409, "manifest_role_mismatch", f"{role} wallet does not match manifest")


def _equal_bytes(actual, expected) -> bool:
    def normalize(value) -> str:
        if isinstance(value, (bytes, bytearray)) or hasattr(value, "hex") and not isinstance(value, str):
            result = value.hex()
            return (result if result.startswith("0x") else "0x" + result).lower()
        return str(value).lower()

    return normalize(actual) == normalize(expected)


def _queue(
    session: Session, *, ns, principal, key: str, kind: str, payload: dict,
    resource_type: str, resource_id: UUID, steps: list[dict[str, Any]],
) -> tuple[Operation, bool]:
    operation, replayed = begin_operation(
        session, namespace_id=ns.id, principal_id=principal.user_id,
        operation_kind=kind, idempotency_key=key,
        validated_payload=payload,
    )
    if not replayed:
        operation.status = "queued"
        operation.result_resource_type = resource_type
        operation.result_resource_id = resource_id
        for index, detail in enumerate(steps):
            session.add(OperationStep(
                operation_id=operation.id, step_index=index, kind=detail["action"],
                status="queued", detail=detail,
            ))
        audit(
            session, principal_id=principal.user_id, operation_id=operation.id,
            action=kind, outcome="queued", resource_type=resource_type,
            resource_id=resource_id, metadata={"chainVerified": False},
        )
    return operation, replayed


def _scoped_payload(resource, body: dict) -> dict:
    return {"resourceId": str(resource.id), "businessId": resource.business_id, "body": body}


def _replay_resource(session, ns, principal, key, kind, body, resource):
    lock_operation_key(session, ns.id, principal.user_id, kind, key)
    operation = find_operation(session, namespace_id=ns.id, principal_id=principal.user_id,
                               operation_kind=kind, idempotency_key=key)
    if operation is None:
        return None
    # Preserve both published candidates' hashes without rewriting stored facts.
    # Every format, including the newest, requires an independently saved target.
    signing_kind = kind in {"signing_request.ai_pre", "signing_request.reserve", "signing_request.receipt"}
    resource_type = "project" if isinstance(resource, Project) else "procurement"
    saved_target = operation.result_resource_type == resource_type and operation.result_resource_id == resource.id
    accepted_hashes = {
        payload_sha256(body),
        payload_sha256(_scoped_payload(resource, body)),
    }
    if signing_kind:
        request = session.scalar(select(SigningRequest).where(SigningRequest.operation_id == operation.id))
        saved_target = (
            request is not None and request.procurement_id == resource.id
            and operation.result_resource_type == "signing_request"
            and operation.result_resource_id == request.id
        )
        accepted_hashes.add(payload_sha256({"procurementId": str(resource.id), "body": body}))
    else:
        accepted_hashes.add(payload_sha256({"resourceType": resource_type, "resourceId": str(resource.id), "body": body}))
    if not saved_target or operation.payload_hash not in accepted_hashes:
        raise APIError(409, "idempotency_payload_conflict", "Idempotency-Key belongs to different input or target", operation_id=str(operation.id))
    saved_step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation.id).order_by(OperationStep.step_index))
    if saved_step is not None and str(saved_step.detail.get("caller", "")).lower() != principal.wallet_address.lower():
        raise APIError(403, "signer_mismatch", "Current wallet is not the original operation caller")
    return operation


def _signing_authority(principal, project, kind):
    role = {"ai_pre": "service_ai", "reserve": "human_approver", "receipt": "recipient"}[kind]
    owner = project.human_approver_user_id if kind == "reserve" else project.recipient_user_id
    if principal.role != role or (kind != "ai_pre" and principal.user_id != owner):
        raise APIError(403, "role_forbidden", "Only this request's designated role may sign")


def _saved_signer(session, ns, principal, request):
    procurement, project = _procurement(session, ns, request.procurement_id)
    _signing_authority(principal, project, request.kind)
    if request.signer_user_id != principal.user_id or request.signer_wallet.lower() != principal.wallet_address.lower():
        raise APIError(403, "signer_mismatch", "Current identity/role-wallet is not the intended signer")
    manifest_role = {"ai_pre": "aiSigner", "reserve": "humanApprover", "receipt": "recipient"}[request.kind]
    if ns.manifest_json:
        _must_match(principal.wallet_address, ns.manifest_json["roles"][manifest_role], manifest_role)
    if request.status in {"prepared", "signed"} and request.submitted_operation_id is None:
        _validate_receipt_source(session, request)
    return procurement, project


def _replay_exact(session, ns, principal, key, kind, payload):
    lock_operation_key(session, ns.id, principal.user_id, kind, key)
    operation = find_operation(session, namespace_id=ns.id, principal_id=principal.user_id,
                               operation_kind=kind, idempotency_key=key)
    if operation is not None and operation.payload_hash != payload_sha256(payload):
        raise APIError(409, "idempotency_payload_conflict", "Idempotency-Key belongs to different input or target", operation_id=str(operation.id))
    return operation


def _guard_nonce_family(session, ns, principal, gate, contract, kind, nonce):
    token = f"signing-family:{ns.id}:{contract.lower()}:{principal.wallet_address.lower()}:{kind}"
    lock = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
    # Re-read under the family lock: an external consume must not produce a stale new request.
    function = {"ai_pre": "aiNonces", "reserve": "humanNonces", "receipt": "recipientNonces"}[kind]
    name = "ProcurementEscrowV2" if kind == "reserve" else "PoGRegistryV2"
    if int(gate.call(name, function, Web3.to_checksum_address(principal.wallet_address))) != nonce:
        raise APIError(409, "signing_nonce_changed", "Nonce changed while preparing request; retry with a new key")
    rows = session.scalars(select(SigningRequest).where(
        SigningRequest.namespace_id == ns.id,
        SigningRequest.contract_address == contract,
        SigningRequest.signer_wallet == Web3.to_checksum_address(principal.wallet_address),
        SigningRequest.kind == kind,
        SigningRequest.status.not_in(("expired", "invalidated_stale", "invalidated_instance", "invalidated_not_broadcast")),
    ).with_for_update()).all()
    chain_now = gate.latest_timestamp()
    for old in rows:
        if old.status == "failed" and old.submitted_operation_id is not None:
            attempts = session.scalars(select(ChainTransaction).where(
                ChainTransaction.operation_id == old.submitted_operation_id,
            )).all()
            submitted = session.get(Operation, old.submitted_operation_id)
            steps = session.scalars(select(OperationStep).where(OperationStep.operation_id == old.submitted_operation_id)).all()
            preflight_only = (
                not attempts and submitted is not None and submitted.status == "failed"
                and submitted.error_code == "chain_preparation_rejected"
                and len(steps) == 1 and steps[0].status == "failed"
            )
            if preflight_only or (attempts and all(tx.status == "not_broadcast" and tx.tx_hash is None for tx in attempts)):
                # Positive, persisted preflight proof, NOT an ambiguous/queued
                # expiry. Keep the submitted operation/attempt audit forever.
                old.status = "invalidated_not_broadcast"
                audit(session, principal_id=principal.user_id, action="signing_request.invalidate",
                      outcome=old.status, resource_type="signing_request", resource_id=old.id,
                      metadata={"reason": "read_only_prepare_failed" if preflight_only else "all_attempts_proven_not_broadcast"})
        if old.status in {"prepared", "signed"} and old.submitted_operation_id is None:
            reason = None
            try:
                _validate_receipt_source(session, old)
            except APIError as exc:
                if exc.code not in {"signing_material_stale", "receipt_evidence_uploader_mismatch"}:
                    raise
                # Only persisted, unsubmitted requests with positively invalid
                # original evidence can retire; never infer this from RPC outage.
                old.status, reason = "invalidated_stale", exc.code
            if reason is None and chain_now > int(old.deadline_text):
                old.status, reason = "expired", "deadline_expired"
            elif reason is None and int(old.nonce_text) != nonce:
                old.status, reason = "invalidated_stale", "nonce_consumed"
            elif reason is None:
                proc, proj = _procurement(session, ns, old.procurement_id)
                try:
                    _validate_signing_fresh(gate, old, proc, proj)
                except APIError as exc:
                    if exc.code != "signing_material_stale":
                        raise
                    old.status, reason = "invalidated_stale", exc.code
            if reason:
                if old.status == "expired":
                    # Keep Hank's published expiry audit contract, including its
                    # original operation pointer and canonical chain timestamp.
                    audit(session, principal_id=principal.user_id, operation_id=old.operation_id,
                          action="signing_request.expire", outcome="expired",
                          resource_type="signing_request", resource_id=old.id,
                          metadata={"deadline": old.deadline_text, "chainTimestamp": str(chain_now)})
                else:
                    audit(session, principal_id=principal.user_id, action="signing_request.invalidate",
                          outcome=old.status, resource_type="signing_request", resource_id=old.id,
                          metadata={"reason": reason})
        if old.status not in {"expired", "invalidated_stale", "invalidated_instance", "invalidated_not_broadcast"} and int(old.nonce_text) == nonce:
            raise APIError(409, "signing_nonce_in_use", "A live or submitted authorization already reserves this nonce")
    session.flush()


def _project(session: Session, ns, project_id: UUID) -> Project:
    value = session.get(Project, project_id)
    if value is None or value.namespace_id != ns.id:
        raise APIError(404, "project_not_found", "Project not found")
    return value


def _procurement(session: Session, ns, procurement_id: UUID) -> tuple[Procurement, Project]:
    value = session.get(Procurement, procurement_id)
    if value is None or value.namespace_id != ns.id:
        raise APIError(404, "procurement_not_found", "Procurement not found")
    return value, _project(session, ns, value.project_id)


def _document_version(session: Session, procurement: Procurement, value: UUID, category: str) -> DocumentVersion:
    version = session.get(DocumentVersion, value)
    if version is None:
        raise APIError(404, "document_version_not_found", "Document version not found")
    document = session.get(Document, version.document_id)
    if document is None or document.procurement_id != procurement.id or document.category != category:
        raise APIError(422, "document_reference_invalid", f"Expected {category} for this procurement")
    return version


def _validate_receipt_source(session: Session, request: SigningRequest) -> None:
    """Check the frozen original version, never a later same-hash replacement.

    Legacy requests lacking a version ID cannot establish upload provenance.
    This is a DB-only check: dependency/connection failures are not source proof.
    """
    if request.kind != "receipt":
        return
    context = request.context_json
    source_id = context.get("receiptEvidenceDocumentVersionId") if isinstance(context, dict) else None
    try:
        if not isinstance(source_id, str):
            raise ValueError("Receipt request has no frozen evidence version")
        version_id = UUID(source_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise APIError(409, "signing_material_stale", "Original receipt evidence version is not bound") from exc
    version = session.get(DocumentVersion, version_id)
    document = session.get(Document, version.document_id) if version is not None else None
    if (
        version is None or document is None
        or document.namespace_id != request.namespace_id
        or document.procurement_id != request.procurement_id
        or document.category != "receipt_evidence"
    ):
        raise APIError(409, "signing_material_stale", "Original receipt evidence reference is invalid")
    if version.uploaded_by_user_id != request.signer_user_id:
        raise APIError(403, "receipt_evidence_uploader_mismatch", "Receipt evidence must be uploaded by the original Recipient")
    message = request.typed_data.get("message") if isinstance(request.typed_data, dict) else None
    frozen_hash = message.get("receiptEvidenceHash") if isinstance(message, dict) else None
    try:
        if (
            not isinstance(frozen_hash, str) or not frozen_hash.startswith("0x")
            or len(frozen_hash) != 66 or not isinstance(version.keccak256_hex, str)
            or len(version.keccak256_hex) != 64
            or len(bytes.fromhex(frozen_hash[2:])) != 32
            or len(bytes.fromhex(version.keccak256_hex)) != 32
            or frozen_hash[2:].lower() != version.keccak256_hex.lower()
        ):
            raise ValueError("Receipt evidence differs from its original version")
    except (ValueError, TypeError) as exc:
        raise APIError(409, "signing_material_stale", "Frozen receipt evidence hash does not match its original version") from exc


def _expire_unsubmitted_nonce_requests(
    session: Session, *, namespace_id: UUID, contract: str, signer: str,
    nonce: int, kind: str, chain_now: int, principal_id: UUID,
) -> None:
    """Release only expired, never-submitted authorizations; retain their audit trail."""
    requests = session.scalars(select(SigningRequest).where(
        SigningRequest.namespace_id == namespace_id,
        SigningRequest.contract_address == contract,
        SigningRequest.signer_wallet == signer,
        SigningRequest.nonce_text == str(nonce),
        SigningRequest.kind == kind,
        SigningRequest.status != "expired",
    ).with_for_update()).all()
    for request in requests:
        if (
            request.status not in {"prepared", "signed"}
            or request.submitted_operation_id is not None
            or chain_now <= int(request.deadline_text)
        ):
            raise APIError(409, "signing_nonce_in_use", "This signer nonce already has an active or submitted request")
        request.status = "expired"
        audit(session, principal_id=principal_id, operation_id=request.operation_id,
              action="signing_request.expire", outcome="expired",
              resource_type="signing_request", resource_id=request.id,
              metadata={"deadline": request.deadline_text, "chainTimestamp": str(chain_now)})
    session.flush()


def _validate_signing_fresh(gate, request: SigningRequest, procurement: Procurement, project: Project) -> None:
    message = request.typed_data.get("message", {})
    registry = gate.contract_address("PoGRegistryV2")
    escrow = gate.contract_address("ProcurementEscrowV2")
    expected_contract = escrow if request.kind == "reserve" else registry
    try:
        expected_typed = (
            ai_assessment_typed(message, 31337, registry)
            if request.kind == "ai_pre"
            else human_intent_typed(message, 31337, escrow)
            if request.kind == "reserve"
            else recipient_receipt_typed(message, 31337, registry)
        )
        signer = message["signer"] if request.kind != "receipt" else message["expectedRecipient"]
        if (
            request.typed_data != expected_typed
            or request.digest != digest(expected_typed)
            or request.contract_address.lower() != expected_contract.lower()
            or request.signer_wallet.lower() != signer.lower()
            or int(request.nonce_text) != int(message["nonce"])
            or int(request.deadline_text) != int(message["deadline"])
        ):
            raise ValueError("stored typed material differs from the frozen signing request")
        chain_now = gate.latest_timestamp()
        if chain_now > int(request.deadline_text):
            raise ValueError("signing deadline expired")
        nonce_contract = "ProcurementEscrowV2" if request.kind == "reserve" else "PoGRegistryV2"
        nonce_function = (
            "humanNonces" if request.kind == "reserve"
            else "aiNonces" if request.kind == "ai_pre" else "recipientNonces"
        )
        if int(gate.call(nonce_contract, nonce_function, request.signer_wallet)) != int(request.nonce_text):
            raise ValueError("signing nonce changed")
        view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
        if request.kind == "ai_pre":
            computed = "0x" + bytes(gate.call(
                "PoGRegistryV2", "computeAssessmentId",
                [message[name] for name in (
                    "stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
                    "evidenceHash", "reportHash", "signer", "nonce", "deadline",
                )],
            )).hex()
            fresh = (
                message["procurementId"].lower() == procurement.business_id.lower()
                and message["evidenceHash"].lower() == ("0x" + bytes(view[7]).hex()).lower()
                and message["assessmentId"].lower() == computed.lower()
                and int(view[-1]) in {1, 2, 3}
            )
        elif request.kind == "reserve":
            ledger = gate.call("ProcurementEscrowV2", "getLedger", project.business_id)
            assessment = gate.call("PoGRegistryV2", "getAssessment", view[8])
            amount = int(request.context_json.get("reserveAmountAtomic", 0))
            terms = "0x" + bytes(gate.call(
                "ProcurementEscrowV2", "reserveTermsHash", procurement.business_id, amount
            )).hex()
            fresh = (
                amount > 0
                and message["targetId"].lower() == procurement.business_id.lower()
                and message["termsHash"].lower() == terms.lower()
                and message["assessmentId"].lower() == ("0x" + bytes(view[8]).hex()).lower()
                and _equal_bytes(assessment[0], view[8])
                and _equal_bytes(assessment[1], procurement.business_id)
                and int(assessment[2]) == 0
                and _equal_bytes(assessment[5], view[7])
                and bool(gate.call("PoGRegistryV2", "aiSigners", assessment[7]))
                and int(assessment[9]) >= chain_now
                and int(message["policyEpoch"]) == int(request.policy_epoch) == int(ledger[9])
                and bool(gate.call(
                    "ProcurementEscrowV2", "isApprover", project.business_id,
                    Web3.to_checksum_address(request.signer_wallet),
                ))
                and int(view[-1]) in {2, 3}
            )
        else:
            project_view = gate.call("PoGRegistryV2", "getProject", project.business_id)
            fresh = (
                message["projectId"].lower() == project.business_id.lower()
                and message["procurementId"].lower() == procurement.business_id.lower()
                and message["expectedRecipient"].lower() == project_view[2].lower()
                and message["vendor"].lower() == view[2].lower()
                and message["poHash"].lower() == ("0x" + bytes(view[4]).hex()).lower()
                and message["invoiceHash"].lower() == ("0x" + bytes(view[10]).hex()).lower()
                and int(message["invoiceAmount"]) == int(view[11])
                and message["goodsHash"].lower() == ("0x" + bytes(view[12]).hex()).lower()
                and int(view[-1]) == 5
            )
        if not fresh:
            raise ValueError("policy, assessment, evidence or business state changed")
    except APIError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise APIError(409, "signing_material_stale", str(exc)) from exc
    except Exception as exc:
        raise APIError(503, "chain_unavailable", "Cannot validate current signing material") from exc


def install_a2_routes(app, *, get_session, current_principal, namespace, gateway, gateway_error, settings):
    @app.post("/v2/projects/{project_id}/chain/create", status_code=202, tags=["chain"])
    def chain_create_project(
        project_id: UUID, body: EmptyMutation, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            project = _project(session, ns, project_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "project_forbidden", "Only the owning Foundation may queue creation")
            operation = _replay_resource(session, ns, principal, key, "project.chain.create", {}, project)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            if project.chain_status not in {"off_chain_draft", "create_queued"}:
                raise APIError(409, "project_state_conflict", "Project is not an unsent draft")
            _must_match(principal.wallet_address, gate.roles["foundation"], "foundation")
            recipient = _wallet(session, project.recipient_user_id, "recipient")
            human = _wallet(session, project.human_approver_user_id, "human_approver")
            _must_match(recipient, gate.roles["recipient"], "recipient")
            _must_match(human, gate.roles["humanApprover"], "humanApprover")
            detail = {
                "action": "project.create", "caller": principal.wallet_address,
                "args": [project.business_id, recipient, gate.contract_address("MockHKD"), [human], 1],
                "expectedEvent": "ProjectCreated", "projectUuid": str(project.id),
            }
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="project.chain.create",
                payload=_scoped_payload(project, {}), resource_type="project", resource_id=project.id, steps=[detail],
            )
            if not replayed:
                project.chain_status = "create_queued"
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/projects/{project_id}/donations", status_code=202, tags=["chain"])
    def donate(
        project_id: UUID, body: DonationCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.amount_atomic)
        if amount == 0:
            raise APIError(422, "zero_amount", "Donation must be greater than zero")
        with session.begin():
            ns = namespace(session, for_chain=True)
            project = _project(session, ns, project_id)
            if principal.role != "donor":
                raise APIError(403, "role_forbidden", "Only Donor may donate")
            payload = body.model_dump(mode="json", by_alias=True)
            operation = _replay_resource(session, ns, principal, key, "project.donation", payload, project)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            if project.chain_status != "active":
                raise APIError(409, "project_not_active", "Project is not confirmed active")
            donor_roles = {gate.roles["donorA"].lower(), gate.roles["donorB"].lower()}
            if principal.wallet_address.lower() not in donor_roles:
                raise APIError(409, "manifest_role_mismatch", "Donor must use donorA or donorB wallet")
            common = {"caller": principal.wallet_address, "projectUuid": str(project.id)}
            steps = [
                {**common, "action": "donation.approve",
                 "args": [gate.contract_address("ProcurementEscrowV2"), amount],
                 "expectedEvent": "Approval"},
                {**common, "action": "donation.deposit", "args": [project.business_id, amount],
                 "expectedEvent": "Donated"},
            ]
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="project.donation",
                payload=_scoped_payload(project, payload), resource_type="project",
                resource_id=project.id, steps=steps,
            )
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/create", status_code=202, tags=["chain"])
    def chain_create_procurement(
        procurement_id: UUID, body: EmptyMutation, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may queue creation")
            operation = _replay_resource(session, ns, principal, key, "procurement.chain.create", {}, procurement)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            if project.chain_status != "active" or procurement.chain_status not in {"off_chain_draft", "create_queued"}:
                raise APIError(409, "procurement_state_conflict", "Parent or procurement state is invalid")
            _must_match(principal.wallet_address, gate.roles["foundation"], "foundation")
            _must_match(procurement.vendor_wallet, gate.roles["vendor"], "vendor")
            detail = {
                "action": "procurement.create", "caller": principal.wallet_address,
                "args": [procurement.business_id, project.business_id, gate.roles["vendor"],
                         int(procurement.budget_cap_atomic)],
                "expectedEvent": "ProcurementCreated", "projectUuid": str(project.id),
                "procurementUuid": str(procurement.id),
            }
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="procurement.chain.create",
                payload=_scoped_payload(procurement, {}), resource_type="procurement", resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "create_queued"
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/purchase-order", status_code=202, tags=["chain"])
    def purchase_order(
        procurement_id: UUID, body: PurchaseOrderCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may record PO")
            payload = body.model_dump(mode="json", by_alias=True)
            operation = _replay_resource(session, ns, principal, key, "procurement.purchase_order", payload, procurement)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            _must_match(principal.wallet_address, gate.roles["foundation"], "foundation")
            if procurement.chain_status not in {"created", "po_queued"}:
                raise APIError(409, "procurement_state_conflict", "Procurement is not Created")
            versions = [
                _document_version(session, procurement, body.po_document_version_id, "purchase_order"),
                _document_version(session, procurement, body.request_document_version_id, "request"),
                _document_version(session, procurement, body.goods_request_document_version_id, "goods_request"),
            ]
            detail = {
                "action": "procurement.po", "caller": principal.wallet_address,
                "args": [procurement.business_id] + ["0x" + item.keccak256_hex for item in versions],
                "expectedEvent": "PurchaseOrderRecorded", "projectUuid": str(project.id),
                "procurementUuid": str(procurement.id),
            }
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="procurement.purchase_order",
                payload=_scoped_payload(procurement, payload), resource_type="procurement",
                resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "po_queued"
                for item in versions:
                    item.referenced = True
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/invoice-and-goods", status_code=202, tags=["chain"])
    def invoice_goods(
        procurement_id: UUID, body: InvoiceAndGoodsCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.invoice_amount_atomic)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may record invoice")
            payload = body.model_dump(mode="json", by_alias=True)
            operation = _replay_resource(session, ns, principal, key, "procurement.invoice_and_goods", payload, procurement)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            _must_match(principal.wallet_address, gate.roles["foundation"], "foundation")
            if procurement.chain_status not in {"reserved", "invoice_queued"}:
                raise APIError(409, "procurement_state_conflict", "Procurement is not Reserved")
            if amount == 0 or amount > int(procurement.reserved_amount_atomic or 0):
                raise APIError(422, "invalid_invoice_amount", "Invoice must be positive and no more than reserved")
            invoice = _document_version(session, procurement, body.invoice_document_version_id, "invoice")
            goods = _document_version(session, procurement, body.goods_document_version_id, "goods_evidence")
            detail = {
                "action": "procurement.invoice", "caller": principal.wallet_address,
                "args": [procurement.business_id, "0x" + invoice.keccak256_hex, amount,
                         "0x" + goods.keccak256_hex],
                "expectedEvent": "InvoiceAndGoodsRecorded", "projectUuid": str(project.id),
                "procurementUuid": str(procurement.id),
            }
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="procurement.invoice_and_goods",
                payload=_scoped_payload(procurement, payload), resource_type="procurement",
                resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "invoice_queued"
                procurement.invoice_hash = "0x" + invoice.keccak256_hex
                procurement.invoice_amount_atomic = Decimal(amount)
                procurement.goods_hash = "0x" + goods.keccak256_hex
                invoice.referenced = goods.referenced = True
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/signing-requests", status_code=202, tags=["signing"])
    def create_signing_request(
        procurement_id: UUID, body: SigningRequestCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            payload = body.model_dump(mode="json", by_alias=True)
            _signing_authority(principal, project, body.kind)
            if body.kind == "ai_pre" and (
                body.reserve_amount_atomic is not None
                or body.receipt_evidence_document_version_id is not None
            ):
                raise APIError(422, "signing_input_invalid", "AI PRE accepts no reserve or receipt fields")
            if body.kind == "reserve" and body.receipt_evidence_document_version_id is not None:
                raise APIError(422, "signing_input_invalid", "Reserve accepts no receipt evidence field")
            if body.kind == "receipt" and body.reserve_amount_atomic is not None:
                raise APIError(422, "signing_input_invalid", "Receipt accepts no reserve amount field")
            existing = _replay_resource(session, ns, principal, key, f"signing_request.{body.kind}", payload, procurement)
            if existing is not None:
                request = session.scalar(select(SigningRequest).where(SigningRequest.operation_id == existing.id))
                if request is None:
                    raise APIError(409, "operation_incomplete", "Signing request is incomplete")
                if request.signer_wallet.lower() != principal.wallet_address.lower():
                    raise APIError(403, "signer_mismatch", "Current role wallet differs from intended signer")
                return {"operation": _operation(existing, True), "signingRequest": _signing(request, True)}
            gate = _require_gateway(gateway, gateway_error)
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind=f"signing_request.{body.kind}", idempotency_key=key,
                validated_payload=_scoped_payload(procurement, payload),
            )
            if replayed:
                request = session.scalar(select(SigningRequest).where(SigningRequest.operation_id == operation.id))
                if request is None:
                    raise APIError(409, "operation_incomplete", "Signing request is incomplete")
            else:
                ttl = body.deadline_ttl_seconds or settings.signing_ttl_seconds
                chain_now = gate.latest_timestamp()
                deadline = chain_now + ttl
                if deadline < 0 or deadline > 2**64 - 1:
                    raise APIError(422, "signing_deadline_invalid", "Deadline exceeds uint64")
                registry = gate.contract_address("PoGRegistryV2")
                escrow = gate.contract_address("ProcurementEscrowV2")
                signer = Web3.to_checksum_address(principal.wallet_address)
                if body.kind == "ai_pre":
                    if principal.role != "service_ai":
                        raise APIError(403, "role_forbidden", "Only service_ai_fixture may request AI PRE")
                    _must_match(principal.wallet_address, gate.roles["aiSigner"], "aiSigner")
                    if procurement.chain_status not in {"po_recorded", "pre_assessed", "reserve_approval_pending"}:
                        raise APIError(409, "procurement_state_conflict", "PO must be confirmed")
                    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
                    if int(view[-1]) not in {1, 2, 3}:
                        raise APIError(409, "procurement_state_conflict", "Current chain state does not permit PRE assessment")
                    evidence = "0x" + bytes(view[7]).hex()
                    report = "0x" + keccak(
                        ("POG_A2_SYNTHETIC_PRE_FIXTURE_V1:" + procurement.business_id).encode()
                    ).hex()
                    nonce = int(gate.call("PoGRegistryV2", "aiNonces", signer))
                    provisional = [0, procurement.business_id, "0x" + "00" * 32, 0, 100,
                                   evidence, report, signer, nonce, deadline]
                    assessment_id = "0x" + bytes(
                        gate.call("PoGRegistryV2", "computeAssessmentId", provisional)
                    ).hex()
                    message = {
                        "stage": 0, "procurementId": procurement.business_id,
                        "assessmentId": assessment_id, "outcome": 0, "riskScoreBps": 100,
                        "evidenceHash": evidence, "reportHash": report,
                        "signer": signer, "nonce": nonce, "deadline": deadline,
                    }
                    data = ai_assessment_typed(message, 31337, registry)
                    contract = registry
                    epoch = 0
                elif body.kind == "reserve":
                    if principal.role != "human_approver" or project.human_approver_user_id != principal.user_id:
                        raise APIError(403, "role_forbidden", "Only this project's human approver may sign")
                    _must_match(principal.wallet_address, gate.roles["humanApprover"], "humanApprover")
                    if body.reserve_amount_atomic is None:
                        raise APIError(422, "reserve_amount_required", "reserveAmountAtomic is required")
                    amount = int(body.reserve_amount_atomic)
                    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
                    ledger = gate.call("ProcurementEscrowV2", "getLedger", project.business_id)
                    assessment = gate.call("PoGRegistryV2", "getAssessment", view[8])
                    nonce = int(gate.call("ProcurementEscrowV2", "humanNonces", signer))
                    terms = "0x" + bytes(gate.call(
                        "ProcurementEscrowV2", "reserveTermsHash", procurement.business_id, amount
                    )).hex()
                    message = {
                        "targetId": procurement.business_id, "action": 0, "termsHash": terms,
                        "assessmentId": "0x" + bytes(view[8]).hex(), "signer": signer,
                        "nonce": nonce, "deadline": deadline, "policyEpoch": int(ledger[9]),
                    }
                    data = human_intent_typed(message, 31337, escrow)
                    contract = escrow
                    epoch = int(ledger[9])
                    if not (
                        int(view[-1]) in {2, 3}
                        and _equal_bytes(assessment[0], view[8])
                        and _equal_bytes(assessment[1], procurement.business_id)
                        and int(assessment[2]) == 0
                        and _equal_bytes(assessment[5], view[7])
                        and bool(gate.call("PoGRegistryV2", "aiSigners", assessment[7]))
                        and int(assessment[9]) >= deadline - ttl
                        and bool(gate.call(
                            "ProcurementEscrowV2", "isApprover", project.business_id, signer
                        ))
                    ):
                        raise APIError(409, "signing_material_stale", "Current PRE assessment or policy is invalid")
                else:
                    if principal.role != "recipient" or project.recipient_user_id != principal.user_id:
                        raise APIError(403, "role_forbidden", "Only the original Recipient may sign receipt")
                    _must_match(principal.wallet_address, gate.roles["recipient"], "recipient")
                    if body.receipt_evidence_document_version_id is None:
                        raise APIError(422, "receipt_evidence_required", "receiptEvidenceDocumentVersionId is required")
                    evidence_version = _document_version(
                        session, procurement, body.receipt_evidence_document_version_id, "receipt_evidence"
                    )
                    if evidence_version.uploaded_by_user_id != principal.user_id:
                        raise APIError(403, "receipt_evidence_uploader_mismatch", "Receipt evidence must be uploaded by the original Recipient")
                    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
                    project_view = gate.call("PoGRegistryV2", "getProject", project.business_id)
                    nonce = int(gate.call("PoGRegistryV2", "recipientNonces", signer))
                    message = {
                        "projectId": project.business_id, "procurementId": procurement.business_id,
                        "expectedRecipient": signer, "vendor": view[2],
                        "poHash": "0x" + bytes(view[4]).hex(), "invoiceHash": "0x" + bytes(view[10]).hex(),
                        "invoiceAmount": int(view[11]), "goodsHash": "0x" + bytes(view[12]).hex(),
                        "receiptEvidenceHash": "0x" + evidence_version.keccak256_hex,
                        "nonce": nonce, "deadline": deadline,
                    }
                    if project_view[2].lower() != signer.lower():
                        raise APIError(409, "recipient_binding_changed", "On-chain Recipient differs")
                    if int(view[-1]) != 5:
                        raise APIError(409, "procurement_state_conflict", "Invoice must be confirmed before receipt")
                    data = recipient_receipt_typed(message, 31337, registry)
                    contract = registry
                    epoch = 0
                    evidence_version.referenced = True
                context = (
                    {"reserveAmountAtomic": str(amount)} if body.kind == "reserve"
                    else {"receiptEvidenceDocumentVersionId": str(evidence_version.id)}
                    if body.kind == "receipt" else {}
                )
                _guard_nonce_family(session, ns, principal, gate, contract, body.kind, nonce)
                request = SigningRequest(
                    namespace_id=ns.id, operation_id=operation.id, procurement_id=procurement.id,
                    signer_user_id=principal.user_id, kind=body.kind, status="prepared",
                    contract_address=contract, signer_wallet=signer,
                    nonce_text=str(nonce), deadline_text=str(deadline), policy_epoch=epoch,
                    typed_data=data, context_json=context, digest=digest(data),
                )
                try:
                    with session.begin_nested():
                        session.add(request)
                        session.flush()
                except IntegrityError as exc:
                    if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != "uq_signing_request_nonce_family":
                        raise
                    raise APIError(409, "signing_nonce_in_use", "A concurrent request already occupies this signer nonce") from exc
                operation.status = "confirmed"
                operation.result_resource_type = "signing_request"
                operation.result_resource_id = request.id
                audit(session, principal_id=principal.user_id, operation_id=operation.id,
                      action=f"signing_request.{body.kind}", outcome="prepared",
                      resource_type="signing_request", resource_id=request.id,
                      metadata={"digest": request.digest, "synthetic": body.kind == "ai_pre"})
        return {"operation": _operation(operation, replayed), "signingRequest": _signing(request, True)}

    def _signing(request: SigningRequest, include_typed: bool) -> dict[str, Any]:
        return {
            "id": str(request.id), "procurementId": str(request.procurement_id),
            "kind": request.kind, "status": request.status, "signer": request.signer_wallet,
            "nonce": request.nonce_text, "deadline": request.deadline_text,
            "policyEpoch": request.policy_epoch, "digest": request.digest,
            "typedData": request.typed_data if include_typed else None,
            "synthetic": request.kind == "ai_pre",
        }

    @app.get("/v2/signing-requests/{request_id}", tags=["signing"])
    def get_signing_request(
        request_id: UUID, principal=Depends(current_principal), session: Session = Depends(get_session),
    ):
        with session.begin():
            ns = namespace(session, for_chain=True)
            request = session.get(SigningRequest, request_id)
            if request is None or request.namespace_id != ns.id:
                raise APIError(404, "signing_request_not_found", "Signing request not found")
            if request.signer_user_id != principal.user_id:
                raise APIError(403, "signing_request_forbidden", "Signing request belongs to another signer")
        return _signing(request, True)

    @app.post("/v2/signing-requests/{request_id}/sign-demo", status_code=202, tags=["signing"])
    def sign_demo(
        request_id: UUID, body: DemoSignRequest, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        if not settings.demo_signing_enabled:
            raise APIError(403, "demo_signing_disabled", "Local unlocked demo signing is disabled")
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            request = session.get(SigningRequest, request_id, with_for_update=True)
            if request is None or request.namespace_id != ns.id:
                raise APIError(404, "signing_request_not_found", "Signing request not found")
            procurement, project = _saved_signer(session, ns, principal, request)
            payload = {"requestId": str(request_id), "confirm": True}
            operation = _replay_exact(session, ns, principal, key, "signing_request.sign_demo", payload)
            if operation is not None:
                return {"operation": _operation(operation, True), "signingRequest": _signing(request, True)}
            gate = _require_gateway(gateway, gateway_error)
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind="signing_request.sign_demo", idempotency_key=key,
                validated_payload={"requestId": str(request_id), "confirm": True},
            )
            if not replayed:
                if request.status != "prepared" or request.submitted_operation_id is not None:
                    raise APIError(409, "signing_request_state", "Signing request is not awaiting signature")
                _validate_signing_fresh(gate, request, procurement, project)
                signature = gate.sign_typed_data(request.signer_wallet, request.typed_data)
                if recover(request.typed_data, signature).lower() != request.signer_wallet.lower():
                    raise APIError(409, "signature_invalid", "Demo wallet returned wrong signer")
                request.signature = signature
                request.status = "signed"
                request.authorized_at = datetime.now(UTC)
                operation.status = "confirmed"
                operation.result_resource_type = "signing_request"
                operation.result_resource_id = request.id
                audit(session, principal_id=principal.user_id, operation_id=operation.id,
                      action="signing_request.sign_demo", outcome="authorized",
                      resource_type="signing_request", resource_id=request.id,
                      metadata={"explicitConfirm": True})
        return {"operation": _operation(operation, replayed), "signingRequest": _signing(request, True)}

    @app.post("/v2/signing-requests/{request_id}/submit", status_code=202, tags=["signing"])
    def submit_signature(
        request_id: UUID, body: SignatureSubmit, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session, for_chain=True)
            request = session.get(SigningRequest, request_id, with_for_update=True)
            if request is None or request.namespace_id != ns.id:
                raise APIError(404, "signing_request_not_found", "Signing request not found")
            procurement, project = _saved_signer(session, ns, principal, request)
            payload = {"requestId": str(request.id), "signature": body.signature}
            operation = _replay_exact(session, ns, principal, key, f"signing_request.submit.{request.kind}", payload)
            if operation is not None:
                return {"operation": _operation(operation, True), "signingRequest": _signing(request, True)}
            gate = _require_gateway(gateway, gateway_error)
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind=f"signing_request.submit.{request.kind}", idempotency_key=key,
                validated_payload={"requestId": str(request.id), "signature": body.signature},
            )
            if not replayed:
                if request.status not in {"prepared", "signed"} or request.submitted_operation_id is not None:
                    raise APIError(409, "signing_request_state", "Signing request was already submitted")
                try:
                    recovered = recover(request.typed_data, body.signature)
                except Exception as exc:
                    raise APIError(422, "signature_invalid", "Malformed or unrecoverable EOA signature") from exc
                if recovered.lower() != request.signer_wallet.lower():
                    raise APIError(422, "signature_invalid", "Signature does not recover intended EOA")
                _validate_signing_fresh(gate, request, procurement, project)
                message = request.typed_data["message"]
                if request.kind == "ai_pre":
                    action, caller, event = "assessment.ai_pre", gate.roles["relayer"], "AIAssessmentRecorded"
                    args = [[message[name] for name in (
                        "stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
                        "evidenceHash", "reportHash", "signer", "nonce", "deadline",
                    )], body.signature]
                    procurement.chain_status = "ai_pre_queued"
                elif request.kind == "reserve":
                    action, caller, event = "approval.reserve", gate.roles["relayer"], "HumanApprovalSubmitted"
                    amount = int(request.context_json.get("reserveAmountAtomic", 0))
                    if amount <= 0:
                        raise APIError(409, "signing_material_incomplete", "Reserve amount is missing")
                    args = [procurement.business_id, amount, [message[name] for name in (
                        "targetId", "action", "termsHash", "assessmentId", "signer", "nonce",
                        "deadline", "policyEpoch",
                    )], body.signature]
                    procurement.chain_status = "reserve_vote_queued"
                else:
                    action, caller, event = "receipt.submit", gate.roles["relayer"], "RecipientReceiptAccepted"
                    args = [[message[name] for name in (
                        "projectId", "procurementId", "expectedRecipient", "vendor", "poHash",
                        "invoiceHash", "invoiceAmount", "goodsHash", "receiptEvidenceHash", "nonce",
                        "deadline",
                    )], body.signature]
                    procurement.chain_status = "receipt_queued"
                detail = {
                    "action": action, "caller": caller, "args": args, "expectedEvent": event,
                    "projectUuid": str(project.id), "procurementUuid": str(procurement.id),
                }
                operation.status = "queued"
                operation.result_resource_type = "signing_request"
                operation.result_resource_id = request.id
                session.add(OperationStep(operation_id=operation.id, step_index=0, kind=action,
                                          status="queued", detail=detail))
                request.signature = body.signature
                request.status = "queued"
                request.submitted_operation_id = operation.id
                audit(session, principal_id=principal.user_id, operation_id=operation.id,
                      action=f"signing_request.submit.{request.kind}", outcome="queued",
                      resource_type="signing_request", resource_id=request.id)
        return {"operation": _operation(operation, replayed), "signingRequest": _signing(request, True)}

    @app.post("/v2/procurements/{procurement_id}/chain/reserve", status_code=202, tags=["chain"])
    def execute_reserve(
        procurement_id: UUID, body: ReserveCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.reserve_amount_atomic)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            allowed = (
                principal.role == "foundation" and project.foundation_user_id == principal.user_id
            ) or (
                principal.role == "human_approver" and project.human_approver_user_id == principal.user_id
            )
            if not allowed:
                raise APIError(403, "role_forbidden", "Only owning Foundation or project human may execute")
            payload = body.model_dump(mode="json", by_alias=True)
            operation = _replay_resource(session, ns, principal, key, "procurement.reserve.execute", payload, procurement)
            if operation is not None:
                return {"operation": _operation(operation, True)}
            gate = _require_gateway(gateway, gateway_error)
            manifest_role = "foundation" if principal.role == "foundation" else "humanApprover"
            _must_match(principal.wallet_address, gate.roles[manifest_role], manifest_role)
            if procurement.chain_status not in {"reserve_approval_pending", "reserve_queued"}:
                raise APIError(409, "procurement_state_conflict", "Reserve approval is not confirmed")
            detail = {
                "action": "reserve.execute", "caller": principal.wallet_address,
                "args": [procurement.business_id, amount], "expectedEvent": "BudgetReserved",
                "projectUuid": str(project.id), "procurementUuid": str(procurement.id),
            }
            operation, replayed = _queue(
                session, ns=ns, principal=principal, key=key, kind="procurement.reserve.execute",
                payload=_scoped_payload(procurement, payload), resource_type="procurement",
                resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "reserve_queued"
        return {"operation": _operation(operation, replayed)}

    @app.get("/v2/projects/{project_id}/ledger", tags=["chain"])
    def ledger(project_id: UUID, principal=Depends(current_principal), session: Session = Depends(get_session)):
        gate = _require_gateway(gateway, gateway_error)
        with session.begin():
            ns = namespace(session, for_chain=True)
            project = _project(session, ns, project_id)
            member = principal.user_id in {
                project.foundation_user_id, project.recipient_user_id, project.human_approver_user_id,
            }
            if not member and principal.role != "donor":
                raise APIError(403, "project_forbidden", "Project is outside principal scope")
            value = gate.call("ProcurementEscrowV2", "getLedger", project.business_id)
            own_credit = None
            if principal.role == "donor":
                own_credit = str(gate.call(
                    "ProcurementEscrowV2", "donorCredit", project.business_id,
                    Web3.to_checksum_address(principal.wallet_address)
                ))
        return {
            "projectId": str(project.id), "businessId": project.business_id,
            "asset": value[0], "depositsAtomic": str(value[1]), "reservedAtomic": str(value[2]),
            "releasedAtomic": str(value[3]), "returnedAtomic": str(value[4]),
            "refundedAtomic": str(value[5]), "refundPoolAtomic": str(value[6]),
            "donorCount": value[7], "claimedCount": value[8], "policyEpoch": value[9],
            "threshold": value[10], "refundSnapshotted": value[11],
            "currentCallerDonorCreditAtomic": own_credit, "chainVerified": True,
        }
