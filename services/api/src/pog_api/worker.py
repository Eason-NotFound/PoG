from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from uuid import UUID

from requests.exceptions import RequestException
from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from web3 import Web3

from .chain import ChainNotBroadcast, ChainUnavailable, LocalChainGateway, PreparedEnvelope
from .db import build_engine, build_session_factory
from .idempotency import audit, ensure_verified_namespace
from .models import (
    ChainEvent, ChainTransaction, DonorCreditProjection, LedgerProjection,
    DeploymentInstance, IndexerCursor, Operation, OperationStep, Procurement, Project,
    SigningRequest, PolicyProjection,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _envelope(row: ChainTransaction, detail: dict) -> PreparedEnvelope:
    return PreparedEnvelope(
        caller=row.caller_address or "", to=row.to_address or "", chain_id=int(row.chain_id or 0),
        nonce=int(row.evm_nonce_text or "0"), data=row.calldata or "0x",
        value=int(row.value_text or "0"), action=detail["action"],
        expected_event=detail["expectedEvent"],
    )


def _normalized(value):
    if isinstance(value, (bytes, bytearray)) or hasattr(value, "hex") and not isinstance(value, str):
        result = value.hex()
        return (result if result.startswith("0x") else "0x" + result).lower()
    if isinstance(value, str) and value.startswith("0x"):
        return value.lower()
    return value


def _equal(actual, expected) -> bool:
    return _normalized(actual) == _normalized(expected)


PROJECT_STATES = ("active", "closing", "refundable", "closed")
PROCUREMENT_STATES = (
    "created", "po_recorded", "pre_assessed", "reserve_approval_pending",
    "reserved", "invoice_recorded", "receipt_confirmed", "final_assessed",
    "release_approval_pending", "funds_released", "settlement_recorded",
    "settlement_approval_pending", "payment_confirmed",
    "cancellation_approval_pending", "cancelled",
)


class _CanonicalChainChanged(RuntimeError):
    """A block changed while its events and getters were being read."""

    def __init__(self, message: str = "Canonical chain changed", *, transaction_id=None, receipt=None):
        super().__init__(message)
        self.transaction_id = transaction_id
        self.receipt = receipt


class ChainWorker:
    def __init__(self, database_url: str, gateway: LocalChainGateway):
        self.engine = build_engine(database_url)
        self.factory = build_session_factory(self.engine)
        self.gateway = gateway

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _lock_caller(session, namespace_id: UUID, caller: str) -> bool:
        # A stable PostgreSQL transaction lock covers separate operations and
        # workers, not merely two workers selecting the same step.
        material = f"pog-a2-caller:{namespace_id}:{caller.lower()}".encode()
        key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
        return bool(session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}))

    @staticmethod
    def _signing_status(session, operation_id: UUID, status: str) -> None:
        request = session.scalar(select(SigningRequest).where(
            SigningRequest.submitted_operation_id == operation_id
        ))
        if request is not None:
            request.status = status

    @staticmethod
    def _nonce_conflict(session, step: OperationStep, operation: Operation,
                        envelope: PreparedEnvelope) -> None:
        step.status = operation.status = "requires_attention"
        operation.error_code = "chain_nonce_history_conflict"
        operation.error_status = 409
        operation.error_detail = (
            "Caller nonce is reserved in transaction history; inspect canonical and pending "
            "facts before authorizing recovery"
        )
        audit(session, principal_id=operation.principal_id, operation_id=operation.id,
              action="chain.nonce_conflict", outcome="requires_attention",
              metadata={"caller": envelope.caller, "nonce": str(envelope.nonce)})
        ChainWorker._signing_status(session, operation.id, "requires_attention")

    @staticmethod
    def _receipt_source_allowed(session, step: OperationStep, operation: Operation,
                                transaction: ChainTransaction | None = None) -> bool:
        """Check DB provenance only before a new send; never retire an authorization."""
        if step.detail.get("action") != "receipt.submit":
            return True
        # Import the shared API check at runtime, without making worker/app
        # module initialization depend on one another.
        from .a2 import _validate_receipt_source
        from .errors import APIError

        requests = session.scalars(select(SigningRequest).where(
            SigningRequest.submitted_operation_id == operation.id,
        ).with_for_update()).all()
        try:
            if len(requests) != 1:
                raise APIError(409, "signing_material_stale", "Original receipt signing request is not bound")
            request = requests[0]
            try:
                procurement_id = UUID(step.detail.get("procurementUuid", ""))
            except (ValueError, TypeError, AttributeError) as exc:
                raise APIError(409, "signing_material_stale", "Queued receipt procurement is not bound") from exc
            if (
                request.namespace_id != operation.namespace_id or request.kind != "receipt"
                or request.procurement_id != procurement_id
            ):
                raise APIError(409, "signing_material_stale", "Queued receipt differs from its original request")
            _validate_receipt_source(session, request)
        except APIError as exc:
            if exc.code not in {"signing_material_stale", "receipt_evidence_uploader_mismatch"}:
                raise
            # This is not failed/preflight-not-broadcast: the submitted pointer,
            # signature, nonce and any persisted envelope continue protecting
            # the original authorization and cannot be automatically renewed.
            step.status = operation.status = "requires_attention"
            operation.error_code = exc.code
            operation.error_status = exc.status_code
            operation.error_detail = exc.message
            for request in requests:
                request.status = "requires_attention"
            if transaction is not None:
                transaction.status = "requires_attention"
            audit(session, principal_id=operation.principal_id, operation_id=operation.id,
                  action="chain.receipt_source_blocked", outcome="requires_attention",
                  resource_type=operation.result_resource_type, resource_id=operation.result_resource_id,
                  metadata={"errorCode": exc.code,
                            "transactionId": str(transaction.id) if transaction is not None else None})
            return False
        # Driver/connection failures deliberately escape and roll back. They
        # must not be recorded as proof that the source is invalid or unsent.
        return True

    @staticmethod
    def _failed_step(session, step: OperationStep, operation: Operation, *,
                     code: str, detail: str, transaction: ChainTransaction | None = None) -> None:
        step.status = operation.status = "failed"
        operation.error_code = code
        operation.error_status = 409
        operation.error_detail = detail[:1000]
        ChainWorker._signing_status(session, operation.id, "failed")
        audit(
            session, principal_id=operation.principal_id, operation_id=operation.id,
            action="chain_worker.not_broadcast", outcome="failed",
            resource_type=operation.result_resource_type,
            resource_id=operation.result_resource_id,
            metadata={"errorCode": code, "transactionId": str(transaction.id) if transaction else None,
                      "envelopeHash": transaction.envelope_hash if transaction else None},
        )

    def _not_broadcast(self, transaction_id: UUID, exc: Exception) -> None:
        with self.factory() as session, session.begin():
            row = session.get(ChainTransaction, transaction_id, with_for_update=True)
            step = session.get(OperationStep, row.step_id)
            operation = session.get(Operation, row.operation_id)
            # Only this status exits the EVM nonce reservation index. Receipt
            # failures and every ambiguous attempt continue protecting it.
            row.status = "not_broadcast"
            self._failed_step(session, step, operation, code="chain_not_broadcast",
                              detail=str(exc), transaction=row)

    def _unknown(self, transaction_id: UUID, exc: Exception) -> None:
        with self.factory() as session, session.begin():
            row = session.get(ChainTransaction, transaction_id, with_for_update=True)
            step = session.get(OperationStep, row.step_id)
            operation = session.get(Operation, row.operation_id)
            previous_status = row.status
            row.status = step.status = operation.status = "requires_attention"
            row.submitted_at = _utcnow()
            if previous_status == "requires_attention" and operation.error_code in {
                "signing_material_stale", "receipt_evidence_uploader_mismatch",
            }:
                # A source-blocked persisted envelope can still be searched for
                # exact broadcast evidence, but absence/unavailability is not a
                # new source verdict. Keep its stable 403/409 and original audit.
                self._signing_status(session, operation.id, "requires_attention")
                return
            operation.error_code = "chain_submission_unknown"
            operation.error_status = 503
            operation.error_detail = str(exc)[:1000]
            self._signing_status(session, operation.id, "requires_attention")
            if previous_status != "requires_attention":
                audit(
                    session, principal_id=operation.principal_id, operation_id=operation.id,
                    action="chain_worker.submission_unknown", outcome="requires_attention",
                    resource_type=operation.result_resource_type,
                    resource_id=operation.result_resource_id,
                    metadata={"transactionId": str(row.id), "envelopeHash": row.envelope_hash},
                )

    def _submitted(self, transaction_id: UUID, tx_hash: str) -> None:
        with self.factory() as session, session.begin():
            row = session.get(ChainTransaction, transaction_id, with_for_update=True)
            step = session.get(OperationStep, row.step_id)
            operation = session.get(Operation, row.operation_id)
            row.tx_hash = tx_hash
            row.status = step.status = operation.status = "submitted"
            row.submitted_at = _utcnow()
            operation.error_code = operation.error_status = operation.error_detail = None
            self._signing_status(session, operation.id, "queued")
            audit(
                session, principal_id=operation.principal_id, operation_id=operation.id,
                action="chain_worker.submitted", outcome="submitted",
                resource_type=operation.result_resource_type,
                resource_id=operation.result_resource_id,
                metadata={"transactionId": str(row.id), "transactionHash": tx_hash,
                          "envelopeHash": row.envelope_hash},
            )

    def once(self) -> bool:
        """Reserve one caller lane, then send or reconcile its exact persisted envelope."""
        transaction_id: UUID | None = None
        reconcile_only = False
        recovered_prepared_attempt = False
        with self.factory() as session, session.begin():
            namespace = ensure_verified_namespace(session, self.gateway)
            lease_cutoff = _utcnow() - timedelta(seconds=15)
            existing = session.scalar(
                select(ChainTransaction).join(Operation, Operation.id == ChainTransaction.operation_id).where(
                    ChainTransaction.namespace_id == namespace.id,
                    Operation.status != "invalidated_instance",
                    or_(
                        ChainTransaction.status == "prepared",
                        and_(
                            ChainTransaction.status.in_(("sending", "requires_attention")),
                            ChainTransaction.tx_hash.is_(None),
                            or_(ChainTransaction.submitted_at.is_(None),
                                ChainTransaction.submitted_at < lease_cutoff),
                        ),
                    ),
                )
                .order_by(ChainTransaction.created_at)
                .with_for_update(skip_locked=True, of=ChainTransaction).limit(1)
            )
            if existing is None:
                # A known-hash attention record still closes its caller lane.
                # Only diagnose whether a newly queued intent would reuse a
                # durable nonce; never allocate an attempt, scan, or send here.
                known_attention = exists().where(
                    ChainTransaction.namespace_id == namespace.id,
                    func.lower(ChainTransaction.caller_address)
                    == func.lower(OperationStep.detail["caller"].astext),
                    ChainTransaction.status == "requires_attention",
                    ChainTransaction.tx_hash.is_not(None),
                )
                unresolved = exists().where(
                    ChainTransaction.namespace_id == namespace.id,
                    func.lower(ChainTransaction.caller_address)
                    == func.lower(OperationStep.detail["caller"].astext),
                    or_(
                        ChainTransaction.status.in_(("prepared", "sending", "submitted")),
                        and_(ChainTransaction.status == "requires_attention",
                             ChainTransaction.tx_hash.is_(None)),
                    ),
                )
                diagnostic_step = session.scalar(
                    select(OperationStep).join(Operation, Operation.id == OperationStep.operation_id)
                    .where(
                        Operation.namespace_id == namespace.id,
                        Operation.status.in_(("queued", "submitted")),
                        OperationStep.status == "queued", known_attention, ~unresolved,
                    ).order_by(Operation.created_at, OperationStep.step_index)
                    .with_for_update(skip_locked=True, of=OperationStep).limit(1)
                )
                if diagnostic_step is not None:
                    prior = session.scalar(select(func.count()).select_from(OperationStep).where(
                        OperationStep.operation_id == diagnostic_step.operation_id,
                        OperationStep.step_index < diagnostic_step.step_index,
                        OperationStep.status != "confirmed",
                    ))
                    diagnostic_operation = session.get(Operation, diagnostic_step.operation_id)
                    detail = diagnostic_step.detail
                    if not prior and self._lock_caller(session, namespace.id, detail["caller"]):
                        pending = session.scalar(select(func.count()).select_from(ChainTransaction).where(
                            ChainTransaction.namespace_id == namespace.id,
                            func.lower(ChainTransaction.caller_address) == detail["caller"].lower(),
                            or_(
                                ChainTransaction.status.in_(("prepared", "sending", "submitted")),
                                and_(ChainTransaction.status == "requires_attention",
                                     ChainTransaction.tx_hash.is_(None)),
                            ),
                        ))
                        diagnostic_envelope = None
                        if not pending:
                            if not self._receipt_source_allowed(session, diagnostic_step, diagnostic_operation):
                                return True
                            try:
                                diagnostic_envelope = self.gateway.prepare(
                                    detail["action"], detail["caller"], detail["args"], detail["expectedEvent"]
                                )
                            except Exception:
                                # An unavailable diagnostic never changes the
                                # closed lane or marks its intent as a failed send.
                                pass
                        if diagnostic_envelope is not None and session.scalar(
                            select(ChainTransaction.id).where(
                                ChainTransaction.namespace_id == namespace.id,
                                func.lower(ChainTransaction.caller_address) == detail["caller"].lower(),
                                ChainTransaction.evm_nonce_text == str(diagnostic_envelope.nonce),
                                ChainTransaction.status != "not_broadcast",
                            ).limit(1)
                        ) is not None:
                            self._nonce_conflict(session, diagnostic_step, diagnostic_operation,
                                                 diagnostic_envelope)
                            return True
                # A different diagnostic nonce remains blocked below. Continue
                # selecting other callers so a closed lane cannot starve them.
                # Unknown, pending and leased envelopes block this caller even
                # when a different operation owns them. Other callers can work.
                blocked = exists().where(
                    ChainTransaction.namespace_id == namespace.id,
                    func.lower(ChainTransaction.caller_address)
                    == func.lower(OperationStep.detail["caller"].astext),
                    ChainTransaction.status.in_(("prepared", "sending", "submitted", "requires_attention")),
                )
                step = session.scalar(
                    select(OperationStep)
                    .join(Operation, Operation.id == OperationStep.operation_id)
                    .where(
                        Operation.namespace_id == namespace.id,
                        Operation.status.in_(("queued", "submitted")),
                        OperationStep.status == "queued",
                        ~blocked,
                    )
                    .order_by(Operation.created_at, OperationStep.step_index)
                    .with_for_update(skip_locked=True, of=OperationStep)
                    .limit(1)
                )
                if step is None:
                    return False
                prior = session.scalar(
                    select(func.count()).select_from(OperationStep).where(
                        OperationStep.operation_id == step.operation_id,
                        OperationStep.step_index < step.step_index,
                        OperationStep.status != "confirmed",
                    )
                )
                if prior:
                    return False
                operation = session.get(Operation, step.operation_id)
                detail = step.detail
                if not self._lock_caller(session, namespace.id, detail["caller"]):
                    return False
                outstanding = session.scalar(
                    select(func.count()).select_from(ChainTransaction).where(
                        ChainTransaction.namespace_id == operation.namespace_id,
                        func.lower(ChainTransaction.caller_address) == detail["caller"].lower(),
                        ChainTransaction.status.in_(("prepared", "sending", "submitted", "requires_attention")),
                    )
                )
                if outstanding:
                    return False
                if not self._receipt_source_allowed(session, step, operation):
                    return True
                try:
                    envelope = self.gateway.prepare(
                        detail["action"], detail["caller"], detail["args"], detail["expectedEvent"]
                    )
                except Exception as exc:
                    # prepare is a read-only adapter boundary: no send was made
                    # and no EVM reservation exists to strand the next operation.
                    self._failed_step(session, step, operation, code="chain_preparation_rejected",
                                      detail=str(exc))
                    return True
                existing = ChainTransaction(
                    operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
                    caller_address=envelope.caller, to_address=envelope.to,
                    chain_id=envelope.chain_id, evm_nonce_text=str(envelope.nonce),
                    calldata=envelope.data, calldata_hash="0x" + __import__("eth_utils").keccak(
                        bytes.fromhex(envelope.data[2:])
                    ).hex(), value_text=str(envelope.value), envelope_hash=envelope.hash,
                    status="prepared", canonical=False,
                )
                try:
                    with session.begin_nested():
                        session.add(existing)
                        session.flush()
                except IntegrityError as exc:
                    if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != "uq_chain_tx_caller_nonce":
                        raise
                    # A reorg or competing operation can expose a nonce already in
                    # the durable history. Preserve that history and send nothing.
                    self._nonce_conflict(session, step, operation, envelope)
                    return True
                step.status = "prepared"
            else:
                if not self._lock_caller(session, namespace.id, existing.caller_address or ""):
                    return False
                recovered_prepared_attempt = existing.status == "prepared"
                reconcile_only = existing.status != "prepared"
            if existing.status == "prepared":
                existing.status = "sending"
            existing.submitted_at = _utcnow()
            transaction_id = existing.id
        if transaction_id is None:
            return False
        with self.factory() as session:
            row = session.get(ChainTransaction, transaction_id)
            step = session.get(OperationStep, row.step_id)
            envelope = _envelope(row, step.detail)
        try:
            tx_hash = self.gateway.find_envelope_transaction(envelope)
        except Exception as exc:
            if reconcile_only or recovered_prepared_attempt:
                self._unknown(transaction_id, exc)
            else:
                self._not_broadcast(transaction_id, exc)
            return True
        if tx_hash is None and reconcile_only:
            self._unknown(transaction_id, RuntimeError(
                "A prior send may have been accepted; exact envelope is not yet provable"
            ))
            return True
        if tx_hash is None:
            try:
                latest_nonce = self.gateway.w3.eth.get_transaction_count(envelope.caller, "latest")
                pending_nonce = self.gateway.w3.eth.get_transaction_count(envelope.caller, "pending")
            except Exception as exc:
                if recovered_prepared_attempt:
                    self._unknown(transaction_id, exc)
                else:
                    self._not_broadcast(transaction_id, exc)
                return True
            if latest_nonce > envelope.nonce or pending_nonce != envelope.nonce:
                self._unknown(transaction_id, RuntimeError(
                    "Caller nonce changed but the exact prepared envelope was not found"
                ))
                return True
            if envelope.action == "receipt.submit":
                with self.factory() as session, session.begin():
                    attempt = session.get(ChainTransaction, transaction_id, with_for_update=True)
                    queued_step = session.get(OperationStep, attempt.step_id)
                    queued_operation = session.get(Operation, attempt.operation_id)
                    if not self._receipt_source_allowed(session, queued_step, queued_operation, attempt):
                        return True
            try:
                tx_hash = self.gateway.send(envelope)
            except ChainNotBroadcast as exc:
                self._not_broadcast(transaction_id, exc)
                return True
            except Exception as exc:
                try:
                    tx_hash = self.gateway.find_envelope_transaction(envelope)
                except Exception:
                    tx_hash = None
                if tx_hash is None:
                    self._unknown(transaction_id, exc)
                    return True
        try:
            self._submitted(transaction_id, tx_hash)
        except Exception as exc:
            # If the accepted-send result cannot commit, the already committed
            # sending lease remains recoverable. A later worker never sends it
            # again without proving that exact envelope.
            try:
                self._unknown(transaction_id, exc)
            except Exception:
                pass
        return True


class ChainIndexer:
    def __init__(self, database_url: str, gateway: LocalChainGateway):
        self.engine = build_engine(database_url)
        self.factory = build_session_factory(self.engine)
        self.gateway = gateway

    def close(self) -> None:
        self.engine.dispose()

    def _tip(self) -> int:
        try:
            return int(self.gateway.w3.eth.block_number)
        except ChainUnavailable:
            raise
        except Exception as exc:
            raise ChainUnavailable("Canonical tip lookup is temporarily unavailable") from exc

    def once(self) -> bool:
        try:
            return self._once()
        except _CanonicalChainChanged as exc:
            # The confirmation transaction has rolled back, including events and
            # read models. Preserve an orphaned observed receipt as an attention
            # record, then rebuild; a second moving-tip race is retried next tick.
            if exc.transaction_id is not None:
                with self.factory() as session, session.begin():
                    tx = session.get(ChainTransaction, exc.transaction_id, with_for_update=True)
                    operation = session.get(Operation, tx.operation_id)
                    step = session.get(OperationStep, tx.step_id)
                    tx.receipt_json = exc.receipt
                    tx.block_hash = exc.receipt["blockHash"]
                    tx.canonical = False
                    tx.status = operation.status = "requires_attention"
                    if step is not None:
                        step.status = "requires_attention"
                    operation.error_code = "chain_reorganization"
                    operation.error_status = 409
                    operation.error_detail = "Receipt block changed during confirmation; no confirmation committed"
                    for request in session.scalars(select(SigningRequest).where(
                        SigningRequest.submitted_operation_id == operation.id
                    )):
                        request.status = "requires_attention"
            try:
                self.rebuild()
            except _CanonicalChainChanged:
                pass
            return True

    def _once(self) -> bool:
        with self.factory() as session, session.begin():
            namespace = ensure_verified_namespace(session, self.gateway)
            namespace_id = namespace.id
        # A quiet or shorter tip is still capable of orphaning facts. Receipt
        # confirmation can also run ahead of the event cursor, so check both.
        if self._canonical_drift(namespace_id):
            self.rebuild()
            return True
        with self.factory() as session, session.begin():
            tx = session.scalar(
                select(ChainTransaction).where(
                    ChainTransaction.namespace_id == namespace_id,
                    ChainTransaction.status == "submitted",
                )
                .order_by(ChainTransaction.created_at).limit(1)
            )
            transaction_id = None
            if tx is not None and tx.tx_hash:
                step = session.get(OperationStep, tx.step_id)
                expected = step.detail["expectedEvent"]
                transaction_id = tx.id
        if transaction_id is None:
            return self.sync_one_block()
        receipt, events = self.gateway.receipt_with_events(tx.tx_hash, expected)
        if receipt is None:
            return False
        canonical = self.gateway.canonical(receipt["blockNumber"], receipt["blockHash"])
        confirmation_error = None
        if canonical and events:
            try:
                self._validate_confirmation(step.detail, tx, receipt, events[0])
            except ChainUnavailable:
                raise
            except (TimeoutError, ConnectionError, RequestException) as exc:
                raise ChainUnavailable("Historical confirmation getter is temporarily unavailable") from exc
            except Exception as exc:
                confirmation_error = str(exc)[:1000]
        with self.factory() as session, session.begin():
            session.get(DeploymentInstance, namespace_id, with_for_update=True)
            tx = session.get(ChainTransaction, transaction_id, with_for_update=True)
            # Another indexer may have confirmed this receipt while we read RPC.
            if tx.status != "submitted":
                return True
            step = session.get(OperationStep, tx.step_id)
            operation = session.get(Operation, tx.operation_id)
            tx.receipt_json = receipt
            tx.block_hash = receipt["blockHash"]
            if not self.gateway.canonical(receipt["blockNumber"], receipt["blockHash"]):
                raise _CanonicalChainChanged(transaction_id=transaction_id, receipt=receipt)
            if receipt["status"] != 1:
                tx.status = step.status = operation.status = "failed"
                operation.error_code = "chain_transaction_reverted"
                operation.error_status = 409
                operation.error_detail = "Receipt status is 0"
                signing_request = session.scalar(select(SigningRequest).where(
                    SigningRequest.submitted_operation_id == operation.id
                ))
                if signing_request is not None:
                    signing_request.status = "failed"
                return True
            if not canonical or not events or confirmation_error is not None:
                tx.status = step.status = operation.status = "requires_attention"
                operation.error_code = "chain_confirmation_invalid"
                operation.error_status = 409
                operation.error_detail = confirmation_error or (
                    "Receipt is noncanonical or expected event is absent"
                )
                signing_request = session.scalar(select(SigningRequest).where(
                    SigningRequest.submitted_operation_id == operation.id
                ))
                if signing_request is not None:
                    signing_request.status = "requires_attention"
                return True
            tx.status = "confirmed"
            tx.canonical = True
            tx.confirmed_at = _utcnow()
            step.status = "confirmed"
            for event in events:
                self._store_event(session, operation.namespace_id, event)
            try:
                self._project(operation, step, tx, events[0], session)
            except _CanonicalChainChanged:
                if not self.gateway.canonical(receipt["blockNumber"], receipt["blockHash"]):
                    raise _CanonicalChainChanged(transaction_id=transaction_id, receipt=receipt)
                raise
            signing_request = session.scalar(select(SigningRequest).where(
                SigningRequest.submitted_operation_id == operation.id
            ))
            if signing_request is not None:
                signing_request.status = "confirmed"
            session.flush()
            remaining = session.scalar(
                select(func.count()).select_from(OperationStep).where(
                    OperationStep.operation_id == operation.id,
                    OperationStep.status != "confirmed",
                )
            )
            operation.status = "confirmed" if remaining == 0 else "queued"
            # Getter/receipt RPC happened outside and during this DB transaction.
            # A canonical replacement can occur before commit without advancing
            # latest, so recheck the original receipt rather than only the tip.
            if not self.gateway.canonical(receipt["blockNumber"], receipt["blockHash"]):
                raise _CanonicalChainChanged(transaction_id=transaction_id, receipt=receipt)
        return True

    def _validate_confirmation(
        self, detail: dict, tx: ChainTransaction, receipt: dict, event: dict,
    ) -> None:
        action = detail["action"]
        args = detail["args"]
        event_args = event["args"]
        target_contract = {
            "donation.approve": "MockHKD",
            "donation.deposit": "ProcurementEscrowV2",
            "approval.reserve": "ProcurementEscrowV2",
            "reserve.execute": "ProcurementEscrowV2",
        }.get(action, "PoGRegistryV2")
        expected_target = self.gateway.contract_address(target_contract)
        checks = (
            _equal(receipt.get("from"), detail["caller"]),
            _equal(receipt.get("to"), expected_target),
            _equal(tx.to_address, expected_target),
            _equal(event.get("address"), expected_target),
            _equal(event.get("transactionHash"), tx.tx_hash),
            event.get("event") == detail["expectedEvent"],
        )
        if not all(checks):
            raise ValueError("Receipt caller, target, transaction or expected event does not match")

        def call(contract, function, *values):
            return self.gateway.call(
                contract, function, *values, block_identifier=int(receipt["blockNumber"])
            )

        if action == "project.create":
            view = call("PoGRegistryV2", "getProject", args[0])
            exact = (
                _equal(event_args.get("projectId"), args[0]),
                _equal(event_args.get("foundation"), detail["caller"]),
                _equal(event_args.get("recipient"), args[1]),
                _equal(event_args.get("asset"), args[2]),
                int(event_args.get("threshold", -1)) == int(args[4]),
                _equal(view[0], args[0]), _equal(view[1], detail["caller"]),
                _equal(view[2], args[1]), _equal(view[3], args[2]),
                int(view[6]) == int(args[4]), int(view[7]) == 0,
            )
        elif action == "donation.approve":
            allowance = call(
                "MockHKD", "allowance", Web3.to_checksum_address(detail["caller"]),
                Web3.to_checksum_address(args[0]),
            )
            exact = (
                _equal(event_args.get("owner"), detail["caller"]),
                _equal(event_args.get("spender"), args[0]),
                int(event_args.get("value", -1)) == int(args[1]),
                int(allowance) == int(args[1]),
            )
        elif action == "donation.deposit":
            ledger = call("ProcurementEscrowV2", "getLedger", args[0])
            credit = call(
                "ProcurementEscrowV2", "donorCredit", args[0],
                Web3.to_checksum_address(detail["caller"]),
            )
            exact = (
                _equal(event_args.get("projectId"), args[0]),
                _equal(event_args.get("donor"), detail["caller"]),
                int(event_args.get("amount", -1)) == int(args[1]),
                int(event_args.get("cumulativeCredit", -1)) == int(credit),
                int(ledger[1]) >= int(args[1]),
            )
        elif action == "procurement.create":
            view = call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("procurementId"), args[0]),
                _equal(event_args.get("projectId"), args[1]),
                _equal(event_args.get("vendor"), args[2]),
                int(event_args.get("budgetCap", -1)) == int(args[3]),
                _equal(view[0], args[0]), _equal(view[1], args[1]),
                _equal(view[2], args[2]), int(view[3]) == int(args[3]), int(view[-1]) == 0,
            )
        elif action == "procurement.po":
            view = call("PoGRegistryV2", "getProcurement", args[0])
            exact = tuple(_equal(event_args.get(name), args[index]) for name, index in (
                ("procurementId", 0), ("poHash", 1), ("requestHash", 2),
                ("goodsRequestHash", 3),
            )) + (
                _equal(view[4], args[1]), _equal(view[5], args[2]),
                _equal(view[6], args[3]),
                _equal(view[7], event_args.get("preEvidenceHash")), int(view[-1]) == 1,
            )
        elif action == "assessment.ai_pre":
            message = args[0]
            view = call("PoGRegistryV2", "getProcurement", message[1])
            names = (
                "stage", "procurementId", "assessmentId", "outcome", "riskScoreBps",
                "evidenceHash", "reportHash", "signer", "deadline",
            )
            values = (message[0], message[1], message[2], message[3], message[4],
                      message[5], message[6], message[7], message[9])
            exact = tuple(_equal(event_args.get(name), value) for name, value in zip(names, values)) + (
                _equal(view[8], message[2]), int(view[-1]) == 2,
            )
        elif action == "approval.reserve":
            intent = args[2]
            view = call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("targetId"), args[0]),
                int(event_args.get("action", -1)) == 0,
                _equal(event_args.get("signer"), intent[4]),
                int(event_args.get("deadline", -1)) == int(intent[6]),
                int(view[-1]) == 3,
            )
        elif action == "reserve.execute":
            view = call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("procurementId"), args[0]),
                _equal(event_args.get("projectId"), view[1]),
                int(event_args.get("amount", -1)) == int(args[1]),
                int(view[9]) == int(args[1]), int(view[-1]) == 4,
            )
        elif action == "procurement.invoice":
            view = call("PoGRegistryV2", "getProcurement", args[0])
            exact = tuple(_equal(event_args.get(name), args[index]) for name, index in (
                ("procurementId", 0), ("invoiceHash", 1), ("invoiceAmount", 2), ("goodsHash", 3),
            )) + (
                _equal(view[10], args[1]), int(view[11]) == int(args[2]),
                _equal(view[12], args[3]), int(view[-1]) == 5,
            )
        elif action == "receipt.submit":
            message = args[0]
            view = call("PoGRegistryV2", "getProcurement", message[1])
            exact = (
                _equal(event_args.get("procurementId"), message[1]),
                _equal(event_args.get("recipient"), message[2]),
                _equal(event_args.get("receiptEvidenceHash"), message[8]),
                _equal(view[13], event_args.get("receiptDigest")), int(view[-1]) == 6,
            )
        else:
            raise ValueError("Unsupported confirmation action")
        if not all(exact):
            raise ValueError(f"{action} event or canonical getter state does not match queued intent")

    def _canonical_drift(self, namespace_id: UUID) -> bool:
        with self.factory() as session:
            cursor = session.scalar(select(IndexerCursor).where(
                IndexerCursor.namespace_id == namespace_id,
                IndexerCursor.consumer_name == "a2-chain-events",
            ))
            checkpoints = []
            if cursor is not None and cursor.last_block_hash:
                checkpoints.append((cursor.next_block - 1, cursor.last_block_hash))
            # Latest-state projection can precede the scanner checkpoint (for
            # example delayed PO confirmation sees a later external deposit).
            # That independently observed block must also remain canonical.
            for projection in session.scalars(select(LedgerProjection).where(
                LedgerProjection.namespace_id == namespace_id,
            )):
                if projection.block_hash:
                    checkpoints.append((projection.block_number, projection.block_hash))
            for policy in session.scalars(select(PolicyProjection).where(
                PolicyProjection.namespace_id == namespace_id,
            )):
                checkpoints.append((policy.block_number, policy.block_hash))
            for tx in session.scalars(select(ChainTransaction).where(
                ChainTransaction.namespace_id == namespace_id,
                ChainTransaction.status == "confirmed",
            )):
                if not tx.block_hash or not tx.receipt_json:
                    return True
                checkpoints.append((int(tx.receipt_json["blockNumber"]), tx.block_hash))
        return any(not self.gateway.canonical(number, digest) for number, digest in checkpoints)

    @staticmethod
    def _store_event(session, namespace_id: UUID, event: dict) -> None:
        values = {
            "namespace_id": namespace_id, "contract_address": event["address"],
            "tx_hash": event["transactionHash"], "log_index": event["logIndex"],
            "block_hash": event["blockHash"], "block_number": event["blockNumber"],
            "transaction_index": event["transactionIndex"], "event_name": event["event"],
            "canonical": True, "payload": event["args"],
        }
        session.execute(insert(ChainEvent).values(**values).on_conflict_do_update(
            constraint="uq_chain_event_identity", set_={
                "canonical": True, "payload": event["args"],
                "block_number": event["blockNumber"],
                "transaction_index": event["transactionIndex"],
            },
        ))

    def sync_one_block(self) -> bool:
        """Commit canonical events, read models and checkpoint as one unit."""
        with self.factory() as session, session.begin():
            namespace = ensure_verified_namespace(session, self.gateway)
            namespace_id = namespace.id
        if self._canonical_drift(namespace_id):
            self.rebuild()
            return True
        try:
            with self.factory() as session, session.begin():
                session.get(DeploymentInstance, namespace_id, with_for_update=True)
                cursor = session.scalar(select(IndexerCursor).where(
                    IndexerCursor.namespace_id == namespace_id,
                    IndexerCursor.consumer_name == "a2-chain-events",
                ).with_for_update())
                if cursor is None:
                    cursor = IndexerCursor(
                        namespace_id=namespace_id, consumer_name="a2-chain-events",
                        next_block=self.gateway.deployment_start_block(), last_block_hash=None,
                    )
                    session.add(cursor)
                    session.flush()
                next_block = cursor.next_block
                # Recheck under the same namespace lock used by receipt processing.
                if cursor.last_block_hash and not self.gateway.canonical(
                    next_block - 1, cursor.last_block_hash
                ):
                    raise _CanonicalChainChanged()
                if next_block > self._tip():
                    return False
                block_hash, parent_hash = self.gateway.block_identity(next_block)
                if cursor.last_block_hash and not _equal(parent_hash, cursor.last_block_hash):
                    raise _CanonicalChainChanged()
                events = self.gateway.events_in_range(next_block, next_block)
                for event in events:
                    self._store_event(session, namespace_id, event)
                if events:
                    history = self.gateway.events_in_range(
                        self.gateway.deployment_start_block(), next_block
                    )
                    self._refresh_projects(session, namespace_id, history, next_block, block_hash)
                if not self.gateway.canonical(next_block, block_hash):
                    raise _CanonicalChainChanged()
                cursor.next_block = next_block + 1
                cursor.last_block_hash = block_hash
        except _CanonicalChainChanged:
            try:
                self.rebuild()
            except _CanonicalChainChanged:
                pass
        return True

    def rebuild(self) -> None:
        """Reconstruct from the verified canonical deployment anchor; never send/reset."""
        self.gateway.verify()
        with self.factory() as session, session.begin():
            namespace = session.scalar(select(DeploymentInstance).where(
                DeploymentInstance.run_id == self.gateway.run_id,
                DeploymentInstance.instance_id == self.gateway.instance_id,
                DeploymentInstance.active.is_(True),
            ).with_for_update())
            if namespace is None:
                raise RuntimeError("Active A2 namespace is missing")
            latest = self._tip()
            last_hash = self.gateway.block_identity(latest)[0]
            events = self.gateway.events_in_range(self.gateway.deployment_start_block(), latest)
            session.execute(update(ChainEvent).where(
                ChainEvent.namespace_id == namespace.id
            ).values(canonical=False))
            for event in events:
                self._store_event(session, namespace.id, event)
            for tx in session.scalars(select(ChainTransaction).where(
                ChainTransaction.namespace_id == namespace.id
            ).with_for_update()).all():
                tx.canonical = bool(tx.block_hash and tx.receipt_json and self.gateway.canonical(
                    int(tx.receipt_json["blockNumber"]), tx.block_hash
                ))
                if tx.status == "confirmed" and not tx.canonical:
                    tx.status = "requires_attention"
                    step = session.get(OperationStep, tx.step_id)
                    operation = session.get(Operation, tx.operation_id)
                    if step is not None:
                        step.status = "requires_attention"
                    operation.status = "requires_attention"
                    operation.error_code = "chain_reorganization"
                    operation.error_status = 409
                    operation.error_detail = "Previously confirmed block is no longer canonical"
                    for request in session.scalars(select(SigningRequest).where(
                        SigningRequest.submitted_operation_id == operation.id
                    )):
                        request.status = "requires_attention"
            self._refresh_projects(session, namespace.id, events, latest, last_hash, force=True)
            if not self.gateway.canonical(latest, last_hash):
                raise _CanonicalChainChanged("Canonical tip changed during rebuild; retry indexer")
            cursor = session.scalar(select(IndexerCursor).where(
                IndexerCursor.namespace_id == namespace.id,
                IndexerCursor.consumer_name == "a2-chain-events",
            ).with_for_update())
            if cursor is None:
                cursor = IndexerCursor(namespace_id=namespace.id, consumer_name="a2-chain-events")
                session.add(cursor)
            cursor.next_block = latest + 1
            cursor.last_block_hash = last_hash

    def _policy(self, project: Project, view, events: list[dict], block: int) -> dict:
        policy_events = [event for event in events if event["event"] in {
            "ProjectCreated", "ApprovalPolicyUpdated"
        } and _equal(event["args"].get("projectId"), project.business_id)]
        if not policy_events:
            raise ValueError("Canonical policy creation/update event is missing")
        source = policy_events[-1]
        try:
            transaction = self.gateway.w3.eth.get_transaction(source["transactionHash"])
        except Exception as exc:
            raise ChainUnavailable("Canonical policy transaction lookup is unavailable") from exc
        receipt = self.gateway.receipt(source["transactionHash"])
        if receipt is None:
            raise ChainUnavailable("Canonical policy receipt lookup is unavailable")
        registry = self.gateway.contracts["PoGRegistryV2"]
        function, arguments = registry.decode_function_input(transaction["input"])
        expected_function = ("createProject" if source["event"] == "ProjectCreated"
                             else "updateApprovalPolicy")
        if not (
            function.fn_name == expected_function
            and _equal(transaction.get("to"), registry.address)
            and _equal(transaction.get("from"), view[1])
            and _equal(arguments.get("projectId"), project.business_id)
            and receipt and int(receipt["status"]) == 1
            and _equal(receipt["blockHash"], source["blockHash"])
            and self.gateway.canonical(source["blockNumber"], source["blockHash"])
        ):
            raise ValueError("Canonical policy calldata/receipt does not match project")
        approvers = [Web3.to_checksum_address(wallet).lower() for wallet in arguments["approvers"]]
        threshold = int(arguments["threshold"])
        epoch = (1 if source["event"] == "ProjectCreated" else int(source["args"]["policyEpoch"]))
        if not (
            1 <= threshold <= len(approvers) <= 16 and len(set(approvers)) == len(approvers)
            and threshold == int(source["args"]["threshold"]) == int(view[6])
            and epoch == int(view[5])
            and all(self.gateway.call(
                "ProcurementEscrowV2", "isApprover", bytes.fromhex(project.business_id[2:]),
                Web3.to_checksum_address(wallet), block_identifier=block,
            ) for wallet in approvers)
        ):
            raise ValueError("Canonical policy members/epoch/threshold do not match isApprover")
        return {
            "policy_epoch": epoch, "threshold": threshold, "approver_wallets": approvers,
            "block_number": source["blockNumber"], "block_hash": source["blockHash"],
            "tx_hash": source["transactionHash"],
        }

    @staticmethod
    def _reset_procurement(procurement: Procurement) -> None:
        procurement.chain_status = "off_chain_draft"
        for field in (
            "po_hash", "request_hash", "goods_request_hash", "pre_evidence_hash",
            "pre_assessment_id", "reserved_amount_atomic", "invoice_hash", "invoice_amount_atomic",
            "goods_hash", "receipt_digest", "chain_tx_hash", "chain_block_number",
        ):
            setattr(procurement, field, None)

    def _refresh_projects(self, session, namespace_id: UUID, events: list[dict],
                          block: int, block_hash: str, *, force: bool = False) -> None:
        """All values are read at one block, never labelled with an earlier source."""
        for project in session.scalars(select(Project).where(
            Project.namespace_id == namespace_id
        )).all():
            previous = session.scalar(select(LedgerProjection).where(
                LedgerProjection.namespace_id == namespace_id,
                LedgerProjection.project_id == project.id,
            ))
            # Receipt processing may already have projected a newer canonical
            # tip while the event cursor is catching up. Only reorg rebuild may
            # deliberately move a read model backwards.
            if not force and previous is not None and previous.block_number > block:
                continue
            project_events = [event for event in events
                              if _equal(event["args"].get("projectId"), project.business_id)]
            procurements = session.scalars(select(Procurement).where(
                Procurement.project_id == project.id, Procurement.namespace_id == namespace_id,
            )).all()
            if not any(event["event"] == "ProjectCreated" for event in project_events):
                project.chain_status = "off_chain_draft"
                project.chain_tx_hash = project.chain_block_number = None
                for model in (DonorCreditProjection, LedgerProjection, PolicyProjection):
                    session.execute(delete(model).where(
                        model.namespace_id == namespace_id, model.project_id == project.id,
                    ))
                for procurement in procurements:
                    self._reset_procurement(procurement)
                continue
            project_id = bytes.fromhex(project.business_id[2:])
            view = self.gateway.call("PoGRegistryV2", "getProject", project_id, block_identifier=block)
            ledger = self.gateway.call("ProcurementEscrowV2", "getLedger", project_id,
                                       block_identifier=block)
            if not (
                _equal(view[0], project.business_id)
                and _equal(view[1], project.foundation_wallet)
                and _equal(view[2], project.recipient_wallet)
                and _equal(view[3], self.gateway.contract_address("MockHKD"))
            ):
                raise ValueError("Canonical project parties/asset do not match the fixed draft")
            policy = self._policy(project, view, events, block)
            if not (_equal(view[3], ledger[0]) and int(view[5]) == int(ledger[9])
                    and int(view[6]) == int(ledger[10])):
                raise ValueError("Registry project and Escrow ledger policy/asset disagree")
            project.chain_status = PROJECT_STATES[int(view[7])]
            # State provenance follows the canonical lifecycle event, not an
            # unrelated donation or policy event observed at the same tip.
            project_state_event = (
                "ProjectCreated", "ProjectClosingRequested", "ProjectRefundable", "ProjectClosed"
            )[int(view[7])]
            project_event = next((event for event in reversed(project_events)
                                  if event["event"] == project_state_event), None)
            project.chain_tx_hash = project_event["transactionHash"] if project_event else None
            project.chain_block_number = project_event["blockNumber"] if project_event else None
            ledger_values = {
                "asset_address": ledger[0], "deposits_atomic": Decimal(ledger[1]),
                "reserved_atomic": Decimal(ledger[2]), "released_atomic": Decimal(ledger[3]),
                "returned_atomic": Decimal(ledger[4]), "policy_epoch": int(ledger[9]),
                "threshold": int(ledger[10]), "block_number": block, "block_hash": block_hash,
            }
            session.execute(insert(LedgerProjection).values(
                namespace_id=namespace_id, project_id=project.id, **ledger_values,
            ).on_conflict_do_update(constraint="uq_ledger_projection_project", set_=ledger_values))
            session.execute(insert(PolicyProjection).values(
                namespace_id=namespace_id, project_id=project.id, **policy,
            ).on_conflict_do_update(constraint="uq_policy_projection_project", set_=policy))
            donors = {event["args"]["donor"].lower() for event in project_events
                      if event["event"] == "Donated"}
            session.execute(delete(DonorCreditProjection).where(
                DonorCreditProjection.namespace_id == namespace_id,
                DonorCreditProjection.project_id == project.id,
                DonorCreditProjection.donor_wallet.not_in(donors),
            ))
            for donor in donors:
                credit = self.gateway.call("ProcurementEscrowV2", "donorCredit", project_id,
                                           Web3.to_checksum_address(donor), block_identifier=block)
                session.execute(insert(DonorCreditProjection).values(
                    namespace_id=namespace_id, project_id=project.id, donor_wallet=donor,
                    credit_atomic=Decimal(credit), block_number=block,
                ).on_conflict_do_update(constraint="uq_donor_credit", set_={
                    "credit_atomic": Decimal(credit), "block_number": block,
                }))
            for procurement in procurements:
                procurement_events = [event for event in events if
                                      _equal(event["args"].get("procurementId"), procurement.business_id)
                                      or (event["event"] == "HumanApprovalSubmitted"
                                          and _equal(event["args"].get("targetId"), procurement.business_id))]
                if not any(event["event"] == "ProcurementCreated" for event in procurement_events):
                    self._reset_procurement(procurement)
                    continue
                proc_view = self.gateway.call("PoGRegistryV2", "getProcurement",
                                              bytes.fromhex(procurement.business_id[2:]),
                                              block_identifier=block)
                if not (
                    _equal(proc_view[0], procurement.business_id)
                    and _equal(proc_view[1], project.business_id)
                    and _equal(proc_view[2], procurement.vendor_wallet)
                    and int(proc_view[3]) == int(procurement.budget_cap_atomic)
                ):
                    raise ValueError("Canonical procurement parties/budget do not match the fixed draft")
                procurement.chain_status = PROCUREMENT_STATES[int(proc_view[-1])]
                for field, index in (
                    ("po_hash", 4), ("request_hash", 5), ("goods_request_hash", 6),
                    ("pre_evidence_hash", 7), ("pre_assessment_id", 8),
                    ("invoice_hash", 10), ("goods_hash", 12), ("receipt_digest", 13),
                ):
                    setattr(procurement, field, _normalized(proc_view[index]))
                procurement.reserved_amount_atomic = Decimal(proc_view[9])
                procurement.invoice_amount_atomic = Decimal(proc_view[11])
                procurement.chain_tx_hash = procurement_events[-1]["transactionHash"]
                procurement.chain_block_number = procurement_events[-1]["blockNumber"]

    def _project(self, operation: Operation, step: OperationStep, tx: ChainTransaction,
                 event: dict, session) -> None:
        # Historical getters and complete canonical events also cover externally
        # submitted transactions, and are shared with the cursor/rebuild paths.
        if step.detail["action"] == "donation.approve":
            return
        latest = self._tip()
        block_hash = self.gateway.block_identity(latest)[0]
        history = self.gateway.events_in_range(self.gateway.deployment_start_block(), latest)
        history.sort(key=lambda row: (row["blockNumber"], row["transactionIndex"], row["logIndex"]))
        for source in history:
            self._store_event(session, operation.namespace_id, source)
        self._refresh_projects(session, operation.namespace_id, history, latest, block_hash)
        cursor = session.scalar(select(IndexerCursor).where(
            IndexerCursor.namespace_id == operation.namespace_id,
            IndexerCursor.consumer_name == "a2-chain-events",
        ).with_for_update())
        if cursor is None:
            cursor = IndexerCursor(namespace_id=operation.namespace_id, consumer_name="a2-chain-events")
            session.add(cursor)
        cursor.next_block = latest + 1
        cursor.last_block_hash = block_hash
        if not self.gateway.canonical(latest, block_hash):
            raise _CanonicalChainChanged("Chain changed during receipt projection; retry indexer")
