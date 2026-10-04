"""Fresh payment source guards shared by HTTP and pre-broadcast workers."""
import hashlib
import json
from uuid import UUID
from eth_utils import keccak
from sqlalchemy import select
from web3 import Web3
from .errors import APIError
from .models import Procurement, Project, SigningRequest
from .payment_models import PaymentResource, PaymentEvidence
from .payment_domain import assert_resource_canonical, canonical_evidence_bytes
from .tail_signing import hex32


def validate_settlement_sources(session, ns, proc, project, gate, *, recorded=False):
    rows = session.scalars(select(PaymentResource).where(PaymentResource.namespace_id==ns.id,
        PaymentResource.procurement_id==proc.id,PaymentResource.kind.in_(("redemption","supplier_payment"))).with_for_update()).all()
    resources={r.kind:r for r in rows}
    if set(resources)!={"redemption","supplier_payment"}:
        raise APIError(409,"payment_reconciliation_required","Both reconciled payment resources are required")
    for r in rows:
        assert_resource_canonical(session,ns,r,gate)
        if r.project_id!=project.id or r.actor_user_id!=project.foundation_user_id:
            raise APIError(409,"payment_source_changed","Payment parties differ from procurement")
    evidence={e.kind:e for e in session.scalars(select(PaymentEvidence).where(
        PaymentEvidence.namespace_id==ns.id,PaymentEvidence.procurement_id==proc.id))}
    if set(evidence)!={"conversion","supplier_payment"}:
        raise APIError(409,"payment_evidence_required","Both immutable payment evidence artifacts are required")
    hashes=[]
    redemption=resources["redemption"]
    for kind in ("conversion","supplier_payment"):
        e=evidence[kind]
        r=redemption if kind=="conversion" else resources["supplier_payment"]
        try:
            m=json.loads(e.canonical_bytes)
            valid=(canonical_evidence_bytes(m)==e.canonical_bytes
                and hashlib.sha256(e.canonical_bytes).hexdigest()==e.sha256_hex
                and keccak(e.canonical_bytes).hex()==e.keccak256_hex and e.resource_id==r.id
                and m["procurementId"]==str(proc.id) and m["invoiceDocumentVersionId"]==str(r.invoice_version_id)
                and m["redemptionResourceId"]==str(redemption.id) and m["redemptionProof"]==redemption.chain_proof
                and m["releaseProof"]==redemption.source_material["releaseProof"]
                and m["amountAtomic"]==str(int(proc.invoice_amount_atomic)) and m["invoiceHash"]==proc.invoice_hash)
        except (KeyError,ValueError,TypeError):
            valid=False
        if not valid:
            raise APIError(409,"payment_evidence_invalid","Original evidence differs from reconciled sources")
        hashes.append("0x"+e.keccak256_hex)
    if recorded:
        view=gate.call("PoGRegistryV2","getProcurement",proc.business_id)
        if [hex32(view[16]),hex32(view[17])]!=hashes:
            raise APIError(409,"payment_evidence_invalid","Recorded evidence differs from original bytes")
    return hashes


def validate_tail_step(session,ns,step,operation,gate):
    from .a2 import validate_original_sources, _validate_signing_fresh
    action=step.detail["action"]
    try:
        proc=session.get(Procurement,UUID(step.detail["procurementUuid"]))
        project=session.get(Project,proc.project_id)
    except (KeyError,ValueError,TypeError,AttributeError):
        raise APIError(409,"signing_material_stale","Original queued procurement is unproven")
    if proc.namespace_id!=ns.id or project.namespace_id!=ns.id:
        raise APIError(409,"signing_material_stale","Queued namespace changed")
    validate_original_sources(session,proc)
    if action in {"assessment.ai_final","approval.release","approval.settlement"}:
        requests=session.scalars(select(SigningRequest).where(SigningRequest.submitted_operation_id==operation.id)).all()
        if len(requests)!=1:
            raise APIError(409,"signing_material_stale","Original queued signing request is not unique")
        request=requests[0]
        if request.procurement_id!=proc.id or request.namespace_id!=ns.id:
            raise APIError(409,"signing_material_stale","Queued signing target changed")
        validate_original_sources(session,proc,request.context_json.get("sourceVersionIds"))
        _validate_signing_fresh(gate,request,proc,project)
    if action in {"approval.settlement","settlement.execute","procurement.settlement"}:
        hashes=validate_settlement_sources(session,ns,proc,project,gate,recorded=action!="procurement.settlement")
        if action=="procurement.settlement" and step.detail["args"]!=[proc.business_id,*hashes]:
            raise APIError(409,"payment_evidence_invalid","Queued hashes differ from original bytes")
    if action in {"release.execute","settlement.execute"}:
        method="executeRelease" if action=="release.execute" else "executeSettlementConfirmation"
        try:
            getattr(gate.contracts["ProcurementEscrowV2"].functions,method)(proc.business_id).call(
                {"from":Web3.to_checksum_address(step.detail["caller"])})
        except Exception as exc:
            from .chain import ChainUnavailable
            from requests.exceptions import RequestException
            if isinstance(exc,(ChainUnavailable,RequestException,TimeoutError,ConnectionError)):
                raise
            raise APIError(409,"human_approval_not_current","Current terms or live human threshold do not permit execution") from exc
