"""All business actions cross real loopback HTTP; synthetic AI is explicitly labelled.

Only guarded PG fixture provisioning and workers operate directly. The client never
reads a private signature or sends a raw chain transaction for the happy path.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, text, func

from pog_api.chain import LocalChainGateway
from pog_api.db import build_session_factory
from pog_api.idempotency import ensure_verified_namespace
from pog_api.models import ROLE_NAMES, User, WalletAuthorization, Operation, ChainTransaction, SigningRequest
from pog_api.payment_models import HKDJournal, PaymentEvidence, PaymentResource, SimHKDAccount, FundedClaim
from pog_api.payment_domain import provision_simulation
from pog_api.payment_worker import PaymentWorker
from pog_api.cli import seed_chain_demo
from pog_api.test_database import assert_safe_test_target, build_safe_test_engine
from pog_api.worker import ChainWorker, ChainIndexer

DATABASE_URL=os.environ.get("POG_TEST_DATABASE_URL") or ""
assert_safe_test_target(DATABASE_URL)
from test_a2_receipt_confirmed import PASSWORD, _pdf
from test_a3_submit_signed_http import SERVER_PROGRAM

ROOT=Path(__file__).resolve().parents[3]
MANIFEST=Path(os.environ["POG_A2_LIVE_MANIFEST"]).resolve()
RAW_SIGNATURE=re.compile(r"0x[0-9a-fA-F]{130}(?![0-9a-fA-F])")
SPECS=(("foundation","foundation","foundation"),("recipient","recipient","recipient"),
       ("donor","donor","donorA"),("donor-fixture-b","donor","donorB"),
       ("admin","human_approver","humanApprover"),("service-ai-fixture","service_ai","aiSigner"))


@pytest.fixture
def full_env(tmp_path, monkeypatch):
    gate=LocalChainGateway(MANIFEST,ROOT)
    gate.verify()
    assert gate.rpc_url=="http://127.0.0.1:18545"
    engine=build_safe_test_engine(DATABASE_URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE deployment_instances, users CASCADE"))
        for role in ROLE_NAMES:
            c.execute(text("INSERT INTO roles(name) VALUES (:role) ON CONFLICT DO NOTHING"),{"role":role})
    factory=build_session_factory(engine)
    monkeypatch.setenv("POG_DATABASE_URL",DATABASE_URL)
    monkeypatch.setenv("POG_A2_CHAIN_ENABLED","true")
    monkeypatch.setenv("POG_A2_CHAIN_MANIFEST",str(MANIFEST))
    for prefix in ("FOUNDATION","RECIPIENT","DONOR","ADMIN","AI_FIXTURE","DONOR_B"):
        monkeypatch.setenv("POG_SEED_"+prefix+"_PASSWORD",PASSWORD)
    assert seed_chain_demo(True)==0
    ids={}
    with factory() as session,session.begin():
        for name,role,manifest_role in SPECS:
            user=session.scalar(select(User).where(User.username==name))
            auth=session.scalar(select(WalletAuthorization).where(WalletAuthorization.user_id==user.id))
            assert auth.role_name==role and auth.wallet_address==gate.roles[manifest_role].lower()
            ids[name]=str(user.id)
        ns=ensure_verified_namespace(session,gate)
        provision_simulation(session,ns,gate,"100000")
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(("127.0.0.1",0));listener.listen(128)
    child_env={k:v for k,v in os.environ.items() if k.lower() not in {"http_proxy","https_proxy","all_proxy","no_proxy"}}
    child_env.update({"PYTHONPATH":str(ROOT/"services/api/src"),"POG_DATABASE_URL":DATABASE_URL,
        "POG_TEST_DATABASE_URL":DATABASE_URL,"POG_STORAGE_ROOT":str(tmp_path/"evidence"),
        "POG_A2_CHAIN_ENABLED":"true","POG_A2_CHAIN_MANIFEST":str(MANIFEST),
        "POG_A2_DEMO_SIGNING_ENABLED":"true","POG_FULL_DEMO_ENABLED":"true",
        "POG_BIND_HOST":"127.0.0.1","POG_BIND_PORT":str(listener.getsockname()[1]),
        "POG_A3_TEST_API_FD":str(listener.fileno()),"NO_PROXY":"127.0.0.1,localhost"})
    process=client=worker=indexer=payment=None
    tokens={}
    try:
        process=subprocess.Popen([sys.executable,"-c",SERVER_PROGRAM],cwd=ROOT,env=child_env,
            pass_fds=(listener.fileno(),),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        address=f"http://127.0.0.1:{listener.getsockname()[1]}"
        listener.close()
        client=httpx.Client(base_url=address,trust_env=False,follow_redirects=False,timeout=20)
        deadline=time.monotonic()+45
        ready=False
        while time.monotonic()<deadline and process.poll() is None:
            try:
                response=client.get("/ready",timeout=1)
                if response.status_code==200 and response.json().get("ready") is True:
                    ready=True;break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        assert ready,"Owned full API did not become ready"
        for name,_,_ in SPECS:
            response=client.post("/v2/sessions",json={"username":name,"password":PASSWORD})
            assert response.status_code==200,"Fixture login failed"
            tokens[name]=response.json()["token"]
        worker,indexer,payment=ChainWorker(DATABASE_URL,gate),ChainIndexer(DATABASE_URL,gate),PaymentWorker(DATABASE_URL,gate)

        def check(response,expected=200):
            try:
                body=response.json()
            except ValueError:
                body={}
            error=body.get("error",{}).get("code") if isinstance(body,dict) else None
            assert response.status_code==expected,(response.status_code,error)
            assert not RAW_SIGNATURE.search(response.text),"Raw signature leaked"
            assert not any(value in response.text for value in tokens.values()),"Credential leaked"
            def walk(value):
                if isinstance(value,dict):
                    assert not {"signature","password","accessToken","privateKey","mnemonic"} & set(value),"Sensitive response field"
                    for item in value.values():walk(item)
                elif isinstance(value,list):
                    for item in value:walk(item)
            walk(body)
            return body
        def headers(actor,key=None):
            return {"Authorization":"Bearer "+tokens[actor],**({"Idempotency-Key":key} if key else {})}
        def post(path,body,actor,key,expected=202):
            return check(client.post(path,json=body,headers=headers(actor,key)),expected)
        def get(path,actor,expected=200):
            return check(client.get(path,headers=headers(actor)),expected)
        def drain(operation_id):
            for _ in range(80):
                worker.once();indexer.once();payment.once()
                with factory() as session:
                    op=session.get(Operation,UUID(operation_id))
                    if op.status=="confirmed":return
                    assert op.status not in {"failed","requires_attention","invalidated_instance"},(op.status,op.error_code)
            raise AssertionError("Operation did not finish its chain and payment executors")
        def confirmed(path,body,actor,key):
            response=post(path,body,actor,key)
            drain(response["operation"]["operationId"])
            return response
        def upload(proc,category,actor):
            return check(client.post("/v2/documents",data={"procurementId":proc,"category":category},
                files={"file":(category+".pdf",_pdf(),"application/pdf")},headers=headers(actor,"upload-"+category)),202)["document"]
        yield SimpleNamespace(gate=gate,factory=factory,ids=ids,post=post,get=get,confirmed=confirmed,upload=upload,
            drain=drain,client=client,headers=headers,worker=worker,indexer=indexer,payment=payment)
    finally:
        for service in (worker,indexer,payment):
            if service:service.close()
        if client:client.close()
        listener.close()
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        engine.dispose()


def test_full_simulated_http_funding_to_settlement(full_env):
    e=full_env
    escrow_wallet=e.gate.contract_address("ProcurementEscrowV2")
    foundation_wallet=e.gate.roles["foundation"]
    treasury_wallet=e.gate.roles["mockRedemption"]
    token_wallet=e.gate.contract_address("MockHKD")
    def token_balances():
        return tuple(int(e.gate.call("MockHKD","balanceOf",wallet))
                     for wallet in (escrow_wallet,foundation_wallet,treasury_wallet))
    # The CI suite shares this deployment. Earlier projects may have released
    # tokens to the same Foundation; this procurement must leave them untouched.
    escrow_before,foundation_before,treasury_before=token_balances()
    context=e.get("/v2/integration-context","foundation")
    assert context["interfaceId"]=="pog-full-demo-api-v0.1"
    assert context["defaults"]["recipientUserId"]==e.ids["recipient"]
    assert e.get("/v2/integration-context","donor")["defaults"] is None
    assert context["capabilities"]["ai.final.detect"]=={"implemented":False,"enabled":False,"reasonCode":"ai_service_unavailable"}
    p=e.post("/v2/projects",{"title":"Explicit local full simulation","publicSummary":"Synthetic AI and simulated funds only",
        "recipientUserId":e.ids["recipient"],"humanApproverUserId":e.ids["admin"]},"foundation","project")["project"]
    e.confirmed(f"/v2/projects/{p['id']}/chain/create",{},"foundation","create-project")
    sources={}
    for actor,cents,atomic in (("donor","6000","60000000"),("donor-fixture-b","4000","40000000")):
        quote=e.post("/v2/mock-exchanges/quote",{"direction":"hkd_to_mock","projectId":p["id"],"hkdCents":cents},actor,"quote-"+actor,200)
        assert quote["executed"] is False and quote["quote"]["amountAtomic"]==atomic
        with e.factory() as s:
            before=s.scalar(select(func.count()).select_from(HKDJournal))
        f=e.post("/v2/mock-exchanges/funding",{"projectId":p["id"],"hkdCents":cents,"confirm":True},actor,"fund-"+actor)
        assert f["exchange"]["reconciled"] is False
        assert e.post("/v2/mock-exchanges/funding",{"projectId":p["id"],"hkdCents":cents,"confirm":True},actor,"fund-"+actor)["operation"]["operationId"]==f["operation"]["operationId"]
        e.drain(f["operation"]["operationId"])
        own=e.get("/v2/mock-exchanges/"+f["exchange"]["id"],actor)["exchange"]
        assert own["reconciled"] is True and len(own["journalIds"])==1
        with e.factory() as s:
            assert s.scalar(select(func.count()).select_from(HKDJournal))==before+1
        other="donor-fixture-b" if actor=="donor" else "donor"
        e.get("/v2/mock-exchanges/"+own["id"],other,403)
        sources[actor]=own
        d=e.confirmed(f"/v2/projects/{p['id']}/funded-donations",
            {"fundingOperationId":own["operationId"],"amountAtomic":atomic,"confirm":True},actor,"donate-"+actor)
        assert e.get("/v2/operations/"+d["operation"]["operationId"],actor)["chainVerified"] is False
        e.post(f"/v2/projects/{p['id']}/funded-donations",
            {"fundingOperationId":own["operationId"],"amountAtomic":"1","confirm":True},actor,"double-credit-"+actor,409)
    ledger=e.get(f"/v2/projects/{p['id']}/ledger","foundation")
    assert ledger["depositsAtomic"]=="100000000"
    assert token_balances()==(escrow_before+100000000,foundation_before,treasury_before)
    q=e.post("/v2/procurements",{"projectId":p["id"],"title":"Full invoice 72",
        "vendorWallet":e.gate.roles["vendor"],"budgetCapAtomic":"80000000"},"foundation","proc")["procurement"]
    path=f"/v2/procurements/{q['id']}"
    e.confirmed(path+"/chain/create",{},"foundation","create-proc")
    docs={c:e.upload(q["id"],c,"foundation") for c in ("purchase_order","request","goods_request")}
    po={"poDocumentVersionId":docs["purchase_order"]["versionId"],"requestDocumentVersionId":docs["request"]["versionId"],
        "goodsRequestDocumentVersionId":docs["goods_request"]["versionId"]}
    e.confirmed(path+"/chain/purchase-order",po,"foundation","po")
    def authorize(kind,actor,extra=None):
        request=e.post(path+"/signing-requests",{"kind":kind,"deadlineTtlSeconds":3600,**(extra or {})},actor,kind+"-prepare")["signingRequest"]
        if kind in {"ai_pre","ai_final"}:assert request["synthetic"] is True
        e.post(f"/v2/signing-requests/{request['id']}/sign-demo",{"confirm":True},actor,kind+"-sign")
        return e.confirmed(f"/v2/signing-requests/{request['id']}/submit-signed",{"confirm":True},actor,kind+"-submit")
    authorize("ai_pre","service-ai-fixture")
    authorize("reserve","admin",{"reserveAmountAtomic":"80000000"})
    e.confirmed(path+"/chain/reserve",{"reserveAmountAtomic":"80000000"},"foundation","reserve")
    docs.update({c:e.upload(q["id"],c,"foundation") for c in ("invoice","goods_evidence")})
    invoice={"invoiceDocumentVersionId":docs["invoice"]["versionId"],"goodsDocumentVersionId":docs["goods_evidence"]["versionId"],
        "invoiceAmountAtomic":"72000000"}
    e.post(path+"/chain/invoice-and-goods",{**invoice,"invoiceAmountAtomic":"72000001"},"foundation","subcent",422)
    e.confirmed(path+"/chain/invoice-and-goods",invoice,"foundation","invoice")
    receipt=e.upload(q["id"],"receipt_evidence","recipient")
    authorize("receipt","recipient",{"receiptEvidenceDocumentVersionId":receipt["versionId"]})
    e.post(path+"/signing-requests",{"kind":"release"},"foundation","foundation-human",403)
    e.post(path+"/chain/release",{},"recipient","recipient-release",403)
    authorize("ai_final","service-ai-fixture")
    authorize("release","admin")
    pending=e.get(path+"/workspace","admin")
    assert pending["approval"]["liveVoteCount"]=="1" and pending["allowedActions"]["release.execute"]["enabled"] is True
    released=e.confirmed(path+"/chain/release",{},"foundation","release")
    assert token_balances()==(escrow_before+28000000,foundation_before+72000000,treasury_before)
    state=e.get(path+"/payment-status","recipient")
    assert state["release"]["confirmed"] is True and state["supplierPayment"] is None
    quote=e.post("/v2/mock-exchanges/quote",{"direction":"mock_to_hkd","procurementId":q["id"]},"foundation","redemption-quote",200)
    assert quote["executed"] is False and quote["quote"]["hkdCents"]=="7200"
    r=e.confirmed(path+"/mock-redemption",{"confirm":True},"foundation","redeem")
    assert token_balances()==(escrow_before+28000000,foundation_before,treasury_before+72000000)
    # confirmed() returns the original queued response: reload the reconciled
    # resource before independently validating its exact canonical transfer.
    redemption=e.get("/v2/mock-exchanges/"+r["exchange"]["id"],"foundation")["exchange"]
    assert redemption["reconciled"] is True and redemption["kind"]=="redemption"
    assert redemption["operationId"]==r["operation"]["operationId"]
    assert redemption["sourceOperationId"]==released["operation"]["operationId"]
    assert redemption["procurementId"]==q["id"] and redemption["amountAtomic"]=="72000000"
    proof=redemption["chainProof"]
    assert proof["operationId"]==redemption["operationId"]
    receipt,events=e.gate.receipt_with_events(proof["txHash"],"Transfer")
    assert receipt is not None and int(receipt["status"])==1
    assert receipt["transactionHash"].lower()==proof["txHash"].lower()
    assert receipt["blockHash"].lower()==proof["blockHash"].lower()
    assert int(receipt["blockNumber"])==int(proof["blockNumber"])
    assert receipt["from"].lower()==foundation_wallet.lower()
    assert receipt["to"].lower()==token_wallet.lower()
    assert e.gate.canonical(int(receipt["blockNumber"]),receipt["blockHash"])
    transfers=[event for event in events if event["address"].lower()==token_wallet.lower()]
    assert len(transfers)==1
    transfer=transfers[0]
    assert transfer["args"]["from"].lower()==foundation_wallet.lower()
    assert transfer["args"]["to"].lower()==treasury_wallet.lower()
    assert int(transfer["args"]["value"])==72000000
    assert int(transfer["logIndex"])==int(proof["logIndex"])
    assert transfer["blockHash"].lower()==receipt["blockHash"].lower()
    transaction=e.gate.w3.eth.get_transaction(proof["txHash"])
    expected=e.gate.prepare("payment.redeem",foundation_wallet,[treasury_wallet,"72000000"],"Transfer")
    assert e.gate.w3.to_hex(transaction["input"]).lower()==expected.data.lower()
    assert int(transaction["value"])==0
    block=int(receipt["blockNumber"])
    foundation_at=lambda height:int(e.gate.call("MockHKD","balanceOf",foundation_wallet,block_identifier=height))
    treasury_at=lambda height:int(e.gate.call("MockHKD","balanceOf",treasury_wallet,block_identifier=height))
    assert foundation_at(block-1)-foundation_at(block)==72000000
    assert treasury_at(block)-treasury_at(block-1)==72000000
    e.post(path+"/mock-redemption",{"confirm":True},"foundation","redeem-again",409)
    assert e.get("/v2/mock-hkd/accounts/me","foundation")["account"]["availableHkdCents"]=="7200"
    e.post(path+"/mock-supplier-payment",{"confirm":True},"admin","admin-pay",403)
    paid=e.confirmed(path+"/mock-supplier-payment",{"confirm":True},"foundation","pay")
    e.post(path+"/mock-supplier-payment",{"confirm":True},"foundation","pay-again",409)
    assert e.get("/v2/operations/"+paid["operation"]["operationId"],"foundation")["chainVerified"] is False
    payment=e.get("/v2/mock-exchanges/"+paid["payment"]["id"],"foundation")["exchange"]
    assert payment["chainProof"] is None and len(payment["journalIds"])==1
    assert e.get("/v2/mock-hkd/accounts/me","foundation")["account"]["availableHkdCents"]=="0"
    e.get("/v2/payment-evidence/"+payment["evidenceId"],"recipient",403)
    ev=e.get("/v2/payment-evidence/"+payment["evidenceId"],"admin")["evidence"]
    content=e.client.get("/v2/payment-evidence/"+payment["evidenceId"]+"/content",headers=e.headers("admin"))
    assert content.status_code==200
    import hashlib
    from eth_utils import keccak
    assert hashlib.sha256(content.content).hexdigest()==ev["sha256"]
    assert keccak(content.content).hex()==ev["keccak256"]
    e.confirmed(path+"/chain/settlement-evidence",{},"foundation","record-settlement")
    assert e.get(path+"/payment-status","admin")["settlement"]["confirmed"] is False
    authorize("settlement","admin")
    assert e.get(path+"/workspace","admin")["approval"]["liveVoteCount"]=="1"
    e.confirmed(path+"/chain/settlement-confirmation",{},"foundation","settle")
    progress=e.get(f"/v2/projects/{p['id']}/progress","foundation")
    assert (progress["state"],progress["depositsAtomic"],progress["reservedAtomic"],progress["releasedAtomic"],progress["freeLockedAtomic"])==(
        "active","100000000","0","72000000","28000000")
    assert e.get(path+"/payment-status","recipient")["settlement"]["confirmed"] is True
    workspace=e.get(path+"/workspace","recipient")
    assert workspace["finalAssessment"]["synthetic"] is True and len(workspace["documentVersions"])==6
    e.get(path+"/workspace","donor",403)
    e.get("/v2/document-versions/"+docs["invoice"]["versionId"],"donor",403)
    assert len(e.get("/v2/signing-requests","admin")["items"])==3
    with e.factory() as s:
        supplier=s.scalar(select(SimHKDAccount).where(SimHKDAccount.party_key==e.gate.roles["vendor"].lower()))
        assert int(supplier.available_cents)==7200
        assert s.scalar(select(func.count()).select_from(PaymentEvidence))==2
        assert all(c.status=="consumed" for c in s.scalars(select(FundedClaim)))
    view=e.gate.call("PoGRegistryV2","getProcurement",q["businessId"])
    assert int(view[21])==12
    assert int(e.gate.call("PoGRegistryV2","getProject",p["businessId"])[8])==0
    assert token_balances()==(escrow_before+28000000,foundation_before,treasury_before+72000000)
