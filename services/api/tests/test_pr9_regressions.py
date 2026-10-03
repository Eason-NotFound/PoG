from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import pytest

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from pog_api.app import create_app
from pog_api.models import (
    AuditLog, Document, DocumentVersion, Operation, OperationStep,
    Procurement, Project, SigningRequest,
)


def _auth(token, key=None):
    result = {"Authorization": "Bearer " + token}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


class RegressionGateway:
    """Intent-only fake; no RPC and no claim to validate cryptographic vectors."""
    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": "0x" + "aa" * 32}}

    def __init__(self, actors, ai_user):
        self.now = 1000
        self.nonce = 0
        self.projects = {}
        self.procurements = {}
        self.roles = {
            "foundation": actors["foundation"]["wallet"],
            "recipient": actors["recipient"]["wallet"],
            "humanApprover": actors["human"]["wallet"],
            "donorA": actors["donor"]["wallet"],
            "aiSigner": ai_user["wallet"],
            "vendor": "0x00000000000000000000000000000000000000aa",
            "relayer": "0x00000000000000000000000000000000000000b1",
        }
        # Synthetic deployment snapshot required by current signer binding.
        # This fake remains intent-only: it never proves a real deployment.
        self.manifest = {
            "runId": self.run_id,
            "chain": {"instanceId": self.instance_id, "chainId": 31337,
                      "genesisHash": "0x" + "aa" * 32, "rpcUrl": self.rpc_url},
            "roles": dict(self.roles),
        }

    def verify(self):
        return None

    def contract_address(self, name):
        return "0x" + f"{dict(MockHKD=21, PoGRegistryV2=22, ProcurementEscrowV2=23)[name]:040x}"

    def latest_timestamp(self):
        return self.now

    def call(self, contract, method, *args):
        if method in {"aiNonces", "recipientNonces", "humanNonces"}:
            return self.nonce
        if method == "computeAssessmentId":
            # Stable stub needed for lifecycle tests, not an assessment-ID vector.
            return bytes.fromhex("44" * 32)
        if method == "getProcurement":
            return self.procurements[str(args[0])]
        if method == "getProject":
            return self.projects[str(args[0])]
        raise AssertionError(f"unexpected fake call: {contract}.{method}")

    def sign_typed_data(self, *_args):
        raise AssertionError("expired request must be rejected before signing")


@pytest.fixture
def db_a2(monkeypatch, settings, actors, create_user, login, session_factory, project_payload):
    # Do not request created_project/created_procurement: they would seed A1 namespace.
    ai_user = create_user("service_ai", wallet="0x00000000000000000000000000000000000000a1")
    gate = RegressionGateway(actors, ai_user)
    monkeypatch.setattr("pog_api.app.LocalChainGateway", lambda *_args, **_kwargs: gate)
    chain_settings = replace(
        settings, chain_enabled=True, chain_manifest=Path("unused-test-manifest.json"),
        demo_signing_enabled=True,
    )
    tokens = {name: login(user) for name, user in {**actors, "ai": ai_user}.items()}
    with TestClient(create_app(chain_settings)) as client:
        def make_project(key):
            response = client.post("/v2/projects", json=project_payload,
                                   headers=_auth(tokens["foundation"], key))
            assert response.status_code == 202, response.text
            value = response.json()["project"]
            gate.projects[value["businessId"]] = (
                value["businessId"], gate.roles["foundation"], gate.roles["recipient"],
                gate.contract_address("MockHKD"), b"\x00" * 32, 1, 1, 0,
            )
            return value

        def make_procurement(project, key, state="po_recorded"):
            response = client.post("/v2/procurements", json={
                "projectId": project["id"], "title": "Synthetic purchase",
                "vendorWallet": gate.roles["vendor"], "budgetCapAtomic": "80000000",
            }, headers=_auth(tokens["foundation"], key))
            assert response.status_code == 202, response.text
            value = response.json()["procurement"]
            with session_factory() as session, session.begin():
                session.get(Procurement, UUID(value["id"])).chain_status = state
            # V2 tuple positions used by preparation; this does not emulate execution.
            gate.procurements[value["businessId"]] = (
                value["businessId"], project["businessId"], gate.roles["vendor"], 80000000,
                b"p" * 32, b"q" * 32, b"g" * 32, b"e" * 32, b"a" * 32,
                80000000, b"i" * 32, 70000000, b"h" * 32, b"\x00" * 32,
                5 if state == "invoice_recorded" else 1,
            )
            return value

        yield SimpleNamespace(
            client=client, gateway=gate, tokens=tokens, settings=chain_settings,
            make_project=make_project, make_procurement=make_procurement,
            actors=actors, ai_user=ai_user,
        )


def test_db_cross_project_chain_create_conflicts_and_same_project_replays(db_a2, session_factory):
    first = db_a2.make_project("draft-a")
    second = db_a2.make_project("draft-b")
    def queue(project):
        return db_a2.client.post(
            f"/v2/projects/{project['id']}/chain/create", json={},
            headers=_auth(db_a2.tokens["foundation"], "shared-chain-key"),
        )
    initial, replay, conflict = queue(first), queue(first), queue(second)
    assert initial.status_code == replay.status_code == 202
    assert replay.json()["operation"]["replayed"] is True
    assert replay.json()["operation"]["operationId"] == initial.json()["operation"]["operationId"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_payload_conflict"
    with session_factory() as session:
        assert session.scalar(select(func.count(OperationStep.id))) == 1
        assert session.get(Project, UUID(second["id"])).chain_status == "off_chain_draft"


def test_db_cross_procurement_signing_key_conflicts(db_a2, session_factory):
    project = db_a2.make_project("draft")
    a = db_a2.make_procurement(project, "proc-a")
    b = db_a2.make_procurement(project, "proc-b")
    def prepare(proc):
        return db_a2.client.post(
            f"/v2/procurements/{proc['id']}/signing-requests",
            json={"kind": "ai_pre", "deadlineTtlSeconds": 60},
            headers=_auth(db_a2.tokens["ai"], "shared-preparation-key"),
        )
    first, conflict = prepare(a), prepare(b)
    assert first.status_code == 202, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_payload_conflict"
    with session_factory() as session:
        assert session.scalar(select(func.count(SigningRequest.id))) == 1


def _prepare_ai(db_a2, procurement, key):
    return db_a2.client.post(
        f"/v2/procurements/{procurement['id']}/signing-requests",
        json={"kind": "ai_pre", "deadlineTtlSeconds": 60},
        headers=_auth(db_a2.tokens["ai"], key),
    )


@pytest.mark.parametrize("status", ["prepared", "signed"])
def test_db_expired_never_submitted_request_is_retained_and_replaced(
    db_a2, session_factory, status,
):
    procurement = db_a2.make_procurement(db_a2.make_project("draft"), "proc")
    first = _prepare_ai(db_a2, procurement, "old")
    assert first.status_code == 202, first.text
    old_id = UUID(first.json()["signingRequest"]["id"])
    with session_factory() as session, session.begin():
        old = session.get(SigningRequest, old_id)
        old.status = status
        old_deadline, old_digest = old.deadline_text, old.digest
    db_a2.gateway.now = int(old_deadline) + 1
    second = _prepare_ai(db_a2, procurement, "new")
    assert second.status_code == 202, second.text
    assert UUID(second.json()["signingRequest"]["id"]) != old_id
    with session_factory() as session:
        old = session.get(SigningRequest, old_id)
        assert old.status == "expired"
        assert (old.deadline_text, old.digest) == (old_deadline, old_digest)
        assert session.scalar(select(func.count(SigningRequest.id))) == 2
        assert session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "signing_request.expire",
            AuditLog.resource_id == old_id,
        )) == 1
    old_sign = db_a2.client.post(
        f"/v2/signing-requests/{old_id}/sign-demo", json={"confirm": True},
        headers=_auth(db_a2.tokens["ai"], "try-expired-sign"),
    )
    assert old_sign.status_code == 409
    assert old_sign.json()["error"]["code"] == "signing_request_state"


@pytest.mark.parametrize("status,submitted", [
    ("prepared", True), ("signed", True),
    ("queued", True), ("requires_attention", True),
    ("queued", False), ("requires_attention", False),
])
def test_db_submitted_or_unknown_nonce_reservation_is_not_expired(
    db_a2, session_factory, status, submitted,
):
    procurement = db_a2.make_procurement(db_a2.make_project("draft"), "proc")
    first = _prepare_ai(db_a2, procurement, "old")
    assert first.status_code == 202, first.text
    old_id = UUID(first.json()["signingRequest"]["id"])
    with session_factory() as session, session.begin():
        old = session.get(SigningRequest, old_id)
        old.status = status
        # A persisted submission pointer additionally disqualifies TTL release.
        old.submitted_operation_id = old.operation_id if submitted else None
        old_deadline = old.deadline_text
    db_a2.gateway.now = int(old_deadline) + 1
    second = _prepare_ai(db_a2, procurement, "new")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "signing_nonce_in_use"
    with session_factory() as session:
        assert session.get(SigningRequest, old_id).status == status
        assert session.scalar(select(func.count(SigningRequest.id))) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "signing_request.expire",
        )) == 0


def test_db_nonce_is_not_released_at_exact_deadline(db_a2, session_factory):
    procurement = db_a2.make_procurement(db_a2.make_project("draft"), "proc")
    first = _prepare_ai(db_a2, procurement, "old")
    assert first.status_code == 202, first.text
    old_id = UUID(first.json()["signingRequest"]["id"])
    with session_factory() as session:
        db_a2.gateway.now = int(session.get(SigningRequest, old_id).deadline_text)
    second = _prepare_ai(db_a2, procurement, "at-boundary")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "signing_nonce_in_use"
    with session_factory() as session:
        assert session.get(SigningRequest, old_id).status == "prepared"
        assert session.scalar(select(func.count(SigningRequest.id))) == 1


def test_db_concurrent_expiry_replacement_has_one_active_nonce(db_a2, session_factory):
    procurement = db_a2.make_procurement(db_a2.make_project("draft"), "proc")
    first = _prepare_ai(db_a2, procurement, "old")
    assert first.status_code == 202, first.text
    old_id = UUID(first.json()["signingRequest"]["id"])
    with session_factory() as session:
        db_a2.gateway.now = int(session.get(SigningRequest, old_id).deadline_text) + 1

    def prepare(key):
        with TestClient(create_app(db_a2.settings)) as client:
            return client.post(
                f"/v2/procurements/{procurement['id']}/signing-requests",
                json={"kind": "ai_pre", "deadlineTtlSeconds": 60},
                headers=_auth(db_a2.tokens["ai"], key),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, ["replacement-a", "replacement-b"]))
    assert sorted(response.status_code for response in results) == [202, 409]
    rejected = next(response for response in results if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "signing_nonce_in_use"
    with session_factory() as session:
        assert session.get(SigningRequest, old_id).status == "expired"
        assert session.scalar(select(func.count(SigningRequest.id)).where(
            SigningRequest.status != "expired",
        )) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "signing_request.expire",
        )) == 1


@pytest.mark.parametrize("uploader,expected", [("foundation", 403), ("recipient", 202)])
def test_db_receipt_uses_original_recipient_uploaded_version(
    db_a2, session_factory, uploader, expected,
):
    project = db_a2.make_project("draft")
    procurement = db_a2.make_procurement(project, "proc", "invoice_recorded")
    # Seed historical metadata: it must be checked even if upload now rejects Foundation.
    with session_factory() as session, session.begin():
        proc = session.get(Procurement, UUID(procurement["id"]))
        document = Document(
            namespace_id=proc.namespace_id, procurement_id=proc.id,
            category="receipt_evidence", owner_user_id=UUID(db_a2.actors[uploader]["id"]),
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id, version=1, original_filename="synthetic.pdf",
            content_type="application/pdf", size_bytes=1,
            sha256_hex="11" * 32, keccak256_hex="22" * 32,
            storage_key="synthetic-receipt.bin",
            uploaded_by_user_id=UUID(db_a2.actors[uploader]["id"]), referenced=False,
        )
        session.add(version)
        session.flush()
        version_id = version.id
        baseline_operations = session.scalar(select(func.count(Operation.id)))
        baseline_audits = session.scalar(select(func.count(AuditLog.id)))
    response = db_a2.client.post(
        f"/v2/procurements/{procurement['id']}/signing-requests",
        json={"kind": "receipt", "receiptEvidenceDocumentVersionId": str(version_id)},
        headers=_auth(db_a2.tokens["recipient"], "receipt-prepare"),
    )
    assert response.status_code == expected, response.text
    with session_factory() as session:
        if expected == 403:
            assert response.json()["error"]["code"] == "receipt_evidence_uploader_mismatch"
            assert session.scalar(select(func.count(SigningRequest.id))) == 0
            assert session.scalar(select(func.count(Operation.id))) == baseline_operations
            assert session.scalar(select(func.count(AuditLog.id))) == baseline_audits
            assert session.get(DocumentVersion, version_id).referenced is False
        else:
            assert session.scalar(select(func.count(SigningRequest.id))) == 1
            assert session.get(DocumentVersion, version_id).referenced is True
