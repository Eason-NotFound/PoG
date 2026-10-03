from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import text

from pog_api.app import create_app
from pog_api.chain import LocalChainGateway
from pog_api.config import Settings
from pog_api.db import build_session_factory
from pog_api.models import ROLE_NAMES, Role, User, WalletAuthorization
from pog_api.security import hash_password
from pog_api.test_database import assert_safe_test_target as assert_strict_safe_test_target, build_safe_test_engine
from pog_api.test_db_safety import assert_safe_test_target
from pog_api.worker import ChainIndexer, ChainWorker
from pog_api.typed_data import (
    abi_hash, cancellation_terms_hash, close_terms_hash, parties_hash,
    release_terms_hash, reserve_terms_hash, settlement_terms_hash,
)


DATABASE_URL = os.environ.get("POG_TEST_DATABASE_URL") or ""
assert_strict_safe_test_target(DATABASE_URL)
MANIFEST = Path(os.environ["POG_A2_LIVE_MANIFEST"]).resolve()
REPOSITORY = Path(__file__).resolve().parents[3]
PASSWORD = "A2 isolated demo password 123!"


def _pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _auth(token: str, key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if key:
        result["Idempotency-Key"] = key
    return result


def _login(client: TestClient, username: str) -> str:
    response = client.post("/v2/sessions", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _drain(worker: ChainWorker, indexer: ChainIndexer, factory, operation_id: str) -> None:
    for _ in range(20):
        worker.once()
        indexer.once()
        with factory() as session:
            status = session.execute(
                text("SELECT status FROM operations WHERE id=:id"), {"id": operation_id}
            ).scalar_one()
        if status == "confirmed":
            return
        assert status not in {"failed", "requires_attention", "invalidated_instance"}, status
    raise AssertionError(f"operation did not confirm: {operation_id}")


def _upload(client, token, procurement_id, category, key):
    response = client.post(
        "/v2/documents", data={"procurementId": procurement_id, "category": category},
        files={"file": (f"{category}.pdf", _pdf(), "application/pdf")},
        headers=_auth(token, key),
    )
    assert response.status_code == 202, response.text
    return response.json()["document"]


def test_real_anvil_path_stops_at_receipt_confirmed(tmp_path):
    assert_safe_test_target(
        DATABASE_URL, managed_state=os.getenv("POG_MANAGED_POSTGRES_STATE"),
        ci=os.getenv("CI"), target_confirmed=os.getenv("POG_TEST_TARGET_CONFIRMED"),
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    engine = build_safe_test_engine(DATABASE_URL)
    tables = [
        "signing_requests", "policy_projections", "donor_credit_projections", "ledger_projections",
        "receipt_proofs", "document_versions", "risk_reports", "payment_operation_links",
        "documents", "approval_records", "procurements", "operation_steps",
        "chain_transactions", "audit_logs", "wallet_authorizations", "sessions", "projects",
        "operations", "indexer_cursors", "chain_events", "users", "deployment_instances",
    ]
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ",".join(tables) + " CASCADE"))
        for role in ROLE_NAMES:
            connection.execute(text("INSERT INTO roles(name) VALUES (:name) ON CONFLICT DO NOTHING"), {"name": role})
    factory = build_session_factory(engine)
    specs = [
        ("foundation", "foundation", "foundation"),
        ("recipient", "recipient", "recipient"),
        ("donor", "donor", "donorA"),
        ("admin", "human_approver", "humanApprover"),
        ("service-ai-fixture", "service_ai", "aiSigner"),
        ("donor-fixture-b", "donor", "donorB"),
    ]
    ids = {}
    with factory() as session, session.begin():
        for username, role, manifest_role in specs:
            user = User(
                username=username, display_name=username, password_hash=hash_password(PASSWORD), active=True,
            )
            session.add(user)
            session.flush()
            session.add(WalletAuthorization(
                user_id=user.id, role_name=role,
                wallet_address=manifest["roles"][manifest_role], active=True,
            ))
            ids[username] = user.id
    settings = Settings(
        database_url=DATABASE_URL, storage_root=tmp_path / "evidence",
        chain_enabled=True, chain_manifest=MANIFEST, demo_signing_enabled=True,
    )
    gateway = LocalChainGateway(MANIFEST, REPOSITORY)
    worker = ChainWorker(DATABASE_URL, gateway)
    indexer = ChainIndexer(DATABASE_URL, gateway)
    try:
        with TestClient(create_app(settings)) as client:
            tokens = {name: _login(client, name) for name, _role, _manifest_role in specs}

            project_response = client.post(
                "/v2/projects",
                json={
                    "title": "A2 isolated chain project", "publicSummary": "Synthetic local test only.",
                    "recipientUserId": str(ids["recipient"]),
                    "humanApproverUserId": str(ids["admin"]),
                },
                headers=_auth(tokens["foundation"], "live-project-draft"),
            )
            assert project_response.status_code == 202, project_response.text
            project = project_response.json()["project"]
            queued = client.post(
                f"/v2/projects/{project['id']}/chain/create", json={},
                headers=_auth(tokens["foundation"], "live-project-chain"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            for username, amount in (("donor", "50000000"), ("donor-fixture-b", "30000000")):
                queued = client.post(
                    f"/v2/projects/{project['id']}/donations", json={"amountAtomic": amount},
                    headers=_auth(tokens[username], f"live-donation-{username}"),
                )
                assert queued.status_code == 202, queued.text
                _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            procurement_response = client.post(
                "/v2/procurements",
                json={
                    "projectId": project["id"], "title": "A2 medical kit",
                    "vendorWallet": manifest["roles"]["vendor"], "budgetCapAtomic": "70000000",
                },
                headers=_auth(tokens["foundation"], "live-procurement-draft"),
            )
            assert procurement_response.status_code == 202, procurement_response.text
            procurement = procurement_response.json()["procurement"]
            queued = client.post(
                f"/v2/procurements/{procurement['id']}/chain/create", json={},
                headers=_auth(tokens["foundation"], "live-procurement-chain"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            po = _upload(client, tokens["foundation"], procurement["id"], "purchase_order", "live-po-doc")
            request = _upload(client, tokens["foundation"], procurement["id"], "request", "live-request-doc")
            goods_request = _upload(client, tokens["foundation"], procurement["id"], "goods_request", "live-goods-request-doc")
            queued = client.post(
                f"/v2/procurements/{procurement['id']}/chain/purchase-order",
                json={
                    "poDocumentVersionId": po["versionId"], "requestDocumentVersionId": request["versionId"],
                    "goodsRequestDocumentVersionId": goods_request["versionId"],
                },
                headers=_auth(tokens["foundation"], "live-po-chain"),
            )
            assert queued.status_code == 202, queued.text
            pending_po = client.get(f"/v2/procurements/{procurement['id']}", headers=_auth(tokens["foundation"]))
            assert pending_po.json()["chainState"]["verified"] is False
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            ai_request = client.post(
                f"/v2/procurements/{procurement['id']}/signing-requests", json={"kind": "ai_pre"},
                headers=_auth(tokens["service-ai-fixture"], "live-ai-request"),
            )
            assert ai_request.status_code == 202, ai_request.text
            ai_typed = ai_request.json()["signingRequest"]["typedData"]
            ai_message = ai_typed["message"]
            assert "0x" + bytes(gateway.call(
                "PoGRegistryV2", "assessmentDigest", [ai_message[name] for name in (
                    "stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
                    "evidenceHash", "reportHash", "signer", "nonce", "deadline",
                )]
            )).hex() == ai_request.json()["signingRequest"]["digest"]
            ai_id = ai_request.json()["signingRequest"]["id"]
            signed = client.post(
                f"/v2/signing-requests/{ai_id}/sign-demo", json={"confirm": True},
                headers=_auth(tokens["service-ai-fixture"], "live-ai-sign"),
            )
            assert signed.status_code == 202, signed.text
            signature = gateway.sign_typed_data(
                manifest["roles"]["aiSigner"], ai_request.json()["signingRequest"]["typedData"]
            )
            queued = client.post(
                f"/v2/signing-requests/{ai_id}/submit", json={"signature": signature},
                headers=_auth(tokens["service-ai-fixture"], "live-ai-submit"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            reserve_request = client.post(
                f"/v2/procurements/{procurement['id']}/signing-requests",
                json={"kind": "reserve", "reserveAmountAtomic": "60000000"},
                headers=_auth(tokens["admin"], "live-reserve-request"),
            )
            assert reserve_request.status_code == 202, reserve_request.text
            reserve_typed = reserve_request.json()["signingRequest"]["typedData"]
            reserve_message = reserve_typed["message"]
            assert "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "intentDigest", [reserve_message[name] for name in (
                    "targetId", "action", "termsHash", "assessmentId", "signer", "nonce",
                    "deadline", "policyEpoch",
                )]
            )).hex() == reserve_request.json()["signingRequest"]["digest"]
            reserve_id = reserve_request.json()["signingRequest"]["id"]
            signed = client.post(
                f"/v2/signing-requests/{reserve_id}/sign-demo", json={"confirm": True},
                headers=_auth(tokens["admin"], "live-reserve-sign"),
            )
            assert signed.status_code == 202, signed.text
            signature = gateway.sign_typed_data(
                manifest["roles"]["humanApprover"], reserve_request.json()["signingRequest"]["typedData"]
            )
            queued = client.post(
                f"/v2/signing-requests/{reserve_id}/submit", json={"signature": signature},
                headers=_auth(tokens["admin"], "live-reserve-submit"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])
            queued = client.post(
                f"/v2/procurements/{procurement['id']}/chain/reserve",
                json={"reserveAmountAtomic": "60000000"},
                headers=_auth(tokens["foundation"], "live-reserve-execute"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            invoice = _upload(client, tokens["foundation"], procurement["id"], "invoice", "live-invoice-doc")
            goods = _upload(client, tokens["foundation"], procurement["id"], "goods_evidence", "live-goods-doc")
            queued = client.post(
                f"/v2/procurements/{procurement['id']}/chain/invoice-and-goods",
                json={
                    "invoiceDocumentVersionId": invoice["versionId"], "goodsDocumentVersionId": goods["versionId"],
                    "invoiceAmountAtomic": "55000000",
                },
                headers=_auth(tokens["foundation"], "live-invoice-chain"),
            )
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])

            receipt_evidence = _upload(
                client, tokens["recipient"], procurement["id"], "receipt_evidence", "live-receipt-doc"
            )
            receipt_request = client.post(
                f"/v2/procurements/{procurement['id']}/signing-requests",
                json={"kind": "receipt", "receiptEvidenceDocumentVersionId": receipt_evidence["versionId"]},
                headers=_auth(tokens["recipient"], "live-receipt-request"),
            )
            assert receipt_request.status_code == 202, receipt_request.text
            receipt_id = receipt_request.json()["signingRequest"]["id"]
            signed = client.post(
                f"/v2/signing-requests/{receipt_id}/sign-demo", json={"confirm": True},
                headers=_auth(tokens["recipient"], "live-receipt-sign"),
            )
            assert signed.status_code == 202, signed.text
            signature = gateway.sign_typed_data(
                manifest["roles"]["recipient"], receipt_request.json()["signingRequest"]["typedData"]
            )
            queued = client.post(
                f"/v2/signing-requests/{receipt_id}/submit", json={"signature": signature},
                headers=_auth(tokens["recipient"], "live-receipt-submit"),
            )
            assert queued.status_code == 202, queued.text
            receipt_operation_id = queued.json()["operation"]["operationId"]
            _drain(worker, indexer, factory, receipt_operation_id)

            replayed_submit = client.post(
                f"/v2/signing-requests/{receipt_id}/submit", json={"signature": signature},
                headers=_auth(tokens["recipient"], "live-receipt-submit"),
            )
            assert replayed_submit.status_code == 202, replayed_submit.text
            assert replayed_submit.json()["operation"]["operationId"] == receipt_operation_id
            assert replayed_submit.json()["operation"]["replayed"] is True
            assert replayed_submit.json()["operation"]["status"] == "confirmed"

            operation_get = client.get(
                f"/v2/operations/{receipt_operation_id}", headers=_auth(tokens["recipient"])
            )
            assert operation_get.status_code == 200, operation_get.text
            operation_fact = operation_get.json()
            assert operation_fact["chainVerified"] is True
            assert operation_fact["steps"][0]["expectedEvent"] == "RecipientReceiptAccepted"
            assert operation_fact["steps"][0]["transaction"]["receiptStatus"] == 1
            assert operation_fact["steps"][0]["transaction"]["canonical"] is True

            procurement_get = client.get(
                f"/v2/procurements/{procurement['id']}", headers=_auth(tokens["foundation"])
            )
            assert procurement_get.json()["chainState"]["status"] == "receipt_confirmed"
            ledger = client.get(
                f"/v2/projects/{project['id']}/ledger", headers=_auth(tokens["donor"])
            ).json()
            assert ledger["depositsAtomic"] == "80000000"
            assert ledger["reservedAtomic"] == "60000000"
            assert ledger["currentCallerDonorCreditAtomic"] == "50000000"
            chain_procurement = gateway.call("PoGRegistryV2", "getProcurement", procurement["businessId"])
            assert int(chain_procurement[-1]) == 6
            assert "0x" + bytes(chain_procurement[13]).hex() != "0x" + "00" * 32
            with factory() as session:
                stored_receipt_status, stored_receipt_digest = session.execute(text(
                    "SELECT status,digest FROM signing_requests WHERE id=:id"
                ), {"id": receipt_id}).one()
                submitted_signing_statuses = session.execute(text(
                    "SELECT status FROM signing_requests WHERE submitted_operation_id IS NOT NULL"
                )).scalars().all()
            assert stored_receipt_status == "confirmed"
            assert submitted_signing_statuses == ["confirmed", "confirmed", "confirmed"]
            assert stored_receipt_digest == "0x" + bytes(chain_procurement[13]).hex()

            project_view = gateway.call("PoGRegistryV2", "getProject", project["businessId"])
            escrow_view = gateway.call("PoGRegistryV2", "getEscrowProcurement", procurement["businessId"])
            ledger_view = gateway.call("ProcurementEscrowV2", "getLedger", project["businessId"])
            parties = parties_hash(
                project["businessId"], project_view[1], project_view[2], escrow_view[2], project_view[3]
            )
            domains = {
                name: "0x" + bytes(gateway.call("ProcurementEscrowV2", name)).hex()
                for name in (
                    "RESERVE_TERMS_DOMAIN", "RELEASE_TERMS_DOMAIN", "SETTLEMENT_TERMS_DOMAIN",
                    "CANCEL_TERMS_DOMAIN", "CLOSE_TERMS_DOMAIN",
                )
            }
            assert reserve_terms_hash(
                domains["RESERVE_TERMS_DOMAIN"], parties, procurement["businessId"],
                int(escrow_view[3]), 60000000, "0x" + bytes(escrow_view[4]).hex(),
                "0x" + bytes(escrow_view[5]).hex(),
            ) == "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "reserveTermsHash", procurement["businessId"], 60000000
            )).hex()
            assert release_terms_hash(
                domains["RELEASE_TERMS_DOMAIN"], parties, procurement["businessId"],
                int(escrow_view[6]), int(escrow_view[7]), "0x" + bytes(escrow_view[8]).hex(),
                "0x" + bytes(escrow_view[9]).hex(),
            ) == "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "releaseTermsHash", procurement["businessId"]
            )).hex()
            assert settlement_terms_hash(
                domains["SETTLEMENT_TERMS_DOMAIN"], parties, procurement["businessId"],
                int(escrow_view[7]), "0x" + bytes(escrow_view[10]).hex(),
            ) == "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "settlementTermsHash", procurement["businessId"]
            )).hex()
            assert cancellation_terms_hash(
                domains["CANCEL_TERMS_DOMAIN"], parties, procurement["businessId"],
                int(escrow_view[6]), "0x" + bytes(escrow_view[11]).hex(),
                "0x" + bytes(escrow_view[12]).hex(),
            ) == "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "cancellationTermsHash", procurement["businessId"]
            )).hex()
            refund_pool = int(ledger_view[1]) - (int(ledger_view[3]) - int(ledger_view[4])) - int(ledger_view[5])
            ledger_hash = abi_hash(
                ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "uint32"],
                [int(ledger_view[1]), int(ledger_view[4]), int(ledger_view[3]), int(ledger_view[5]),
                 int(ledger_view[2]), refund_pool, int(ledger_view[7])],
            )
            assert close_terms_hash(
                domains["CLOSE_TERMS_DOMAIN"], project["businessId"], project_view[1],
                project_view[2], project_view[3], int(project_view[7]), int(project_view[8]),
                ledger_hash, int(ledger_view[9]),
            ) == "0x" + bytes(gateway.call(
                "ProcurementEscrowV2", "closeTermsHash", project["businessId"]
            )).hex()

            snapshot = gateway.w3.provider.make_request("evm_snapshot", [])["result"]
            reorg_donation = client.post(
                f"/v2/projects/{project['id']}/donations", json={"amountAtomic": "1000000"},
                headers=_auth(tokens["donor"], "live-reorg-donation"),
            )
            assert reorg_donation.status_code == 202, reorg_donation.text
            reorg_operation = reorg_donation.json()["operation"]["operationId"]
            _drain(worker, indexer, factory, reorg_operation)
            assert gateway.call(
                "ProcurementEscrowV2", "donorCredit", project["businessId"],
                gateway.roles["donorA"],
            ) == 51000000
            assert gateway.w3.provider.make_request("evm_revert", [snapshot])["result"] is True
            gateway.w3.provider.make_request("evm_mine", [])
            # Automatic once must discover the orphaned confirmation even when
            # the replacement branch is shorter; operators do not hand-rebuild.
            assert indexer.once() is True
            assert gateway.call(
                "ProcurementEscrowV2", "donorCredit", project["businessId"],
                gateway.roles["donorA"],
            ) == 50000000
            with factory() as session:
                row = session.execute(text(
                    "SELECT status,error_code FROM operations WHERE id=:id"
                ), {"id": reorg_operation}).one()
                deposits = session.execute(text(
                    "SELECT deposits_atomic FROM ledger_projections WHERE project_id=:id"
                ), {"id": project["id"]}).scalar_one()
            assert row == ("requires_attention", "chain_reorganization")
            assert int(deposits) == 80000000

            # A separate procurement exercises a PO state rollback without
            # interrupting the primary ReceiptConfirmed path above.
            reorg_draft = client.post("/v2/procurements", json={
                "projectId": project["id"], "title": "Synthetic reorg guard purchase",
                "vendorWallet": gateway.roles["vendor"], "budgetCapAtomic": "1000000",
            }, headers=_auth(tokens["foundation"], "reorg-procurement-draft"))
            assert reorg_draft.status_code == 202, reorg_draft.text
            reorg_procurement = reorg_draft.json()["procurement"]
            reorg_id = reorg_procurement["id"]
            created = client.post(f"/v2/procurements/{reorg_id}/chain/create", json={},
                                  headers=_auth(tokens["foundation"], "reorg-procurement-create"))
            assert created.status_code == 202, created.text
            _drain(worker, indexer, factory, created.json()["operation"]["operationId"])
            reorg_po_body = {}
            for category, field in [("purchase_order", "poDocumentVersionId"),
                                    ("request", "requestDocumentVersionId"),
                                    ("goods_request", "goodsRequestDocumentVersionId")]:
                uploaded = _upload(client, tokens["foundation"], reorg_id, category, "reorg-doc-" + category)
                reorg_po_body[field] = uploaded["versionId"]
            po_snapshot = gateway.w3.provider.make_request("evm_snapshot", [])["result"]
            queued = client.post(f"/v2/procurements/{reorg_id}/chain/purchase-order", json=reorg_po_body,
                                 headers=_auth(tokens["foundation"], "reorg-po-first"))
            assert queued.status_code == 202, queued.text
            _drain(worker, indexer, factory, queued.json()["operation"]["operationId"])
            confirmed_po = client.get(f"/v2/procurements/{reorg_id}", headers=_auth(tokens["foundation"])).json()
            assert confirmed_po["chainState"]["verified"] is True
            orphan_po_hash = confirmed_po["chainState"]["transactionHash"]
            for _ in range(100):
                if not indexer.sync_one_block():
                    break
            else:
                raise AssertionError("Indexer cursor did not catch up before PO reorg")
            assert gateway.w3.provider.make_request("evm_revert", [po_snapshot])["result"] is True
            gateway.w3.provider.make_request("evm_mine", [])
            assert indexer.sync_one_block() is True
            reverted_po = client.get(f"/v2/procurements/{reorg_id}", headers=_auth(tokens["foundation"])).json()
            assert reverted_po["chainState"]["status"] == "created"
            assert reverted_po["chainState"]["transactionHash"] != orphan_po_hash
            retry = client.post(f"/v2/procurements/{reorg_id}/chain/purchase-order", json=reorg_po_body,
                                headers=_auth(tokens["foundation"], "reorg-po-explicit-new-request"))
            assert retry.status_code == 202, retry.text
            assert worker.once() is True
            with factory() as session:
                status = session.execute(text("SELECT status,error_code FROM operations WHERE id=:id"),
                                         {"id": retry.json()["operation"]["operationId"]}).one()
            assert status == ("requires_attention", "chain_nonce_history_conflict")
            assert gateway.receipt(orphan_po_hash) is None
            primary = client.get(f"/v2/procurements/{procurement['id']}", headers=_auth(tokens["foundation"])).json()
            assert primary["chainState"]["status"] == "receipt_confirmed"
            assert primary["chainState"]["verified"] is True
    finally:
        worker.close()
        indexer.close()
        engine.dispose()
