"""Actual accepted V2 contracts: synthetic PRE evidence and human renewal.

Owned loopback Anvil 18545 and guarded PostgreSQL only. No new HTTP actions,
contract changes, AI inference, payment rail, release, or settlement is used.
Review/Reject fixtures are signed external assessments, not an HTTP override.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from eth_utils import keccak
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from web3.exceptions import ContractLogicError, Web3RPCError

from pog_api.app import create_app
from pog_api.chain import LocalChainGateway
from pog_api.config import Settings
from pog_api.db import build_session_factory
from pog_api.models import ROLE_NAMES, SigningRequest, User, WalletAuthorization
from pog_api.security import hash_password
from pog_api.test_database import assert_safe_test_target, build_safe_test_engine
from pog_api.typed_data import ai_assessment_typed
from pog_api.worker import ChainIndexer, ChainWorker
from test_a2_receipt_confirmed import PASSWORD, _auth, _drain, _login, _upload


DATABASE_URL = os.environ.get("POG_TEST_DATABASE_URL") or ""
# Must precede engine construction and every destructive fixture action.
assert_safe_test_target(DATABASE_URL)
MANIFEST = Path(os.environ["POG_A2_LIVE_MANIFEST"]).resolve()
REPOSITORY = Path(__file__).resolve().parents[3]
AMOUNT = 80_000_000
AI_FIELDS = ("stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
             "evidenceHash", "reportHash", "signer", "nonce", "deadline")
HUMAN_FIELDS = ("targetId", "action", "termsHash", "assessmentId", "signer", "nonce",
                "deadline", "policyEpoch")


def _hex(value):
    return "0x" + bytes(value).hex()


@pytest.fixture
def renewal_env(tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["chain"]["rpcUrl"] == "http://127.0.0.1:18545"
    gateway = LocalChainGateway(MANIFEST, REPOSITORY)
    gateway.verify()
    engine = build_safe_test_engine(DATABASE_URL)
    tables = [
        "signing_requests", "policy_projections", "donor_credit_projections", "ledger_projections",
        "receipt_proofs", "document_versions", "risk_reports", "payment_operation_links",
        "documents", "approval_records", "procurements", "operation_steps", "chain_transactions",
        "audit_logs", "wallet_authorizations", "sessions", "projects", "operations",
        "indexer_cursors", "chain_events", "users", "deployment_instances",
    ]
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ",".join(tables) + " CASCADE"))
        for role in ROLE_NAMES:
            connection.execute(text("INSERT INTO roles(name) VALUES (:name) ON CONFLICT DO NOTHING"), {"name": role})
    factory = build_session_factory(engine)
    specs = [
        ("foundation", "foundation", "foundation"), ("recipient", "recipient", "recipient"),
        ("donor", "donor", "donorA"), ("admin", "human_approver", "humanApprover"),
        ("service-ai-fixture", "service_ai", "aiSigner"), ("donor-fixture-b", "donor", "donorB"),
    ]
    ids = {}
    with factory() as session, session.begin():
        for username, role, manifest_role in specs:
            user = User(username=username, display_name=username, password_hash=hash_password(PASSWORD), active=True)
            session.add(user)
            session.flush()
            session.add(WalletAuthorization(user_id=user.id, role_name=role,
                                           wallet_address=manifest["roles"][manifest_role], active=True))
            ids[username] = str(user.id)
    settings = Settings(database_url=DATABASE_URL, storage_root=tmp_path / "evidence", chain_enabled=True,
                        chain_manifest=MANIFEST, demo_signing_enabled=True)
    worker, indexer = ChainWorker(DATABASE_URL, gateway), ChainIndexer(DATABASE_URL, gateway)
    try:
        with TestClient(create_app(settings)) as client:
            tokens = {name: _login(client, name) for name, _role, _manifest_role in specs}

            def post(path, body, actor, key):
                return client.post(path, json=body, headers=_auth(tokens[actor], key))

            def confirmed(path, body, actor, key):
                response = post(path, body, actor, key)
                assert response.status_code == 202, response.text
                _drain(worker, indexer, factory, response.json()["operation"]["operationId"])
                return response.json()

            response = post("/v2/projects", {
                "title": "Synthetic renewal regression", "publicSummary": "Isolated valueless test only",
                "recipientUserId": ids["recipient"], "humanApproverUserId": ids["admin"],
            }, "foundation", "project-draft")
            assert response.status_code == 202, response.text
            project = response.json()["project"]
            confirmed(f"/v2/projects/{project['id']}/chain/create", {}, "foundation", "project-create")
            for actor, amount in (("donor", 60_000_000), ("donor-fixture-b", 40_000_000)):
                confirmed(f"/v2/projects/{project['id']}/donations", {"amountAtomic": str(amount)}, actor, f"donate-{actor}")
            response = post("/v2/procurements", {
                "projectId": project["id"], "title": "Synthetic PRE renewal procurement",
                "vendorWallet": gateway.roles["vendor"], "budgetCapAtomic": str(AMOUNT),
            }, "foundation", "procurement-draft")
            assert response.status_code == 202, response.text
            procurement = response.json()["procurement"]
            confirmed(f"/v2/procurements/{procurement['id']}/chain/create", {}, "foundation", "procurement-create")
            versions = {category: _upload(client, tokens["foundation"], procurement["id"], category, f"upload-{category}")
                        for category in ("purchase_order", "request", "goods_request")}
            confirmed(f"/v2/procurements/{procurement['id']}/chain/purchase-order", {
                "poDocumentVersionId": versions["purchase_order"]["versionId"],
                "requestDocumentVersionId": versions["request"]["versionId"],
                "goodsRequestDocumentVersionId": versions["goods_request"]["versionId"],
            }, "foundation", "po-record")
            # Fixture baseline catches up pre-existing test history. Recovery is
            # not under test here; subsequent external assessment uses ordinary once().
            indexer.rebuild()
            yield SimpleNamespace(client=client, gateway=gateway, factory=factory, worker=worker, indexer=indexer,
                                  project=project, procurement=procurement, tokens=tokens, post=post, confirmed=confirmed)
    finally:
        worker.close()
        indexer.close()
        engine.dispose()


def _request(env, kind, key, ttl=3600):
    body = {"kind": kind, "deadlineTtlSeconds": ttl}
    actor = "service-ai-fixture" if kind == "ai_pre" else "admin"
    if kind == "reserve":
        body["reserveAmountAtomic"] = str(AMOUNT)
    response = env.post(f"/v2/procurements/{env.procurement['id']}/signing-requests", body, actor, key)
    assert response.status_code == 202, response.text
    return response.json()["signingRequest"]


def _sign(env, request, key):
    actor = "service-ai-fixture" if request["kind"] == "ai_pre" else "admin"
    response = env.post(f"/v2/signing-requests/{request['id']}/sign-demo", {"confirm": True}, actor, key)
    assert response.status_code == 202, response.text
    # Read the signature resulting from explicit user confirmation; do not invoke
    # a second hidden signing action or expose the private signature in logs.
    with env.factory() as session:
        return session.get(SigningRequest, UUID(request["id"])).signature


def _submit(env, request, signature, key):
    actor = "service-ai-fixture" if request["kind"] == "ai_pre" else "admin"
    return env.confirmed(f"/v2/signing-requests/{request['id']}/submit", {"signature": signature}, actor, key)


def _assess(env, key, ttl=3600):
    request = _request(env, "ai_pre", key + "-request", ttl)
    signature = _sign(env, request, key + "-sign")
    _submit(env, request, signature, key + "-submit")
    return request, signature


def _reserve(env, key):
    env.confirmed(f"/v2/procurements/{env.procurement['id']}/chain/reserve",
                  {"reserveAmountAtomic": str(AMOUNT)}, "foundation", key)
    ledger = env.gateway.call("ProcurementEscrowV2", "getLedger", env.project["businessId"])
    assert (int(ledger[1]), int(ledger[2]), int(ledger[3])) == (100_000_000, AMOUNT, 0)
    assert int(env.gateway.call("ProcurementEscrowV2", "freeLocked", env.project["businessId"])) == 20_000_000
    assert int(env.gateway.call("PoGRegistryV2", "getProcurement", env.procurement["businessId"])[-1]) == 4


def _expire(env, deadline):
    assert env.gateway.rpc_url == "http://127.0.0.1:18545"
    for method, params in (("evm_setNextBlockTimestamp", [int(deadline) + 1]), ("evm_mine", [])):
        response = env.gateway.w3.provider.make_request(method, params)
        assert "error" not in response, response
    assert env.gateway.latest_timestamp() > int(deadline)


def _reserve_call_reverts(env):
    with pytest.raises((ContractLogicError, Web3RPCError)):
        env.gateway.contracts["ProcurementEscrowV2"].functions.executeReserve(
            env.procurement["businessId"], AMOUNT,
        ).call({"from": env.gateway.roles["foundation"]})
    assert int(env.gateway.call("ProcurementEscrowV2", "getLedger", env.project["businessId"])[2]) == 0


@pytest.mark.parametrize("outcome", [1, 2], ids=["Review", "Reject"])
def test_authenticated_review_or_reject_pre_is_evidence_and_human_decides(renewal_env, outcome):
    env, gate = renewal_env, renewal_env.gateway
    view = gate.call("PoGRegistryV2", "getProcurement", env.procurement["businessId"])
    message = {
        "stage": 0, "procurementId": env.procurement["businessId"], "assessmentId": "0x" + "00" * 32,
        "outcome": outcome, "riskScoreBps": 4000 if outcome == 1 else 9000,
        "evidenceHash": _hex(view[7]), "reportHash": _hex(keccak(text=f"Synthetic external outcome {outcome}")),
        "signer": gate.roles["aiSigner"], "nonce": int(gate.call("PoGRegistryV2", "aiNonces", gate.roles["aiSigner"])),
        "deadline": gate.latest_timestamp() + 3600,
    }
    message["assessmentId"] = _hex(gate.call("PoGRegistryV2", "computeAssessmentId", [message[key] for key in AI_FIELDS]))
    typed = ai_assessment_typed(message, 31337, gate.contract_address("PoGRegistryV2"))
    signature = gate.sign_typed_data(gate.roles["aiSigner"], typed)
    envelope = gate.prepare("assessment.ai_pre", gate.roles["relayer"], [[message[key] for key in AI_FIELDS], signature],
                            "AIAssessmentRecorded")
    tx_hash = gate.send(envelope)
    receipt = gate.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=10)
    assert int(receipt["status"]) == 1
    assert gate.canonical(int(receipt["blockNumber"]), _hex(receipt["blockHash"]))
    assert env.indexer.once() is True
    assessment = gate.call("PoGRegistryV2", "getAssessment", message["assessmentId"])
    assert int(assessment[3]) == outcome and _hex(assessment[5]) == message["evidenceHash"]
    assert int(gate.call("PoGRegistryV2", "aiNonces", gate.roles["aiSigner"])) == message["nonce"] + 1
    no_vote = env.post(f"/v2/procurements/{env.procurement['id']}/chain/reserve",
                       {"reserveAmountAtomic": str(AMOUNT)}, "foundation", "no-human-vote")
    assert no_vote.status_code == 409, no_vote.text
    _reserve_call_reverts(env)
    human = _request(env, "reserve", "independent-human-request")
    assert human["typedData"]["message"]["assessmentId"] == message["assessmentId"]
    _submit(env, human, _sign(env, human, "human-explicit-confirm"), "human-vote-submit")
    _reserve(env, "human-approved-execute")


def test_expired_onchain_ai_renews_assessment_and_terms_old_bundle_rejected(renewal_env):
    env, gate = renewal_env, renewal_env.gateway
    old_ai, _ = _assess(env, "old-ai", ttl=60)
    old_human = _request(env, "reserve", "old-human-request")
    old_signature = _sign(env, old_human, "old-human-confirm")
    old_message = old_human["typedData"]["message"]
    _expire(env, old_ai["typedData"]["message"]["deadline"])
    _reserve_call_reverts(env)
    renewed_ai, _ = _assess(env, "fresh-ai")
    new_ai_message = renewed_ai["typedData"]["message"]
    assert new_ai_message["nonce"] == old_ai["typedData"]["message"]["nonce"] + 1
    assert new_ai_message["deadline"] > old_ai["typedData"]["message"]["deadline"]
    assert new_ai_message["assessmentId"] != old_message["assessmentId"]
    assert _hex(gate.call("PoGRegistryV2", "getProcurement", env.procurement["businessId"])[8]) == new_ai_message["assessmentId"]
    assert _hex(gate.call("ProcurementEscrowV2", "reserveTermsHash", env.procurement["businessId"], AMOUNT)) != old_message["termsHash"]
    old_submit = env.post(f"/v2/signing-requests/{old_human['id']}/submit", {"signature": old_signature}, "admin", "old-bundle-submit")
    assert old_submit.status_code == 409, old_submit.text
    with pytest.raises((ContractLogicError, Web3RPCError)):
        gate.contracts["ProcurementEscrowV2"].functions.submitReserveApproval(
            env.procurement["businessId"], AMOUNT, [old_message[key] for key in HUMAN_FIELDS], old_signature,
        ).call({"from": gate.roles["relayer"]})
    fresh_human = _request(env, "reserve", "fresh-human-request")
    fresh_message = fresh_human["typedData"]["message"]
    assert fresh_message["nonce"] == old_message["nonce"] and fresh_message["assessmentId"] == new_ai_message["assessmentId"]
    assert fresh_message["termsHash"] != old_message["termsHash"]
    wrong = env.post(f"/v2/signing-requests/{fresh_human['id']}/submit", {"signature": old_signature}, "admin", "old-signature-new-bundle")
    assert wrong.status_code == 422, wrong.text
    _submit(env, fresh_human, _sign(env, fresh_human, "fresh-human-confirm"), "fresh-human-vote")
    _reserve(env, "fresh-ai-and-human-execute")


def test_expired_onchain_human_ticket_renews_current_nonce_and_fresh_deadline(renewal_env):
    env, gate = renewal_env, renewal_env.gateway
    _assess(env, "long-ai")
    old_human = _request(env, "reserve", "short-human-request", ttl=60)
    old_signature = _sign(env, old_human, "short-human-confirm")
    _submit(env, old_human, old_signature, "short-human-vote")
    old_message = old_human["typedData"]["message"]
    assert int(gate.call("PoGRegistryV2", "getProcurement", env.procurement["businessId"])[-1]) == 3
    _expire(env, old_message["deadline"])
    _reserve_call_reverts(env)
    stale = env.post(f"/v2/signing-requests/{old_human['id']}/submit", {"signature": old_signature}, "admin", "old-vote-new-key")
    assert stale.status_code == 409, stale.text
    fresh_human = _request(env, "reserve", "renew-human-request", ttl=300)
    message = fresh_human["typedData"]["message"]
    assert message["nonce"] == old_message["nonce"] + 1
    assert message["deadline"] > old_message["deadline"]
    assert message["assessmentId"] == old_message["assessmentId"] and message["termsHash"] == old_message["termsHash"]
    wrong = env.post(f"/v2/signing-requests/{fresh_human['id']}/submit", {"signature": old_signature}, "admin", "old-signature-fresh-ticket")
    assert wrong.status_code == 422, wrong.text
    _submit(env, fresh_human, _sign(env, fresh_human, "renew-human-confirm"), "renew-human-vote")
    with env.factory() as session:
        assert session.get(SigningRequest, UUID(old_human["id"])).status == "confirmed"
        assert session.get(SigningRequest, UUID(fresh_human["id"])).status == "confirmed"
    _reserve(env, "renewed-human-execute")
