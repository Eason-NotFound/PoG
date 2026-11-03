"""Real PostgreSQL accounting with explicit fake chain facts, never live proof.

These tests validate balances, atomic recovery, claims and policy enforcement.
The integrated HTTP/Anvil test separately establishes actual chain execution.
"""
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from pog_api.chain import PreparedEnvelope
from pog_api.errors import APIError
from pog_api.idempotency import begin_operation
from pog_api.models import ChainEvent, ChainTransaction, DeploymentInstance, Operation, OperationStep, Procurement, Project
from pog_api.payment_domain import (ZERO_ADDRESS, _journal, account_dto, assert_resource_canonical,
    freeze_namespace, provision_simulation, reconcile_donor_outflows, reserve_funded_donation, reserve_funding)
from pog_api.payment_domain import validate_pending_chain, assert_project_backing, chain_proof
from pog_api.payment_models import CanonicalOutflow, FundedClaim, HKDJournal, PaymentEvidence, PaymentResource, SimHKDAccount
from pog_api.payment_worker import PaymentWorker
from pog_api.security import Principal

from _route_helpers import route_env


@pytest.fixture
def payment_env(route_env, monkeypatch):
    env = route_env
    gate = env.gate
    original_call = gate.call
    gate.balances = {gate.roles["donorA"].lower(): 1_000_000_000}
    gate.historical_balances = {}
    gate.fake_events = []
    gate.fake_receipts = {}
    gate.fake_blocks = {}
    gate.w3 = SimpleNamespace(eth=SimpleNamespace(block_number=30))
    def call(contract, function, *args, **kwargs):
        if contract == "MockHKD" and function == "balanceOf":
            wallet = str(args[0]).lower()
            block = kwargs.get("block_identifier", "latest")
            return gate.historical_balances.get((wallet, block), gate.balances.get(wallet, 0))
        return original_call(contract, function, *args, **kwargs)
    def prepare(action, caller, args, event):
        target = gate.contract_address("MockHKD" if action.startswith("payment.") else "ProcurementEscrowV2")
        encoded = json.dumps([action, args], sort_keys=True, separators=(",", ":")).encode().hex()
        return PreparedEnvelope(caller=caller, to=target, chain_id=31337, nonce=1,
                                data="0x" + encoded, value=0, action=action, expected_event=event)
    monkeypatch.setattr(gate, "call", call)
    monkeypatch.setattr(gate, "prepare", prepare, raising=False)
    monkeypatch.setattr(gate, "canonical", lambda block, digest: gate.fake_blocks.get(block) == digest, raising=False)
    monkeypatch.setattr(gate, "events_in_range", lambda start, end: [event for event in gate.fake_events if start <= event["blockNumber"] <= end], raising=False)
    monkeypatch.setattr(gate, "receipt_with_events", lambda tx_hash, event: (gate.fake_receipts.get(tx_hash),
                        [row for row in gate.fake_events if row["transactionHash"] == tx_hash and row["event"] == event]), raising=False)
    return env


def principal(env, name="donor"):
    user = env.users[name]
    return Principal(user_id=UUID(user["id"]), username=user["username"], role=user["role"],
                     wallet_address=user["wallet"], session_id=uuid4())


def operation(session, env, actor, kind="payment.funding", key=None):
    value, replay = begin_operation(session, namespace_id=env.namespace_id, principal_id=actor.user_id,
        operation_kind=kind, idempotency_key=key or str(uuid4()), validated_payload={"fixture": True})
    assert not replay
    value.status = "queued"
    return value


def provision(env):
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        return provision_simulation(session, ns, env.gate)


def funding(env, *, cents="10000", project_index=0):
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        actor = principal(env)
        op = operation(session, env, actor)
        resource = reserve_funding(session, ns, actor, session.get(Project, env.projects[project_index].id), op, cents, env.gate)
        return op.id, resource.id


def fact(session, env, op, action, args, caller, event_name, event_args, *, block=20, index=0):
    gate = env.gate
    target = gate.contract_address("MockHKD" if action.startswith("payment.") else "ProcurementEscrowV2")
    block_hash = "0x" + f"{block:064x}"
    tx_hash = "0x" + uuid4().hex + uuid4().hex
    gate.fake_blocks[block] = block_hash
    envelope = gate.prepare(action, caller, args, event_name)
    step = OperationStep(operation_id=op.id, step_index=index, kind=action, executor="chain", status="confirmed",
        detail={"action": action, "args": args, "caller": caller, "expectedEvent": event_name})
    session.add(step)
    session.flush()
    receipt = {"transactionHash": tx_hash, "blockHash": block_hash, "blockNumber": block,
               "from": caller, "to": target, "status": 1}
    tx = ChainTransaction(operation_id=op.id, step_id=step.id, namespace_id=env.namespace_id,
        caller_address=caller, to_address=target, chain_id=31337, evm_nonce_text=str(block),
        calldata=envelope.data, value_text="0", status="confirmed", tx_hash=tx_hash,
        receipt_json=receipt, block_hash=block_hash, canonical=True)
    session.add(tx)
    session.add(ChainEvent(namespace_id=env.namespace_id, contract_address=target, tx_hash=tx_hash,
        log_index=0, block_number=block, transaction_index=0, block_hash=block_hash,
        event_name=event_name, canonical=True, payload=event_args))
    gate.fake_receipts[tx_hash] = receipt
    gate.fake_events.append({"address": target, "event": event_name, "args": event_args,
        "transactionHash": tx_hash, "blockHash": block_hash, "blockNumber": block, "logIndex": 0, "transactionIndex": 0})
    session.flush()
    return tx


def reconciled_funding(env):
    provision(env)
    op_id, resource_id = funding(env)
    wallet = principal(env).wallet_address
    amount = 100_000_000
    gate = env.gate
    gate.balances[wallet.lower()] = 1_000_000_000 + amount
    gate.historical_balances[(wallet.lower(), 19)] = 1_000_000_000
    gate.historical_balances[(wallet.lower(), 20)] = 1_000_000_000 + amount
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        op = session.get(Operation, op_id)
        resource = session.get(PaymentResource, resource_id)
        fact(session, env, op, "payment.mint", [wallet, str(amount)], gate.roles["relayer"],
             "Transfer", {"from": ZERO_ADDRESS, "to": wallet, "value": amount})
        worker = object.__new__(PaymentWorker)
        worker.gateway = gate
        worker._funding(session, ns, op, resource)
    return op_id, resource_id


def test_explicit_provisioning_is_once_not_login_topup(payment_env):
    env = payment_env
    provision(env)
    provision(env)
    with env.session_factory() as session:
        ns = session.get(DeploymentInstance, env.namespace_id)
        dto = account_dto(session, ns, principal(env))
        assert dto["availableHkdCents"] == "100000" and dto["heldHkdCents"] == "0"
        assert session.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind == "opening")) == 1
        accounts = session.scalars(select(SimHKDAccount).where(SimHKDAccount.namespace_id == ns.id)).all()
        assert sum(int(row.available_cents) + int(row.held_cents) for row in accounts) == 0
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            provision_simulation(session, session.get(DeploymentInstance, env.namespace_id), env.gate, 100001)
    assert exc.value.code == "simulation_fixture_conflict"


def test_unprovisioned_fails_closed_faucet_creates_no_credit(payment_env):
    env = payment_env
    with pytest.raises(APIError) as exc:
        funding(env)
    assert exc.value.code == "simulation_account_unprovisioned"
    with env.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentResource)) == 0
        assert session.scalar(select(func.count()).select_from(FundedClaim)) == 0


def test_funding_hold_is_atomic_and_insufficient_attempt_does_not_debit(payment_env):
    env = payment_env
    provision(env)
    _, resource_id = funding(env)
    with pytest.raises(APIError) as exc:
        funding(env, cents="100000")
    assert exc.value.code == "sim_hkd_insufficient"
    with env.session_factory() as session:
        ns = session.get(DeploymentInstance, env.namespace_id)
        dto = account_dto(session, ns, principal(env))
        assert dto["availableHkdCents"] == "90000" and dto["heldHkdCents"] == "10000"
        assert session.get(PaymentResource, resource_id).status == "held"
        assert session.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind == "funding")) == 0


@pytest.mark.parametrize("name", ["foundation", "recipient", "admin", "ai"])
def test_non_donor_cannot_hold_funding(payment_env, name):
    env = payment_env
    provision(env)
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            actor = principal(env, name)
            reserve_funding(session, session.get(DeploymentInstance, env.namespace_id), actor,
                session.get(Project, env.projects[0].id), operation(session, env, actor), "100", env.gate)
    assert exc.value.status_code == 403


def test_mint_without_finalizer_still_held_unknown_never_recycles(payment_env):
    env = payment_env
    provision(env)
    op_id, resource_id = funding(env)
    with env.session_factory() as session, session.begin():
        op = session.get(Operation, op_id)
        op.status = "requires_attention"
        op.error_code = "chain_submission_unknown"
    with env.session_factory() as session:
        ns = session.get(DeploymentInstance, env.namespace_id)
        assert account_dto(session, ns, principal(env))["heldHkdCents"] == "10000"
        assert session.get(PaymentResource, resource_id).status == "held"
        assert session.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind == "funding")) == 0


def test_canonical_fake_mint_finalizes_one_balanced_journal_and_credit(payment_env):
    env = payment_env
    op_id, resource_id = reconciled_funding(env)
    with env.session_factory() as session:
        ns = session.get(DeploymentInstance, env.namespace_id)
        resource = session.get(PaymentResource, resource_id)
        assert resource.status == "reconciled" and len(resource.journal_ids) == 1
        assert_resource_canonical(session, ns, resource, env.gate)
        dto = account_dto(session, ns, principal(env))
        assert (dto["availableHkdCents"], dto["heldHkdCents"]) == ("90000", "0")
        clearing = session.scalar(select(SimHKDAccount).where(SimHKDAccount.party_key == "clearing"))
        assert int(clearing.available_cents) == 10000
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            _journal(session, session.get(DeploymentInstance, env.namespace_id), debit_key=principal(env).wallet_address,
                     credit_key="clearing", cents=10000, effect="funding", operation=session.get(Operation, op_id), from_hold=True)
    assert exc.value.code == "payment_effect_conflict"


def test_funded_credit_reserved_once_and_wrong_project_rejected(payment_env):
    env = payment_env
    funding_id, resource_id = reconciled_funding(env)
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        actor = principal(env)
        claim = reserve_funded_donation(session, ns, actor, session.get(Project, env.projects[0].id),
                                       operation(session, env, actor, "funded.donation"), funding_id, "100000000", env.gate)
        assert claim.status == "reserved"
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            actor = principal(env)
            reserve_funded_donation(session, session.get(DeploymentInstance, env.namespace_id), actor,
                session.get(Project, env.projects[0].id), operation(session, env, actor, "funded.donation"), funding_id, "1", env.gate)
    assert exc.value.code == "funded_credit_insufficient"
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            actor = principal(env)
            reserve_funded_donation(session, session.get(DeploymentInstance, env.namespace_id), actor,
                session.get(Project, env.projects[1].id), operation(session, env, actor, "funded.donation"), funding_id, "1", env.gate)
    assert exc.value.code == "funding_source_forbidden"


def test_foreign_donor_outflow_freezes_credit_not_infer_faucet_origin(payment_env):
    env = payment_env
    _, resource_id = reconciled_funding(env)
    gate = env.gate
    digest = "0x" + "77" * 32
    gate.fake_blocks[21] = digest
    gate.fake_events.append({"address": gate.contract_address("MockHKD"), "event": "Transfer",
        "args": {"from": principal(env).wallet_address, "to": gate.roles["vendor"], "value": 1},
        "transactionHash": "0x" + "88" * 32, "blockHash": digest, "blockNumber": 21, "logIndex": 0, "transactionIndex": 0})
    with env.session_factory() as session, session.begin():
        assert not reconcile_donor_outflows(session, session.get(DeploymentInstance, env.namespace_id), principal(env).wallet_address, gate)
    with env.session_factory() as session:
        assert session.get(PaymentResource, resource_id).status == "requires_attention"
        assert session.scalar(select(func.count()).select_from(CanonicalOutflow).where(CanonicalOutflow.matched.is_(False))) == 1
        assert session.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind == "funding")) == 1


def test_reorg_reset_freeze_preserves_journals_and_never_refills(payment_env):
    env = payment_env
    _, resource_id = reconciled_funding(env)
    with env.session_factory() as session, session.begin():
        freeze_namespace(session, env.namespace_id, "deployment_instance_changed", invalidated=True)
    with env.session_factory() as session:
        assert session.get(PaymentResource, resource_id).status == "invalidated_instance"
        assert session.scalar(select(func.count()).select_from(HKDJournal).where(HKDJournal.effect_kind == "funding")) == 1
        dto = account_dto(session, session.get(DeploymentInstance, env.namespace_id), principal(env))
        assert dto["availableHkdCents"] == "90000" and dto["heldHkdCents"] == "0"


def test_database_rejects_immutable_resource_scope_even_direct_sql(payment_env):
    env = payment_env
    provision(env)
    op_id, resource_id = funding(env)
    with pytest.raises(DBAPIError) as exc:
        with env.session_factory() as session, session.begin():
            session.execute(text("UPDATE payment_resources SET amount_atomic=amount_atomic+1 WHERE id=:id"), {"id": resource_id})
    assert "immutable" in str(exc.value).lower()


def test_database_journal_immutable_even_direct_sql(payment_env):
    env = payment_env
    provision(env)
    with pytest.raises(DBAPIError) as exc:
        with env.session_factory() as session, session.begin():
            session.execute(text("UPDATE sim_hkd_journals SET cents=cents+1"))
    assert "immutable" in str(exc.value).lower()


def test_database_payment_evidence_immutable_even_direct_sql(payment_env):
    env = payment_env
    provision(env)
    source_id, _ = funding(env)
    with env.session_factory() as session, session.begin():
        actor = principal(env, "foundation")
        op = operation(session, env, actor, "payment.evidence.fixture")
        resource = PaymentResource(namespace_id=env.namespace_id, operation_id=op.id,
            source_operation_id=source_id, actor_user_id=actor.user_id, project_id=env.projects[0].id,
            procurement_id=env.procurements[0].id, kind="redemption", status="queued", hkd_cents=7200,
            amount_atomic=72000000, allocated_atomic=0, actor_wallet=actor.wallet_address,
            counterparty_wallet=env.gate.roles["vendor"], token_address=env.gate.contract_address("MockHKD"),
            source_material={"guardFixtureOnly": True}, journal_ids=[])
        session.add(resource)
        session.flush()
        # Deliberately inert metadata fixture, never evidence accepted by a route.
        session.add(PaymentEvidence(namespace_id=env.namespace_id, resource_id=resource.id,
            procurement_id=env.procurements[0].id, kind="conversion", schema_version="guard-fixture-only",
            canonical_bytes=b"{}", sha256_hex="11" * 32, keccak256_hex="22" * 32))
    with pytest.raises(DBAPIError) as exc:
        with env.session_factory() as session, session.begin():
            session.execute(text("UPDATE payment_evidence SET schema_version='replacement'"))
    assert "immutable" in str(exc.value).lower()


def test_balanced_accounting_caches_reconstruct_from_journal_for_simulated_100_72(payment_env):
    env = payment_env
    reconciled_funding(env)
    actor = principal(env, "foundation")
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        redemption = operation(session, env, actor, "payment.redemption.fixture")
        _journal(session, ns, debit_key="clearing", credit_key=actor.wallet_address,
                 cents=7200, effect="redemption", operation=redemption)
        payment = operation(session, env, actor, "payment.supplier.fixture")
        _journal(session, ns, debit_key=actor.wallet_address, credit_key=env.gate.roles["vendor"],
                 cents=7200, effect="supplier_payment", operation=payment)
    with env.session_factory() as session:
        accounts = session.scalars(select(SimHKDAccount).where(SimHKDAccount.namespace_id == env.namespace_id)).all()
        journals = session.scalars(select(HKDJournal).where(HKDJournal.namespace_id == env.namespace_id)).all()
        rebuilt = {account.id: 0 for account in accounts}
        for journal in journals:
            rebuilt[journal.debit_account_id] -= int(journal.cents)
            rebuilt[journal.credit_account_id] += int(journal.cents)
        for account in accounts:
            assert rebuilt[account.id] == int(account.available_cents) + int(account.held_cents)
        by_key = {account.party_key: account for account in accounts}
        assert int(by_key["clearing"].available_cents) == 2800
        assert int(by_key[actor.wallet_address.lower()].available_cents) == 0
        assert int(by_key[env.gate.roles["vendor"].lower()].available_cents) == 7200


def test_worker_does_not_consume_payment_step_before_chain_confirmed(payment_env):
    env = payment_env
    provision(env)
    op_id, resource_id = funding(env)
    with env.session_factory() as session, session.begin():
        session.add(OperationStep(operation_id=op_id, step_index=0, kind="payment.mint", executor="chain",
            status="requires_attention", detail={"action": "payment.mint"}))
        session.add(OperationStep(operation_id=op_id, step_index=1, kind="payment.finalize_funding", executor="payment",
            status="queued", detail={"action": "payment.finalize_funding", "paymentResourceUuid": str(resource_id)}))
    worker = PaymentWorker(env.settings.database_url, env.gate)
    try:
        assert worker.once() is False
    finally:
        worker.close()
    with env.session_factory() as session:
        assert session.get(PaymentResource, resource_id).status == "held"


@pytest.mark.parametrize("tamper", ["caller", "amount", "resource", "frozen", "unknown"])
def test_pending_mint_guard_rejects_binding_or_hold_attention_before_broadcast(payment_env, tamper):
    env = payment_env
    provision(env)
    op_id, resource_id = funding(env)
    with env.session_factory() as session, session.begin():
        op = session.get(Operation, op_id)
        resource = session.get(PaymentResource, resource_id)
        step = OperationStep(operation_id=op_id, step_index=0, kind="payment.mint", executor="chain", status="queued",
            detail={"action": "payment.mint", "paymentResourceUuid": str(resource_id), "projectUuid": str(resource.project_id),
                    "args": [resource.actor_wallet, str(int(resource.amount_atomic))], "caller": env.gate.roles["relayer"], "expectedEvent": "Transfer"})
        session.add(step)
        session.flush()
        if tamper == "caller":
            step.detail = {**step.detail, "caller": env.gate.roles["foundation"]}
        elif tamper == "amount":
            step.detail = {**step.detail, "args": [resource.actor_wallet, "1"]}
        elif tamper == "resource":
            step.detail = {**step.detail, "paymentResourceUuid": str(uuid4())}
        elif tamper == "frozen":
            resource.status = "requires_attention"
        else:
            op.status = "requires_attention"
        with pytest.raises(APIError) as exc:
            validate_pending_chain(session, session.get(DeploymentInstance, env.namespace_id), step, op, env.gate)
        assert exc.value.code == "payment_chain_preflight_invalid"
        assert env.gate.broadcast_calls == 0


def test_orphan_funding_cannot_borrow_faucet_to_approve_or_deposit(payment_env):
    env = payment_env
    source, resource_id = reconciled_funding(env)
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        actor = principal(env)
        op = operation(session, env, actor, "payment.funded_donation")
        project = session.get(Project, env.projects[0].id)
        claim = reserve_funded_donation(session, ns, actor, project, op, source, "100000000", env.gate)
        step = OperationStep(operation_id=op.id, step_index=0, kind="donation.approve", executor="chain", status="queued",
            detail={"action": "donation.approve", "projectUuid": str(project.id), "fundedClaimUuid": str(claim.id),
                    "caller": actor.wallet_address, "args": [env.gate.contract_address("ProcurementEscrowV2"), "100000000"], "expectedEvent": "Approval"})
        session.add(step)
        session.flush()
        # Faucet remains abundant, but the exact original mint becomes orphaned.
        env.gate.fake_blocks[20] = "0x" + "ff" * 32
        with pytest.raises(APIError) as exc:
            validate_pending_chain(session, ns, step, op, env.gate)
        assert exc.value.code == "payment_chain_reorganization"
        assert env.gate.broadcast_calls == 0


def test_namespace_freeze_blocks_pending_steps_preserves_unknown_envelope(payment_env):
    env = payment_env
    provision(env)
    op_id, resource_id = funding(env)
    with env.session_factory() as session, session.begin():
        step = OperationStep(operation_id=op_id, step_index=0, kind="payment.mint", executor="chain", status="queued",
                             detail={"action": "payment.mint"})
        session.add(step)
        session.flush()
        tx = ChainTransaction(operation_id=op_id, step_id=step.id, namespace_id=env.namespace_id,
            caller_address=env.gate.roles["relayer"], to_address=env.gate.contract_address("MockHKD"),
            chain_id=31337, evm_nonce_text="99", calldata="0x1234", value_text="0", status="requires_attention",
            envelope_hash="0x" + "22" * 32, canonical=False)
        session.add(tx)
        session.flush()
        tx_id, step_id = tx.id, step.id
        freeze_namespace(session, env.namespace_id, "payment_chain_reorganization")
    with env.session_factory() as session:
        assert session.get(Operation, op_id).status == "requires_attention"
        assert session.get(OperationStep, step_id).status == "requires_attention"
        tx = session.get(ChainTransaction, tx_id)
        assert tx.status == "requires_attention" and tx.evm_nonce_text == "99" and tx.calldata == "0x1234"
        assert account_dto(session, session.get(DeploymentInstance, env.namespace_id), principal(env))["heldHkdCents"] == "10000"


def consumed_project_backing(env):
    source_id, resource_id = reconciled_funding(env)
    actor = principal(env)
    amount = 100_000_000
    old_call = env.gate.call
    env.gate.call = lambda contract, function, *args, **kwargs: amount if function == "donorCredit" else old_call(contract, function, *args, **kwargs)
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        project = session.get(Project, env.projects[0].id)
        op = operation(session, env, actor, "payment.funded_donation")
        claim = reserve_funded_donation(session, ns, actor, project, op, source_id, str(amount), env.gate)
        tx = fact(session, env, op, "donation.deposit", [project.business_id, str(amount)], actor.wallet_address,
                  "Donated", {"projectId": project.business_id, "donor": actor.wallet_address, "amount": amount,
                              "cumulativeCredit": amount}, block=21)
        env.gate.fake_events.append({"address": env.gate.contract_address("MockHKD"), "event": "Transfer",
            "args": {"from": actor.wallet_address, "to": env.gate.contract_address("ProcurementEscrowV2"), "value": amount},
            "transactionHash": tx.tx_hash, "blockHash": tx.block_hash, "blockNumber": 21, "logIndex": 1, "transactionIndex": 0})
        env.gate.balances[actor.wallet_address.lower()] = 1_000_000_000
        worker = object.__new__(PaymentWorker)
        worker.gateway = env.gate
        worker._donation(session, ns, op, claim)
    return source_id, resource_id


def reserve_quota_fixture(session, env, ns, project, *, status="queued", second_procurement=False):
    # A local accounting-only redemption claim, not a canonical payment proof.
    actor = principal(env, "foundation")
    op = operation(session, env, actor, "payment.redemption")
    source = session.scalar(select(PaymentResource).where(PaymentResource.kind == "funding"))
    procurement_id = env.procurements[0].id
    if second_procurement:
        proc = Procurement(namespace_id=ns.id, project_id=project.id, business_id="0x" + uuid4().hex + uuid4().hex,
            title="Second quota fixture", foundation_user_id=actor.user_id, vendor_wallet=env.gate.roles["vendor"],
            budget_cap_atomic=100000000, chain_status="created")
        session.add(proc)
        session.flush()
        procurement_id = proc.id
    resource = PaymentResource(namespace_id=ns.id, operation_id=op.id, source_operation_id=source.operation_id,
        actor_user_id=actor.user_id, project_id=project.id, procurement_id=procurement_id,
        kind="redemption", status=status, hkd_cents=7200, amount_atomic=72000000, allocated_atomic=0,
        actor_wallet=actor.wallet_address, counterparty_wallet=env.gate.roles["vendor"],
        token_address=env.gate.contract_address("MockHKD"), source_material={"quotaFixtureOnly": True}, journal_ids=[])
    session.add(resource)
    session.flush()
    return resource


def test_other_projects_hkd_clearing_cannot_back_faucet_only_project(payment_env):
    env = payment_env
    consumed_project_backing(env)
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            assert_project_backing(session, session.get(DeploymentInstance, env.namespace_id),
                session.get(Project, env.projects[1].id), env.gate, claim_amount=72000000)
    assert exc.value.code == "payment_project_backing_insufficient"


@pytest.mark.parametrize("status", ["queued", "chain_submitted", "requires_attention", "failed"])
def test_pending_unknown_attention_redemption_never_recycles_project_quota(payment_env, status):
    env = payment_env
    consumed_project_backing(env)
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        project = session.get(Project, env.projects[0].id)
        assert_project_backing(session, ns, project, env.gate, claim_amount=72000000)
        resource = reserve_quota_fixture(session, env, ns, project, status=status)
        assert_project_backing(session, ns, project, env.gate, claim_amount=72000000, exclude_resource_id=resource.id)
    with pytest.raises(APIError) as exc:
        with env.session_factory() as session, session.begin():
            assert_project_backing(session, session.get(DeploymentInstance, env.namespace_id),
                session.get(Project, env.projects[0].id), env.gate, claim_amount=72000000)
    assert exc.value.code == "payment_project_backing_insufficient"


def test_two_procurements_concurrently_claim_only_one_project_redemption_quota(payment_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    env = payment_env
    consumed_project_backing(env)
    barrier = Barrier(2)
    def attempt(index):
        try:
            with env.session_factory() as session, session.begin():
                ns = session.get(DeploymentInstance, env.namespace_id)
                project = session.get(Project, env.projects[0].id)
                barrier.wait(timeout=10)
                assert_project_backing(session, ns, project, env.gate, claim_amount=72000000)
                reserve_quota_fixture(session, env, ns, project, second_procurement=True)
                return "queued"
        except APIError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (0, 1)))
    assert sorted(results) == ["payment_project_backing_insufficient", "queued"]
    with env.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentResource).where(PaymentResource.kind == "redemption")) == 1
