from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import threading
import time
from uuid import UUID

import pytest
from sqlalchemy import func, select

from pog_api.chain import ChainNotBroadcast, PreparedEnvelope
from pog_api.models import AuditLog, ChainTransaction, DeploymentInstance, Operation, OperationStep
from pog_api.worker import ChainWorker


CALLER = "0x" + "11" * 20
OTHER_CALLER = "0x" + "12" * 20
TARGET = "0x" + "22" * 20


class _Eth:
    def __init__(self, gateway):
        self.gateway = gateway

    def get_transaction_count(self, caller, state):
        if state == "pending":
            return self.gateway.pending_nonces.get(caller.lower(), self.gateway.nonces.get(caller.lower(), 7))
        return self.gateway.nonces.get(caller.lower(), 7)


class _W3:
    def __init__(self, gateway):
        self.eth = _Eth(gateway)


class _Gateway:
    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": "0x" + "aa" * 32}}

    def __init__(self, *, mode="ok", prepare_delay=0.0):
        self.mode = mode
        self.prepare_delay = prepare_delay
        self.w3 = _W3(self)
        self.nonces = {}
        self.pending_nonces = {}
        self.accepted = {}
        self.prepare_calls = 0
        self.send_calls = 0
        self.broadcasts = 0
        self.find_calls = 0
        self.lock = threading.Lock()

    def verify(self):
        return None

    def prepare(self, action, caller, args, expected_event):
        with self.lock:
            self.prepare_calls += 1
        if self.mode == "prepare_rejected":
            raise ValueError("invalid business state before transaction preparation")
        if self.prepare_delay:
            time.sleep(self.prepare_delay)
        return PreparedEnvelope(
            caller=caller, to=TARGET, chain_id=31337,
            nonce=self.w3.eth.get_transaction_count(caller, "pending"),
            data="0x" + f"{args[0]:08x}", value=0, action=action, expected_event=expected_event,
        )

    def find_envelope_transaction(self, envelope):
        with self.lock:
            self.find_calls += 1
        if self.mode == "unknown_lookup_failure" and self.send_calls:
            raise TimeoutError("reconciliation RPC unavailable")
        return self.accepted.get(envelope.hash)

    def send(self, envelope):
        with self.lock:
            self.send_calls += 1
            if self.mode == "not_broadcast":
                raise ChainNotBroadcast("estimate reverted before eth_sendTransaction")
            if self.mode in {"unknown", "unknown_lookup_failure"}:
                # The remote may have accepted a pending transaction. Absence
                # from our scanner is deliberately not a proof of rejection.
                self.pending_nonces[envelope.caller.lower()] = envelope.nonce + 1
                raise TimeoutError("send response unavailable")
            self.broadcasts += 1
            tx_hash = "0x" + f"{self.broadcasts:064x}"
            self.accepted[envelope.hash] = tx_hash
            self.nonces[envelope.caller.lower()] = envelope.nonce + 1
        if self.mode == "response_lost":
            raise TimeoutError("accepted transaction response lost")
        return tx_hash


def _queue(session_factory, created_project, callers):
    original_id = UUID(created_project[1]["operation"]["operationId"])
    operation_ids = []
    with session_factory() as session, session.begin():
        original = session.get(Operation, original_id)
        namespace = session.get(DeploymentInstance, original.namespace_id)
        namespace.schema_version = "a2-chain-1"
        namespace.chain_id = 31337
        namespace.genesis_hash = _Gateway.manifest["chain"]["genesisHash"]
        namespace.mode = "verified"
        namespace.verified = True
        namespace.rpc_url = _Gateway.rpc_url
        namespace.manifest_sha256 = _Gateway.manifest_sha256
        namespace.manifest_json = _Gateway.manifest
        for index, caller in enumerate(callers):
            operation = Operation(
                namespace_id=namespace.id, principal_id=original.principal_id,
                operation_kind="worker.fixture", idempotency_key=f"worker-fixture-{index}",
                payload_hash=f"{index:064x}", status="queued",
            )
            session.add(operation)
            session.flush()
            session.add(OperationStep(
                operation_id=operation.id, step_index=0, kind="project.create", status="queued",
                detail={"action": "project.create", "caller": caller, "args": [index + 1],
                        "expectedEvent": "ProjectCreated"},
            ))
            operation_ids.append(operation.id)
    return operation_ids


def _persist_envelope(session_factory, operation_id, *, status="sending"):
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation.id))
        detail = step.detail
        envelope = PreparedEnvelope(
            caller=detail["caller"], to=TARGET, chain_id=31337, nonce=7,
            data="0x" + f"{detail['args'][0]:08x}", value=0,
            action=detail["action"], expected_event=detail["expectedEvent"],
        )
        transaction = ChainTransaction(
            operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
            caller_address=envelope.caller, to_address=envelope.to, chain_id=envelope.chain_id,
            evm_nonce_text="7", calldata=envelope.data, value_text="0", envelope_hash=envelope.hash,
            calldata_hash="0x" + "33" * 32, status=status,
            submitted_at=datetime.now(UTC) - timedelta(seconds=30),
        )
        session.add(transaction)
        session.flush()
        step.status = "prepared" if status == "prepared" else status
        operation.status = "queued" if status in {"prepared", "sending"} else status
        return transaction.id, envelope


def _age_lease(session_factory, transaction_id):
    with session_factory() as session, session.begin():
        transaction = session.get(ChainTransaction, transaction_id)
        transaction.submitted_at = datetime.now(UTC) - timedelta(seconds=30)


def test_separate_operations_same_caller_reserve_once_under_two_workers(
    created_project, session_factory, settings,
):
    operation_ids = _queue(session_factory, created_project, [CALLER, CALLER])
    gateway = _Gateway(prepare_delay=0.1)
    workers = [ChainWorker(settings.database_url, gateway) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda worker: worker.once(), workers))
        assert sorted(results) == [False, True]
        assert gateway.prepare_calls == gateway.send_calls == gateway.broadcasts == 1
        with session_factory() as session:
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert sorted(session.get(Operation, item).status for item in operation_ids) == ["queued", "submitted"]
    finally:
        for worker in workers:
            worker.close()


def test_definite_preflight_rejection_preserves_attempt_and_releases_nonce_for_next_operation(
    created_project, session_factory, settings,
):
    failed_id, next_id = _queue(session_factory, created_project, [CALLER, CALLER])
    gateway = _Gateway(mode="not_broadcast")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        gateway.mode = "ok"
        assert worker.once() is True
        assert gateway.broadcasts == 1
        with session_factory() as session:
            attempts = session.scalars(select(ChainTransaction).order_by(ChainTransaction.created_at)).all()
            assert [item.status for item in attempts] == ["not_broadcast", "submitted"]
            assert [item.evm_nonce_text for item in attempts] == ["7", "7"]
            assert len({item.envelope_hash for item in attempts}) == 2
            assert session.get(Operation, failed_id).error_code == "chain_not_broadcast"
            assert session.get(Operation, next_id).status == "submitted"
            assert session.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.operation_id == failed_id, AuditLog.action == "chain_worker.not_broadcast"
            )) == 1
    finally:
        worker.close()


def test_preparation_failure_has_audited_step_without_stranding_caller(
    created_project, session_factory, settings,
):
    failed_id, next_id = _queue(session_factory, created_project, [CALLER, CALLER])
    gateway = _Gateway(mode="prepare_rejected")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        gateway.mode = "ok"
        assert worker.once() is True
        with session_factory() as session:
            assert session.get(Operation, failed_id).error_code == "chain_preparation_rejected"
            assert session.get(Operation, next_id).status == "submitted"
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.operation_id == failed_id, AuditLog.action == "chain_worker.not_broadcast"
            )) == 1
    finally:
        worker.close()


@pytest.mark.parametrize("accepted", [True, False])
def test_expired_send_lease_only_reconciles_never_broadcasts_again(
    created_project, session_factory, settings, accepted,
):
    operation_id = _queue(session_factory, created_project, [CALLER])[0]
    transaction_id, envelope = _persist_envelope(session_factory, operation_id)
    gateway = _Gateway()
    if accepted:
        gateway.accepted[envelope.hash] = "0x" + "55" * 32
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.send_calls == gateway.prepare_calls == 0
        with session_factory() as session:
            attempt = session.get(ChainTransaction, transaction_id)
            assert attempt.status == ("submitted" if accepted else "requires_attention")
            assert attempt.envelope_hash == envelope.hash
    finally:
        worker.close()


def test_durable_prepared_envelope_can_send_after_restart_without_new_nonce_allocation(
    created_project, session_factory, settings,
):
    operation_id = _queue(session_factory, created_project, [CALLER])[0]
    transaction_id, envelope = _persist_envelope(session_factory, operation_id, status="prepared")
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.prepare_calls == 0 and gateway.broadcasts == 1
        with session_factory() as session:
            assert session.get(ChainTransaction, transaction_id).status == "submitted"
            assert session.get(ChainTransaction, transaction_id).envelope_hash == envelope.hash
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
    finally:
        worker.close()


def test_unknown_pending_caller_blocks_new_operations_but_other_caller_can_progress(
    created_project, session_factory, settings,
):
    unknown_id, blocked_id, other_id = _queue(session_factory, created_project, [CALLER, CALLER, OTHER_CALLER])
    gateway = _Gateway(mode="unknown")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.w3.eth.get_transaction_count(CALLER, "latest") == 7
        assert gateway.w3.eth.get_transaction_count(CALLER, "pending") == 8
        gateway.mode = "ok"
        assert worker.once() is True
        assert worker.once() is False
        with session_factory() as session:
            assert session.get(Operation, unknown_id).status == "requires_attention"
            assert session.get(Operation, blocked_id).status == "queued"
            assert session.get(Operation, other_id).status == "submitted"
            assert session.scalar(select(func.count(ChainTransaction.id))) == 2
        assert gateway.send_calls == 2 and gateway.broadcasts == 1
    finally:
        worker.close()


def test_accepted_response_loss_binds_only_exact_envelope_without_second_send(
    created_project, session_factory, settings,
):
    operation_id = _queue(session_factory, created_project, [CALLER])[0]
    gateway = _Gateway(mode="response_lost")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.send_calls == gateway.broadcasts == 1
        with session_factory() as session:
            attempt = session.scalar(select(ChainTransaction))
            assert attempt.status == session.get(Operation, operation_id).status == "submitted"
            assert attempt.tx_hash == gateway.accepted[attempt.envelope_hash]
    finally:
        worker.close()


def test_accepted_send_database_write_failure_recovers_original_attempt(
    created_project, session_factory, settings, monkeypatch,
):
    operation_id = _queue(session_factory, created_project, [CALLER])[0]
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    original = worker._submitted
    calls = 0

    def fail_once(transaction_id, tx_hash):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Inject an actual PostgreSQL CHECK failure after the send returned;
            # the failed write transaction must roll back before recovery.
            with worker.factory() as session, session.begin():
                attempt = session.get(ChainTransaction, transaction_id)
                attempt.status = "simulated_invalid_submission_write"
                session.flush()
        return original(transaction_id, tx_hash)

    monkeypatch.setattr(worker, "_submitted", fail_once)
    try:
        assert worker.once() is True
        with session_factory() as session:
            attempt = session.scalar(select(ChainTransaction))
            assert attempt.status == "requires_attention"
            transaction_id, envelope_hash = attempt.id, attempt.envelope_hash
        _age_lease(session_factory, transaction_id)
        assert worker.once() is True
        assert gateway.send_calls == gateway.broadcasts == 1
        with session_factory() as session:
            attempt = session.get(ChainTransaction, transaction_id)
            assert attempt.status == session.get(Operation, operation_id).status == "submitted"
            assert attempt.envelope_hash == envelope_hash
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
    finally:
        worker.close()


def test_reconciliation_rpc_failure_after_send_is_unknown_and_does_not_escape_worker(
    created_project, session_factory, settings,
):
    operation_id = _queue(session_factory, created_project, [CALLER])[0]
    gateway = _Gateway(mode="unknown_lookup_failure")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.send_calls == 1
        with session_factory() as session:
            assert session.get(Operation, operation_id).error_code == "chain_submission_unknown"
            assert session.scalar(select(ChainTransaction.status)) == "requires_attention"
    finally:
        worker.close()


def test_nonce_constraint_collision_becomes_attention_without_killing_worker(
    created_project, session_factory, settings,
):
    old_id, new_id = _queue(session_factory, created_project, [CALLER, CALLER])
    old_transaction_id, old_envelope = _persist_envelope(session_factory, old_id, status="confirmed")
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.send_calls == 0
        with session_factory() as session:
            operation = session.get(Operation, new_id)
            assert operation.error_code == "chain_nonce_history_conflict"
            assert operation.error_status == 409
            assert operation.status == "requires_attention"
            assert session.scalar(select(OperationStep.status).where(
                OperationStep.operation_id == new_id
            )) == "requires_attention"
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            old_transaction = session.get(ChainTransaction, old_transaction_id)
            assert old_transaction.status == "confirmed"
            assert old_transaction.envelope_hash == old_envelope.hash
            conflicts = session.scalars(select(AuditLog).where(
                AuditLog.operation_id == new_id,
                AuditLog.action == "chain.nonce_conflict",
            )).all()
            assert len(conflicts) == 1
            assert conflicts[0].principal_id == operation.principal_id
            assert conflicts[0].outcome == "requires_attention"
            assert conflicts[0].metadata_json == {"caller": CALLER, "nonce": "7"}
    finally:
        worker.close()


def test_known_hash_attention_different_nonce_stays_blocked_without_starving_other_caller(
    created_project, session_factory, settings,
):
    old_id, blocked_id = _queue(session_factory, created_project, [CALLER, CALLER])
    old_transaction_id, old_envelope = _persist_envelope(
        session_factory, old_id, status="requires_attention",
    )
    old_hash = "0x" + "77" * 32
    with session_factory() as session, session.begin():
        old_transaction = session.get(ChainTransaction, old_transaction_id)
        old_transaction.tx_hash = old_hash
        old_transaction.canonical = False
    gateway = _Gateway()
    gateway.nonces[CALLER.lower()] = 8
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is False
        assert gateway.prepare_calls == 1
        assert gateway.send_calls == gateway.find_calls == gateway.broadcasts == 0
        with session_factory() as session:
            assert session.get(Operation, blocked_id).status == "queued"
            assert session.get(Operation, blocked_id).error_code is None
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.action == "chain.nonce_conflict"
            )) == 0
        with session_factory() as session, session.begin():
            blocked_operation = session.get(Operation, blocked_id)
            other_operation = Operation(
                namespace_id=blocked_operation.namespace_id,
                principal_id=blocked_operation.principal_id,
                operation_kind="worker.fixture", idempotency_key="worker-fixture-other-caller",
                payload_hash="88" * 32, status="queued",
            )
            session.add(other_operation)
            session.flush()
            other_id = other_operation.id
            session.add(OperationStep(
                operation_id=other_id, step_index=0, kind="project.create", status="queued",
                detail={"action": "project.create", "caller": OTHER_CALLER, "args": [3],
                        "expectedEvent": "ProjectCreated"},
            ))
        assert worker.once() is True
        assert gateway.send_calls == gateway.find_calls == gateway.broadcasts == 1
        with session_factory() as session:
            assert session.get(Operation, other_id).status == "submitted"
            assert session.get(Operation, blocked_id).status == "queued"
            transactions = session.scalars(select(ChainTransaction)).all()
            assert len(transactions) == 2
            new_transaction = next(row for row in transactions if row.id != old_transaction_id)
            assert new_transaction.caller_address == OTHER_CALLER
            assert new_transaction.operation_id == other_id
            old_transaction = session.get(ChainTransaction, old_transaction_id)
            assert old_transaction.status == "requires_attention"
            assert old_transaction.tx_hash == old_hash
            assert old_transaction.canonical is False
            assert old_transaction.envelope_hash == old_envelope.hash
    finally:
        worker.close()
