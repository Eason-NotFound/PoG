"""Real PostgreSQL route fixtures with a deliberately fake, connection-free chain.

These fixtures prove HTTP authorization and durable recovery behavior, not Anvil
deployment, transaction, event, or contract verification.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from web3 import Web3

from pog_api.app import create_app
from pog_api.chain import ChainMismatch, ChainUnavailable
from pog_api.config import Settings
from pog_api.idempotency import ensure_verified_namespace
from pog_api.models import Document, DocumentVersion, Procurement, Project


def auth(token, key=None):
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


class FakeRouteGate:
    """No RPC/send path; only explicit fixture getters and local EOA signatures."""

    def __init__(self, manifest_path, accounts):
        self.manifest_path = manifest_path
        self.manifest = json.loads(manifest_path.read_text())
        self.accounts = accounts
        self.mode = "online"
        self.now = 1_000
        self.verify_calls = self.getter_calls = self.demo_sign_calls = self.broadcast_calls = 0
        self.nonces = {"aiNonces": 0, "humanNonces": 0, "recipientNonces": 0}
        self.projects = {}
        self.procurements = {}
        self.assessments = {}
        self.policy_epoch = 1
        self.is_approver = True
        self.ai_signer_allowed = True

    @property
    def manifest_sha256(self):
        return hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()

    @property
    def run_id(self):
        return self.manifest["runId"]

    @property
    def instance_id(self):
        return self.manifest["chain"]["instanceId"]

    @property
    def rpc_url(self):
        return self.manifest["chain"]["rpcUrl"]

    @property
    def roles(self):
        return self.manifest["roles"]

    def verify(self):
        self.verify_calls += 1
        if self.mode == "offline":
            raise ChainUnavailable("fake local RPC is offline")
        if self.mode == "mismatch":
            raise ChainMismatch("fake instance mismatch")

    def contract_address(self, name):
        return self.manifest["contracts"][name]["address"]

    def latest_timestamp(self):
        self.verify()
        return self.now

    @staticmethod
    def _key(value):
        if isinstance(value, bytes):
            return "0x" + value.hex()
        return str(value).lower()

    def call(self, contract, function, *args, **kwargs):
        self.verify()
        self.getter_calls += 1
        if function in self.nonces:
            return self.nonces[function]
        if function == "getProcurement":
            return self.procurements[self._key(args[0])]
        if function == "getProject":
            return self.projects[self._key(args[0])]
        if function == "getLedger":
            return [self.contract_address("MockHKD"), 100_000_000, 60_000_000, 0, 0, 0, 0,
                    2, 0, self.policy_epoch, 1, False]
        if function == "getAssessment":
            return self.assessments[self._key(args[0])]
        if function == "isApprover":
            return self.is_approver and str(args[1]).lower() == self.roles["humanApprover"].lower()
        if function == "aiSigners":
            return self.ai_signer_allowed and str(args[0]).lower() == self.roles["aiSigner"].lower()
        if function == "computeAssessmentId":
            material = args[0][:2] + args[0][3:]
            return keccak(json.dumps(material, sort_keys=True).encode())
        if function == "reserveTermsHash":
            return keccak(f"fake-reserve:{self._key(args[0])}:{args[1]}".encode())
        if function == "donorCredit":
            return 50_000_000
        raise AssertionError(f"Unimplemented fake getter: {contract}.{function}")

    def sign_typed_data(self, signer, data):
        self.verify()
        self.demo_sign_calls += 1
        account = next(value for value in self.accounts.values() if value.address.lower() == signer.lower())
        signature = Account.sign_message(encode_typed_data(full_message=data), account.key).signature.hex()
        return "0x" + signature.removeprefix("0x")

    def send(self, *_args):
        self.broadcast_calls += 1
        raise AssertionError("HTTP fixture must never broadcast")

    def register(self, project, procurement):
        project_id = project.business_id
        procurement_id = procurement.business_id
        self.projects[project_id] = [bytes.fromhex(project_id[2:]), self.roles["foundation"],
                                    self.roles["recipient"], self.contract_address("MockHKD"), 0, 0, 1, 0]
        assessment_id = "0x" + keccak(f"fake-assessment:{procurement_id}".encode()).hex()
        # Stable nonzero commitments; only the final enum changes per test.
        self.procurements[procurement_id] = [
            bytes.fromhex(procurement_id[2:]), bytes.fromhex(project_id[2:]), self.roles["vendor"],
            80_000_000, bytes.fromhex("11" * 32), bytes.fromhex("12" * 32),
            bytes.fromhex("13" * 32), bytes.fromhex("14" * 32), bytes.fromhex(assessment_id[2:]),
            60_000_000, bytes.fromhex("15" * 32), 50_000_000, bytes.fromhex("16" * 32),
            bytes(32), 1,
        ]
        self.assessments[assessment_id] = [bytes.fromhex(assessment_id[2:]), bytes.fromhex(procurement_id[2:]),
                                          0, 0, 100, bytes.fromhex("14" * 32), bytes.fromhex("17" * 32),
                                          self.roles["aiSigner"], 0, self.now + 3_600]


@pytest.fixture
def route_env(tmp_path, monkeypatch, settings, create_user, session_factory):
    account_roles = {
        "foundation": ("foundation", "foundation"), "recipient": ("recipient", "recipient"),
        "donor": ("donor", "donorA"), "admin": ("human_approver", "humanApprover"),
        "ai": ("service_ai", "aiSigner"), "other_foundation": ("foundation", None),
        "other_recipient": ("recipient", None), "other_human": ("human_approver", None),
    }
    accounts = {name: Account.create() for name in account_roles}
    roles = {manifest_role: accounts[name].address for name, (_role, manifest_role) in account_roles.items()
             if manifest_role is not None}
    for name in ("owner", "donorB", "vendor", "relayer", "paymentAttestor"):
        roles[name] = Account.create().address
    manifest = tmp_path / "fake-route-manifest.json"
    manifest.write_text(json.dumps({
        "runId": "route-test-run", "chain": {"instanceId": "route-test-instance", "chainId": 31337,
        "genesisHash": "0x" + "aa" * 32, "rpcUrl": "http://127.0.0.1:18545"}, "roles": roles,
        "contracts": {name: {"address": Web3.to_checksum_address("0x" + f"{index:040x}")}
                      for index, name in enumerate(("MockHKD", "PoGRegistryV2", "ProcurementEscrowV2"), 100)},
    }), encoding="utf-8")
    gate = FakeRouteGate(manifest, accounts)
    monkeypatch.setattr("pog_api.app.LocalChainGateway", lambda *_args: gate)
    users = {name: create_user(role, username={"admin": "admin", "ai": "service-ai-fixture"}.get(name, name),
                               wallet=accounts[name].address)
             for name, (role, _manifest_role) in account_roles.items()}
    projects, procurements, document_versions = [], [], {}
    with session_factory() as session, session.begin():
        ns = ensure_verified_namespace(session, gate)
        for index in range(2):
            project = Project(
                namespace_id=ns.id, business_id="0x" + f"{index + 1:064x}", title=f"Route fixture {index}",
                public_summary="Connection-free fake chain fixture", fairness_rule="Confirmed costs shared",
                foundation_user_id=UUID(users["foundation"]["id"]), recipient_user_id=UUID(users["recipient"]["id"]),
                human_approver_user_id=UUID(users["admin"]["id"]), foundation_wallet=roles["foundation"],
                recipient_wallet=roles["recipient"], chain_status="active",
            )
            session.add(project)
            session.flush()
            procurement = Procurement(
                namespace_id=ns.id, project_id=project.id, business_id="0x" + f"{index + 10:064x}",
                title=f"Procurement fixture {index}", foundation_user_id=UUID(users["foundation"]["id"]),
                vendor_wallet=roles["vendor"], budget_cap_atomic=Decimal(80_000_000), chain_status="created",
                reserved_amount_atomic=Decimal(60_000_000),
            )
            session.add(procurement)
            session.flush()
            projects.append(project)
            procurements.append(procurement)
            gate.register(project, procurement)
        for index, category in enumerate(("purchase_order", "request", "goods_request", "invoice",
                                           "goods_evidence", "receipt_evidence")):
            uploader = users["recipient"] if category == "receipt_evidence" else users["foundation"]
            document = Document(namespace_id=ns.id, procurement_id=procurements[0].id,
                                category=category, owner_user_id=UUID(uploader["id"]))
            session.add(document)
            session.flush()
            version = DocumentVersion(
                document_id=document.id, version=1, original_filename=f"{category}.pdf",
                content_type="application/pdf", size_bytes=1, sha256_hex=f"{index + 1:064x}",
                keccak256_hex=f"{index + 11:064x}", storage_key=f"fake-route/{uuid4()}.pdf",
                uploaded_by_user_id=UUID(uploader["id"]), referenced=False,
            )
            session.add(version)
            session.flush()
            document_versions[category] = version
        namespace_id = ns.id
    route_settings = Settings(
        database_url=settings.database_url, storage_root=tmp_path / "route-storage", run_id=settings.run_id,
        instance_id=settings.instance_id, chain_enabled=True, chain_manifest=manifest, demo_signing_enabled=True,
    )

    def states(*, project="active", procurement="created", chain_enum=1):
        with session_factory() as session, session.begin():
            for item in projects:
                session.get(Project, item.id).chain_status = project
            for item in procurements:
                session.get(Procurement, item.id).chain_status = procurement
                gate.procurements[item.business_id][-1] = chain_enum

    with TestClient(create_app(route_settings), raise_server_exceptions=False) as client:
        tokens = {}
        for name, user in users.items():
            response = client.post("/v2/sessions", json={"username": user["username"], "password": user["password"]})
            assert response.status_code == 200, response.text
            tokens[name] = response.json()["token"]
        yield SimpleNamespace(
            client=client, gate=gate, users=users, actors=users, accounts=accounts, tokens=tokens,
            projects=projects, procurements=procurements, documents=document_versions,
            namespace_id=namespace_id, manifest_path=manifest, settings=route_settings,
            session_factory=session_factory, states=states,
        )
