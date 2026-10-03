from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from requests.exceptions import Timeout as RequestsTimeout
from sqlalchemy import func, select
from web3.exceptions import BlockNotFound

from pog_api.chain import ChainUnavailable, LocalChainGateway
from pog_api.models import (
    ChainEvent, ChainTransaction, DeploymentInstance, DonorCreditProjection,
    IndexerCursor, LedgerProjection, Operation, OperationStep, PolicyProjection,
    Procurement, Project,
)
from pog_api.worker import ChainIndexer


def _hash(number: int) -> str:
    return "0x" + f"{number:064x}"


TOKEN = "0x" + "a1" * 20
REGISTRY = "0x" + "b2" * 20
ESCROW = "0x" + "c3" * 20
DONOR = "0x" + "d4" * 20
APPROVER = "0x" + "e5" * 20
NEW_APPROVER = "0x" + "f6" * 20


class ProjectionGateway:
    """Deterministic chain histories; persistence/locking use real PostgreSQL."""

    run_id = "test-run"
    instance_id = "test-instance"
    rpc_url = "http://127.0.0.1:18545"
    manifest_sha256 = "bb" * 32
    manifest = {"chain": {"genesisHash": _hash(0)}}

    def __init__(self, project, procurement):
        self.project = project
        self.procurement = procurement
        self.tip = 4
        self.blocks = {number: _hash(100 + number) for number in range(5)}
        self.calls = []
        self.policy_update = False
        self.replacement = False
        self.ai_advanced = False
        self.wrong_parties = False
        self.reject_membership = False
        self.contracts = {"PoGRegistryV2": SimpleNamespace(
            address=REGISTRY, decode_function_input=self.decode_policy,
        )}
        self.w3 = SimpleNamespace(eth=self)

    @property
    def block_number(self):
        return self.tip

    def verify(self):
        return None

    def deployment_start_block(self):
        return 1

    def block_identity(self, number):
        return self.blocks[number], self.blocks[max(0, number - 1)]

    def canonical(self, number, block_hash):
        return number <= self.tip and self.blocks.get(number) == block_hash

    def contract_address(self, name):
        return {"MockHKD": TOKEN, "PoGRegistryV2": REGISTRY, "ProcurementEscrowV2": ESCROW}[name]

    def event(self, name, block, args, *, log=0, contract=REGISTRY, tx=None):
        return {
            "address": contract, "event": name, "args": args, "logIndex": log,
            "transactionIndex": 0, "transactionHash": tx or _hash(200 + block),
            "blockHash": self.blocks[block], "blockNumber": block,
        }

    def events_in_range(self, start, end):
        events = [
            self.event("ProjectCreated", 1, {
                "projectId": self.project.business_id, "foundation": self.project.foundation_wallet,
                "recipient": self.project.recipient_wallet, "asset": TOKEN, "threshold": 1,
            }),
            self.event("Donated", 2, {
                "projectId": self.project.business_id, "donor": DONOR,
                "amount": 50, "cumulativeCredit": 50,
            }, contract=ESCROW),
            self.event("ProcurementCreated", 3, {
                "procurementId": self.procurement.business_id,
                "projectId": self.project.business_id, "vendor": self.procurement.vendor_wallet,
                "budgetCap": int(self.procurement.budget_cap_atomic),
            }, tx=_hash(303)),
            self.event("PurchaseOrderRecorded", 3, {
                "procurementId": self.procurement.business_id, "poHash": _hash(11),
                "requestHash": _hash(12), "goodsRequestHash": _hash(13),
                "preEvidenceHash": _hash(14),
            }, log=1),
        ]
        if self.policy_update:
            events.append(self.event("ApprovalPolicyUpdated", 3, {
                "projectId": self.project.business_id, "policyEpoch": 2, "threshold": 1,
            }, log=2, tx=_hash(503)))
        if self.replacement:
            events.append(self.event("Donated", 4, {
                "projectId": self.project.business_id, "donor": DONOR,
                "amount": 20, "cumulativeCredit": 70,
            }, contract=ESCROW, tx=_hash(604)))
        elif self.ai_advanced:
            events.append(self.event("AIAssessmentRecorded", 4, {
                "procurementId": self.procurement.business_id, "assessmentId": _hash(15),
            }))
        else:
            events.append(self.event("BudgetReserved", 4, {
                "procurementId": self.procurement.business_id,
                "projectId": self.project.business_id, "amount": 40,
            }, contract=ESCROW))
        return sorted((event for event in events if start <= event["blockNumber"] <= min(end, self.tip)),
                      key=lambda event: (event["blockNumber"], event["transactionIndex"], event["logIndex"]))

    def call(self, contract, function, *args, block_identifier="latest"):
        block = self.tip if block_identifier == "latest" else int(block_identifier)
        self.calls.append((contract, function, block))
        epoch = 2 if self.policy_update and block >= 3 else 1
        current_approver = NEW_APPROVER if epoch == 2 else APPROVER
        deposits = (70 if self.replacement and block >= 4 else 50) if block >= 2 else 0
        reserved = 40 if block >= 4 and not self.replacement and not self.ai_advanced else 0
        if function == "getProject":
            return (
                bytes.fromhex(self.project.business_id[2:]),
                NEW_APPROVER if self.wrong_parties else self.project.foundation_wallet,
                self.project.recipient_wallet, TOKEN, 6, epoch, 1, 0, 1 if block >= 3 else 0, 1,
            )
        if function == "getLedger":
            return (TOKEN, deposits, reserved, 0, 0, 0, 0, 1 if deposits else 0, 0, epoch, 1, False)
        if function == "isApprover":
            return not self.reject_membership and args[1].lower() == current_approver.lower()
        if function == "donorCredit":
            return deposits
        if function == "allowance":
            return 50 if block == 2 else 0
        if function == "getProcurement":
            if block < 3:
                raise ValueError("unknown procurement")
            state = 1 if block == 3 or self.replacement else 2 if self.ai_advanced else 4
            zero = bytes(32)
            return (
                bytes.fromhex(self.procurement.business_id[2:]), bytes.fromhex(self.project.business_id[2:]),
                self.procurement.vendor_wallet, int(self.procurement.budget_cap_atomic),
                bytes.fromhex(_hash(11)[2:]), bytes.fromhex(_hash(12)[2:]),
                bytes.fromhex(_hash(13)[2:]), bytes.fromhex(_hash(14)[2:]),
                bytes.fromhex(_hash(15)[2:]) if state >= 2 else zero,
                reserved, zero, 0, zero, zero, zero, zero, zero, zero, zero, zero, 0, state,
            )
        raise AssertionError(f"unexpected getter {contract}.{function}")

    def get_transaction(self, tx_hash):
        return {
            "to": REGISTRY, "from": self.project.foundation_wallet,
            "input": "update" if tx_hash == _hash(503) else "create",
        }

    def decode_policy(self, data):
        return SimpleNamespace(fn_name="updateApprovalPolicy" if data == "update" else "createProject"), {
            "projectId": bytes.fromhex(self.project.business_id[2:]),
            "approvers": [NEW_APPROVER if data == "update" else APPROVER], "threshold": 1,
        }

    def receipt(self, tx_hash):
        number = 3 if tx_hash == _hash(503) else 1
        return {"status": 1, "blockHash": self.blocks[number], "blockNumber": number}

    def receipt_with_events(self, tx_hash, expected):
        if expected == "Approval":
            event = self.event("Approval", 2, {"owner": DONOR, "spender": ESCROW, "value": 50},
                               contract=TOKEN, tx=tx_hash)
            caller, target, block = DONOR, TOKEN, 2
        else:
            event = next(event for event in self.events_in_range(3, 3)
                         if event["event"] == expected)
            caller, target, block = self.project.foundation_wallet, REGISTRY, 3
        return {
            "transactionHash": tx_hash, "status": 1, "blockNumber": block,
            "blockHash": self.blocks[block], "from": caller, "to": target,
        }, [event]


@pytest.fixture
def projection_fixture(created_procurement, session_factory):
    _token, project_result, procurement_result = created_procurement
    project_id = UUID(project_result["project"]["id"])
    procurement_id = UUID(procurement_result["procurement"]["id"])
    with session_factory() as session, session.begin():
        project = session.get(Project, project_id)
        procurement = session.get(Procurement, procurement_id)
        namespace = session.get(DeploymentInstance, project.namespace_id)
        namespace.schema_version = "a2-chain-1"
        namespace.chain_id = 31337
        namespace.genesis_hash = _hash(0)
        namespace.mode = "verified"
        namespace.verified = True
        namespace.rpc_url = "http://127.0.0.1:18545"
        namespace.manifest_sha256 = "bb" * 32
        namespace.manifest_json = {"chain": {"genesisHash": _hash(0)}}
        gateway = ProjectionGateway(project, procurement)
    return gateway, project_id, procurement_id


def _drain(indexer):
    for _ in range(10):
        if not indexer.once():
            return
    raise AssertionError("event cursor failed to catch up")


def _submitted(session_factory, gateway, action, expected):
    project = gateway.project
    procurement = gateway.procurement
    with session_factory() as session, session.begin():
        operation = Operation(
            namespace_id=project.namespace_id, principal_id=project.foundation_user_id,
            operation_kind=action, idempotency_key=action, payload_hash="ab" * 32, status="submitted",
        )
        session.add(operation)
        session.flush()
        args = ([ESCROW, 50] if action == "donation.approve" else
                [procurement.business_id, _hash(11), _hash(12), _hash(13)])
        caller = DONOR if action == "donation.approve" else project.foundation_wallet
        target = TOKEN if action == "donation.approve" else REGISTRY
        step = OperationStep(operation_id=operation.id, step_index=0, kind=action, status="submitted",
                             detail={"action": action, "caller": caller, "args": args,
                                     "expectedEvent": expected})
        session.add(step)
        session.flush()
        tx = ChainTransaction(
            operation_id=operation.id, step_id=step.id, namespace_id=project.namespace_id,
            caller_address=caller, to_address=target, chain_id=31337, evm_nonce_text="2",
            calldata="0x1234", value_text="0", envelope_hash=_hash(901), status="submitted",
            tx_hash=_hash(202 if action == "donation.approve" else 203),
        )
        session.add(tx)
    return operation.id


def test_external_events_project_ledger_credit_procurement_and_confirmed_policy(
    projection_fixture, settings, session_factory,
):
    gateway, project_id, procurement_id = projection_fixture
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        _drain(indexer)
        assert indexer.once() is False
    finally:
        indexer.close()
    with session_factory() as session:
        ledger = session.scalar(select(LedgerProjection))
        policy = session.scalar(select(PolicyProjection))
        procurement = session.get(Procurement, procurement_id)
        assert (int(ledger.deposits_atomic), int(ledger.reserved_atomic), ledger.block_number) == (50, 40, 4)
        assert session.scalar(select(DonorCreditProjection.credit_atomic)) == Decimal(50)
        assert policy.approver_wallets == [APPROVER.lower()]
        assert (policy.policy_epoch, policy.threshold, policy.tx_hash) == (1, 1, _hash(201))
        assert procurement.chain_status == "reserved"
        assert procurement.po_hash == _hash(11)
        assert procurement.reserved_amount_atomic == Decimal(40)
        assert session.scalar(select(IndexerCursor.next_block)) == 5
        assert session.scalar(select(func.count()).select_from(ChainEvent)) == 5


@pytest.mark.parametrize("replacement", [False, True])
def test_ordinary_once_revokes_orphaned_facts_when_tip_shrinks_or_height_is_replaced(
    projection_fixture, settings, session_factory, replacement,
):
    gateway, project_id, procurement_id = projection_fixture
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        _drain(indexer)
        operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation_id))
            tx = session.scalar(select(ChainTransaction).where(ChainTransaction.operation_id == operation_id))
            operation.status = step.status = tx.status = "confirmed"
            tx.block_hash = gateway.blocks[4]
            tx.receipt_json = {"status": 1, "blockNumber": 4}
            tx.canonical = True
        if replacement:
            gateway.blocks[4] = _hash(1004)
            gateway.replacement = True
        else:
            gateway.tip = 2
        assert indexer.once() is True  # No explicit rebuild and no new height.
        assert indexer.once() is False
    finally:
        indexer.close()
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        procurement = session.get(Procurement, procurement_id)
        ledger = session.scalar(select(LedgerProjection))
        assert (operation.status, operation.error_code) == ("requires_attention", "chain_reorganization")
        assert session.scalar(select(ChainTransaction.canonical)) is False
        assert int(ledger.reserved_atomic) == 0
        assert int(ledger.deposits_atomic) == (70 if replacement else 50)
        assert int(session.scalar(select(DonorCreditProjection.credit_atomic))) == (70 if replacement else 50)
        assert procurement.chain_status == ("po_recorded" if replacement else "off_chain_draft")
        assert procurement.reserved_amount_atomic == (Decimal(0) if replacement else None)
        if not replacement:
            assert procurement.po_hash is procurement.receipt_digest is procurement.chain_tx_hash is None
        assert session.scalar(select(func.count()).select_from(ChainEvent).where(
            ChainEvent.canonical.is_(False))) >= 1


def test_policy_update_uses_canonical_calldata_and_reorg_restores_original_members(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, _procurement_id = projection_fixture
    gateway.policy_update = True
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        _drain(indexer)
        with session_factory() as session:
            policy = session.scalar(select(PolicyProjection))
            assert (policy.policy_epoch, policy.approver_wallets) == (2, [NEW_APPROVER.lower()])
        gateway.tip = 2
        assert indexer.once() is True
    finally:
        indexer.close()
    with session_factory() as session:
        policy = session.scalar(select(PolicyProjection))
        assert (policy.policy_epoch, policy.approver_wallets) == (1, [APPROVER.lower()])


@pytest.mark.parametrize("fault", ["wrong_parties", "reject_membership"])
def test_projection_failure_rolls_back_events_and_cursor_together(
    projection_fixture, settings, session_factory, fault,
):
    gateway, _project_id, _procurement_id = projection_fixture
    setattr(gateway, fault, True)
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        with pytest.raises(ValueError):
            indexer.once()
        with session_factory() as session:
            for model in (ChainEvent, IndexerCursor, LedgerProjection, PolicyProjection):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        setattr(gateway, fault, False)
        assert indexer.once() is True
    finally:
        indexer.close()


def test_two_indexers_serialize_event_projection_without_duplicate_rows(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, _procurement_id = projection_fixture
    indexers = [ChainIndexer(settings.database_url, gateway) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(lambda indexer: indexer.sync_one_block(), indexers)) == [True, True]
    finally:
        for indexer in indexers:
            indexer.close()
    with session_factory() as session:
        assert session.scalar(select(IndexerCursor.next_block)) == 3
        assert session.scalar(select(func.count()).select_from(ChainEvent)) == 2
        assert session.scalar(select(func.count()).select_from(LedgerProjection)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyProjection)) == 1


@pytest.mark.parametrize("action,expected", [
    ("procurement.po", "PurchaseOrderRecorded"), ("donation.approve", "Approval"),
])
def test_delayed_confirmation_uses_receipt_block_after_canonical_state_advances(
    projection_fixture, settings, session_factory, action, expected,
):
    gateway, _project_id, procurement_id = projection_fixture
    gateway.ai_advanced = True
    operation_id = _submitted(session_factory, gateway, action, expected)
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        assert indexer.once() is True
        _drain(indexer)  # A catching-up cursor must not overwrite the newer projection.
    finally:
        indexer.close()
    with session_factory() as session:
        assert session.get(Operation, operation_id).status == "confirmed"
        assert session.get(Procurement, procurement_id).chain_status == "pre_assessed"
    if action == "procurement.po":
        assert ("PoGRegistryV2", "getProcurement", 3) in gateway.calls
    else:
        assert ("MockHKD", "allowance", 2) in gateway.calls


def test_reorg_between_receipt_validation_and_commit_rolls_back_and_recovers_once(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, procurement_id = projection_fixture
    gateway.ai_advanced = True
    operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
    original_canonical = gateway.canonical
    checks = {"receipt": 0}

    def replace_before_commit(number, block_hash):
        if number == 3:
            checks["receipt"] += 1
            if checks["receipt"] == 3:
                gateway.tip = 2  # RPC validation passed; original DB transaction is about to commit.
        return original_canonical(number, block_hash)

    gateway.canonical = replace_before_commit
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        assert indexer.once() is True
        assert indexer.once() is False  # The indexer survives and the rebuilt cursor is current.
    finally:
        indexer.close()
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        tx = session.scalar(select(ChainTransaction))
        assert (operation.status, operation.error_code) == ("requires_attention", "chain_reorganization")
        assert tx.status == "requires_attention" and tx.canonical is False
        assert session.get(Procurement, procurement_id).chain_status == "off_chain_draft"
        assert session.scalar(select(LedgerProjection.reserved_atomic)) == Decimal(0)
        assert session.scalar(select(IndexerCursor.next_block)) == 3
        assert session.scalar(select(func.count()).select_from(ChainEvent).where(
            ChainEvent.block_number > 2, ChainEvent.canonical.is_(True))) == 0


def test_once_revalidates_latest_projection_even_when_receipt_and_cursor_are_older(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, procurement_id = projection_fixture
    gateway.ai_advanced = True
    operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        assert indexer.once() is True  # Receipt block 3; latest projected PRE is block 4.
        with session_factory() as session, session.begin():
            assert session.scalar(select(LedgerProjection.block_number)) == 4
            # Reconstruct an older scanner checkpoint to prove the independently
            # observed projection is checked, too (normal confirmation now
            # atomically stores the complete history and advances this cursor).
            cursor = session.scalar(select(IndexerCursor))
            cursor.next_block = 4
            cursor.last_block_hash = gateway.blocks[3]
        gateway.tip = 3  # Only the projection block is orphaned, not the PO receipt.
        assert indexer.once() is True
    finally:
        indexer.close()
    with session_factory() as session:
        assert session.get(Operation, operation_id).status == "confirmed"
        assert session.get(Procurement, procurement_id).chain_status == "po_recorded"
        assert session.get(Procurement, procurement_id).pre_assessment_id == _hash(0)
        assert session.scalar(select(LedgerProjection.block_number)) == 3


def test_rpc_uncertainty_before_confirmation_commit_rolls_back_without_attention(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, _procurement_id = projection_fixture
    gateway.ai_advanced = True
    operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
    original_canonical = gateway.canonical
    checks = {"receipt": 0}

    def timeout_before_commit(number, block_hash):
        if number == 3:
            checks["receipt"] += 1
            if checks["receipt"] == 3:
                raise ChainUnavailable("isolated final canonical lookup timeout")
        return original_canonical(number, block_hash)

    gateway.canonical = timeout_before_commit
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        with pytest.raises(ChainUnavailable):
            indexer.once()
        with session_factory() as session:
            assert session.get(Operation, operation_id).status == "submitted"
            tx = session.scalar(select(ChainTransaction))
            assert tx.status == "submitted" and tx.receipt_json is None
            for model in (ChainEvent, IndexerCursor, LedgerProjection, PolicyProjection):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        gateway.canonical = original_canonical
        assert indexer.once() is True
    finally:
        indexer.close()
    with session_factory() as session:
        assert session.get(Operation, operation_id).status == "confirmed"
        assert session.scalar(select(IndexerCursor.next_block)) == 5
        assert session.scalar(select(func.count()).select_from(ChainEvent)) == 5


def test_historical_block_timeout_preserves_confirmed_projection_until_rpc_recovers(
    projection_fixture, settings, session_factory,
):
    gateway, _project_id, procurement_id = projection_fixture
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        _drain(indexer)
        operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation_id))
            tx = session.scalar(select(ChainTransaction).where(ChainTransaction.operation_id == operation_id))
            operation.status = step.status = tx.status = "confirmed"
            tx.block_hash = gateway.blocks[3]
            tx.receipt_json = {"status": 1, "blockNumber": 3}
            tx.canonical = True
        # All ordinary RPC/getter behavior remains healthy. Only an old block
        # identity lookup fails, which is uncertainty rather than reorg evidence.
        def get_block(number):
            if number == 1:
                raise TimeoutError("isolated historical RPC timeout")
            return {"hash": gateway.blocks[number], "number": number}

        gateway.get_block = get_block
        gateway.canonical = lambda number, digest: LocalChainGateway.canonical(gateway, number, digest)
        with pytest.raises(ChainUnavailable, match="temporarily unavailable"):
            indexer.once()
        with session_factory() as session:
            assert session.get(Operation, operation_id).status == "confirmed"
            assert session.scalar(select(ChainTransaction.canonical)) is True
            assert session.get(Procurement, procurement_id).chain_status == "reserved"
            assert session.scalar(select(LedgerProjection.reserved_atomic)) == Decimal(40)
            assert session.scalar(select(func.count()).select_from(ChainEvent).where(
                ChainEvent.canonical.is_(False))) == 0
            assert session.scalar(select(IndexerCursor.next_block)) == 5
        gateway.get_block = lambda number: {"hash": gateway.blocks[number], "number": number}
        assert indexer.once() is False
    finally:
        indexer.close()


def test_canonical_lookup_only_treats_explicit_missing_or_different_block_as_reorg():
    gateway = SimpleNamespace(w3=SimpleNamespace(eth=SimpleNamespace()))
    gateway.w3.eth.get_block = lambda number: {"hash": _hash(1), "number": number}
    assert LocalChainGateway.canonical(gateway, 1, _hash(1)) is True
    assert LocalChainGateway.canonical(gateway, 1, _hash(2)) is False

    def missing(_number):
        raise BlockNotFound("explicit absent block")

    gateway.w3.eth.get_block = missing
    assert LocalChainGateway.canonical(gateway, 2, _hash(2)) is False
    gateway.w3.eth.get_block = lambda _number: {"hash": "bad hash", "number": 1}
    with pytest.raises(ChainUnavailable, match="invalid identity"):
        LocalChainGateway.canonical(gateway, 1, _hash(1))


@pytest.mark.parametrize("error", [ChainUnavailable, TimeoutError, RequestsTimeout])
def test_historical_getter_timeout_keeps_submission_recoverable_without_resend(
    projection_fixture, settings, session_factory, error,
):
    gateway, _project_id, procurement_id = projection_fixture
    gateway.ai_advanced = True
    operation_id = _submitted(session_factory, gateway, "procurement.po", "PurchaseOrderRecorded")
    original_call = gateway.call
    failures = {"remaining": 1}

    def timeout_once(contract, function, *args, block_identifier="latest"):
        if function == "getProcurement" and block_identifier == 3 and failures["remaining"]:
            failures["remaining"] -= 1
            raise error("single historical getter timeout")
        return original_call(contract, function, *args, block_identifier=block_identifier)

    gateway.call = timeout_once
    indexer = ChainIndexer(settings.database_url, gateway)
    try:
        with pytest.raises(ChainUnavailable):
            indexer.once()
        with session_factory() as session:
            operation = session.get(Operation, operation_id)
            step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation_id))
            tx = session.scalar(select(ChainTransaction))
            assert (operation.status, step.status, tx.status) == ("submitted", "submitted", "submitted")
            assert tx.receipt_json is None and operation.error_code is None
            assert session.scalar(select(func.count()).select_from(ChainTransaction)) == 1
        assert indexer.once() is True
    finally:
        indexer.close()
    with session_factory() as session:
        assert session.get(Operation, operation_id).status == "confirmed"
        assert session.get(Procurement, procurement_id).chain_status == "pre_assessed"
        assert session.scalar(select(func.count()).select_from(ChainTransaction)) == 1
