from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import threading
import time
from uuid import UUID

from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from conftest import auth
from pog_api.app import create_app
from pog_api.chain import ChainMismatch, LocalChainGateway, PreparedEnvelope
from pog_api.cli import LEGACY_RENAMES, migrate_standard_usernames
from pog_api.idempotency import ensure_verified_namespace
from pog_api.models import (
    AuditLog, ChainTransaction, DeploymentInstance, Operation, OperationStep, SessionRecord, User,
    WalletAuthorization,
)
from pog_api.security import expires_at, hash_password, issue_session_token
from pog_api.typed_data import (
    UINT32_MAX, UINT64_MAX, UINT256_MAX, ai_assessment_typed, digest,
    human_intent_typed, recipient_receipt_typed, recover,
)
from pog_api.worker import ChainIndexer, ChainWorker


def _sign_and_recover(data):
    account = Account.create()
    data["message"]["signer"] = account.address
    signature = Account.sign_message(encode_typed_data(full_message=data), account.key).signature.hex()
    signature = signature if signature.startswith("0x") else "0x" + signature
    assert recover(data, signature) == account.address
    assert digest(data).startswith("0x") and len(digest(data)) == 66


def test_three_eip712_families_are_strict_and_recover_eoa():
    ai = ai_assessment_typed({
        "stage": 0, "procurementId": "0x" + "11" * 32,
        "assessmentId": "0x" + "12" * 32, "outcome": 0, "riskScoreBps": 100,
        "evidenceHash": "0x" + "13" * 32, "reportHash": "0x" + "14" * 32,
        "signer": "0x" + "15" * 20, "nonce": UINT256_MAX, "deadline": UINT64_MAX,
    }, 31337, "0x" + "16" * 20)
    _sign_and_recover(ai)
    human = human_intent_typed({
        "targetId": "0x" + "21" * 32, "action": 0, "termsHash": "0x" + "22" * 32,
        "assessmentId": "0x" + "23" * 32, "signer": "0x" + "24" * 20,
        "nonce": 0, "deadline": UINT64_MAX, "policyEpoch": UINT32_MAX,
    }, 31337, "0x" + "25" * 20)
    _sign_and_recover(human)
    receipt = recipient_receipt_typed({
        "projectId": "0x" + "31" * 32, "procurementId": "0x" + "32" * 32,
        "expectedRecipient": "0x" + "33" * 20, "vendor": "0x" + "34" * 20,
        "poHash": "0x" + "35" * 32, "invoiceHash": "0x" + "36" * 32,
        "invoiceAmount": UINT256_MAX, "goodsHash": "0x" + "37" * 32,
        "receiptEvidenceHash": "0x" + "38" * 32, "nonce": 0, "deadline": UINT64_MAX,
    }, 31337, "0x" + "39" * 20)
    account = Account.create()
    receipt["message"]["expectedRecipient"] = account.address
    signature = Account.sign_message(encode_typed_data(full_message=receipt), account.key).signature.hex()
    assert recover(receipt, "0x" + signature.removeprefix("0x")) == account.address


def test_eip712_wrong_domain_recovers_a_different_signer():
    account = Account.create()
    data = human_intent_typed({
        "targetId": "0x" + "21" * 32, "action": 0, "termsHash": "0x" + "22" * 32,
        "assessmentId": "0x" + "23" * 32, "signer": account.address,
        "nonce": 0, "deadline": 1, "policyEpoch": 1,
    }, 31337, "0x" + "25" * 20)
    signature = Account.sign_message(encode_typed_data(full_message=data), account.key).signature.hex()
    tampered = json.loads(json.dumps(data))
    tampered["domain"]["version"] = "1"
    assert recover(tampered, "0x" + signature.removeprefix("0x")) != account.address


@pytest.mark.parametrize("field,value", [("nonce", -1), ("nonce", 1.0), ("deadline", 2**64)])
def test_typed_data_rejects_negative_float_and_overflow(field, value):
    message = {
        "stage": 0, "procurementId": "0x" + "11" * 32,
        "assessmentId": "0x" + "12" * 32, "outcome": 0, "riskScoreBps": 100,
        "evidenceHash": "0x" + "13" * 32, "reportHash": "0x" + "14" * 32,
        "signer": "0x" + "15" * 20, "nonce": 0, "deadline": 1,
    }
    message[field] = value
    with pytest.raises(ValueError):
        ai_assessment_typed(message, 31337, "0x" + "16" * 20)


def test_human_policy_epoch_rejects_uint32_overflow():
    with pytest.raises(ValueError):
        human_intent_typed({
            "targetId": "0x" + "21" * 32, "action": 0,
            "termsHash": "0x" + "22" * 32, "assessmentId": "0x" + "23" * 32,
            "signer": "0x" + "24" * 20, "nonce": 0, "deadline": 1,
            "policyEpoch": UINT32_MAX + 1,
        }, 31337, "0x" + "25" * 20)


def test_existing_session_fails_closed_when_wallet_mapping_becomes_ambiguous(
    client, create_user, login, session_factory,
):
    user = create_user("donor")
    token = login(user)
    with session_factory() as session, session.begin():
        session.add(WalletAuthorization(
            user_id=UUID(user["id"]), role_name="foundation",
            wallet_address="0x" + "ab" * 20, active=True,
        ))
    response = client.get("/v2/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ambiguous_role_wallet"


def test_chain_mutation_is_disabled_by_default_and_rejects_extra_fields(
    client, created_project,
):
    token, created = created_project
    project_id = created["project"]["id"]
    headers = {**auth(token), "Idempotency-Key": "disabled-chain"}
    extra = client.post(
        f"/v2/projects/{project_id}/chain/create",
        json={"caller": "0x" + "11" * 20}, headers=headers,
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_error"
    disabled = client.post(
        f"/v2/projects/{project_id}/chain/create", json={}, headers=headers,
    )
    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "chain_unavailable"


def test_demo_signing_disabled_fails_before_request_lookup(
    monkeypatch, settings, client, actors, login,
):
    token = login(actors["human"])

    class VerifiedFakeGateway:
        def __init__(self, *_args, **_kwargs):
            pass

        def verify(self):
            return None

    monkeypatch.setattr("pog_api.app.LocalChainGateway", VerifiedFakeGateway)
    chain_settings = replace(
        settings, chain_enabled=True, chain_manifest=Path("unused-test-manifest.json"),
        demo_signing_enabled=False,
    )
    with TestClient(create_app(chain_settings)) as chain_client:
        response = chain_client.post(
            "/v2/signing-requests/00000000-0000-0000-0000-000000000001/sign-demo",
            json={"confirm": True},
            headers={**auth(token), "Idempotency-Key": "demo-sign-disabled"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "demo_signing_disabled"


def test_manifest_abi_tamper_fails_before_rpc_use(tmp_path):
    repository_root = Path(__file__).resolve().parents[3]
    manifest = {
        "chain": {"rpcUrl": "http://127.0.0.1:1", "chainId": 31337},
        "contracts": {
            "MockHKD": {
                "abi": {
                    "path": "packages/contract-abis/v2/MockHKD.json",
                    "sha256": "00" * 32,
                }
            }
        },
    }
    path = tmp_path / "tampered-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ChainMismatch, match="ABI digest mismatch"):
        LocalChainGateway(path, repository_root)


def test_operation_query_reports_independent_canonical_step_and_transaction_facts(
    client, created_project, session_factory,
):
    token, created = created_project
    operation_id = UUID(created["operation"]["operationId"])
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        operation.status = "confirmed"
        step = OperationStep(
            operation_id=operation.id, step_index=0, kind="project.create",
            status="confirmed",
            detail={"action": "project.create", "expectedEvent": "ProjectCreated"},
        )
        session.add(step)
        session.flush()
        session.add(ChainTransaction(
            operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
            caller_address="0x" + "11" * 20, to_address="0x" + "22" * 20,
            chain_id=31337, evm_nonce_text="0", calldata="0x1234", value_text="0",
            envelope_hash="0x" + "33" * 32, calldata_hash="0x" + "44" * 32,
            status="confirmed", tx_hash="0x" + "55" * 32,
            receipt_json={"status": 1, "blockNumber": 42},
            block_hash="0x" + "66" * 32, canonical=True,
        ))
    response = client.get(f"/v2/operations/{operation_id}", headers=auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["chainVerified"] is True
    assert body["steps"] == [{
        "stepIndex": 0,
        "kind": "project.create",
        "status": "confirmed",
        "action": "project.create",
        "expectedEvent": "ProjectCreated",
        "transaction": {
            "status": "confirmed",
            "transactionHash": "0x" + "55" * 32,
            "receiptStatus": 1,
            "blockNumber": 42,
            "blockHash": "0x" + "66" * 32,
            "canonical": True,
        },
    }]


def _queue_worker_fixture(session_factory, created):
    operation_id = UUID(created["operation"]["operationId"])
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        namespace = session.get(DeploymentInstance, operation.namespace_id)
        namespace.schema_version = "a2-chain-1"
        namespace.chain_id = 31337
        namespace.genesis_hash = "0x" + "aa" * 32
        namespace.mode = "verified"
        namespace.verified = True
        namespace.rpc_url = "http://127.0.0.1:18545"
        namespace.manifest_sha256 = "bb" * 32
        namespace.manifest_json = {"chain": {"genesisHash": namespace.genesis_hash}}
        operation.status = "queued"
        step = OperationStep(
            operation_id=operation.id, step_index=0, kind="project.create", status="queued",
            detail={
                "action": "project.create", "caller": "0x" + "11" * 20,
                "args": [], "expectedEvent": "ProjectCreated",
            },
        )
        session.add(step)
    return operation_id


class _EthNonce:
    @staticmethod
    def get_transaction_count(_caller, _state):
        return 7


class _W3Nonce:
    eth = _EthNonce()


class _WorkerGateway:
    w3 = _W3Nonce()
    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": "0x" + "aa" * 32}}

    def __init__(self, *, reconcile_after_error=False, fail_send=False, delay=0.0):
        self.reconcile_after_error = reconcile_after_error
        self.fail_send = fail_send
        self.delay = delay
        self.find_calls = 0
        self.send_calls = 0
        self.lock = threading.Lock()

    @staticmethod
    def verify():
        return None

    def prepare(self, action, caller, _args, expected_event):
        return PreparedEnvelope(
            caller=caller, to="0x" + "22" * 20, chain_id=31337, nonce=7,
            data="0x1234", value=0, action=action, expected_event=expected_event,
        )

    def find_envelope_transaction(self, _envelope):
        with self.lock:
            self.find_calls += 1
            if self.reconcile_after_error and self.find_calls >= 2:
                return "0x" + "55" * 32
        return None

    def send(self, _envelope):
        with self.lock:
            self.send_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_send:
            raise TimeoutError("simulated response loss")
        return "0x" + "55" * 32


def test_two_workers_claim_one_step_and_send_once(
    created_project, session_factory, settings,
):
    _token, created = created_project
    _queue_worker_fixture(session_factory, created)
    gateway = _WorkerGateway(delay=0.2)
    workers = [ChainWorker(settings.database_url, gateway) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda worker: worker.once(), workers))
        assert sorted(results) == [False, True]
        assert gateway.send_calls == 1
        with session_factory() as session:
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.scalar(select(ChainTransaction.status)) == "submitted"
    finally:
        for worker in workers:
            worker.close()


@pytest.mark.parametrize(
    "reconcile_after_error,expected_status",
    [(True, "submitted"), (False, "requires_attention")],
)
def test_worker_reconciles_response_loss_or_stops_without_blind_resend(
    created_project, session_factory, settings, reconcile_after_error, expected_status,
):
    _token, created = created_project
    operation_id = _queue_worker_fixture(session_factory, created)
    gateway = _WorkerGateway(
        reconcile_after_error=reconcile_after_error, fail_send=True,
    )
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
    finally:
        worker.close()
    assert gateway.send_calls == 1
    with session_factory() as session:
        assert session.scalar(select(ChainTransaction.status)) == expected_status
        operation = session.get(Operation, operation_id)
        assert operation.status == expected_status


class _IndexerGateway:
    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": "0x" + "aa" * 32}}

    def __init__(self, status):
        self.status = status

    @staticmethod
    def verify():
        return None

    def receipt_with_events(self, tx_hash, _expected):
        return ({
            "transactionHash": tx_hash, "blockNumber": 7,
            "blockHash": "0x" + "66" * 32, "from": "0x" + "11" * 20,
            "to": "0x" + "22" * 20, "status": self.status,
        }, [])

    @staticmethod
    def canonical(_number, _hash):
        return True


@pytest.mark.parametrize(
    "receipt_status,expected_status,expected_code",
    [(0, "failed", "chain_transaction_reverted"),
     (1, "requires_attention", "chain_confirmation_invalid")],
)
def test_indexer_never_confirms_status_zero_or_missing_expected_event(
    created_project, session_factory, settings, receipt_status, expected_status, expected_code,
):
    _token, created = created_project
    operation_id = _queue_worker_fixture(session_factory, created)
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        operation.status = "submitted"
        step = session.scalar(select(OperationStep).where(
            OperationStep.operation_id == operation.id
        ))
        step.status = "submitted"
        session.add(ChainTransaction(
            operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
            caller_address="0x" + "11" * 20, to_address="0x" + "22" * 20,
            chain_id=31337, evm_nonce_text="7", calldata="0x1234", value_text="0",
            envelope_hash="0x" + "33" * 32, calldata_hash="0x" + "44" * 32,
            status="submitted", tx_hash="0x" + "55" * 32, submitted_at=datetime.now(UTC),
        ))
    indexer = ChainIndexer(settings.database_url, _IndexerGateway(receipt_status))
    try:
        assert indexer.once() is True
    finally:
        indexer.close()
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert (operation.status, operation.error_code) == (expected_status, expected_code)


def test_new_verified_instance_invalidates_old_pending_work_without_deleting_audit(
    created_project, session_factory,
):
    _token, created = created_project
    operation_id = _queue_worker_fixture(session_factory, created)
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        step = session.scalar(select(OperationStep).where(
            OperationStep.operation_id == operation.id
        ))
        session.add(ChainTransaction(
            operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
            caller_address="0x" + "11" * 20, to_address="0x" + "22" * 20,
            chain_id=31337, evm_nonce_text="7", calldata="0x1234", value_text="0",
            envelope_hash="0x" + "33" * 32, calldata_hash="0x" + "44" * 32,
            status="sending", submitted_at=datetime.now(UTC),
        ))

    class NewInstanceGateway:
        run_id = "replacement-run"
        instance_id = "replacement-instance"
        rpc_url = "http://127.0.0.1:18545"
        manifest_sha256 = "cc" * 32
        manifest = {"chain": {"genesisHash": "0x" + "dd" * 32}}

        @staticmethod
        def verify():
            return None

    with session_factory() as session, session.begin():
        current = ensure_verified_namespace(session, NewInstanceGateway())
        assert current.active is True
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        transaction = session.scalar(select(ChainTransaction))
        old_namespace = session.get(DeploymentInstance, operation.namespace_id)
        assert operation.status == "invalidated_instance"
        assert operation.error_code == "deployment_instance_changed"
        assert transaction.status == "invalidated_instance"
        assert old_namespace.active is False
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 1


def test_standard_username_migration_preserves_identity_password_wallet_and_session(
    monkeypatch, session_factory, client,
):
    import os
    monkeypatch.setenv("POG_DATABASE_URL", os.environ["POG_TEST_DATABASE_URL"])
    password = "legacy password remains 123"
    records = {}
    session_tokens = {}
    with session_factory() as session, session.begin():
        for index, (old, new, _display) in enumerate(LEGACY_RENAMES, 1):
            role = ("foundation", "recipient", "donor", "human_approver")[index - 1]
            user = User(username=old, display_name=old, password_hash=hash_password(password), active=True)
            session.add(user)
            session.flush()
            session.add(WalletAuthorization(
                user_id=user.id, role_name=role, wallet_address="0x" + f"{index:040x}", active=True,
            ))
            token, token_hash = issue_session_token()
            session.add(SessionRecord(user_id=user.id, token_hash=token_hash, expires_at=expires_at(3600)))
            records[new] = (user.id, user.password_hash, role, "0x" + f"{index:040x}")
            session_tokens[new] = token
    assert migrate_standard_usernames(True) == 0
    assert migrate_standard_usernames(True) == 0
    with session_factory() as session:
        for old, new, _display in LEGACY_RENAMES:
            assert session.scalar(select(User).where(User.username == old)) is None
            user = session.scalar(select(User).where(User.username == new))
            wallet = session.scalar(select(WalletAuthorization).where(WalletAuthorization.user_id == user.id))
            assert (user.id, user.password_hash, wallet.role_name, wallet.wallet_address) == records[new]
    for old, new, _display in LEGACY_RENAMES:
        assert client.post("/v2/sessions", json={"username": old, "password": password}).status_code == 401
        login = client.post("/v2/sessions", json={"username": new, "password": password})
        assert login.status_code == 200
        me = client.get("/v2/me", headers={"Authorization": f"Bearer {session_tokens[new]}"})
        assert me.status_code == 200
        assert me.json()["username"] == new
    admin_token = session_tokens["admin"]
    denied = client.post(
        "/v2/projects",
        json={
            "title": "must remain forbidden", "publicSummary": "admin is not Foundation",
            "recipientUserId": str(records["recipient"][0]),
            "humanApproverUserId": str(records["admin"][0]),
        },
        headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "admin-no-foundation"},
    )
    assert denied.status_code == 403


def test_standard_username_conflict_rolls_back_all(monkeypatch, session_factory):
    import os
    monkeypatch.setenv("POG_DATABASE_URL", os.environ["POG_TEST_DATABASE_URL"])
    with session_factory() as session, session.begin():
        session.add_all([
            User(username="foundation-demo", display_name="old", password_hash="x", active=True),
            User(username="foundation", display_name="target", password_hash="y", active=True),
            User(username="recipient-demo", display_name="old2", password_hash="z", active=True),
        ])
    with pytest.raises(RuntimeError):
        migrate_standard_usernames(True)
    with session_factory() as session:
        assert session.scalar(select(User).where(User.username == "recipient-demo")) is not None
        assert session.scalar(select(User).where(User.username == "recipient")) is None
