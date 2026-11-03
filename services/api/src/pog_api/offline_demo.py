"""Explicit local-only AI substitute; historical samples are never signed reports.

Foundation requests this action, but a separate manifest service_ai identity signs
the existing deterministic technical fixture. Human approval, receipt, reserve,
release and payment remain separate actions. No network/model call is made.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import Depends
from pydantic import Field
from sqlalchemy import func, select

from .a2 import (IdempotencyHeader, _document_version, _equal_bytes, _must_match,
                 _operation, _procurement, _require_gateway, validate_original_sources)
from .errors import APIError
from .full_demo import owner, private
from .hashing import keccak256
from .idempotency import audit, begin_operation, validate_idempotency_key
from .models import DeploymentInstance, Operation, SigningRequest, User, WalletAuthorization
from .payment_domain import binding
from .schemas import DemoSignRequest, SigningRequestCreate, SubmitSignedRequest
from .security import Principal

INTERFACE = "pog-offline-demo-v0.1"


class OfflineCreate(SubmitSignedRequest):
    stage: Literal["PRE", "FINAL"]
    expected_namespace_id: UUID = Field(alias="expectedNamespaceId")
    expected_run_id: str = Field(alias="expectedRunId", min_length=1, max_length=128)
    expected_instance_id: str = Field(alias="expectedInstanceId", min_length=1, max_length=128)
    chain_id: Literal["31337"] = Field(alias="chainId")


def load_reports(path):
    """Only startup-configured local text; never client paths or model results."""
    if path is None or not path.is_absolute() or path.is_symlink():
        raise ValueError("Private report bundle is unavailable")
    raw = path.read_bytes()
    if len(raw) > 262144:
        raise ValueError("Private report bundle is too large")
    value = json.loads(raw)
    if value.get("schema") != "pog-offline-report-samples-v1" or value.get("actualModelExecuted") is not False:
        raise ValueError("Reports must be declared historical samples")
    result = {}
    for stage in ("PRE", "FINAL"):
        row = value["reports"][stage]
        if not isinstance(row["markdown"], str) or not isinstance(row["title"], str):
            raise ValueError("Reports must contain plain text")
        score = row["sampleRiskScoreBps"]
        if type(score) is not int or not 0 <= score <= 10000:
            raise ValueError("Sample risk score is invalid")
        result[stage] = {"title": row["title"], "sampleRiskScoreBps": score,
            "markdown": row["markdown"], "provenance": "mock_demo", "actualModelExecuted": False,
            "sourceSha256": "0x" + hashlib.sha256(row["markdown"].encode()).hexdigest(),
            "context": "historical_reference_not_current_evidence"}
    return result


def validate_pre_sources(session, procurement, project, file_store, *, include_final=False):
    source = procurement.source_versions_json or {}
    fields = [
        ("poDocumentVersionId", "purchase_order", "po_hash"),
        ("requestDocumentVersionId", "request", "request_hash"),
        ("goodsRequestDocumentVersionId", "goods_request", "goods_request_hash"),
    ]
    if include_final:
        fields.extend((("invoiceDocumentVersionId", "invoice", "invoice_hash"),
                       ("goodsDocumentVersionId", "goods_evidence", "goods_hash")))
    for name, category, field in fields:
        try:
            version = _document_version(session, procurement, UUID(source[name]), category)
            valid = (version.referenced and version.uploaded_by_user_id == project.foundation_user_id
                and source["bindings"][name] == {"versionId": str(version.id), "hash": version.keccak256_hex,
                                               "uploaderId": str(version.uploaded_by_user_id)}
                and _equal_bytes(getattr(procurement, field), "0x" + version.keccak256_hex))
            raw = file_store.resolve(version.storage_key).read_bytes()
            valid = (valid and len(raw) == version.size_bytes
                and hashlib.sha256(raw).hexdigest() == version.sha256_hex
                and keccak256(raw) == version.keccak256_hex)
        except (KeyError, ValueError, TypeError, AttributeError, OSError, APIError) as exc:
            raise APIError(409, "offline_demo_source_unproven", "Original selected PRE sources are unavailable") from exc
        if not valid:
            raise APIError(409, "offline_demo_source_unproven", "Original selected PRE sources failed integrity checks")


def install_offline_demo_routes(app, *, get_session, current_principal, namespace,
                               gateway, gateway_error, settings, file_store, signing_actions):
    reports, reports_error = None, None
    try:
        reports = load_reports(settings.offline_demo_reports_file)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        reports_error = "offline_demo_reports_unavailable"

    def reason():
        if not settings.offline_demo_enabled:
            return "offline_demo_disabled"
        if not settings.full_demo_enabled or not settings.demo_signing_enabled:
            return "offline_demo_signing_disabled"
        return reports_error or ("chain_unavailable" if gateway is None else None)

    def gate():
        if reason():
            raise APIError(403, reason(), "Explicit local offline AI demo is unavailable")
        if settings.bind_host not in {"127.0.0.1", "localhost"}:
            raise APIError(403, "offline_demo_loopback_required", "Offline AI demo is local-only")
        return _require_gateway(gateway, gateway_error)

    def ai_principal(session, requesting, chain):
        rows = session.execute(select(User, WalletAuthorization).join(WalletAuthorization).where(
            User.active.is_(True), WalletAuthorization.active.is_(True),
            WalletAuthorization.role_name == "service_ai",
            func.lower(WalletAuthorization.wallet_address) == chain.roles["aiSigner"].lower(),
        )).all()
        if len(rows) != 1 or rows[0][0].id == requesting.user_id:
            raise APIError(409, "offline_demo_ai_identity_unavailable", "Independent manifest service_ai identity is required")
        user, wallet = rows[0]
        if not chain.call("PoGRegistryV2", "aiSigners", wallet.wallet_address):
            raise APIError(409, "offline_demo_ai_identity_unavailable", "Manifest AI signer is not currently authorized")
        return Principal(user.id, user.username, "service_ai", wallet.wallet_address, requesting.session_id)

    def item(session, row):
        message = row.typed_data["message"]
        operation = session.get(Operation, row.submitted_operation_id) if row.submitted_operation_id else None
        return {"stage": "PRE" if row.kind == "ai_pre" else "FINAL", "requestId": str(row.id),
            "status": row.status, "synthetic": True, "actualModelExecuted": False,
            "technicalRiskScoreBps": message["riskScoreBps"], "technicalReportHash": message["reportHash"],
            "evidenceHash": message["evidenceHash"], "nonce": row.nonce_text, "deadline": row.deadline_text,
            "sourceVersionIds": row.context_json.get("offlineSourceVersionIds", {}),
            "operation": _operation(operation, False) if operation else None}

    def envelope(ns, procurement, **extra):
        return {"interfaceId": INTERFACE, "binding": binding(ns, gateway),
            "availability": {"enabled": reason() is None, "reasonCode": reason()},
            "provenance": "mock_demo", "actualModelExecuted": False, "reports": reports,
            "procurement": {"id": str(procurement.id), "projectId": str(procurement.project_id),
                "businessId": procurement.business_id, "sourceVersionIds": procurement.source_versions_json}, **extra}

    @app.get("/v2/procurements/{procurement_id}/offline-demo-ai", tags=["offline-demo"])
    def listing(procurement_id: UUID, principal=Depends(current_principal), session=Depends(get_session)):
        _require_gateway(gateway, gateway_error)
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            private(project, principal)
            rows = session.scalars(select(SigningRequest).join(Operation,
                Operation.result_resource_id == SigningRequest.id).where(
                    Operation.namespace_id == ns.id, SigningRequest.procurement_id == procurement.id,
                    Operation.operation_kind.in_(("offline_demo.ai_pre", "offline_demo.ai_final")),
                ).order_by(SigningRequest.created_at.desc(), SigningRequest.id.desc()).limit(20)).all()
            return envelope(ns, procurement, items=[item(session, row) for row in rows])

    @app.post("/v2/procurements/{procurement_id}/offline-demo-ai", status_code=202, tags=["offline-demo"])
    def create(procurement_id: UUID, body: OfflineCreate, principal=Depends(current_principal),
               session=Depends(get_session), idempotency_key: IdempotencyHeader = None):
        chain = gate()
        key = validate_idempotency_key(idempotency_key)
        kind = "ai_pre" if body.stage == "PRE" else "ai_final"
        # Persist the caller's explicit intent separately from the independent
        # AI prepare/sign/submit operations. Partial attempts resume with this key.
        with session.begin():
            ns = namespace(session, for_chain=True)
            session.get(DeploymentInstance, ns.id, with_for_update=True)
            procurement, project = _procurement(session, ns, procurement_id)
            owner(project, principal)
            _must_match(principal.wallet_address, chain.roles["foundation"], "foundation")
            if (str(ns.id) != str(body.expected_namespace_id) or ns.run_id != body.expected_run_id
                    or ns.instance_id != body.expected_instance_id or ns.chain_id != 31337):
                raise APIError(409, "offline_demo_binding_mismatch", "Offline demo must target the current local deployment")
            service = ai_principal(session, principal, chain)
            wrapper, replayed = begin_operation(session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind="offline_demo." + kind, idempotency_key=key,
                validated_payload={"procurementId": str(procurement_id), **body.model_dump(mode="json", by_alias=True),
                                   "sampleSha256": reports[body.stage]["sourceSha256"]})
            wrapper_id = wrapper.id
            request = session.get(SigningRequest, wrapper.result_resource_id) if wrapper.result_resource_id else None
            if request is not None and request.submitted_operation_id is not None:
                current = item(session, request)
                return envelope(ns, procurement, assessment=current, items=[current],
                    operation=_operation(session.get(Operation, request.submitted_operation_id), True))
            validate_pre_sources(session, procurement, project, file_store, include_final=kind == "ai_final")
            if kind == "ai_final":
                validate_original_sources(session, procurement)
            audit(session, principal_id=principal.user_id, operation_id=wrapper_id,
                action="offline_demo.request", outcome="explicit_request", resource_type="procurement", resource_id=procurement.id,
                metadata={"stage": body.stage, "actualModelExecuted": False, "serviceUserId": str(service.user_id)})
        internal_key = "offline-" + str(wrapper_id)
        prepared = signing_actions["prepare"](procurement_id,
            SigningRequestCreate(kind=kind, deadlineTtlSeconds=3600), service, session, internal_key + "-prepare")
        request_id = UUID(prepared["signingRequest"]["id"])
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            owner(project, principal)
            wrapper = session.get(Operation, wrapper_id, with_for_update=True)
            request = session.get(SigningRequest, request_id, with_for_update=True)
            validate_pre_sources(session, procurement, project, file_store, include_final=kind == "ai_final")
            if kind == "ai_final":
                validate_original_sources(session, procurement)
            if request.status == "prepared":
                source = dict(procurement.source_versions_json)
                request.context_json = {**request.context_json, "offlineSourceVersionIds": source,
                                        "offlineSampleSha256": reports[body.stage]["sourceSha256"]}
            wrapper.result_resource_type, wrapper.result_resource_id = "signing_request", request_id
        signing_actions["sign"](request_id, DemoSignRequest(confirm=True), service, session, internal_key + "-sign")
        submitted = signing_actions["submit"](request_id, SubmitSignedRequest(confirm=True), service, session, internal_key + "-submit")
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            owner(project, principal)
            wrapper = session.get(Operation, wrapper_id, with_for_update=True)
            wrapper.status = "confirmed"  # Orchestration complete, NOT chain confirmation.
            request = session.get(SigningRequest, request_id)
            current = item(session, request)
            return envelope(ns, procurement, assessment=current, items=[current], operation=submitted["operation"])
