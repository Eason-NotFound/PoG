from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from web3 import Web3

from .chain import LocalChainGateway, PreparedEnvelope
from .db import build_engine, build_session_factory
from .idempotency import ensure_verified_namespace
from .models import (
    ChainEvent, ChainTransaction, DonorCreditProjection, LedgerProjection,
    DeploymentInstance, IndexerCursor, Operation, OperationStep, Procurement, Project,
    SigningRequest,
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


class ChainWorker:
    def __init__(self, database_url: str, gateway: LocalChainGateway):
        self.engine = build_engine(database_url)
        self.factory = build_session_factory(self.engine)
        self.gateway = gateway

    def close(self) -> None:
        self.engine.dispose()

    def once(self) -> bool:
        """Prepare durably under SKIP LOCKED, then send without holding a DB transaction."""
        transaction_id: UUID | None = None
        with self.factory() as session, session.begin():
            namespace = ensure_verified_namespace(session, self.gateway)
            lease_cutoff = _utcnow() - timedelta(seconds=15)
            existing = session.scalar(
                select(ChainTransaction).where(
                    ChainTransaction.namespace_id == namespace.id,
                    or_(
                        ChainTransaction.status == "prepared",
                        and_(
                            ChainTransaction.status == "sending",
                            ChainTransaction.submitted_at < lease_cutoff,
                        ),
                    ),
                )
                .order_by(ChainTransaction.created_at).with_for_update(skip_locked=True).limit(1)
            )
            if existing is None:
                step = session.scalar(
                    select(OperationStep)
                    .join(Operation, Operation.id == OperationStep.operation_id)
                    .where(
                        Operation.namespace_id == namespace.id,
                        Operation.status.in_(("queued", "submitted")),
                        OperationStep.status == "queued",
                    )
                    .order_by(Operation.created_at, OperationStep.step_index)
                    .with_for_update(skip_locked=True)
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
                outstanding = session.scalar(
                    select(func.count()).select_from(ChainTransaction).where(
                        ChainTransaction.namespace_id == operation.namespace_id,
                        func.lower(ChainTransaction.caller_address) == detail["caller"].lower(),
                        ChainTransaction.status.in_(("prepared", "sending", "submitted")),
                    )
                )
                if outstanding:
                    return False
                envelope = self.gateway.prepare(
                    detail["action"], detail["caller"], detail["args"], detail["expectedEvent"]
                )
                existing = ChainTransaction(
                    operation_id=operation.id, step_id=step.id, namespace_id=operation.namespace_id,
                    caller_address=envelope.caller, to_address=envelope.to,
                    chain_id=envelope.chain_id, evm_nonce_text=str(envelope.nonce),
                    calldata=envelope.data, calldata_hash="0x" + __import__("eth_utils").keccak(
                        bytes.fromhex(envelope.data[2:])
                    ).hex(), value_text=str(envelope.value), envelope_hash=envelope.hash,
                    status="prepared", canonical=False,
                )
                session.add(existing)
                session.flush()
                step.status = "prepared"
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
            if tx_hash is None:
                latest_nonce = self.gateway.w3.eth.get_transaction_count(envelope.caller, "latest")
                if latest_nonce > envelope.nonce:
                    raise RuntimeError("Caller nonce advanced but exact prepared envelope was not found")
                tx_hash = self.gateway.send(envelope)
        except Exception as exc:
            reconciled = self.gateway.find_envelope_transaction(envelope)
            if reconciled is not None:
                tx_hash = reconciled
            else:
                with self.factory() as session, session.begin():
                    row = session.get(ChainTransaction, transaction_id, with_for_update=True)
                    step = session.get(OperationStep, row.step_id)
                    operation = session.get(Operation, row.operation_id)
                    row.status = "requires_attention"
                    step.status = "requires_attention"
                    operation.status = "requires_attention"
                    operation.error_code = "chain_submission_unknown"
                    operation.error_status = 503
                    operation.error_detail = str(exc)[:1000]
                    signing_request = session.scalar(select(SigningRequest).where(
                        SigningRequest.submitted_operation_id == operation.id
                    ))
                    if signing_request is not None:
                        signing_request.status = "requires_attention"
                return True
        with self.factory() as session, session.begin():
            row = session.get(ChainTransaction, transaction_id, with_for_update=True)
            step = session.get(OperationStep, row.step_id)
            operation = session.get(Operation, row.operation_id)
            row.tx_hash = tx_hash
            row.status = "submitted"
            row.submitted_at = _utcnow()
            step.status = "submitted"
            operation.status = "submitted"
        return True


class ChainIndexer:
    def __init__(self, database_url: str, gateway: LocalChainGateway):
        self.engine = build_engine(database_url)
        self.factory = build_session_factory(self.engine)
        self.gateway = gateway

    def close(self) -> None:
        self.engine.dispose()

    def once(self) -> bool:
        with self.factory() as session, session.begin():
            namespace = ensure_verified_namespace(session, self.gateway)
            tx = session.scalar(
                select(ChainTransaction).where(
                    ChainTransaction.namespace_id == namespace.id,
                    ChainTransaction.status == "submitted",
                )
                .order_by(ChainTransaction.created_at).limit(1)
            )
            if tx is None or not tx.tx_hash:
                return self.sync_one_block()
            step = session.get(OperationStep, tx.step_id)
            expected = step.detail["expectedEvent"]
            transaction_id = tx.id
        receipt, events = self.gateway.receipt_with_events(tx.tx_hash, expected)
        if receipt is None:
            return False
        canonical = self.gateway.canonical(receipt["blockNumber"], receipt["blockHash"])
        confirmation_error = None
        if canonical and events:
            try:
                self._validate_confirmation(step.detail, tx, receipt, events[0])
            except Exception as exc:
                confirmation_error = str(exc)[:1000]
        with self.factory() as session, session.begin():
            tx = session.get(ChainTransaction, transaction_id, with_for_update=True)
            step = session.get(OperationStep, tx.step_id)
            operation = session.get(Operation, tx.operation_id)
            tx.receipt_json = receipt
            tx.block_hash = receipt["blockHash"]
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
                session.execute(
                    insert(ChainEvent).values(
                        namespace_id=operation.namespace_id,
                        contract_address=event["address"], tx_hash=event["transactionHash"],
                        log_index=event["logIndex"], block_hash=event["blockHash"],
                        event_name=event["event"], canonical=True, payload=event["args"],
                    ).on_conflict_do_nothing(constraint="uq_chain_event_identity")
                )
            self._project(operation, step, tx, events[0], session)
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

        if action == "project.create":
            view = self.gateway.call("PoGRegistryV2", "getProject", args[0])
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
            allowance = self.gateway.call(
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
            ledger = self.gateway.call("ProcurementEscrowV2", "getLedger", args[0])
            credit = self.gateway.call(
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
            view = self.gateway.call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("procurementId"), args[0]),
                _equal(event_args.get("projectId"), args[1]),
                _equal(event_args.get("vendor"), args[2]),
                int(event_args.get("budgetCap", -1)) == int(args[3]),
                _equal(view[0], args[0]), _equal(view[1], args[1]),
                _equal(view[2], args[2]), int(view[3]) == int(args[3]), int(view[-1]) == 0,
            )
        elif action == "procurement.po":
            view = self.gateway.call("PoGRegistryV2", "getProcurement", args[0])
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
            view = self.gateway.call("PoGRegistryV2", "getProcurement", message[1])
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
            view = self.gateway.call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("targetId"), args[0]),
                int(event_args.get("action", -1)) == 0,
                _equal(event_args.get("signer"), intent[4]),
                int(event_args.get("deadline", -1)) == int(intent[6]),
                int(view[-1]) == 3,
            )
        elif action == "reserve.execute":
            view = self.gateway.call("PoGRegistryV2", "getProcurement", args[0])
            exact = (
                _equal(event_args.get("procurementId"), args[0]),
                _equal(event_args.get("projectId"), view[1]),
                int(event_args.get("amount", -1)) == int(args[1]),
                int(view[9]) == int(args[1]), int(view[-1]) == 4,
            )
        elif action == "procurement.invoice":
            view = self.gateway.call("PoGRegistryV2", "getProcurement", args[0])
            exact = tuple(_equal(event_args.get(name), args[index]) for name, index in (
                ("procurementId", 0), ("invoiceHash", 1), ("invoiceAmount", 2), ("goodsHash", 3),
            )) + (
                _equal(view[10], args[1]), int(view[11]) == int(args[2]),
                _equal(view[12], args[3]), int(view[-1]) == 5,
            )
        elif action == "receipt.submit":
            message = args[0]
            view = self.gateway.call("PoGRegistryV2", "getProcurement", message[1])
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

    def sync_one_block(self) -> bool:
        """Index one canonical block in block/transaction/log order with cursor atomically."""
        with self.factory() as session, session.begin():
            namespace = session.scalar(select(DeploymentInstance).where(
                DeploymentInstance.run_id == self.gateway.run_id,
                DeploymentInstance.instance_id == self.gateway.instance_id,
                DeploymentInstance.active.is_(True),
            ))
            if namespace is None:
                return False
            cursor = session.scalar(select(IndexerCursor).where(
                IndexerCursor.namespace_id == namespace.id,
                IndexerCursor.consumer_name == "a2-chain-events",
            ).with_for_update())
            if cursor is None:
                cursor = IndexerCursor(
                    namespace_id=namespace.id, consumer_name="a2-chain-events",
                    next_block=self.gateway.deployment_start_block(), last_block_hash=None,
                )
                session.add(cursor)
                session.flush()
            next_block = cursor.next_block
            last_hash = cursor.last_block_hash
            namespace_id = namespace.id
        if next_block > self.gateway.w3.eth.block_number:
            return False
        block_hash, parent_hash = self.gateway.block_identity(next_block)
        if last_hash is not None and parent_hash.lower() != last_hash.lower():
            self.rebuild()
            return True
        events = self.gateway.events_in_range(next_block, next_block)
        with self.factory() as session, session.begin():
            cursor = session.scalar(select(IndexerCursor).where(
                IndexerCursor.namespace_id == namespace_id,
                IndexerCursor.consumer_name == "a2-chain-events",
            ).with_for_update())
            if cursor.next_block != next_block:
                return True
            for event in events:
                session.execute(insert(ChainEvent).values(
                    namespace_id=namespace_id, contract_address=event["address"],
                    tx_hash=event["transactionHash"], log_index=event["logIndex"],
                    block_hash=event["blockHash"], event_name=event["event"],
                    canonical=True, payload=event["args"],
                ).on_conflict_do_update(
                    constraint="uq_chain_event_identity", set_={"canonical": True, "payload": event["args"]},
                ))
            cursor.next_block = next_block + 1
            cursor.last_block_hash = block_hash
        return True

    def rebuild(self) -> None:
        """Read-only chain rebuild. It never submits transactions or resets Anvil."""
        self.gateway.verify()
        start = self.gateway.deployment_start_block()
        latest = self.gateway.w3.eth.block_number
        events = self.gateway.events_in_range(start, latest)
        last_hash = self.gateway.block_identity(latest)[0]
        with self.factory() as session, session.begin():
            namespace = session.scalar(select(DeploymentInstance).where(
                DeploymentInstance.run_id == self.gateway.run_id,
                DeploymentInstance.instance_id == self.gateway.instance_id,
                DeploymentInstance.active.is_(True),
            ).with_for_update())
            if namespace is None:
                raise RuntimeError("Active A2 namespace is missing")
            session.execute(update(ChainEvent).where(
                ChainEvent.namespace_id == namespace.id
            ).values(canonical=False))
            for event in events:
                session.execute(insert(ChainEvent).values(
                    namespace_id=namespace.id, contract_address=event["address"],
                    tx_hash=event["transactionHash"], log_index=event["logIndex"],
                    block_hash=event["blockHash"], event_name=event["event"], canonical=True,
                    payload=event["args"],
                ).on_conflict_do_update(
                    constraint="uq_chain_event_identity", set_={"canonical": True, "payload": event["args"]},
                ))
            for tx in session.scalars(select(ChainTransaction).where(
                ChainTransaction.namespace_id == namespace.id
            )).all():
                canonical = bool(tx.block_hash and tx.receipt_json and self.gateway.canonical(
                    int(tx.receipt_json["blockNumber"]), tx.block_hash
                ))
                tx.canonical = canonical
                if tx.status == "confirmed" and not canonical:
                    tx.status = "requires_attention"
                    step = session.get(OperationStep, tx.step_id)
                    operation = session.get(Operation, tx.operation_id)
                    step.status = "requires_attention"
                    operation.status = "requires_attention"
                    operation.error_code = "chain_reorganization"
                    operation.error_status = 409
                    operation.error_detail = "Previously confirmed block is no longer canonical"
                    signing_request = session.scalar(select(SigningRequest).where(
                        SigningRequest.submitted_operation_id == operation.id
                    ))
                    if signing_request is not None:
                        signing_request.status = "requires_attention"
            session.execute(delete(DonorCreditProjection).where(
                DonorCreditProjection.namespace_id == namespace.id
            ))
            session.execute(delete(LedgerProjection).where(
                LedgerProjection.namespace_id == namespace.id
            ))
            canonical_events = [event for event in events]
            for project in session.scalars(select(Project).where(Project.namespace_id == namespace.id)).all():
                try:
                    project_view = self.gateway.call(
                        "PoGRegistryV2", "getProject", bytes.fromhex(project.business_id[2:])
                    )
                    ledger = self.gateway.call(
                        "ProcurementEscrowV2", "getLedger", bytes.fromhex(project.business_id[2:])
                    )
                except Exception:
                    project.chain_status = "off_chain_draft"
                    project.chain_tx_hash = None
                    project.chain_block_number = None
                    for procurement in session.scalars(select(Procurement).where(
                        Procurement.project_id == project.id
                    )).all():
                        procurement.chain_status = "off_chain_draft"
                    continue
                project.chain_status = ("active", "closing", "refundable", "closed")[int(project_view[7])]
                project_event = next((event for event in reversed(canonical_events)
                                      if event["event"] == "ProjectCreated"
                                      and str(event["args"].get("projectId", "")).lower() == project.business_id.lower()), None)
                if project_event:
                    project.chain_tx_hash = project_event["transactionHash"]
                    project.chain_block_number = project_event["blockNumber"]
                session.add(LedgerProjection(
                    namespace_id=namespace.id, project_id=project.id, asset_address=ledger[0],
                    deposits_atomic=Decimal(ledger[1]), reserved_atomic=Decimal(ledger[2]),
                    released_atomic=Decimal(ledger[3]), returned_atomic=Decimal(ledger[4]),
                    policy_epoch=int(ledger[9]), threshold=int(ledger[10]),
                    block_number=latest, block_hash=last_hash,
                ))
                donors = {
                    event["args"]["donor"] for event in canonical_events
                    if event["event"] == "Donated"
                    and str(event["args"].get("projectId", "")).lower() == project.business_id.lower()
                }
                for donor in donors:
                    credit = self.gateway.call(
                        "ProcurementEscrowV2", "donorCredit", bytes.fromhex(project.business_id[2:]),
                        Web3.to_checksum_address(donor),
                    )
                    session.add(DonorCreditProjection(
                        namespace_id=namespace.id, project_id=project.id, donor_wallet=donor.lower(),
                        credit_atomic=Decimal(credit), block_number=latest,
                    ))
                for procurement in session.scalars(select(Procurement).where(
                    Procurement.project_id == project.id
                )).all():
                    try:
                        view = self.gateway.call(
                            "PoGRegistryV2", "getProcurement", bytes.fromhex(procurement.business_id[2:])
                        )
                    except Exception:
                        procurement.chain_status = "off_chain_draft"
                        continue
                    states = (
                        "created", "po_recorded", "pre_assessed", "reserve_approval_pending",
                        "reserved", "invoice_recorded", "receipt_confirmed", "final_assessed",
                        "release_approval_pending", "funds_released", "settlement_recorded",
                        "settlement_approval_pending", "payment_confirmed",
                        "cancellation_approval_pending", "cancelled",
                    )
                    procurement.chain_status = states[int(view[-1])]
                    procurement.reserved_amount_atomic = Decimal(view[9])
                    procurement.invoice_amount_atomic = Decimal(view[11])
                    procurement.receipt_digest = "0x" + bytes(view[13]).hex()
            cursor = session.scalar(select(IndexerCursor).where(
                IndexerCursor.namespace_id == namespace.id,
                IndexerCursor.consumer_name == "a2-chain-events",
            ))
            if cursor is None:
                cursor = IndexerCursor(namespace_id=namespace.id, consumer_name="a2-chain-events")
                session.add(cursor)
            cursor.next_block = latest + 1
            cursor.last_block_hash = last_hash

    def _project(self, operation: Operation, step: OperationStep, tx: ChainTransaction,
                 event: dict, session) -> None:
        detail = step.detail
        block = event["blockNumber"]
        action = detail["action"]
        project = session.get(Project, UUID(detail["projectUuid"])) if detail.get("projectUuid") else None
        procurement = (
            session.get(Procurement, UUID(detail["procurementUuid"]))
            if detail.get("procurementUuid") else None
        )
        if procurement is not None:
            procurement.chain_tx_hash = tx.tx_hash
            procurement.chain_block_number = block
        if action == "project.create" and project:
            project.chain_status = "active"
            project.chain_tx_hash = tx.tx_hash
            project.chain_block_number = block
            ledger = LedgerProjection(
                namespace_id=operation.namespace_id, project_id=project.id,
                asset_address=self.gateway.contract_address("MockHKD"), policy_epoch=1,
                threshold=1, block_number=block, block_hash=tx.block_hash,
            )
            session.add(ledger)
        elif action == "procurement.create" and procurement:
            procurement.chain_status = "created"
        elif action == "procurement.po" and procurement:
            procurement.chain_status = "po_recorded"
            view = self.gateway.call("PoGRegistryV2", "getProcurement", bytes.fromhex(procurement.business_id[2:]))
            procurement.po_hash = "0x" + bytes(view[4]).hex()
            procurement.request_hash = "0x" + bytes(view[5]).hex()
            procurement.goods_request_hash = "0x" + bytes(view[6]).hex()
            procurement.pre_evidence_hash = "0x" + bytes(view[7]).hex()
        elif action == "assessment.ai_pre" and procurement:
            procurement.chain_status = "pre_assessed"
            procurement.pre_assessment_id = event["args"]["assessmentId"]
        elif action == "approval.reserve" and procurement:
            procurement.chain_status = "reserve_approval_pending"
        elif action == "reserve.execute" and procurement:
            procurement.chain_status = "reserved"
            procurement.reserved_amount_atomic = Decimal(str(event["args"]["amount"]))
        elif action == "procurement.invoice" and procurement:
            procurement.chain_status = "invoice_recorded"
        elif action == "receipt.submit" and procurement:
            procurement.chain_status = "receipt_confirmed"
            procurement.receipt_digest = event["args"]["receiptDigest"]
        elif action == "donation.deposit" and project:
            donor = Web3.to_checksum_address(detail["caller"])
            credit = int(self.gateway.call(
                "ProcurementEscrowV2", "donorCredit", bytes.fromhex(project.business_id[2:]), donor
            ))
            ledger_view = self.gateway.call(
                "ProcurementEscrowV2", "getLedger", bytes.fromhex(project.business_id[2:])
            )
            ledger = session.scalar(select(LedgerProjection).where(
                LedgerProjection.namespace_id == operation.namespace_id,
                LedgerProjection.project_id == project.id,
            ))
            ledger.deposits_atomic = Decimal(ledger_view[1])
            ledger.reserved_atomic = Decimal(ledger_view[2])
            ledger.released_atomic = Decimal(ledger_view[3])
            ledger.returned_atomic = Decimal(ledger_view[4])
            ledger.block_number = block
            ledger.block_hash = tx.block_hash
            session.execute(
                insert(DonorCreditProjection).values(
                    namespace_id=operation.namespace_id, project_id=project.id,
                    donor_wallet=donor.lower(), credit_atomic=credit, block_number=block,
                ).on_conflict_do_update(
                    constraint="uq_donor_credit",
                    set_={"credit_atomic": credit, "block_number": block},
                )
            )
