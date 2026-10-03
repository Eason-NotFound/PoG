"""CEO number example, actual HTTP/PG/accepted V2 chain, ReceiptConfirmed stop.

Reuses the guarded owned 18545 fixture. Evidence is synthetic and private;
stdout contains only public IDs, hashes, receipts, events, and ledger facts.
No release, payment, closure, refund, AI service, or real asset is exercised.
"""
from __future__ import annotations

from collections import Counter
import json
from uuid import UUID

from sqlalchemy import select

from pog_api.models import ChainTransaction, DocumentVersion, OperationStep, SigningRequest
from test_a2_receipt_confirmed import _auth, _upload
from test_a2_renewal_human import _hex, _request, _sign, _submit, renewal_env


def test_ceo_60_40_80_72_joint_acceptance_stops_at_recipient_receipt(renewal_env):
    env, gate = renewal_env, renewal_env.gateway
    project_id, procurement_id = env.project["businessId"], env.procurement["businessId"]
    foundation_before = int(gate.call("MockHKD", "balanceOf", gate.roles["foundation"]))
    escrow_before = int(gate.call("MockHKD", "balanceOf", gate.contract_address("ProcurementEscrowV2")))
    identities = {}
    for actor in ("foundation", "recipient", "admin", "service-ai-fixture"):
        response = env.client.get("/v2/me", headers=_auth(env.tokens[actor]))
        assert response.status_code == 200, response.text
        identities[actor] = response.json()
    assert identities["admin"]["role"] == "human_approver"
    assert identities["admin"]["username"] == "admin"
    assert len({item["walletAddress"].lower() for item in identities.values()}) == 4

    ai = _request(env, "ai_pre", "joint-ai-request")
    assert ai["synthetic"] is True
    _submit(env, ai, _sign(env, ai, "joint-ai-explicit-confirm"), "joint-ai-submit")
    no_vote = env.post(f"/v2/procurements/{env.procurement['id']}/chain/reserve",
                       {"reserveAmountAtomic": "80000000"}, "foundation", "joint-no-human-vote")
    assert no_vote.status_code == 409, no_vote.text
    assert int(gate.call("ProcurementEscrowV2", "getLedger", project_id)[2]) == 0
    human = _request(env, "reserve", "joint-human-request")
    assert human["signer"].lower() == identities["admin"]["walletAddress"].lower()
    assert human["typedData"]["message"]["assessmentId"] == ai["typedData"]["message"]["assessmentId"]
    _submit(env, human, _sign(env, human, "joint-human-explicit-confirm"), "joint-human-vote")
    env.confirmed(f"/v2/procurements/{env.procurement['id']}/chain/reserve",
                  {"reserveAmountAtomic": "80000000"}, "foundation", "joint-reserve-execute")

    invoice = _upload(env.client, env.tokens["foundation"], env.procurement["id"], "invoice", "joint-invoice-document")
    goods = _upload(env.client, env.tokens["foundation"], env.procurement["id"], "goods_evidence", "joint-goods-document")
    env.confirmed(f"/v2/procurements/{env.procurement['id']}/chain/invoice-and-goods", {
        "invoiceDocumentVersionId": invoice["versionId"], "goodsDocumentVersionId": goods["versionId"],
        "invoiceAmountAtomic": "72000000",
    }, "foundation", "joint-invoice-and-goods")
    evidence = _upload(env.client, env.tokens["recipient"], env.procurement["id"], "receipt_evidence", "joint-recipient-evidence")
    response = env.post(f"/v2/procurements/{env.procurement['id']}/signing-requests", {
        "kind": "receipt", "receiptEvidenceDocumentVersionId": evidence["versionId"],
    }, "recipient", "joint-recipient-request")
    assert response.status_code == 202, response.text
    receipt_request = response.json()["signingRequest"]
    assert receipt_request["signer"].lower() == identities["recipient"]["walletAddress"].lower()
    response = env.post(f"/v2/signing-requests/{receipt_request['id']}/sign-demo", {"confirm": True},
                        "recipient", "joint-recipient-explicit-confirm")
    assert response.status_code == 202, response.text
    with env.factory() as session:
        stored_request = session.get(SigningRequest, UUID(receipt_request["id"]))
        signature = stored_request.signature
        assert str(stored_request.signer_user_id) == identities["recipient"]["id"]
        stored_version = session.get(DocumentVersion, UUID(evidence["versionId"]))
        assert str(stored_version.uploaded_by_user_id) == identities["recipient"]["id"]
        assert receipt_request["typedData"]["message"]["receiptEvidenceHash"] == "0x" + stored_version.keccak256_hex
    submitted = env.confirmed(f"/v2/signing-requests/{receipt_request['id']}/submit", {"signature": signature},
                              "recipient", "joint-recipient-submit")
    operation_id = submitted["operation"]["operationId"]
    operation_response = env.client.get(f"/v2/operations/{operation_id}", headers=_auth(env.tokens["recipient"]))
    assert operation_response.status_code == 200, operation_response.text
    operation_fact = operation_response.json()
    assert operation_fact["status"] == "confirmed" and operation_fact["chainVerified"] is True
    assert operation_fact["steps"][0]["expectedEvent"] == "RecipientReceiptAccepted"
    assert operation_fact["steps"][0]["transaction"]["receiptStatus"] == 1
    assert operation_fact["steps"][0]["transaction"]["canonical"] is True

    procurement_response = env.client.get(f"/v2/procurements/{env.procurement['id']}", headers=_auth(env.tokens["foundation"]))
    assert procurement_response.status_code == 200, procurement_response.text
    assert procurement_response.json()["chainState"]["status"] == "receipt_confirmed"
    assert procurement_response.json()["chainState"]["verified"] is True
    view = gate.call("PoGRegistryV2", "getProcurement", procurement_id)
    assert int(view[-1]) == 6 and int(view[9]) == 80_000_000 and int(view[11]) == 72_000_000
    assert _hex(view[13]) == receipt_request["digest"]
    ledger = gate.call("ProcurementEscrowV2", "getLedger", project_id)
    assert tuple(int(ledger[index]) for index in (1, 2, 3, 4)) == (100_000_000, 80_000_000, 0, 0)
    assert int(gate.call("ProcurementEscrowV2", "freeLocked", project_id)) == 20_000_000
    foundation_after = int(gate.call("MockHKD", "balanceOf", gate.roles["foundation"]))
    escrow_after = int(gate.call("MockHKD", "balanceOf", gate.contract_address("ProcurementEscrowV2")))
    assert foundation_after == foundation_before and escrow_after == escrow_before
    donor_facts = []
    for actor, role, amount in (("donor", "donorA", 60_000_000), ("donor-fixture-b", "donorB", 40_000_000)):
        assert int(gate.call("ProcurementEscrowV2", "donorCredit", project_id, gate.roles[role])) == amount
        response = env.client.get(f"/v2/projects/{env.project['id']}/ledger", headers=_auth(env.tokens[actor]))
        assert response.status_code == 200, response.text
        fact = response.json()
        assert (fact["depositsAtomic"], fact["reservedAtomic"], fact["releasedAtomic"],
                fact["currentCallerDonorCreditAtomic"]) == ("100000000", "80000000", "0", str(amount))
        donor_facts.append({"role": role, "wallet": gate.roles[role], "creditAtomic": amount})

    trace = []
    with env.factory() as session:
        rows = session.execute(select(OperationStep, ChainTransaction).join(
            ChainTransaction, ChainTransaction.step_id == OperationStep.id,
        ).order_by(ChainTransaction.created_at)).all()
        assert Counter(step.detail["action"] for step, _tx in rows) == Counter({
            "project.create": 1, "donation.approve": 2, "donation.deposit": 2,
            "procurement.create": 1, "procurement.po": 1, "assessment.ai_pre": 1,
            "approval.reserve": 1, "reserve.execute": 1, "procurement.invoice": 1, "receipt.submit": 1,
        })
        for step, tx in rows:
            assert step.status == tx.status == "confirmed" and tx.canonical is True
            expected = step.detail["expectedEvent"]
            actual_receipt, events = gate.receipt_with_events(tx.tx_hash, expected)
            assert actual_receipt["status"] == 1 and len(events) == 1
            assert actual_receipt["transactionHash"] == tx.tx_hash
            assert actual_receipt["blockHash"] == tx.block_hash == tx.receipt_json["blockHash"]
            assert gate.canonical(actual_receipt["blockNumber"], actual_receipt["blockHash"])
            event = events[0]
            assert event["event"] == expected and event["transactionHash"] == tx.tx_hash
            assert event["blockHash"] == tx.block_hash
            args = event["args"]
            if step.detail["action"] == "donation.deposit":
                assert args["projectId"].lower() == project_id.lower()
                assert int(args["amount"]) in {60_000_000, 40_000_000}
                assert int(gate.call("ProcurementEscrowV2", "donorCredit", project_id, args["donor"],
                                     block_identifier=actual_receipt["blockNumber"])) == int(args["cumulativeCredit"])
            elif step.detail["action"] == "approval.reserve":
                assert args["signer"].lower() == gate.roles["humanApprover"].lower() and int(args["action"]) == 0
            elif step.detail["action"] == "reserve.execute":
                assert int(args["amount"]) == 80_000_000
            elif step.detail["action"] == "procurement.invoice":
                assert int(args["invoiceAmount"]) == 72_000_000
            elif step.detail["action"] == "receipt.submit":
                assert args["recipient"].lower() == gate.roles["recipient"].lower()
                assert args["receiptDigest"] == receipt_request["digest"]
                assert args["receiptEvidenceHash"] == receipt_request["typedData"]["message"]["receiptEvidenceHash"]
                at_receipt = gate.call("PoGRegistryV2", "getProcurement", procurement_id,
                                       block_identifier=actual_receipt["blockNumber"])
                assert int(at_receipt[-1]) == 6 and _hex(at_receipt[13]) == args["receiptDigest"]
            trace.append({"action": step.detail["action"], "expectedEvent": expected, "event": args,
                          "transactionHash": tx.tx_hash, "blockNumber": actual_receipt["blockNumber"],
                          "blockHash": tx.block_hash, "receiptStatus": 1, "canonical": True})
        assert all(item.status == "confirmed" for item in session.scalars(select(SigningRequest)))
    print("CEO_A2_JOINT_ACCEPTANCE=" + json.dumps({
        "runId": gate.run_id, "instanceId": gate.instance_id, "rpcUrl": gate.rpc_url,
        "projectId": project_id, "procurementId": procurement_id, "state": "RecipientReceiptConfirmed",
        "donors": donor_facts, "depositsAtomic": 100_000_000, "reservedAtomic": 80_000_000,
        "invoiceAtomic": 72_000_000, "releasedAtomic": 0, "freeLockedAtomic": 20_000_000,
        "foundationStablecoinChangeAtomic": foundation_after - foundation_before,
        "escrowStablecoinChangeAfterDonationAtomic": escrow_after - escrow_before,
        "humanApprover": identities["admin"]["walletAddress"], "recipient": identities["recipient"]["walletAddress"],
        "receiptDigest": receipt_request["digest"], "trace": trace,
        "limits": "Synthetic evidence/AI, MockHKD, local only; no Release/Payment/Close/Refund",
    }, sort_keys=True))
