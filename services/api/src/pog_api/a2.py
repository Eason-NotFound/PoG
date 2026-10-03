from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import Annotated, Any
from uuid import UUID

from eth_utils import keccak
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session
from web3 import Web3

from .amounts import decimal_to_uint_string
from .errors import APIError
from .idempotency import audit, begin_operation, validate_idempotency_key
from .models import (
    Document, DocumentVersion, DonorCreditProjection, LedgerProjection, Operation,
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
        operation_kind=kind, idempotency_key=key, validated_payload=payload,
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
                and int(view[-1]) == 1
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
                and int(assessment[2]) == 0 and int(assessment[3]) == 0
                and int(assessment[9]) >= chain_now
                and int(message["policyEpoch"]) == int(request.policy_epoch) == int(ledger[9])
                and bool(gate.call(
                    "ProcurementEscrowV2", "isApprover", project.business_id,
                    Web3.to_checksum_address(request.signer_wallet),
                ))
                and int(view[-1]) == 2
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
    except Exception as exc:
        raise APIError(409, "signing_material_stale", str(exc)) from exc


def install_a2_routes(app, *, get_session, current_principal, namespace, gateway, gateway_error, settings):
    @app.post("/v2/projects/{project_id}/chain/create", status_code=202, tags=["chain"])
    def chain_create_project(
        project_id: UUID, body: EmptyMutation, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            project = _project(session, ns, project_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "project_forbidden", "Only the owning Foundation may queue creation")
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
                payload={}, resource_type="project", resource_id=project.id, steps=[detail],
            )
            if not replayed:
                project.chain_status = "create_queued"
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/projects/{project_id}/donations", status_code=202, tags=["chain"])
    def donate(
        project_id: UUID, body: DonationCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.amount_atomic)
        if amount == 0:
            raise APIError(422, "zero_amount", "Donation must be greater than zero")
        with session.begin():
            ns = namespace(session)
            project = _project(session, ns, project_id)
            if principal.role != "donor":
                raise APIError(403, "role_forbidden", "Only Donor may donate")
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
                payload=body.model_dump(mode="json", by_alias=True), resource_type="project",
                resource_id=project.id, steps=steps,
            )
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/create", status_code=202, tags=["chain"])
    def chain_create_procurement(
        procurement_id: UUID, body: EmptyMutation, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may queue creation")
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
                payload={}, resource_type="procurement", resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "create_queued"
        return {"operation": _operation(operation, replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/purchase-order", status_code=202, tags=["chain"])
    def purchase_order(
        procurement_id: UUID, body: PurchaseOrderCreate, principal=Depends(current_principal),
        session: Session = Depends(get_session), idempotency_key: IdempotencyHeader = None,
    ):
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may record PO")
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
                payload=body.model_dump(mode="json", by_alias=True), resource_type="procurement",
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
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.invoice_amount_atomic)
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
                raise APIError(403, "procurement_forbidden", "Only the owning Foundation may record invoice")
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
                payload=body.model_dump(mode="json", by_alias=True), resource_type="procurement",
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
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            payload = body.model_dump(mode="json", by_alias=True)
            if body.kind == "ai_pre" and (
                body.reserve_amount_atomic is not None
                or body.receipt_evidence_document_version_id is not None
            ):
                raise APIError(422, "signing_input_invalid", "AI PRE accepts no reserve or receipt fields")
            if body.kind == "reserve" and body.receipt_evidence_document_version_id is not None:
                raise APIError(422, "signing_input_invalid", "Reserve accepts no receipt evidence field")
            if body.kind == "receipt" and body.reserve_amount_atomic is not None:
                raise APIError(422, "signing_input_invalid", "Receipt accepts no reserve amount field")
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind=f"signing_request.{body.kind}", idempotency_key=key,
                validated_payload=payload,
            )
            if replayed:
                request = session.scalar(select(SigningRequest).where(SigningRequest.operation_id == operation.id))
                if request is None:
                    raise APIError(409, "operation_incomplete", "Signing request is incomplete")
            else:
                ttl = body.deadline_ttl_seconds or settings.signing_ttl_seconds
                deadline = gate.latest_timestamp() + ttl
                registry = gate.contract_address("PoGRegistryV2")
                escrow = gate.contract_address("ProcurementEscrowV2")
                signer = Web3.to_checksum_address(principal.wallet_address)
                if body.kind == "ai_pre":
                    if principal.role != "service_ai":
                        raise APIError(403, "role_forbidden", "Only service_ai_fixture may request AI PRE")
                    _must_match(principal.wallet_address, gate.roles["aiSigner"], "aiSigner")
                    if procurement.chain_status != "po_recorded":
                        raise APIError(409, "procurement_state_conflict", "PO must be confirmed")
                    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
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
                        int(view[-1]) == 2
                        and _equal_bytes(assessment[0], view[8])
                        and _equal_bytes(assessment[1], procurement.business_id)
                        and int(assessment[2]) == 0 and int(assessment[3]) == 0
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
                context = {"reserveAmountAtomic": str(amount)} if body.kind == "reserve" else {}
                request = SigningRequest(
                    namespace_id=ns.id, operation_id=operation.id, procurement_id=procurement.id,
                    signer_user_id=principal.user_id, kind=body.kind, status="prepared",
                    contract_address=contract, signer_wallet=signer,
                    nonce_text=str(nonce), deadline_text=str(deadline), policy_epoch=epoch,
                    typed_data=data, context_json=context, digest=digest(data),
                )
                session.add(request)
                session.flush()
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
            ns = namespace(session)
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
        gate = _require_gateway(gateway, gateway_error)
        if not settings.demo_signing_enabled:
            raise APIError(403, "demo_signing_disabled", "Local unlocked demo signing is disabled")
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            request = session.get(SigningRequest, request_id)
            if request is None or request.namespace_id != ns.id:
                raise APIError(404, "signing_request_not_found", "Signing request not found")
            if request.signer_user_id != principal.user_id or request.signer_wallet.lower() != principal.wallet_address.lower():
                raise APIError(403, "signer_mismatch", "Only the intended signer may authorize demo signing")
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind="signing_request.sign_demo", idempotency_key=key,
                validated_payload={"requestId": str(request_id), "confirm": True},
            )
            if not replayed:
                if request.status != "prepared":
                    raise APIError(409, "signing_request_state", "Signing request is not awaiting signature")
                procurement, project = _procurement(session, ns, request.procurement_id)
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
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session)
            request = session.get(SigningRequest, request_id, with_for_update=True)
            if request is None or request.namespace_id != ns.id:
                raise APIError(404, "signing_request_not_found", "Signing request not found")
            if request.signer_user_id != principal.user_id:
                raise APIError(403, "signer_mismatch", "Only the intended signer may submit")
            operation, replayed = begin_operation(
                session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind=f"signing_request.submit.{request.kind}", idempotency_key=key,
                validated_payload={"requestId": str(request.id), "signature": body.signature},
            )
            if not replayed:
                if request.status not in {"prepared", "signed"}:
                    raise APIError(409, "signing_request_state", "Signing request was already submitted")
                if recover(request.typed_data, body.signature).lower() != request.signer_wallet.lower():
                    raise APIError(422, "signature_invalid", "Signature does not recover intended EOA")
                procurement, project = _procurement(session, ns, request.procurement_id)
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
        gate = _require_gateway(gateway, gateway_error)
        key = validate_idempotency_key(idempotency_key)
        amount = int(body.reserve_amount_atomic)
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            allowed = (
                principal.role == "foundation" and project.foundation_user_id == principal.user_id
            ) or (
                principal.role == "human_approver" and project.human_approver_user_id == principal.user_id
            )
            if not allowed:
                raise APIError(403, "role_forbidden", "Only owning Foundation or project human may execute")
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
                payload=body.model_dump(mode="json", by_alias=True), resource_type="procurement",
                resource_id=procurement.id, steps=[detail],
            )
            if not replayed:
                procurement.chain_status = "reserve_queued"
        return {"operation": _operation(operation, replayed)}

    @app.get("/v2/projects/{project_id}/ledger", tags=["chain"])
    def ledger(project_id: UUID, principal=Depends(current_principal), session: Session = Depends(get_session)):
        gate = _require_gateway(gateway, gateway_error)
        with session.begin():
            ns = namespace(session)
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
