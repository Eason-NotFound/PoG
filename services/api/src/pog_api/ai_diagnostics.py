"""Explicit, private LAN-model diagnostics; never a wallet/signature/chain action."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, Response
from sqlalchemy import select

from .a2 import IdempotencyHeader, _operation, _procurement, _require_gateway, _equal_bytes
from .ai_diagnostic_core import (DiagnosticCreate, INTERFACE, OPERATION_KIND, VERSIONS, authorize,
                                 binding, build_input, diagnostic_dto, request_bytes, save_report)
from .ai_diagnostic_models import AIDiagnostic
from .ai_protocol.verify_service import Frozen, CheckFailure, endpoint, fetch, read_token, clean_response, exact, check
from .errors import APIError
from .idempotency import audit, begin_operation, find_operation, lock_operation_key, validate_idempotency_key
from .models import DocumentVersion, Operation, Procurement


def install_ai_diagnostic_routes(app, *, get_session, current_principal, namespace, gateway, gateway_error, settings, file_store):
    frozen, base_url, setup_error = None, None, "ai_diagnostic_disabled"
    if settings.ai_diagnostic_enabled:
        try:
            base_url = endpoint(settings.ai_diagnostic_url)
            if base_url != "http://192.168.0.246:18765":
                raise CheckFailure("DIAGNOSTIC_DESTINATION_NOT_APPROVED")
            if settings.ai_diagnostic_token_file is None or not settings.ai_diagnostic_token_file.is_absolute():
                raise CheckFailure("TOKEN_FILE_REQUIRED")
            frozen = Frozen(Path(__file__).parent / "ai_protocol" / "repoSnapshot")
            setup_error = None
        except Exception:
            setup_error = "ai_diagnostic_configuration_unavailable"

    def envelope(ns, **data):
        return {"interfaceId": INTERFACE, "binding": binding(ns, gateway), "signingEnabled": False, **data}

    def availability():
        # Configuration readiness alone; never reported as a successful model call.
        return {"enabled": setup_error is None and gateway is not None,
                "reasonCode": setup_error or ("ai_diagnostic_chain_unavailable" if gateway is None else None)}

    def load(row_id, session, ns, principal):
        row = session.get(AIDiagnostic, row_id)
        if row is None or row.namespace_id != ns.id:
            raise APIError(404, "ai_diagnostic_not_found", "Private diagnostic not found")
        procurement, project = _procurement(session, ns, row.procurement_id)
        authorize(project, principal)
        return row, procurement

    def complete(diagnostic_id, resources):
        factory = app.state.session_factory
        try:
            with factory() as session, session.begin():
                row = session.get(AIDiagnostic, diagnostic_id)
                if row is None or row.status != "queued":
                    return
                value = row.input_json
                request, wire = request_bytes(frozen, value, resources, row.operation_id, row.id)
                ns_id = row.namespace_id
            token = read_token(settings.ai_diagnostic_token_file)
            health_status, health_raw, health = fetch(base_url + "/internal/v1/health", token, "GET", timeout=15)
            clean_response(health_raw, health, token)
            exact(health, ("schemaVersion", "status", "backendReady", "signingEnabled"), "HEALTH_KEYS")
            check(health_status == 200 and health["schemaVersion"] == "pog.ai.health/1" and health["status"] == "ok"
                  and health["signingEnabled"] is False and health["backendReady"] is True, "AI_BACKEND_NOT_READY")
            status, response_raw, response = fetch(base_url + "/internal/v1/assess-bytes", token, "POST", wire, timeout=130)
            clean_response(response_raw, response, token)
            raw, report, error = frozen.response(status, response, request, value, VERSIONS)
            if error:
                raise CheckFailure(error)
            clean_response(raw, report, token)
            # A model response cannot change canonical chain facts or revive a stale deployment.
            _require_gateway(gateway, gateway_error)
            deployment, registry = value["evidenceSnapshot"]["deployment"], value["registrySnapshot"]
            check(deployment["runId"] == gateway.run_id and deployment["instanceId"] == gateway.instance_id
                  and deployment["registry"] == gateway.contract_address("PoGRegistryV2").lower(), "REGISTRY_SNAPSHOT_STALE")
            current = gateway.call("PoGRegistryV2", "getProcurement", registry["procurementId"])
            check(gateway.canonical(int(registry["blockNumber"]), registry["blockHash"])
                  and int(current[-1]) in {1, 2, 3} and _equal_bytes(current[7], registry["currentEvidenceHash"]), "REGISTRY_SNAPSHOT_STALE")
            key = save_report(file_store, ns_id, diagnostic_id, raw)
            with factory() as session, session.begin():
                row = session.get(AIDiagnostic, diagnostic_id, with_for_update=True)
                if row is None or row.status != "queued":
                    return
                ns = namespace(session, for_chain=True)
                check(ns.id == ns_id, "REGISTRY_SNAPSHOT_STALE")
                operation = session.get(Operation, row.operation_id, with_for_update=True)
                check(operation.status == "queued", "REGISTRY_SNAPSHOT_STALE")
                row.status, row.report_storage_key, row.report_body = "completed", key, report
                row.report_hash, row.report_sha256, row.report_size_bytes = frozen.cjson.keccak256(raw), "0x" + hashlib.sha256(raw).hexdigest(), len(raw)
                operation.status = "confirmed"
                audit(session, principal_id=row.creator_user_id, operation_id=row.operation_id,
                      action=OPERATION_KIND, outcome="completed", resource_type="ai_diagnostic", resource_id=row.id,
                      metadata={"signingEnabled": False, "reportHash": row.report_hash, "contextSource": row.context_source})
        except Exception as exc:
            # No raw exceptions, response bytes, credential paths or bearer material in logs/DB.
            code = exc.code if isinstance(exc, (CheckFailure, APIError)) else "AI_DIAGNOSTIC_DEPENDENCY_UNAVAILABLE"
            with factory() as session, session.begin():
                row = session.get(AIDiagnostic, diagnostic_id, with_for_update=True)
                if row is not None and row.status == "queued":
                    operation = session.get(Operation, row.operation_id, with_for_update=True)
                    row.status, row.error_code = "failed" if operation.status == "queued" else "requires_attention", str(code)[:80]
                    if operation.status == "queued":
                        operation.status, operation.error_code = "failed", str(code)[:80]
                        operation.error_status, operation.error_detail = 502, "Private AI diagnostic failed; no signature or chain action was created"

    @app.post("/v2/procurements/{procurement_id}/ai-diagnostics", tags=["ai-diagnostics"], status_code=202)
    def create(procurement_id: UUID, body: DiagnosticCreate, background: BackgroundTasks,
               idempotency_key: IdempotencyHeader = None, principal=Depends(current_principal), session=Depends(get_session)):
        key = validate_idempotency_key(idempotency_key)
        payload = {"procurementId": str(procurement_id), **body.model_dump(mode="json", by_alias=True)}
        with session.begin():
            ns = namespace(session, for_chain=True)
            procurement, project = _procurement(session, ns, procurement_id)
            authorize(project, principal)
            session.get(Procurement, procurement_id, with_for_update=True)
            lock_operation_key(session, ns.id, principal.user_id, OPERATION_KIND, key)
            existing = find_operation(session, namespace_id=ns.id, principal_id=principal.user_id, operation_kind=OPERATION_KIND, idempotency_key=key)
            if existing is not None:
                operation, _replayed = begin_operation(session, namespace_id=ns.id, principal_id=principal.user_id,
                    operation_kind=OPERATION_KIND, idempotency_key=key, validated_payload=payload)
                row = session.scalar(select(AIDiagnostic).where(AIDiagnostic.operation_id == operation.id))
                if row is None:
                    raise APIError(409, "ai_diagnostic_operation_incomplete", "Diagnostic operation requires attention")
                return envelope(ns, operation=_operation(operation, True), diagnostic=diagnostic_dto(row, procurement, ns, gateway))
            if setup_error:
                raise APIError(503, setup_error, "AI diagnostic connection is unavailable")
            chain = _require_gateway(gateway, gateway_error)
            value, resources, fingerprint, snapshot_at = build_input(session, ns, procurement, project, body, chain, file_store, frozen)
            operation, _replayed = begin_operation(session, namespace_id=ns.id, principal_id=principal.user_id,
                operation_kind=OPERATION_KIND, idempotency_key=key, validated_payload=payload)
            input_raw = frozen.cjson.canonical_bytes(value)
            row = AIDiagnostic(id=uuid4(), namespace_id=ns.id, procurement_id=procurement.id, operation_id=operation.id,
                creator_user_id=principal.user_id, purchase_order_version_id=body.purchase_order_version_id, status="queued",
                context_source=body.context_source, evidence_version=int(value["evidenceSnapshot"]["evidenceVersion"]),
                snapshot_id=value["evidenceSnapshot"]["snapshotId"], snapshot_created_at=snapshot_at,
                snapshot_fingerprint=fingerprint, evidence_hash=value["registrySnapshot"]["currentEvidenceHash"],
                input_json=value, input_hash=frozen.cjson.keccak256(input_raw), input_sha256="0x" + hashlib.sha256(input_raw).hexdigest())
            request_bytes(frozen, value, resources, operation.id, row.id)
            session.add(row)
            operation.status, operation.result_resource_type, operation.result_resource_id = "queued", "ai_diagnostic", row.id
            session.flush()
            result = envelope(ns, operation=_operation(operation, False), diagnostic=diagnostic_dto(row, procurement, ns, gateway))
        background.add_task(complete, row.id, resources)
        return result

    @app.get("/v2/procurements/{procurement_id}/ai-diagnostics", tags=["ai-diagnostics"])
    def listing(procurement_id: UUID, principal=Depends(current_principal), session=Depends(get_session)):
        with session.begin():
            ns = namespace(session)
            procurement, project = _procurement(session, ns, procurement_id)
            authorize(project, principal)
            rows = session.scalars(select(AIDiagnostic).where(AIDiagnostic.namespace_id == ns.id,
                AIDiagnostic.procurement_id == procurement.id).order_by(AIDiagnostic.created_at.desc(), AIDiagnostic.id.desc()).limit(20)).all()
            po_id = (procurement.source_versions_json or {}).get("poDocumentVersionId")
            po = session.get(DocumentVersion, UUID(po_id)) if po_id else None
            purchase_order = None if po is None else {"versionId": str(po.id), "contentType": po.content_type,
                "originalFilename": po.original_filename, "version": str(po.version)}
            return envelope(ns, availability=availability(), purchaseOrder=purchase_order,
                items=[diagnostic_dto(row, procurement, ns, gateway) for row in rows], nextCursor=None)

    @app.get("/v2/ai-diagnostics/{diagnostic_id}", tags=["ai-diagnostics"])
    def detail(diagnostic_id: UUID, principal=Depends(current_principal), session=Depends(get_session)):
        with session.begin():
            ns = namespace(session)
            row, procurement = load(diagnostic_id, session, ns, principal)
            operation = session.get(Operation, row.operation_id)
            return envelope(ns, operation=_operation(operation, False), diagnostic=diagnostic_dto(row, procurement, ns, gateway))

    @app.get("/v2/ai-diagnostics/{diagnostic_id}/report", tags=["ai-diagnostics"])
    def report_download(diagnostic_id: UUID, principal=Depends(current_principal), session=Depends(get_session)):
        with session.begin():
            ns = namespace(session)
            row, _procurement_value = load(diagnostic_id, session, ns, principal)
            if row.status != "completed":
                raise APIError(409, "ai_diagnostic_report_unavailable", "Diagnostic has no verified report yet")
            raw = file_store.resolve(row.report_storage_key).read_bytes()
            from .hashing import keccak256
            if (len(raw) != row.report_size_bytes or "0x" + hashlib.sha256(raw).hexdigest() != row.report_sha256
                    or "0x" + keccak256(raw) != row.report_hash):
                raise APIError(409, "ai_diagnostic_report_hash_mismatch", "Stored exact diagnostic report failed integrity verification")
            return Response(raw, media_type="application/json", headers={"Cache-Control": "no-store",
                "X-Report-SHA256": row.report_sha256, "X-Report-Hash": row.report_hash,
                "Content-Disposition": 'attachment; filename="ai-diagnostic-' + str(row.id) + '.cjson"'})
