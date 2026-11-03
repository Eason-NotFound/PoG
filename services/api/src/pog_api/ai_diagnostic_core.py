"""Local-only input/report validation and private storage for unsigned diagnostics."""
from __future__ import annotations

import base64
from datetime import UTC, date, datetime
import hashlib
import json
import os
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, field_validator, model_validator
from sqlalchemy import select

from .a2 import _document_version, _equal_bytes
from .ai_diagnostic_models import AIDiagnostic
from .ai_protocol.verify_service import CheckFailure
from .amounts import UInt256String
from .errors import APIError
from .hash_vectors import pre_evidence_hash
from .models import Document
from .schemas import SafeStrictStr, StrictModel
from typing import Literal

INTERFACE = "pog-ai-diagnostic-api-v0.1"
OPERATION_KIND = "ai.diagnostic.create"
VERSIONS = {
    "extractionSchemaVersion": "pog.ai.visible-fields.pixels/2",
    "modelId": "Qwen/Qwen3-VL-2B-Instruct",
    "modelProvider": "local_transformers",
    "modelVersion": "89644892e4d85e24eaac8bacfd4f463576704203",
    "promptVersion": "pog.ai.visible-fields-prompt/2.3",
    "ruleSetVersion": "diagnostic-single-po/1",
    "scorePolicyVersion": None,
    "serviceVersion": "ai-qwen-byte-gateway/1",
}
SOURCE_ROLES = (
    ("goodsRequestDocumentVersionId", "goods_request", "goodsRequestHash", 6, "ProcurementRequest"),
    ("poDocumentVersionId", "purchase_order", "poHash", 4, "PO"),
    ("requestDocumentVersionId", "request", "requestHash", 5, "ProcurementRequest"),
)


class DiagnosticContext(StrictModel):
    quantity: UInt256String
    quote_amount_atomic: UInt256String = Field(alias="quoteAmountAtomic")
    unit_price_limit_atomic: UInt256String = Field(alias="unitPriceLimitAtomic")
    category: SafeStrictStr = Field(min_length=1, max_length=160)
    period_start: date = Field(alias="periodStart")
    period_end: date = Field(alias="periodEnd")
    description: SafeStrictStr = Field(min_length=1, max_length=8192)

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def exact_date(cls, value):
        import re
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Diagnostic dates require explicit YYYY-MM-DD strings")
        return value

    @model_validator(mode="after")
    def complete_context(self):
        if any(int(value) <= 0 for value in (self.quantity, self.quote_amount_atomic, self.unit_price_limit_atomic)):
            raise ValueError("Quantity, quote and unit-price limit must be positive explicit values")
        if self.period_start > self.period_end:
            raise ValueError("periodStart must not follow periodEnd")
        if not self.category.strip() or not self.description.strip():
            raise ValueError("Category and description must be explicit nonblank text")
        return self


class DiagnosticCreate(StrictModel):
    purchase_order_version_id: UUID = Field(alias="purchaseOrderVersionId")
    diagnostic_context: DiagnosticContext = Field(alias="diagnosticContext")
    context_source: Literal["user_declared", "demo_generated"] = Field(default="user_declared", alias="contextSource")
    confirm: StrictBool

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if self.confirm is not True:
            raise ValueError("Explicit evidence-upload and diagnostic confirmation is required")
        return self


def authorize(project, principal):
    allowed = ((principal.role == "foundation" and principal.user_id == project.foundation_user_id)
               or (principal.role == "human_approver" and principal.user_id == project.human_approver_user_id))
    if not allowed:
        raise APIError(403, "ai_diagnostic_forbidden", "Only the owning Foundation or assigned human approver may access this private diagnostic")


def binding(ns, gateway):
    if gateway is None:
        raise APIError(503, "ai_diagnostic_chain_unavailable", "Diagnostic deployment binding is unavailable")
    return {"namespaceId": str(ns.id), "runId": ns.run_id, "instanceId": ns.instance_id,
            "chainId": str(ns.chain_id), "registry": gateway.contract_address("PoGRegistryV2").lower()}


def diagnostic_dto(row, procurement, ns, gateway):
    report = row.report_body
    return {
        "id": str(row.id), "operationId": str(row.operation_id), "projectId": str(procurement.project_id),
        "procurementId": str(row.procurement_id), "purchaseOrderVersionId": str(row.purchase_order_version_id),
        "binding": binding(ns, gateway), "createdAt": row.created_at.isoformat(),
        "status": row.status, "stage": 0, "purpose": "diagnostic", "signingEnabled": False,
        "contextSource": row.context_source, "syntheticInput": row.context_source == "demo_generated",
        "modelReal": row.status == "completed", "realAI": row.status == "completed",
        "reportProvenance": "real_qwen_diagnostic" if row.status == "completed" else None,
        "evidenceVersion": str(row.evidence_version), "evidenceHash": row.evidence_hash,
        "inputHash": row.input_hash, "reportHash": row.report_hash,
        "reportSha256": row.report_sha256, "reportSizeBytes": row.report_size_bytes,
        "contentType": "application/json" if report else None,
        "diagnosticContext": {
            **{key: row.input_json["procurementData"][key] for key in ("quantity", "quoteAmountAtomic", "category", "description")},
            **{key: row.input_json["projectPolicy"][key] for key in ("unitPriceLimitAtomic", "periodStart", "periodEnd")},
        },
        "summary": None if report is None else {
            "text": report["summary"], "outcome": report["outcome"], "riskScoreBps": report["riskScoreBps"],
            "completeness": report["completeness"], "findings": report["findings"], "missingInputs": report["missingInputs"],
        },
        "provenance": {
            "executionMode": report["executionMode"] if report else None,
            "versions": report["versions"] if report else None,
            "contextSource": "user_declared_diagnostic_context" if row.context_source == "user_declared" else "demo_generated_diagnostic_context",
            "budgetSource": "registry_procurement_budget_cap", "reviewScope": "single_po_visible_fields",
            "allDocumentsReviewed": False,
        },
        "errorCode": row.error_code,
    }


def _hex(value):
    return "0x" + bytes(value).hex()


def build_input(session, ns, procurement, project, body, gateway, file_store, frozen):
    """Freeze local same-block Registry facts and original bytes without network/model calls."""
    head = gateway.w3.eth.get_block("latest")
    number, block_hash = int(head["number"]), _hex(head["hash"])
    view = gateway.call("PoGRegistryV2", "getProcurement", procurement.business_id, block_identifier=number)
    project_view = gateway.call("PoGRegistryV2", "getProject", project.business_id, block_identifier=number)
    if int(view[-1]) not in {1, 2, 3}:
        raise APIError(409, "ai_diagnostic_pre_state_required", "Only a confirmed PRE purchase-order state supports this diagnostic")
    if not (_equal_bytes(view[0], procurement.business_id) and _equal_bytes(view[1], project.business_id)
            and _equal_bytes(project_view[0], project.business_id)
            and project_view[1].lower() == project.foundation_wallet.lower()
            and project_view[2].lower() == project.recipient_wallet.lower()
            and view[2].lower() == procurement.vendor_wallet.lower()
            and int(view[3]) == int(procurement.budget_cap_atomic) and int(project_view[4]) == 6):
        raise APIError(409, "ai_diagnostic_registry_binding", "Registry identity or budget differs from this procurement")
    sources = procurement.source_versions_json or {}
    documents, commitments, resources = [], [], []
    for source, category, role, index, kind in SOURCE_ROLES:
        try:
            version_id = UUID(sources[source])
        except (KeyError, ValueError, TypeError):
            raise APIError(422, "ai_diagnostic_original_sources_required", "Confirmed PO, request and goods-request original versions are required") from None
        if category == "purchase_order" and version_id != body.purchase_order_version_id:
            raise APIError(409, "ai_diagnostic_po_version_mismatch", "Select the exact purchase-order version already recorded on chain")
        version = _document_version(session, procurement, version_id, category)
        document = session.get(Document, version.document_id)
        expected = {"versionId": str(version.id), "hash": version.keccak256_hex,
                    "uploaderId": str(version.uploaded_by_user_id)}
        if (document.namespace_id != ns.id or not version.referenced
                or version.uploaded_by_user_id != project.foundation_user_id
                or sources.get("bindings", {}).get(source) != expected
                or not _equal_bytes(view[index], "0x" + version.keccak256_hex)):
            raise APIError(409, "ai_diagnostic_source_binding", "Original evidence provenance or Registry commitment differs")
        if version.content_type not in {"image/png", "image/jpeg"}:
            raise APIError(422, "ai_diagnostic_image_required", "This single-PO diagnostic currently supports PNG or JPEG evidence only")
        raw = file_store.resolve(version.storage_key).read_bytes()
        if (len(raw) != version.size_bytes or len(raw) > 10_485_760
                or hashlib.sha256(raw).hexdigest() != version.sha256_hex
                or frozen.cjson.keccak256(raw) != "0x" + version.keccak256_hex):
            raise APIError(409, "ai_diagnostic_document_hash_mismatch", "Immutable evidence no longer matches its stored hashes")
        ref = {"evidenceId": str(document.id), "kind": kind, "version": str(version.version),
               "contentSha256": "0x" + version.sha256_hex, "leafKeccak256": "0x" + version.keccak256_hex, "pages": None}
        documents.append({**ref, "documentId": str(document.id), "documentVersionId": str(version.id),
                          "category": category, "contentType": version.content_type, "sizeBytes": len(raw)})
        commitments.append({"evidenceRole": role, "commitmentHash": _hex(view[index]), "scheme": "raw_file_keccak256",
                            "documentVersionIds": [str(version.id)], "sourceRecordId": str(version.id), "recipeVersion": None})
        resources.append({"documentVersionId": str(version.id), "bytesBase64": base64.b64encode(raw).decode("ascii")})
    documents.sort(key=lambda d: (d["evidenceId"], int(d["version"])))
    commitments.sort(key=lambda c: c["evidenceRole"])
    resources.sort(key=lambda r: r["documentVersionId"])
    registry = {
        "verified": True, "blockNumber": str(number), "blockHash": block_hash,
        "projectId": project.business_id, "procurementId": procurement.business_id,
        "foundation": project_view[1].lower(), "recipient": project_view[2].lower(), "vendor": view[2].lower(),
        "asset": project_view[3].lower(), "budgetCap": str(view[3]), "poHash": _hex(view[4]),
        "requestHash": _hex(view[5]), "goodsRequestHash": _hex(view[6]), "currentEvidenceHash": _hex(view[7]),
        "state": {1: "PORecorded", 2: "PreAssessed", 3: "ReserveApprovalPending"}[int(view[-1])],
    }
    actual = pre_evidence_hash(project_id=registry["projectId"], procurement_id=registry["procurementId"],
        foundation=registry["foundation"], recipient=registry["recipient"], vendor=registry["vendor"], asset=registry["asset"],
        budget_cap=int(registry["budgetCap"]), po_hash=registry["poHash"], request_hash=registry["requestHash"], goods_request_hash=registry["goodsRequestHash"])
    if actual != registry["currentEvidenceHash"] or not gateway.canonical(number, block_hash):
        raise APIError(409, "ai_diagnostic_registry_unverified", "Same-block Registry evidence or canonical block could not be verified")
    fingerprint = hashlib.sha256(frozen.cjson.canonical_bytes({"evidenceHash": actual, "documents": documents, "commitments": commitments})).hexdigest()
    previous = session.scalar(select(AIDiagnostic).where(AIDiagnostic.namespace_id == ns.id,
        AIDiagnostic.procurement_id == procurement.id).order_by(AIDiagnostic.evidence_version.desc(), AIDiagnostic.created_at.desc()).limit(1))
    if previous is not None and previous.snapshot_fingerprint == fingerprint:
        evidence_version, snapshot_id, snapshot_at = int(previous.evidence_version), previous.snapshot_id, previous.snapshot_created_at
    else:
        evidence_version = 1 if previous is None else int(previous.evidence_version) + 1
        snapshot_id, snapshot_at = str(uuid4()), datetime.now(UTC)
    context = body.diagnostic_context.model_dump(mode="json", by_alias=True)
    value = {
        "schemaVersion": "pog.ai.input/0.2-candidate", "stage": 0, "claimAlias": None,
        "projectPolicy": {"version": body.context_source + "-diagnostic-context/1", "assetDecimals": 6,
            "budgetAtomic": registry["budgetCap"], **{key: context[key] for key in ("category", "periodStart", "periodEnd", "unitPriceLimitAtomic")}},
        "vendorContext": {"vendorId": registry["vendor"], "identityStatus": "not_verified", "accountHistorySnapshot": None, "relatedPartySnapshot": None},
        "comparisonContext": {"quoteComparatorsSnapshot": None, "crossProjectDuplicateSnapshot": None, "procurementHistorySnapshot": None},
        "registrySnapshot": registry,
        "documents": [{key: d[key] for key in ("evidenceId", "kind", "version", "contentSha256", "leafKeccak256", "pages")} for d in documents],
        "evidenceSnapshot": {"snapshotId": snapshot_id, "evidenceVersion": str(evidence_version),
            "createdAt": snapshot_at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"), "owner": "api",
            "deployment": {key: binding(ns, gateway)[key] for key in ("runId", "instanceId", "chainId", "registry")},
            "documentVersions": documents, "commitments": commitments, "externalSnapshots": []},
        "procurementData": {"requestVersion": next(d["version"] for d in documents if d["category"] == "request"),
            "poVersion": next(d["version"] for d in documents if d["category"] == "purchase_order"),
            **{key: context[key] for key in ("category", "description", "quantity", "quoteAmountAtomic")}},
    }
    frozen.schema("input", value)
    return value, resources, fingerprint, snapshot_at


def request_bytes(frozen, value, resources, operation_id, diagnostic_id):
    raw = frozen.cjson.canonical_bytes(value)
    request = {"schemaVersion": "pog.ai.byte-request/1", "operationId": str(operation_id), "aiRequestId": str(diagnostic_id),
               "purpose": "diagnostic", "attempt": 1, "remainingMillis": 120_000,
               "payloadHash": "0x" + hashlib.sha256(raw).hexdigest(), "inputHash": frozen.cjson.keccak256(raw),
               "inputBytesBase64": base64.b64encode(raw).decode("ascii"), "documents": resources, "externalSnapshots": []}
    wire = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    frozen.request(wire)
    return request, wire


def save_report(file_store, namespace_id, diagnostic_id, raw):
    namespace_id, diagnostic_id = UUID(str(namespace_id)), UUID(str(diagnostic_id))
    directory = file_store.root / str(namespace_id) / "ai-diagnostics"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / (str(diagnostic_id) + ".cjson")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise CheckFailure("SAVED_REPORT_BYTES_MISMATCH")
    return str(path.relative_to(file_store.root))
