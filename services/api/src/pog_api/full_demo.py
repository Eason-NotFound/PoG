"""Opt-in full local simulation: explicit actions and private recovery reads."""
from __future__ import annotations
import base64
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import Field, model_validator
from sqlalchemy import func, select

from .a2 import (IdempotencyHeader, _operation, _procurement, _project, _queue,
                 _require_gateway, _must_match, _replay_resource, _replay_exact,
                 validate_original_sources)
from .amounts import UInt256String
from .errors import APIError
from .idempotency import begin_operation, validate_idempotency_key
from .models import (Document, DocumentVersion, Operation, OperationStep, Procurement, PolicyProjection, DeploymentInstance,
                     Project, SigningRequest, WalletAuthorization, User)
from .payment_models import PaymentResource, FundedClaim, PaymentEvidence, SimHKDAccount
from .payment_domain import (binding, account_dto, cents_to_atomic, atomic_to_cents,
                             reserve_funding, reserve_funded_donation, reserve_redemption,
                             reserve_supplier_payment, resource_dto, payment_status)
from .schemas import StrictModel, SubmitSignedRequest, EmptyMutation
from .tail_confirmation import bundle_key
from .tail_signing import hex32, ZERO
from web3 import Web3
from .full_checks import validate_settlement_sources, validate_tail_step

INTERFACE = "pog-full-demo-api-v0.1"


class FundingRequest(SubmitSignedRequest):
    project_id: UUID = Field(alias="projectId")
    hkd_cents: UInt256String = Field(alias="hkdCents")


class FundedDonationRequest(SubmitSignedRequest):
    funding_operation_id: UUID = Field(alias="fundingOperationId")
    amount_atomic: UInt256String = Field(alias="amountAtomic")


class QuoteRequest(StrictModel):
    direction: Literal["hkd_to_mock", "mock_to_hkd"]
    project_id: UUID | None = Field(default=None, alias="projectId")
    procurement_id: UUID | None = Field(default=None, alias="procurementId")
    hkd_cents: UInt256String | None = Field(default=None, alias="hkdCents")

    @model_validator(mode="after")
    def exact_scope(self):
        if self.direction == "hkd_to_mock":
            if self.project_id is None or self.hkd_cents is None or self.procurement_id is not None:
                raise ValueError("Funding quote requires only projectId and hkdCents")
        elif self.procurement_id is None or self.project_id is not None or self.hkd_cents is not None:
            raise ValueError("Redemption quote requires only procurementId")
        return self


CAP_ROLES = {
    "integration.context.read": None, "mock.account.read": {"donor","foundation"},
    "mock.exchange.quote": {"donor","foundation"}, "donor.funding.convert": {"donor"},
    "mock.exchange.list": {"donor","foundation"}, "mock.exchange.read": {"donor","foundation"},
    "donation.funded.deposit": {"donor"}, "operation.list": None,
    "authorization.list": {"recipient","human_approver","service_ai"},
    "procurement.workspace.read": {"foundation","recipient","human_approver"},
    "release.execute": {"foundation","human_approver"},
    "release.human.approve": {"human_approver"}, "settlement.human.approve": {"human_approver"},
    "foundation.redemption.convert": {"foundation"}, "supplier.payment.execute": {"foundation"},
    "settlement.evidence.record": {"foundation"}, "settlement.execute": {"foundation","human_approver"},
    "procurement.payment.status": {"foundation","recipient","human_approver"},
    "project.progress.read": {"foundation","recipient","human_approver","donor"},
    "payment.evidence.read": {"foundation","human_approver"},
    "payment.evidence.content": {"foundation","human_approver"},
    "document.version.read": {"foundation","recipient","human_approver"},
    "document.version.content": {"foundation","recipient","human_approver"},
    "project.draft.create": {"foundation"}, "project.chain.create": {"foundation"},
    "procurement.draft.create": {"foundation"}, "procurement.chain.create": {"foundation"},
    "document.upload": {"foundation","recipient"}, "purchase-order.record": {"foundation"},
    "invoice-goods.record": {"foundation"}, "reserve.execute": {"foundation","human_approver"},
    "authorization.prepare": {"recipient","human_approver","service_ai"},
    "authorization.sign": {"recipient","human_approver","service_ai"},
    "authorization.submit-signed": {"recipient","human_approver","service_ai"},
    "session.login": None, "session.logout": None, "identity.read": None, "deployment.read": None,
    "project.list": None, "project.read": None, "project.ledger.read": {"donor","foundation","recipient","human_approver"},
    "procurement.list": {"foundation","recipient","human_approver"}, "procurement.read": {"foundation","recipient","human_approver"},
    "document.read": {"foundation","recipient","human_approver"}, "document.content.read": {"foundation","recipient","human_approver"},
    "procurement.po.record": {"foundation"}, "procurement.invoice.record": {"foundation"},
    "authorization.read": {"recipient","human_approver","service_ai"},
    "authorization.demo.sign": {"recipient","human_approver","service_ai"},
    "authorization.external.submit": {"recipient","human_approver","service_ai"},
    "authorization.saved.submit": {"recipient","human_approver","service_ai"},
    "procurement.reserve.execute": {"foundation","human_approver"}, "operation.read": None,
}


def private(project, principal, *, evidence=False):
    allowed = ((principal.role == "foundation" and project.foundation_user_id == principal.user_id)
               or (principal.role == "human_approver" and project.human_approver_user_id == principal.user_id)
               or (not evidence and principal.role == "recipient" and project.recipient_user_id == principal.user_id))
    if not allowed:
        raise APIError(403, "project_forbidden", "Resource is outside this principal's private scope")


def owner(project, principal):
    if principal.role != "foundation" or project.foundation_user_id != principal.user_id:
        raise APIError(403, "role_forbidden", "Only the owning Foundation may perform this action")


def capabilities(principal, enabled=True):
    result = {}
    for action, roles in CAP_ROLES.items():
        allowed = enabled and (roles is None or principal.role in roles)
        result[action] = {"implemented": True, "enabled": allowed,
                          "reasonCode": None if allowed else "role_forbidden" if enabled else "full_demo_disabled"}
    for action in ("ai.pre.detect","ai.final.detect"):
        result[action] = {"implemented": False, "enabled": False, "reasonCode": "ai_service_unavailable"}
    result["donation.deposit"] = {"implemented":True,"enabled":False,"reasonCode":"funded_credit_required"}
    return result


def cursor_query(query, model, cursor, ns, principal):
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if value["namespace"] != str(ns.id) or value["actor"] != str(principal.user_id):
                raise ValueError("Cursor scope mismatch")
            query = query.where(model.id > UUID(value["after"]))
        except (ValueError, KeyError, TypeError):
            raise APIError(422, "cursor_invalid", "Cursor is invalid for this principal and namespace")
    return query.order_by(model.id)


def next_cursor(rows, limit, ns, principal):
    if len(rows) <= limit:
        return None
    return base64.urlsafe_b64encode(json.dumps(
        {"namespace": str(ns.id), "actor": str(principal.user_id), "after": str(rows[limit-1].id)},
        separators=(",",":")).encode()).decode().rstrip("=")


def install_full_routes(app, *, get_session, current_principal, namespace, gateway, gateway_error, settings, file_store):
    original_namespace = namespace
    def namespace(session, *, for_chain=False):
        value = original_namespace(session, for_chain=for_chain)
        session.get(DeploymentInstance, value.id, with_for_update=True)
        return value

    def gate():
        if not settings.full_demo_enabled:
            raise APIError(403, "full_demo_disabled", "Opt-in local simulation is disabled")
        return _require_gateway(gateway, gateway_error)

    def envelope(ns, chain, **data):
        return {"interfaceId": INTERFACE, "mode": "simulation", "binding": binding(ns,chain), **data}

    @app.get("/v2/integration-context", tags=["simulation"])
    def context(principal=Depends(current_principal), session=Depends(get_session)):
        chain = gate()
        with session.begin():
            ns = namespace(session, for_chain=True)
            defaults = None
            provisioned = session.scalar(select(SimHKDAccount.id).where(SimHKDAccount.namespace_id == ns.id,
                SimHKDAccount.owner_user_id == principal.user_id))
            if principal.role == "foundation" and provisioned:
                users = {}
                for role, manifest_role in (("recipient","recipient"),("human_approver","humanApprover")):
                    row = session.scalar(select(WalletAuthorization).join(User).where(
                        WalletAuthorization.role_name == role, WalletAuthorization.active.is_(True),
                        User.active.is_(True), func.lower(WalletAuthorization.wallet_address) == chain.roles[manifest_role].lower()))
                    if row:
                        users[role] = str(row.user_id)
                if len(users) == 2:
                    defaults = {"recipientUserId": users["recipient"], "humanApproverUserId": users["human_approver"],
                                "supplierWallet": chain.roles["vendor"]}
            return envelope(ns,chain,capabilities=capabilities(principal),defaults=defaults)

    @app.get("/v2/mock-hkd/accounts/me", tags=["simulation"])
    def account(principal=Depends(current_principal), session=Depends(get_session)):
        chain = gate()
        with session.begin():
            ns = namespace(session, for_chain=True)
            return envelope(ns,chain,account=account_dto(session,ns,principal))

    @app.post("/v2/mock-exchanges/quote", tags=["simulation"])
    def quote(body: QuoteRequest, principal=Depends(current_principal), session=Depends(get_session)):
        chain = gate()
        with session.begin():
            ns = namespace(session, for_chain=True)
            reason = None
            if body.direction == "hkd_to_mock":
                if principal.role != "donor":
                    raise APIError(403,"role_forbidden","Only Donor may quote funding")
                project = _project(session,ns,body.project_id)
                cents, amount = cents_to_atomic(body.hkd_cents)
                account = account_dto(session,ns,principal)
                state = chain.call("PoGRegistryV2","getProject",project.business_id)
                if int(state[7]) != 0:
                    reason = "project_not_active"
                elif int(account["availableHkdCents"]) < cents:
                    reason = "sim_hkd_insufficient"
            else:
                proc, project = _procurement(session,ns,body.procurement_id)
                owner(project,principal)
                validate_original_sources(session,proc)
                view = chain.call("PoGRegistryV2","getProcurement",proc.business_id)
                cents, amount = atomic_to_cents(view[11])
                if int(view[21]) != 9 or int(view[20]) != 0:
                    reason = "canonical_release_required"
                if session.scalar(select(PaymentResource.id).where(PaymentResource.namespace_id == ns.id,
                    PaymentResource.procurement_id == proc.id, PaymentResource.kind == "redemption")):
                    reason = "redemption_already_reserved"
            return envelope(ns,chain,executed=False,quote={"direction":body.direction,
                "hkdCents":str(cents),"amountAtomic":str(amount),"rate":"1 HKD = 1 mHKD",
                "feeHkdCents":"0","eligible":reason is None,"reasonCode":reason})

    def add_steps(session, operation, details):
        operation.status = "queued"
        for index, (executor,detail) in enumerate(details):
            session.add(OperationStep(operation_id=operation.id,step_index=index,executor=executor,
                kind=detail["action"],status="queued",detail=detail))

    def reserve_checked(session, operation, function, *args):
        try:
            return function(*args)
        except APIError as exc:
            if exc.code != "funding_requires_attention":
                raise
            # This exact error follows a DB-only freeze and precedes any new
            # hold/allocation. Commit the freeze, not an economic side effect.
            operation.status = "failed"
            operation.error_code, operation.error_status, operation.error_detail = exc.code, exc.status_code, exc.message
            session.commit()
            raise

    @app.post("/v2/mock-exchanges/funding", status_code=202, tags=["simulation"])
    def funding(body: FundingRequest, principal=Depends(current_principal), session=Depends(get_session), idempotency_key: IdempotencyHeader=None):
        chain = gate()
        key = validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session,for_chain=True)
            project = _project(session,ns,body.project_id)
            payload = body.model_dump(mode="json",by_alias=True)
            old = _replay_exact(session,ns,principal,key,"payment.funding",payload)
            if old:
                resource = session.scalar(select(PaymentResource).where(PaymentResource.operation_id==old.id))
                if resource is None:
                    raise APIError(old.error_status or 409, old.error_code or "payment_operation_failed", old.error_detail or "Original operation did not create a resource", operation_id=str(old.id))
                return envelope(ns,chain,operation=_operation(old,True),exchange=resource_dto(session,resource))
            operation,_ = begin_operation(session,namespace_id=ns.id,principal_id=principal.user_id,
                operation_kind="payment.funding",idempotency_key=key,validated_payload=payload)
            resource = reserve_checked(session,operation,reserve_funding,session,ns,principal,project,operation,body.hkd_cents,chain)
            common = {"projectUuid":str(project.id),"paymentResourceUuid":str(resource.id)}
            add_steps(session,operation,[
                ("chain",{**common,"action":"payment.mint","caller":chain.roles["relayer"],
                    "args":[principal.wallet_address,str(int(resource.amount_atomic))],"expectedEvent":"Transfer"}),
                ("payment",{**common,"action":"payment.finalize_funding"})])
            return envelope(ns,chain,operation=_operation(operation,False),exchange=resource_dto(session,resource))

    @app.post("/v2/projects/{project_id}/funded-donations", status_code=202, tags=["simulation"])
    def funded(project_id: UUID,body: FundedDonationRequest,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        chain,key = gate(),validate_idempotency_key(idempotency_key)
        with session.begin():
            ns = namespace(session,for_chain=True)
            project = _project(session,ns,project_id)
            payload = {"projectId":str(project_id),**body.model_dump(mode="json",by_alias=True)}
            old = _replay_exact(session,ns,principal,key,"payment.funded_donation",payload)
            if old:
                return envelope(ns,chain,operation=_operation(old,True))
            operation,_ = begin_operation(session,namespace_id=ns.id,principal_id=principal.user_id,
                operation_kind="payment.funded_donation",idempotency_key=key,validated_payload=payload)
            claim = reserve_checked(session,operation,reserve_funded_donation,session,ns,principal,project,operation,body.funding_operation_id,body.amount_atomic,chain)
            common={"projectUuid":str(project.id),"fundedClaimUuid":str(claim.id),"caller":principal.wallet_address}
            add_steps(session,operation,[
                ("chain",{**common,"action":"donation.approve","args":[chain.contract_address("ProcurementEscrowV2"),body.amount_atomic],"expectedEvent":"Approval"}),
                ("chain",{**common,"action":"donation.deposit","args":[project.business_id,body.amount_atomic],"expectedEvent":"Donated"}),
                ("payment",{**common,"action":"payment.finalize_donation"})])
            return envelope(ns,chain,operation=_operation(operation,False))

    def reserve_payment(procurement_id, body, principal, session, key, kind):
        chain,key=gate(),validate_idempotency_key(key)
        with session.begin():
            ns=namespace(session,for_chain=True)
            proc,project=_procurement(session,ns,procurement_id)
            owner(project,principal)
            payload={"procurementId":str(procurement_id),"confirm":True}
            old=_replay_exact(session,ns,principal,key,"payment."+kind,payload)
            if old:
                resource=session.scalar(select(PaymentResource).where(PaymentResource.operation_id==old.id))
                return envelope(ns,chain,operation=_operation(old,True),**{"exchange" if kind=="redemption" else "payment":resource_dto(session,resource)})
            validate_original_sources(session,proc)
            operation,_=begin_operation(session,namespace_id=ns.id,principal_id=principal.user_id,
                operation_kind="payment."+kind,idempotency_key=key,validated_payload=payload)
            resource=(reserve_redemption if kind=="redemption" else reserve_supplier_payment)(session,ns,principal,proc,project,operation,chain)
            common={"projectUuid":str(project.id),"procurementUuid":str(proc.id),"paymentResourceUuid":str(resource.id)}
            details=[("payment",{**common,"action":"payment.supplier_pay"})]
            if kind=="redemption":
                details=[("chain",{**common,"action":"payment.redeem","caller":principal.wallet_address,
                    "args":[chain.roles["mockRedemption"],str(int(resource.amount_atomic))],"expectedEvent":"Transfer"}),
                    ("payment",{**common,"action":"payment.finalize_redemption"})]
            add_steps(session,operation,details)
            return envelope(ns,chain,operation=_operation(operation,False),**{"exchange" if kind=="redemption" else "payment":resource_dto(session,resource)})

    @app.post("/v2/procurements/{procurement_id}/mock-redemption",status_code=202,tags=["simulation"])
    def redemption(procurement_id: UUID,body: SubmitSignedRequest,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        return reserve_payment(procurement_id,body,principal,session,idempotency_key,"redemption")

    @app.post("/v2/procurements/{procurement_id}/mock-supplier-payment",status_code=202,tags=["simulation"])
    def pay(procurement_id: UUID,body: SubmitSignedRequest,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        return reserve_payment(procurement_id,body,principal,session,idempotency_key,"supplier_payment")

    def tail_execute(procurement_id,principal,session,key,action):
        chain,key=gate(),validate_idempotency_key(key)
        with session.begin():
            ns=namespace(session,for_chain=True)
            proc,project=_procurement(session,ns,procurement_id)
            private(project,principal,evidence=True)
            if action=="procurement.settlement":
                owner(project,principal)
            old=_replay_resource(session,ns,principal,key,action,{},proc)
            if old:
                return {"operation":_operation(old,True)}
            if action=="procurement.settlement":
                validate_settlement_sources(session,ns,proc,project,chain)
            validate_original_sources(session,proc)
            view=chain.call("PoGRegistryV2","getProcurement",proc.business_id)
            wanted={"release.execute":8,"procurement.settlement":9,"settlement.execute":11}[action]
            if int(view[21])!=wanted or int(view[20])!=0:
                raise APIError(409,"procurement_state_conflict","Current canonical state does not permit this explicit action")
            args=[proc.business_id]
            if action=="procurement.settlement":
                resources=session.scalars(select(PaymentResource).where(PaymentResource.namespace_id==ns.id,
                    PaymentResource.procurement_id==proc.id,PaymentResource.kind.in_(("redemption","supplier_payment")))).all()
                if len(resources)!=2 or any(r.status!="reconciled" for r in resources):
                    raise APIError(409,"payment_reconciliation_required","Both conversion and supplier payment must be reconciled")
                evidence={e.kind:e for e in session.scalars(select(PaymentEvidence).where(
                    PaymentEvidence.namespace_id==ns.id,PaymentEvidence.procurement_id==proc.id))}
                if set(evidence)!={"conversion","supplier_payment"}:
                    raise APIError(409,"payment_evidence_required","Immutable conversion and payment bytes are required")
                for e in evidence.values():
                    if hashlib.sha256(e.canonical_bytes).hexdigest()!=e.sha256_hex:
                        raise APIError(409,"payment_evidence_invalid","Evidence integrity failed")
                args += ["0x"+evidence["conversion"].keccak256_hex,"0x"+evidence["supplier_payment"].keccak256_hex]
            from types import SimpleNamespace
            validate_tail_step(session,ns,SimpleNamespace(detail={"action":action,"caller":principal.wallet_address,
                "args":args,"procurementUuid":str(proc.id)}),None,chain)
            event={"release.execute":"FundsReleasedToFoundation","procurement.settlement":"SettlementEvidenceRecorded","settlement.execute":"MockPaymentConfirmed"}[action]
            operation,replayed=_queue(session,ns=ns,principal=principal,key=key,kind=action,payload={"resourceId":str(proc.id),"businessId":proc.business_id,"body":{}},
                resource_type="procurement",resource_id=proc.id,steps=[{"action":action,"caller":principal.wallet_address,"args":args,"expectedEvent":event,"projectUuid":str(project.id),"procurementUuid":str(proc.id)}])
            proc.chain_status={"release.execute":"release_queued","procurement.settlement":"settlement_queued","settlement.execute":"settlement_confirmation_queued"}[action]
            return {"operation":_operation(operation,replayed)}

    @app.post("/v2/procurements/{procurement_id}/chain/release",status_code=202,tags=["simulation"])
    def release(procurement_id: UUID,body: EmptyMutation,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        return tail_execute(procurement_id,principal,session,idempotency_key,"release.execute")

    @app.post("/v2/procurements/{procurement_id}/chain/settlement-evidence",status_code=202,tags=["simulation"])
    def settlement_evidence(procurement_id: UUID,body: EmptyMutation,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        return tail_execute(procurement_id,principal,session,idempotency_key,"procurement.settlement")

    @app.post("/v2/procurements/{procurement_id}/chain/settlement-confirmation",status_code=202,tags=["simulation"])
    def settlement_execute(procurement_id: UUID,body: EmptyMutation,principal=Depends(current_principal),session=Depends(get_session),idempotency_key: IdempotencyHeader=None):
        return tail_execute(procurement_id,principal,session,idempotency_key,"settlement.execute")

    def list_rows(model,query,cursor,limit,ns,principal):
        return cursor_query(query,model,cursor,ns,principal).limit(limit+1)

    @app.get("/v2/mock-exchanges",tags=["simulation"])
    def exchange_list(cursor: str|None=None,limit: int=Query(25,ge=1,le=100),principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            rows=session.scalars(list_rows(PaymentResource,select(PaymentResource).where(PaymentResource.namespace_id==ns.id,PaymentResource.actor_user_id==principal.user_id),cursor,limit,ns,principal)).all()
            return envelope(ns,chain,items=[resource_dto(session,r) for r in rows[:limit]],nextCursor=next_cursor(rows,limit,ns,principal))

    @app.get("/v2/mock-exchanges/{resource_id}",tags=["simulation"])
    def exchange_get(resource_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            r=session.get(PaymentResource,resource_id)
            if r is None or r.namespace_id!=ns.id:
                raise APIError(404,"exchange_not_found","Resource not found")
            if r.actor_user_id!=principal.user_id:
                raise APIError(403,"exchange_forbidden","Resource belongs to another actor")
            return envelope(ns,chain,exchange=resource_dto(session,r))

    @app.get("/v2/operations",tags=["simulation"])
    def operations(cursor: str|None=None,limit: int=Query(25,ge=1,le=100),principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            rows=session.scalars(list_rows(Operation,select(Operation).where(Operation.namespace_id==ns.id,Operation.principal_id==principal.user_id),cursor,limit,ns,principal)).all()
            return envelope(ns,chain,items=[_operation(r,False) for r in rows[:limit]],nextCursor=next_cursor(rows,limit,ns,principal))

    @app.get("/v2/signing-requests",tags=["simulation"])
    def signings(cursor: str|None=None,limit: int=Query(25,ge=1,le=100),principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            rows=session.scalars(list_rows(SigningRequest,select(SigningRequest).where(SigningRequest.namespace_id==ns.id,SigningRequest.signer_user_id==principal.user_id),cursor,limit,ns,principal)).all()
            items=[{"id":str(r.id),"procurementId":str(r.procurement_id),"kind":r.kind,"status":r.status,
                "signer":r.signer_wallet,"nonce":r.nonce_text,"deadline":r.deadline_text,"policyEpoch":r.policy_epoch,
                "digest":r.digest,"typedData":r.typed_data,"synthetic":r.kind in {"ai_pre","ai_final"}} for r in rows[:limit]]
            return envelope(ns,chain,items=items,nextCursor=next_cursor(rows,limit,ns,principal))

    def status(session,ns,proc,principal):
        result=payment_status(session,ns,proc)
        view=gateway.call("PoGRegistryV2","getProcurement",proc.business_id)
        result["release"]={"confirmed":int(view[21]) in {9,10,11,12},
                           "amountAtomic":str(int(proc.invoice_amount_atomic or 0))}
        result["settlement"]={"confirmed":int(view[21])==12,"settlementHash":hex32(view[18])}
        if principal.role=="recipient":
            for key in ("redemption","supplierPayment"):
                r=result.get(key)
                if r:
                    result[key]={"status":r["status"],"reconciled":r["reconciled"],"amountAtomic":r["amountAtomic"]}
        return result

    @app.get("/v2/procurements/{procurement_id}/payment-status",tags=["simulation"])
    def payment_state(procurement_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            proc,project=_procurement(session,ns,procurement_id)
            private(project,principal)
            return envelope(ns,chain,**status(session,ns,proc,principal))

    @app.get("/v2/projects/{project_id}/progress",tags=["simulation"])
    def progress(project_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            project=_project(session,ns,project_id)
            if principal.role!="donor":
                private(project,principal)
            ledger=chain.call("ProcurementEscrowV2","getLedger",project.business_id)
            result={"projectId":str(project.id),"state":project.chain_status,"depositsAtomic":str(ledger[1]),
                    "reservedAtomic":str(ledger[2]),"releasedAtomic":str(ledger[3]),"freeLockedAtomic":str(chain.call("ProcurementEscrowV2","freeLocked",project.business_id))}
            if principal.role=="donor":
                result["ownFunding"]=[resource_dto(session,r) for r in session.scalars(select(PaymentResource).where(
                    PaymentResource.namespace_id==ns.id,PaymentResource.project_id==project.id,PaymentResource.actor_user_id==principal.user_id))]
            else:
                result["procurements"]=[{"id":str(p.id),"chainState":p.chain_status,**status(session,ns,p,principal)}
                    for p in session.scalars(select(Procurement).where(Procurement.namespace_id==ns.id,Procurement.project_id==project.id))]
            return envelope(ns,chain,**result)

    def version_scope(session,ns,version_id,principal):
        version=session.get(DocumentVersion,version_id)
        document=session.get(Document,version.document_id) if version else None
        if document is None or document.namespace_id!=ns.id:
            raise APIError(404,"document_version_not_found","Document version not found")
        proc,project=_procurement(session,ns,document.procurement_id)
        private(project,principal)
        return document,version

    @app.get("/v2/document-versions/{version_id}",tags=["simulation"])
    def version_get(version_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        from .app import _document_response
        gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            document,version=version_scope(session,ns,version_id,principal)
            return _document_response(document,version)

    @app.get("/v2/document-versions/{version_id}/content",tags=["simulation"])
    def version_content(version_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            _,version=version_scope(session,ns,version_id,principal)
            path=file_store.resolve(version.storage_key)
            return FileResponse(path,media_type=version.content_type,filename=version.original_filename,
                headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

    def evidence_scope(session,ns,evidence_id,principal):
        e=session.get(PaymentEvidence,evidence_id)
        if e is None or e.namespace_id!=ns.id:
            raise APIError(404,"payment_evidence_not_found","Evidence not found")
        _,project=_procurement(session,ns,e.procurement_id)
        private(project,principal,evidence=True)
        if hashlib.sha256(e.canonical_bytes).hexdigest()!=e.sha256_hex:
            raise APIError(409,"payment_evidence_invalid","Immutable evidence integrity failed")
        return e

    @app.get("/v2/payment-evidence/{evidence_id}",tags=["simulation"])
    def evidence_get(evidence_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            e=evidence_scope(session,ns,evidence_id,principal)
            return envelope(ns,chain,evidence={"id":str(e.id),"procurementId":str(e.procurement_id),"kind":e.kind,
                "schemaVersion":e.schema_version,"sha256":e.sha256_hex,"keccak256":e.keccak256_hex,"sizeBytes":str(len(e.canonical_bytes))})

    @app.get("/v2/payment-evidence/{evidence_id}/content",tags=["simulation"])
    def evidence_content(evidence_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            e=evidence_scope(session,ns,evidence_id,principal)
            return Response(e.canonical_bytes,media_type="application/json",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

    @app.get("/v2/procurements/{procurement_id}/workspace",tags=["simulation"])
    def workspace(procurement_id: UUID,principal=Depends(current_principal),session=Depends(get_session)):
        from .app import _document_response
        chain=gate()
        with session.begin():
            ns=namespace(session,for_chain=True)
            proc,project=_procurement(session,ns,procurement_id)
            private(project,principal)
            rows=session.execute(select(Document,DocumentVersion).join(DocumentVersion,DocumentVersion.document_id==Document.id).where(
                Document.namespace_id==ns.id,Document.procurement_id==proc.id).order_by(DocumentVersion.created_at,DocumentVersion.id)).all()
            versions=[_document_response(doc,version) for doc,version in rows]
            view=chain.call("PoGRegistryV2","getProcurement",proc.business_id)
            reports={}
            for name,index in (("preAssessment",8),("finalAssessment",15)):
                if hex32(view[index])==ZERO:
                    reports[name]=None
                else:
                    a=chain.call("PoGRegistryV2","getAssessment",view[index])
                    reports[name]={"assessmentId":hex32(a[0]),"stage":str(a[2]),"outcome":str(a[3]),
                        "riskScoreBps":str(a[4]),"evidenceHash":hex32(a[5]),"reportHash":hex32(a[6]),
                        "deadline":str(a[9]),"synthetic":True,"realAI":False}
            allowed=capabilities(principal)
            valid_state={"release.execute":8,"release.human.approve":7,"foundation.redemption.convert":9,
                "supplier.payment.execute":9,"settlement.evidence.record":9,"settlement.human.approve":10,"settlement.execute":11}
            for name,wanted in valid_state.items():
                if int(view[21])!=wanted:
                    allowed[name]={"implemented":True,"enabled":False,"reasonCode":"procurement_state_conflict"}
            approval=None
            if int(view[21]) in {7,8,10,11}:
                release=int(view[21]) in {7,8}
                ledger=chain.call("ProcurementEscrowV2","getLedger",project.business_id)
                policy=session.scalar(select(PolicyProjection).where(PolicyProjection.namespace_id==ns.id,PolicyProjection.project_id==project.id))
                epoch,threshold=int(ledger[9]),int(ledger[10])
                action=1 if release else 2
                terms=hex32(chain.call("ProcurementEscrowV2","releaseTermsHash" if release else "settlementTermsHash",proc.business_id))
                key=bundle_key([proc.business_id,action,terms,hex32(view[15]) if release else ZERO,"",0,0,epoch])
                policy_current=bool(policy and policy.policy_epoch==epoch and chain.canonical(policy.block_number,policy.block_hash))
                now=chain.latest_timestamp()
                votes=[]
                if policy_current:
                    for wallet in policy.approver_wallets:
                        wallet=Web3.to_checksum_address(wallet)
                        if chain.call("ProcurementEscrowV2","isApprover",project.business_id,wallet):
                            deadline=int(chain.call("ProcurementEscrowV2","voteDeadline",key,wallet))
                            votes.append({"signer":wallet,"deadline":str(deadline),"live":deadline>0 and deadline>=now})
                count=sum(v["live"] for v in votes)
                approval={"bundleKey":key,"action":str(action),"termsHash":terms,"policyEpoch":str(epoch),
                    "threshold":str(threshold),"liveVoteCount":str(count),"policyVerified":policy_current,"votes":votes}
                execute="release.execute" if release else "settlement.execute"
                if not policy_current or count<threshold:
                    allowed[execute]={"implemented":True,"enabled":False,"reasonCode":"human_approval_not_current"}
                if release and reports["finalAssessment"] and int(reports["finalAssessment"]["deadline"])<now:
                    allowed[execute]={"implemented":True,"enabled":False,"reasonCode":"assessment_expired"}
            return envelope(ns,chain,procurementId=str(proc.id),documentVersions=versions,allowedActions=allowed,
                amounts={"reservedAmountAtomic":str(view[9]),"invoiceAmountAtomic":str(view[11]),"returnedAmountAtomic":str(view[20])},
                chainState=proc.chain_status,approval=approval,originalSourceVersionIds={key:value for key,value in proc.source_versions_json.items() if key.endswith("DocumentVersionId")},
                **reports,**status(session,ns,proc,principal))
