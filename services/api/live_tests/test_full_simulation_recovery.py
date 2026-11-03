"""Actual loopback negative/recovery mechanics; raw RPC only injects test faults."""
from datetime import UTC, datetime, timedelta
from uuid import UUID
import pytest
from sqlalchemy import select, func
from pog_api.models import Operation, OperationStep, ChainTransaction
from pog_api.payment_models import PaymentResource, HKDJournal, FundedClaim
from pog_api.payment_worker import PaymentWorker
from test_full_simulation_http import DATABASE_URL, full_env  # noqa: F401


def project(e):
    p=e.post("/v2/projects",{"title":"Owned fault-injection simulation","publicSummary":"No real funds or AI",
        "recipientUserId":e.ids["recipient"],"humanApproverUserId":e.ids["admin"]},"foundation","fault-project")["project"]
    e.confirmed(f"/v2/projects/{p['id']}/chain/create",{},"foundation","fault-create")
    return p


def funding(e,p,key="fault-fund"):
    return e.post("/v2/mock-exchanges/funding",{"projectId":p["id"],"hkdCents":"1000","confirm":True},"donor",key)


def donation(e,p,f,key="fault-donation",expected=202):
    return e.post(f"/v2/projects/{p['id']}/funded-donations",
        {"fundingOperationId":f["operation"]["operationId"],"amountAtomic":"10000000","confirm":True},"donor",key,expected)


def test_actual_lost_mint_response_restart_reconciles_once(full_env,monkeypatch):
    e=full_env;p=project(e);f=funding(e,p)
    original_send=e.gate.send;original_find=e.gate.find_envelope_transaction
    sent=False;count=0
    def send(envelope):
        nonlocal sent,count
        count+=1;original_send(envelope);sent=True
        raise TimeoutError("Injected local response loss")
    def find(envelope):
        if sent:raise TimeoutError("Injected temporary reconciliation outage")
        return original_find(envelope)
    monkeypatch.setattr(e.gate,"send",send)
    monkeypatch.setattr(e.gate,"find_envelope_transaction",find)
    assert e.worker.once()
    with e.factory() as s:
        tx=s.scalar(select(ChainTransaction).where(ChainTransaction.operation_id==UUID(f["operation"]["operationId"])))
        assert tx.status=="requires_attention" and tx.tx_hash is None
        assert s.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind=="funding"))==0
    same=funding(e,p)
    assert same["operation"]["operationId"]==f["operation"]["operationId"]
    assert e.get("/v2/mock-exchanges/"+f["exchange"]["id"],"donor")["exchange"]["reconciled"] is False
    # Controlled lease-expiry fault, not a signature or business action from SQL.
    with e.factory() as s,s.begin():
        tx=s.scalar(select(ChainTransaction).where(ChainTransaction.operation_id==UUID(f["operation"]["operationId"])))
        tx.submitted_at=datetime.now(UTC)-timedelta(seconds=30)
    monkeypatch.setattr(e.gate,"find_envelope_transaction",original_find)
    def recovery_send(envelope):
        nonlocal count
        count+=1
        return original_send(envelope)
    monkeypatch.setattr(e.gate,"send",recovery_send)
    restarted=PaymentWorker(DATABASE_URL,e.gate)
    try:
        for _ in range(80):
            e.worker.once();e.indexer.once();restarted.once()
            with e.factory() as s:
                status=s.get(Operation,UUID(f["operation"]["operationId"])).status
            if status=="confirmed":break
            assert status not in {"failed","requires_attention"},status
        assert status=="confirmed"
    finally:
        restarted.close()
    with e.factory() as s:
        assert s.scalar(select(func.count()).select_from(ChainTransaction).where(ChainTransaction.operation_id==UUID(f["operation"]["operationId"])))==1
        assert s.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind=="funding"))==1
    assert count==1


def test_orphan_mint_blocks_queued_deposit_before_any_broadcast(full_env,monkeypatch):
    e=full_env;p=project(e)
    snapshot=e.gate.w3.provider.make_request("evm_snapshot",[])["result"]
    f=funding(e,p);e.drain(f["operation"]["operationId"])
    d=donation(e,p,f)
    with e.factory() as s:
        journals=s.scalar(select(func.count()).select_from(HKDJournal))
    assert e.gate.w3.provider.make_request("evm_revert",[snapshot])["result"] is True
    sends=[]
    monkeypatch.setattr(e.gate,"send",lambda envelope:sends.append(envelope.action))
    assert e.worker.once()
    assert sends==[]
    with e.factory() as s:
        assert s.get(Operation,UUID(d["operation"]["operationId"])).status=="requires_attention"
        assert s.scalar(select(func.count()).select_from(ChainTransaction).where(ChainTransaction.operation_id==UUID(d["operation"]["operationId"])))==0
        assert s.get(PaymentResource,UUID(f["exchange"]["id"])).status=="requires_attention"
        assert s.scalar(select(func.count()).select_from(HKDJournal))==journals
    assert int(e.gate.call("ProcurementEscrowV2","donorCredit",p["businessId"],e.gate.roles["donorA"]))==0


def test_external_outflow_freezes_credit_despite_remaining_faucet_balance(full_env):
    e=full_env;p=project(e);f=funding(e,p);e.drain(f["operation"]["operationId"])
    tx=e.gate.contracts["MockHKD"].functions.transfer(e.gate.roles["vendor"],1).transact({"from":e.gate.roles["donorA"]})
    e.gate.w3.eth.wait_for_transaction_receipt(tx)
    donation(e,p,f,expected=409)
    with e.factory() as s:
        assert s.get(PaymentResource,UUID(f["exchange"]["id"])).status=="requires_attention"
        assert s.scalar(select(func.count()).select_from(FundedClaim))==0
        assert s.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind=="funding"))==1
    donation(e,p,f,key="external-retry",expected=409)
