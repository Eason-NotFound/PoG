from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from eth_utils import keccak
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

import pog_api.a2 as a2
from pog_api.chain import PreparedEnvelope
from pog_api.models import (
    AuditLog, ChainTransaction, DeploymentInstance, Document, DocumentVersion,
    Operation, OperationStep, Procurement, Project, SigningRequest,
)
from pog_api.typed_data import digest, recipient_receipt_typed
from pog_api.worker import ChainWorker


RELAYER = "0x" + "11" * 20
REGISTRY = "0x" + "22" * 20
EVIDENCE = "33" * 32
# A synthetic adapter-test placeholder, not an executable EOA authorization.
SYNTHETIC_SIGNATURE = "0x" + "55" * 65
FOUND_HASH = "0x" + "66" * 32


class _Gateway:
    """DB-only worker adapter double; no real RPC or signatures are used."""

    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": "0x" + "aa" * 32}}

    def __init__(self):
        self.prepare_calls = self.find_calls = self.send_calls = 0
        self.accepted = {}
        self.read_error = None
        self.w3 = SimpleNamespace(eth=SimpleNamespace(get_transaction_count=lambda *_: 7))

    def verify(self):
        return None

    @staticmethod
    def envelope():
        return PreparedEnvelope(
            caller=RELAYER, to=REGISTRY, chain_id=31337, nonce=7,
            data="0x1234", value=0, action="receipt.submit",
            expected_event="RecipientReceiptAccepted",
        )

    def prepare(self, action, caller, args, expected_event):
        self.prepare_calls += 1
        assert action == "receipt.submit" and caller == RELAYER
        assert expected_event == "RecipientReceiptAccepted"
        return self.envelope()

    def find_envelope_transaction(self, envelope):
        self.find_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.accepted.get(envelope.hash)

    def send(self, envelope):
        self.send_calls += 1
        self.accepted[envelope.hash] = FOUND_HASH
        return FOUND_HASH


def _queue_receipt(session_factory, created_procurement, *, source="recipient", attempt=None):
    procurement_id = UUID(created_procurement[2]["procurement"]["id"])
    with session_factory() as session, session.begin():
        procurement = session.get(Procurement, procurement_id)
        project = session.get(Project, procurement.project_id)
        namespace = session.get(DeploymentInstance, project.namespace_id)
        namespace.schema_version = "a2-chain-1"
        namespace.chain_id = 31337
        namespace.genesis_hash = _Gateway.manifest["chain"]["genesisHash"]
        namespace.mode = "verified"
        namespace.verified = True
        namespace.rpc_url = _Gateway.rpc_url
        namespace.manifest_sha256 = _Gateway.manifest_sha256
        namespace.manifest_json = _Gateway.manifest
        document = Document(
            namespace_id=namespace.id, procurement_id=procurement.id, category="receipt_evidence",
            owner_user_id=project.recipient_user_id,
        )
        session.add(document)
        session.flush()

        def add_version(version_number, uploader):
            version = DocumentVersion(
                document_id=document.id, version=version_number, original_filename="synthetic-receipt.pdf",
                content_type="application/pdf", size_bytes=17, sha256_hex="44" * 32,
                keccak256_hex=EVIDENCE, storage_key=f"synthetic-receipt/{uuid4()}",
                uploaded_by_user_id=uploader, referenced=True,
            )
            session.add(version)
            session.flush()
            return version

        original_uploader = (project.foundation_user_id if source in {"foundation", "same_hash_replacement"}
                             else project.recipient_user_id)
        version = add_version(1, original_uploader)
        if source == "same_hash_replacement":
            # A later Recipient version has the same hash but cannot establish
            # provenance for the already frozen Foundation-uploaded version.
            add_version(2, project.recipient_user_id)
        context = {} if source == "missing_context" else {
            "receiptEvidenceDocumentVersionId": str(version.id),
        }
        message = {
            "projectId": project.business_id, "procurementId": procurement.business_id,
            "expectedRecipient": project.recipient_wallet, "vendor": procurement.vendor_wallet,
            "poHash": "0x" + "77" * 32, "invoiceHash": "0x" + "88" * 32,
            "invoiceAmount": 80, "goodsHash": "0x" + "99" * 32,
            "receiptEvidenceHash": "0x" + EVIDENCE, "nonce": 0, "deadline": 1000,
        }
        typed = recipient_receipt_typed(message, 31337, REGISTRY)
        preparation = Operation(
            namespace_id=namespace.id, principal_id=project.recipient_user_id,
            operation_kind="signing_request.receipt", idempotency_key="receipt-source-prepare",
            payload_hash="aa" * 32, status="confirmed",
        )
        operation = Operation(
            namespace_id=namespace.id, principal_id=project.recipient_user_id,
            operation_kind="signing_request.submit.receipt", idempotency_key="receipt-source-submit",
            payload_hash="bb" * 32, status="queued", result_resource_type="signing_request",
        )
        session.add_all([preparation, operation])
        session.flush()
        request = SigningRequest(
            namespace_id=namespace.id, operation_id=preparation.id, procurement_id=procurement.id,
            signer_user_id=project.recipient_user_id, kind="receipt", status="queued",
            contract_address=REGISTRY, signer_wallet=project.recipient_wallet,
            nonce_text="0", deadline_text="1000", policy_epoch=0, typed_data=typed,
            context_json=context, digest=digest(typed), signature=SYNTHETIC_SIGNATURE,
            submitted_operation_id=operation.id,
        )
        session.add(request)
        session.flush()
        operation.result_resource_id = request.id
        preparation.result_resource_type, preparation.result_resource_id = "signing_request", request.id
        step = OperationStep(
            operation_id=operation.id, step_index=0, kind="receipt.submit",
            status="prepared" if attempt == "prepared" else "queued",
            detail={
                "action": "receipt.submit", "caller": RELAYER,
                "args": [[message[name] for name in (
                    "projectId", "procurementId", "expectedRecipient", "vendor", "poHash",
                    "invoiceHash", "invoiceAmount", "goodsHash", "receiptEvidenceHash", "nonce", "deadline",
                )], SYNTHETIC_SIGNATURE], "expectedEvent": "RecipientReceiptAccepted",
                "projectUuid": str(project.id), "procurementUuid": str(procurement.id),
            },
        )
        session.add(step)
        session.flush()
        transaction = None
        if attempt is not None:
            envelope = _Gateway.envelope()
            transaction = ChainTransaction(
                operation_id=operation.id, step_id=step.id, namespace_id=namespace.id,
                caller_address=envelope.caller, to_address=envelope.to, chain_id=envelope.chain_id,
                evm_nonce_text=str(envelope.nonce), calldata=envelope.data,
                calldata_hash="0x" + keccak(bytes.fromhex(envelope.data[2:])).hex(),
                value_text=str(envelope.value), envelope_hash=envelope.hash,
                status=attempt, canonical=False,
                submitted_at=datetime.now(UTC) - timedelta(seconds=30),
            )
            session.add(transaction)
            session.flush()
        return SimpleNamespace(
            operation_id=operation.id, step_id=step.id, request_id=request.id,
            transaction_id=transaction.id if transaction is not None else None,
            context=context, typed=typed, digest=request.digest,
        )


def _assert_request_preserved(session, queued, *, status):
    request = session.get(SigningRequest, queued.request_id)
    assert request.status == status
    assert request.submitted_operation_id == queued.operation_id
    assert request.signature == SYNTHETIC_SIGNATURE
    assert request.nonce_text == "0"
    assert request.context_json == queued.context
    assert request.typed_data == queued.typed
    assert request.digest == queued.digest


def _source_audits(session, queued):
    return session.scalars(select(AuditLog).where(
        AuditLog.operation_id == queued.operation_id,
        AuditLog.action == "chain.receipt_source_blocked",
    )).all()


@pytest.mark.parametrize("source,code,status", [
    ("missing_context", "signing_material_stale", 409),
    ("foundation", "receipt_evidence_uploader_mismatch", 403),
    ("same_hash_replacement", "receipt_evidence_uploader_mismatch", 403),
])
def test_legacy_queued_receipt_source_blocks_before_preparation_without_releasing_request(
    created_procurement, session_factory, settings, source, code, status,
):
    queued = _queue_receipt(session_factory, created_procurement, source=source)
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert worker.once() is False
        assert gateway.prepare_calls == gateway.find_calls == gateway.send_calls == 0
        with session_factory() as session:
            operation = session.get(Operation, queued.operation_id)
            assert (operation.status, operation.error_code, operation.error_status) == (
                "requires_attention", code, status,
            )
            assert session.get(OperationStep, queued.step_id).status == "requires_attention"
            assert session.scalar(select(func.count(ChainTransaction.id))) == 0
            _assert_request_preserved(session, queued, status="requires_attention")
            audits = _source_audits(session, queued)
            assert len(audits) == 1
            assert audits[0].outcome == "requires_attention"
            assert audits[0].metadata_json == {"errorCode": code, "transactionId": None}
    finally:
        worker.close()


@pytest.mark.parametrize("source,code,error_status", [
    ("missing_context", "signing_material_stale", 409),
    ("foundation", "receipt_evidence_uploader_mismatch", 403),
])
def test_prepared_receipt_without_exact_match_blocks_new_send_and_preserves_nonce_envelope(
    created_procurement, session_factory, settings, source, code, error_status,
):
    queued = _queue_receipt(session_factory, created_procurement, source=source, attempt="prepared")
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.prepare_calls == gateway.send_calls == 0 and gateway.find_calls == 1
        with session_factory() as session:
            attempt = session.get(ChainTransaction, queued.transaction_id)
            assert attempt.status == "requires_attention" and attempt.tx_hash is None
            assert attempt.evm_nonce_text == "7" and attempt.calldata == "0x1234"
            assert attempt.envelope_hash == _Gateway.envelope().hash
            assert attempt.canonical is False
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            operation = session.get(Operation, queued.operation_id)
            assert (operation.error_code, operation.error_status) == (code, error_status)
            _assert_request_preserved(session, queued, status="requires_attention")
            audits = _source_audits(session, queued)
            assert len(audits) == 1
            assert audits[0].metadata_json["transactionId"] == str(queued.transaction_id)
        with session_factory() as session, session.begin():
            session.get(ChainTransaction, queued.transaction_id).submitted_at = (
                datetime.now(UTC) - timedelta(seconds=30)
            )
        # Once protected, an unproven outcome is reconcile-only, never resent.
        assert worker.once() is True
        assert gateway.send_calls == 0 and gateway.find_calls == 2
        with session_factory() as session:
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.get(ChainTransaction, queued.transaction_id).status == "requires_attention"
            operation = session.get(Operation, queued.operation_id)
            assert (operation.error_code, operation.error_status) == (code, error_status)
            attempt = session.get(ChainTransaction, queued.transaction_id)
            assert attempt.evm_nonce_text == "7" and attempt.envelope_hash == _Gateway.envelope().hash
            assert session.get(OperationStep, queued.step_id).status == "requires_attention"
            _assert_request_preserved(session, queued, status="requires_attention")
            assert len(_source_audits(session, queued)) == 1
        # Exact broadcast evidence remains admissible after the source block;
        # bind the original attempt, never create or resend an authorization.
        gateway.accepted[_Gateway.envelope().hash] = FOUND_HASH
        with session_factory() as session, session.begin():
            session.get(ChainTransaction, queued.transaction_id).submitted_at = (
                datetime.now(UTC) - timedelta(seconds=30)
            )
        assert worker.once() is True
        assert gateway.send_calls == 0 and gateway.find_calls == 3
        with session_factory() as session:
            attempt = session.get(ChainTransaction, queued.transaction_id)
            assert attempt.status == "submitted" and attempt.tx_hash == FOUND_HASH
            assert attempt.envelope_hash == _Gateway.envelope().hash
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            _assert_request_preserved(session, queued, status="queued")
            assert len(_source_audits(session, queued)) == 1
    finally:
        worker.close()


@pytest.mark.parametrize("attempt", ["prepared", "sending"])
def test_exact_broadcast_receipt_is_bound_without_rechecking_source_or_resending(
    created_procurement, session_factory, settings, monkeypatch, attempt,
):
    queued = _queue_receipt(session_factory, created_procurement, source="missing_context", attempt=attempt)
    gateway = _Gateway()
    gateway.accepted[_Gateway.envelope().hash] = FOUND_HASH

    def never_revalidate_history(*_):
        raise AssertionError("An exact already-broadcast envelope was revalidated as a new send")

    monkeypatch.setattr(a2, "_validate_receipt_source", never_revalidate_history)
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.find_calls == 1 and gateway.prepare_calls == gateway.send_calls == 0
        with session_factory() as session:
            transaction = session.get(ChainTransaction, queued.transaction_id)
            assert transaction.status == "submitted" and transaction.tx_hash == FOUND_HASH
            assert transaction.envelope_hash == _Gateway.envelope().hash
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.get(Operation, queued.operation_id).status == "submitted"
            _assert_request_preserved(session, queued, status="queued")
            assert _source_audits(session, queued) == []
    finally:
        worker.close()


@pytest.mark.parametrize("attempt", [None, "prepared"])
def test_original_recipient_bound_version_permits_new_receipt_send(
    created_procurement, session_factory, settings, attempt,
):
    queued = _queue_receipt(session_factory, created_procurement, attempt=attempt)
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.prepare_calls == (1 if attempt is None else 0)
        assert gateway.find_calls == gateway.send_calls == 1
        with session_factory() as session:
            transaction = session.scalar(select(ChainTransaction))
            assert transaction.status == "submitted" and transaction.tx_hash == FOUND_HASH
            assert session.scalar(select(func.count(ChainTransaction.id))) == 1
            assert session.get(Operation, queued.operation_id).status == "submitted"
            _assert_request_preserved(session, queued, status="queued")
            assert _source_audits(session, queued) == []
    finally:
        worker.close()


def test_receipt_request_must_match_the_queued_procurement_before_preparation(
    created_procurement, session_factory, settings,
):
    queued = _queue_receipt(session_factory, created_procurement)
    with session_factory() as session, session.begin():
        step = session.get(OperationStep, queued.step_id)
        step.detail = {**step.detail, "procurementUuid": str(uuid4())}
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.prepare_calls == gateway.find_calls == gateway.send_calls == 0
        with session_factory() as session:
            assert session.get(Operation, queued.operation_id).error_code == "signing_material_stale"
            _assert_request_preserved(session, queued, status="requires_attention")
            assert session.scalar(select(func.count(ChainTransaction.id))) == 0
    finally:
        worker.close()


@pytest.mark.parametrize("attempt", [None, "prepared"])
def test_receipt_source_database_fault_is_not_invalid_source_or_not_broadcast_proof(
    created_procurement, session_factory, settings, monkeypatch, attempt,
):
    queued = _queue_receipt(session_factory, created_procurement, attempt=attempt)

    def unavailable_source(*_):
        raise OperationalError("synthetic source lookup", {}, RuntimeError("DB unavailable"))

    monkeypatch.setattr(a2, "_validate_receipt_source", unavailable_source)
    gateway = _Gateway()
    worker = ChainWorker(settings.database_url, gateway)
    try:
        with pytest.raises(OperationalError):
            worker.once()
        assert gateway.prepare_calls == gateway.send_calls == 0
        with session_factory() as session:
            operation = session.get(Operation, queued.operation_id)
            assert operation.status == "queued" and operation.error_code is None
            _assert_request_preserved(session, queued, status="queued")
            assert _source_audits(session, queued) == []
            if attempt is None:
                assert session.get(OperationStep, queued.step_id).status == "queued"
                assert session.scalar(select(func.count(ChainTransaction.id))) == 0
            else:
                transaction = session.get(ChainTransaction, queued.transaction_id)
                assert transaction.status == "sending" and transaction.tx_hash is None
                assert transaction.envelope_hash == _Gateway.envelope().hash
                assert transaction.evm_nonce_text == "7"
    finally:
        worker.close()


def test_prepared_receipt_history_lookup_fault_never_releases_existing_nonce(
    created_procurement, session_factory, settings,
):
    queued = _queue_receipt(session_factory, created_procurement, source="missing_context", attempt="prepared")
    gateway = _Gateway()
    gateway.read_error = TimeoutError("Synthetic historical RPC unavailable")
    worker = ChainWorker(settings.database_url, gateway)
    try:
        assert worker.once() is True
        assert gateway.find_calls == 1 and gateway.prepare_calls == gateway.send_calls == 0
        with session_factory() as session:
            transaction = session.get(ChainTransaction, queued.transaction_id)
            assert transaction.status == "requires_attention" and transaction.tx_hash is None
            assert transaction.evm_nonce_text == "7"
            assert transaction.envelope_hash == _Gateway.envelope().hash
            assert session.get(Operation, queued.operation_id).error_code == "chain_submission_unknown"
            _assert_request_preserved(session, queued, status="requires_attention")
            assert _source_audits(session, queued) == []
    finally:
        worker.close()
