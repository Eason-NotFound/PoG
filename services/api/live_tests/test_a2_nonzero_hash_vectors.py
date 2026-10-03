"""Contract verification fixtures only; later actions have no A2 HTTP route.

Uses an owned isolated valueless Anvil, synthetic evidence and independent
signers. It does not provision application users, write PostgreSQL, or call AI
or payment services. Later state is prepared solely to verify accepted hashes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from eth_utils import keccak

from pog_api.chain import LocalChainGateway
from pog_api.hash_vectors import assessment_id, final_evidence_hash, pre_evidence_hash
from pog_api.test_database import assert_safe_test_target
from pog_api.typed_data import (
    abi_hash, ai_assessment_typed, cancellation_terms_hash, close_terms_hash, digest,
    human_intent_typed, parties_hash, recipient_receipt_typed, release_terms_hash,
    reserve_terms_hash, settlement_terms_hash,
)


DATABASE_URL = os.environ["POG_TEST_DATABASE_URL"]
assert_safe_test_target(DATABASE_URL)
MANIFEST = Path(os.environ["POG_A2_LIVE_MANIFEST"]).resolve()
REPOSITORY = Path(__file__).resolve().parents[3]
ZERO = "0x" + "00" * 32


def _hex(value):
    return value.lower() if isinstance(value, str) else "0x" + bytes(value).hex()


def _leaf(label):
    return "0x" + keccak(text="A2 nonzero synthetic verification: " + label).hex()


def test_accepted_nonzero_evidence_assessment_and_five_terms_vectors():
    gateway = LocalChainGateway(MANIFEST, REPOSITORY)
    assert ":8545" not in gateway.rpc_url
    roles = gateway.roles
    result = []
    salt = uuid4().hex
    project_id = _leaf("project " + salt)
    main_id = _leaf("main procurement " + salt)
    cancel_id = _leaf("cancellable procurement " + salt)

    def send(contract, function, args, role):
        gateway.verify()
        tx_hash = getattr(gateway.contracts[contract].functions, function)(*args).transact(
            {"from": roles[role]}
        )
        receipt = gateway.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=10)
        assert int(receipt["status"]) == 1
        assert gateway.canonical(int(receipt["blockNumber"]), _hex(receipt["blockHash"]))

    def compare(name, inputs, python_hash, contract_hash):
        contract_hash = _hex(contract_hash)
        assert python_hash == contract_hash != ZERO
        result.append({"name": name, "inputs": inputs, "python": python_hash, "contract": contract_hash})
        return python_hash

    def assess(procurement_id, stage, evidence):
        nonce = int(gateway.call("PoGRegistryV2", "aiNonces", roles["aiSigner"]))
        deadline = gateway.latest_timestamp() + 300
        inputs = {
            "stage": stage, "procurement_id": procurement_id, "outcome": 1,
            "risk_score_bps": 2400, "evidence_hash": evidence,
            "report_hash": _leaf(f"synthetic Review report stage {stage} procurement {procurement_id}"),
            "signer": roles["aiSigner"], "nonce": nonce, "deadline": deadline,
        }
        identity = assessment_id(**inputs)
        message = {
            "stage": stage, "procurementId": procurement_id, "assessmentId": identity,
            "outcome": 1, "riskScoreBps": 2400, "evidenceHash": evidence,
            "reportHash": inputs["report_hash"], "signer": roles["aiSigner"],
            "nonce": nonce, "deadline": deadline,
        }
        ordered = [message[key] for key in (
            "stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
            "evidenceHash", "reportHash", "signer", "nonce", "deadline",
        )]
        compare(f"assessmentId-stage-{stage}", inputs, identity,
                gateway.call("PoGRegistryV2", "computeAssessmentId", ordered))
        typed = ai_assessment_typed(message, 31337, gateway.contract_address("PoGRegistryV2"))
        compare(f"assessmentDigest-stage-{stage}", message, digest(typed),
                gateway.call("PoGRegistryV2", "assessmentDigest", ordered))
        signature = gateway.sign_typed_data(roles["aiSigner"], typed)
        send("PoGRegistryV2", "submitAIAssessment", [ordered, signature], "relayer")
        return identity

    def vote(target_id, action, terms, assessment=ZERO, reserve_amount=None):
        epoch = int(gateway.call("ProcurementEscrowV2", "getLedger", project_id)[9])
        message = {
            "targetId": target_id, "action": action, "termsHash": terms,
            "assessmentId": assessment, "signer": roles["humanApprover"],
            "nonce": int(gateway.call("ProcurementEscrowV2", "humanNonces", roles["humanApprover"])),
            "deadline": gateway.latest_timestamp() + 300, "policyEpoch": epoch,
        }
        typed = human_intent_typed(message, 31337, gateway.contract_address("ProcurementEscrowV2"))
        ordered = [message[key] for key in (
            "targetId", "action", "termsHash", "assessmentId", "signer", "nonce", "deadline", "policyEpoch",
        )]
        compare(f"humanDigest-action-{action}", message, digest(typed),
                gateway.call("ProcurementEscrowV2", "intentDigest", ordered))
        signature = gateway.sign_typed_data(roles["humanApprover"], typed)
        function = ("submitReserveApproval", "submitReleaseApproval", "submitSettlementApproval",
                    "submitCancellationApproval", "submitCloseApproval")[action]
        args = ([target_id, reserve_amount, ordered, signature] if action == 0
                else [target_id, ordered, signature])
        send("ProcurementEscrowV2", function, args, "relayer")

    parties = {
        "project_id": project_id, "foundation": roles["foundation"], "recipient": roles["recipient"],
        "vendor": roles["vendor"], "asset": gateway.contract_address("MockHKD"),
    }
    send("PoGRegistryV2", "createProject", [
        project_id, roles["recipient"], parties["asset"], [roles["humanApprover"]], 1,
    ], "foundation")
    for donor_role, amount in (("donorA", 60000000), ("donorB", 40000000)):
        send("MockHKD", "approve", [gateway.contract_address("ProcurementEscrowV2"), amount], donor_role)
        send("ProcurementEscrowV2", "deposit", [project_id, amount], donor_role)
    party_hash = parties_hash(project_id, roles["foundation"], roles["recipient"], roles["vendor"], parties["asset"])
    domains = {name: "0x" + keccak(text=text).hex() for name, text in {
        "reserve": "POG_V2_RESERVE_TERMS", "release": "POG_V2_RELEASE_TERMS",
        "settlement": "POG_V2_SETTLEMENT_TERMS", "cancel": "POG_V2_CANCEL_TERMS",
        "close": "POG_V2_CLOSE_TERMS",
    }.items()}

    def prepare_reserved(procurement_id, amount, invoice):
        leaves = {name: _leaf(f"{procurement_id} {name}") for name in ("po", "request", "goodsRequest", "invoice", "goods")}
        send("PoGRegistryV2", "createProcurement", [procurement_id, project_id, roles["vendor"], amount], "foundation")
        send("PoGRegistryV2", "recordPurchaseOrder", [procurement_id, leaves["po"], leaves["request"], leaves["goodsRequest"]], "foundation")
        pre_inputs = {
            **parties, "procurement_id": procurement_id, "budget_cap": amount,
            "po_hash": leaves["po"], "request_hash": leaves["request"], "goods_request_hash": leaves["goodsRequest"],
        }
        pre_hash = compare("preEvidence", pre_inputs, pre_evidence_hash(**pre_inputs),
                           gateway.call("PoGRegistryV2", "computePreEvidenceHash", procurement_id))
        identity = assess(procurement_id, 0, pre_hash)
        assert int(gateway.call("PoGRegistryV2", "getProcurement", procurement_id)[-1]) == 2
        reserve_inputs = {"domain": domains["reserve"], "parties": party_hash, "procurement_id": procurement_id,
                          "budget": amount, "amount": amount, "evidence": pre_hash, "assessment": identity}
        terms = compare("reserveTerms", reserve_inputs, reserve_terms_hash(**reserve_inputs),
                        gateway.call("ProcurementEscrowV2", "reserveTermsHash", procurement_id, amount))
        vote(procurement_id, 0, terms, identity, amount)
        send("ProcurementEscrowV2", "executeReserve", [procurement_id, amount], "foundation")
        send("PoGRegistryV2", "recordInvoiceAndGoods", [procurement_id, leaves["invoice"], invoice, leaves["goods"]], "foundation")
        return leaves

    main_leaves = prepare_reserved(main_id, 80000000, 72000000)
    receipt_message = {
        "projectId": project_id, "procurementId": main_id, "expectedRecipient": roles["recipient"],
        "vendor": roles["vendor"], "poHash": main_leaves["po"], "invoiceHash": main_leaves["invoice"],
        "invoiceAmount": 72000000, "goodsHash": main_leaves["goods"], "receiptEvidenceHash": _leaf("recipient synthetic evidence " + salt),
        "nonce": int(gateway.call("PoGRegistryV2", "recipientNonces", roles["recipient"])),
        "deadline": gateway.latest_timestamp() + 300,
    }
    receipt_typed = recipient_receipt_typed(receipt_message, 31337, gateway.contract_address("PoGRegistryV2"))
    receipt_digest = digest(receipt_typed)
    receipt_ordered = [receipt_message[key] for key in (
        "projectId", "procurementId", "expectedRecipient", "vendor", "poHash", "invoiceHash",
        "invoiceAmount", "goodsHash", "receiptEvidenceHash", "nonce", "deadline",
    )]
    send("PoGRegistryV2", "submitRecipientReceipt", [
        receipt_ordered, gateway.sign_typed_data(roles["recipient"], receipt_typed),
    ], "relayer")
    assert _hex(gateway.call("PoGRegistryV2", "getProcurement", main_id)[13]) == receipt_digest
    receipt_ledger = gateway.call("ProcurementEscrowV2", "getLedger", project_id)
    receipt_checkpoint = {
        "depositsAtomic": int(receipt_ledger[1]), "reservedAtomic": int(receipt_ledger[2]),
        "releasedAtomic": int(receipt_ledger[3]),
        "freeLockedAtomic": int(gateway.call("ProcurementEscrowV2", "freeLocked", project_id)),
    }
    assert receipt_checkpoint == {
        "depositsAtomic": 100000000, "reservedAtomic": 80000000,
        "releasedAtomic": 0, "freeLockedAtomic": 20000000,
    }
    final_inputs = {
        **parties, "procurement_id": main_id, "reserved_amount": 80000000, "po_hash": main_leaves["po"],
        "invoice_hash": main_leaves["invoice"], "invoice_amount": 72000000,
        "goods_hash": main_leaves["goods"], "receipt_digest": receipt_digest,
    }
    final_hash = compare("finalEvidence", final_inputs, final_evidence_hash(**final_inputs),
                         gateway.call("PoGRegistryV2", "computeFinalEvidenceHash", main_id))
    final_identity = assess(main_id, 1, final_hash)
    release_inputs = {"domain": domains["release"], "parties": party_hash, "procurement_id": main_id,
                      "reserved": 80000000, "invoice": 72000000, "evidence": final_hash, "assessment": final_identity}
    release_terms = compare("releaseTerms", release_inputs, release_terms_hash(**release_inputs),
                            gateway.call("ProcurementEscrowV2", "releaseTermsHash", main_id))
    vote(main_id, 1, release_terms, final_identity)
    send("ProcurementEscrowV2", "executeRelease", [main_id], "foundation")
    send("PoGRegistryV2", "recordSettlement", [main_id, _leaf("synthetic conversion " + salt), _leaf("synthetic payment " + salt)], "foundation")
    settlement_hash = _hex(gateway.call("PoGRegistryV2", "getEscrowProcurement", main_id)[10])
    settlement_inputs = {"domain": domains["settlement"], "parties": party_hash, "procurement_id": main_id,
                         "invoice": 72000000, "settlement": settlement_hash}
    terms = compare("settlementTerms", settlement_inputs, settlement_terms_hash(**settlement_inputs),
                    gateway.call("ProcurementEscrowV2", "settlementTermsHash", main_id))
    vote(main_id, 2, terms)
    send("ProcurementEscrowV2", "executeSettlementConfirmation", [main_id], "foundation")

    prepare_reserved(cancel_id, 10000000, 9000000)
    reason = _leaf("synthetic cancellation reason " + salt)
    send("ProcurementEscrowV2", "requestReservedCancellation", [cancel_id, reason], "foundation")
    cancellation_view = gateway.call("PoGRegistryV2", "getEscrowProcurement", cancel_id)
    cancellation_inputs = {"domain": domains["cancel"], "parties": party_hash, "procurement_id": cancel_id,
                           "reserved": 10000000, "evidence": _hex(cancellation_view[11]), "reason": reason}
    assert cancellation_inputs["evidence"] != ZERO and cancellation_inputs["reason"] != ZERO
    terms = compare("cancellationTerms", cancellation_inputs, cancellation_terms_hash(**cancellation_inputs),
                    gateway.call("ProcurementEscrowV2", "cancellationTermsHash", cancel_id))
    vote(cancel_id, 3, terms)
    send("ProcurementEscrowV2", "executeReservedCancellation", [cancel_id], "foundation")
    send("PoGRegistryV2", "requestClosing", [project_id], "foundation")
    ledger = gateway.call("ProcurementEscrowV2", "getLedger", project_id)
    project = gateway.call("PoGRegistryV2", "getProject", project_id)
    assert int(project[7]) == 1 and int(project[8]) == int(ledger[2]) == 0
    refund_pool = int(ledger[1]) - (int(ledger[3]) - int(ledger[4])) - int(ledger[5])
    ledger_hash = abi_hash(
        ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "uint32"],
        [int(ledger[1]), int(ledger[4]), int(ledger[3]), int(ledger[5]), int(ledger[2]), refund_pool, int(ledger[7])],
    )
    closing_inputs = {"domain": domains["close"], "project_id": project_id,
                      "foundation": roles["foundation"], "recipient": roles["recipient"], "asset": parties["asset"],
                      "state": int(project[7]), "unresolved": int(project[8]), "ledger_hash": ledger_hash,
                      "epoch": int(ledger[9])}
    terms = compare("closeTerms", closing_inputs, close_terms_hash(**closing_inputs),
                    gateway.call("ProcurementEscrowV2", "closeTermsHash", project_id))
    vote(project_id, 4, terms)
    assert (int(ledger[1]), int(ledger[3]), refund_pool) == (100000000, 72000000, 28000000)
    # This fixture stops before executeClose/refund: its purpose is hash verification.
    assert len(result) == 21
    print(json.dumps({
        "fixture": "synthetic-direct-contract-hash-verification", "comparisonCount": len(result),
        "receiptConfirmedCheckpoint": receipt_checkpoint, "vectors": result,
    }, sort_keys=True))
