"""Real owned PostgreSQL, fake connection-free chain; never a live Mint proof."""
from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from _route_helpers import route_env
from test_payment_domain_postgres import payment_env, principal, provision
from pog_api.chain import ChainUnavailable
from pog_api.errors import APIError
from pog_api.idempotency import begin_operation
from pog_api.models import (AuditLog, ChainTransaction, DeploymentInstance, Operation,
                            OperationStep, Project, User, WalletAuthorization)
from pog_api.payment_domain import reserve_funding
from pog_api.payment_models import FundedClaim, HKDJournal, PaymentResource, SimHKDAccount
from pog_api.payment_recovery import FAILURE_CODE, RECOVERY_ACTION, retry_funding_preflight
from pog_api.worker import ChainWorker


@pytest.fixture
def failed_funding(payment_env):
    env = payment_env
    provision(env)
    actor = principal(env)
    with env.session_factory() as session, session.begin():
        ns = session.get(DeploymentInstance, env.namespace_id)
        project = session.get(Project, env.projects[0].id)
        op, _ = begin_operation(session, namespace_id=ns.id, principal_id=actor.user_id,
            operation_kind="payment.funding", idempotency_key=str(uuid4()),
            validated_payload={"confirm": True, "projectId": str(project.id), "hkdCents": "10000"})
        resource = reserve_funding(session, ns, actor, project, op, "10000", env.gate)
        common = {"projectUuid": str(project.id), "paymentResourceUuid": str(resource.id)}
        mint = OperationStep(operation_id=op.id, step_index=0, executor="chain", kind="payment.mint", status="queued",
            detail={**common, "action": "payment.mint", "caller": env.gate.roles["relayer"],
                    "args": [resource.actor_wallet, str(int(resource.amount_atomic))], "expectedEvent": "Transfer"})
        finalizer = OperationStep(operation_id=op.id, step_index=1, executor="payment",
            kind="payment.finalize_funding", status="queued", detail={**common, "action": "payment.finalize_funding"})
        session.add_all([mint, finalizer])
        session.flush()
        ChainWorker._failed_step(session, mint, op, code=FAILURE_CODE, detail="fake ABI preparation rejection; no send")
        env.recovery_op_id, env.recovery_resource_id, env.recovery_step_id = op.id, resource.id, mint.id
    return env


def rows(session, env):
    return (session.get(DeploymentInstance, env.namespace_id), session.get(Operation, env.recovery_op_id),
            session.get(PaymentResource, env.recovery_resource_id),
            session.scalars(select(OperationStep).where(OperationStep.operation_id == env.recovery_op_id)
                .order_by(OperationStep.step_index)).all())


def snapshot(session, env):
    _, op, resource, steps = rows(session, env)
    account = session.scalar(select(SimHKDAccount).where(SimHKDAccount.owner_user_id == op.principal_id,
                                                        SimHKDAccount.namespace_id == env.namespace_id))
    return {"operation": (op.status, op.error_code, op.error_status, op.error_detail, op.payload_hash, op.idempotency_key),
        "steps": [(step.id, step.status, step.executor, step.kind, deepcopy(step.detail)) for step in steps],
        "resource": (resource.status, resource.error_code, int(resource.allocated_atomic),
                     deepcopy(resource.source_material), deepcopy(resource.chain_proof), deepcopy(resource.journal_ids)),
        "account": (int(account.available_cents), int(account.held_cents)),
        "journals": session.scalar(select(func.count()).select_from(HKDJournal)),
        "transactions": session.scalar(select(func.count()).select_from(ChainTransaction)),
        "claims": session.scalar(select(func.count()).select_from(FundedClaim)),
        "audit": session.scalar(select(func.count()).select_from(AuditLog))}


def reject_unchanged(env, expected=None):
    with env.session_factory() as session, session.begin():
        before = snapshot(session, env)
        with pytest.raises(APIError) as exc:
            retry_funding_preflight(session, session.get(DeploymentInstance, env.namespace_id), env.recovery_op_id, env.gate)
        if expected:
            assert exc.value.code == expected
        session.expire_all()
        assert snapshot(session, env) == before
    assert env.gate.broadcast_calls == 0


def test_exact_preparation_failure_requeues_once_without_new_hold_or_envelope(failed_funding, monkeypatch):
    env = failed_funding
    original_prepare = env.gate.prepare
    calls = []
    def prepare(*args):
        calls.append(deepcopy(args))
        return original_prepare(*args)
    monkeypatch.setattr(env.gate, "prepare", prepare)
    with env.session_factory() as session, session.begin():
        before = snapshot(session, env)
        ns, op, resource, steps = rows(session, env)
        result = retry_funding_preflight(session, ns, op.id, env.gate)
        assert result == {"operationId": str(op.id), "paymentResourceId": str(resource.id), "status": "queued", "replayed": False}
        after = snapshot(session, env)
        assert after["operation"][:4] == ("queued", None, None, None)
        assert after["operation"][4:] == before["operation"][4:]
        assert after["steps"][0][1] == "queued" and after["steps"][1] == before["steps"][1]
        assert after["steps"][0][2:] == before["steps"][0][2:]
        for key in ("resource", "account", "journals", "transactions", "claims"):
            assert after[key] == before[key]
        assert after["audit"] == before["audit"] + 1
        recovery = session.scalar(select(AuditLog).where(AuditLog.action == RECOVERY_ACTION))
        assert recovery.metadata_json["heldPreserved"] is True and recovery.metadata_json["zeroAttempt"] is True
        assert recovery.metadata_json["originalFailureCode"] == FAILURE_CODE
        assert session.get(AuditLog, UUID(recovery.metadata_json["originalFailureAuditId"])).action == "chain_worker.not_broadcast"
    with env.session_factory() as session, session.begin():
        before = snapshot(session, env)
        assert retry_funding_preflight(session, session.get(DeploymentInstance, env.namespace_id), env.recovery_op_id, env.gate)["replayed"] is True
        assert snapshot(session, env) == before
    assert len(calls) == 1 and calls[0] == ("payment.mint", env.gate.roles["relayer"],
        [principal(env).wallet_address, "100000000"], "Transfer")
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("status", ["prepared", "sending", "submitted", "confirmed", "failed",
                                    "requires_attention", "invalidated_instance", "not_broadcast"])
def test_any_original_transaction_history_forbids_retry(failed_funding, status):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        session.add(ChainTransaction(operation_id=env.recovery_op_id, step_id=env.recovery_step_id,
            namespace_id=env.namespace_id, status=status, caller_address=env.gate.roles["relayer"],
            evm_nonce_text="1", canonical=False))
    reject_unchanged(env)


@pytest.mark.parametrize("tamper", ["payload", "kind", "principal", "step_action", "step_args", "step_caller",
    "finalizer_detail", "extra_step", "finalizer_status", "resource_status", "resource_error", "failure_audit", "allocated", "proof", "journal_ids"])
def test_original_binding_failure_or_scope_cannot_be_bypassed(failed_funding, tamper):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        _, op, resource, steps = rows(session, env)
        if tamper == "payload":
            op.payload_hash = "f" * 64
        elif tamper == "kind":
            op.operation_kind = "payment.redemption"
        elif tamper == "principal":
            op.principal_id = UUID(env.users["foundation"]["id"])
        elif tamper.startswith("step_"):
            field, value = {"step_action": ("action", "payment.redeem"), "step_args": ("args", [resource.actor_wallet, "1"]),
                            "step_caller": ("caller", resource.actor_wallet)}[tamper]
            steps[0].detail = {**steps[0].detail, field: value}
        elif tamper == "finalizer_detail":
            steps[1].detail = {**steps[1].detail, "paymentResourceUuid": str(uuid4())}
        elif tamper == "extra_step":
            session.add(OperationStep(operation_id=op.id, step_index=2, executor="payment", kind="payment.finalize_funding", status="queued", detail={}))
        elif tamper == "finalizer_status":
            steps[1].status = "confirmed"
        elif tamper == "resource_status":
            resource.status = "requires_attention"
        elif tamper == "resource_error":
            resource.error_code = "unresolved_source"
        elif tamper == "failure_audit":
            log = session.scalar(select(AuditLog).where(AuditLog.action == "chain_worker.not_broadcast"))
            log.metadata_json = {**log.metadata_json, "transactionId": str(uuid4())}
        elif tamper == "allocated":
            resource.allocated_atomic = Decimal(1)
        elif tamper == "proof":
            resource.chain_proof = {"txHash": "0x" + "f" * 64}
        elif tamper == "journal_ids":
            resource.journal_ids = [str(uuid4())]
    reject_unchanged(env)


@pytest.mark.parametrize("effect", ["journal", "claim"])
def test_even_unlinked_durable_economic_effect_forbids_retry(failed_funding, effect):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        _, op, resource, _ = rows(session, env)
        if effect == "journal":
            donor = session.scalar(select(SimHKDAccount).where(SimHKDAccount.owner_user_id == resource.actor_user_id))
            clearing = session.scalar(select(SimHKDAccount).where(SimHKDAccount.role == "clearing"))
            session.add(HKDJournal(namespace_id=env.namespace_id, operation_id=op.id, effect_kind="funding",
                debit_account_id=donor.id, credit_account_id=clearing.id, cents=Decimal(1)))
        else:
            claim_op, _ = begin_operation(session, namespace_id=env.namespace_id, principal_id=resource.actor_user_id,
                operation_kind="payment.funded_donation", idempotency_key=str(uuid4()), validated_payload={"fixture": True})
            session.add(FundedClaim(namespace_id=env.namespace_id, operation_id=claim_op.id, funding_resource_id=resource.id,
                actor_user_id=resource.actor_user_id, project_id=resource.project_id, donor_wallet=resource.actor_wallet,
                amount_atomic=Decimal(1), status="reserved"))
    reject_unchanged(env)


@pytest.mark.parametrize("tamper", ["inactive_user", "inactive_wallet", "short_hold", "extra_hold"])
def test_current_donor_authority_and_complete_hold_required(failed_funding, tamper):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        _, _, resource, _ = rows(session, env)
        if tamper == "inactive_user":
            session.get(User, resource.actor_user_id).active = False
        elif tamper == "inactive_wallet":
            session.scalar(select(WalletAuthorization).where(WalletAuthorization.user_id == resource.actor_user_id)).active = False
        else:
            account = session.scalar(select(SimHKDAccount).where(SimHKDAccount.owner_user_id == resource.actor_user_id))
            account.held_cents += Decimal(-1 if tamper == "short_hold" else 1)
    reject_unchanged(env)


@pytest.mark.parametrize("status,nonce", [("requires_attention", "9"), ("prepared", "9"), ("confirmed", "1"), ("not_broadcast", "1")])
def test_busy_lane_and_any_occupied_prepared_nonce_fail_closed(failed_funding, status, nonce):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        other, _ = begin_operation(session, namespace_id=env.namespace_id, principal_id=principal(env).user_id,
            operation_kind="fixture.lane", idempotency_key=str(uuid4()), validated_payload={"fixture": True})
        session.add(ChainTransaction(operation_id=other.id, namespace_id=env.namespace_id, caller_address=env.gate.roles["relayer"],
            status=status, evm_nonce_text=nonce, canonical=False))
    reject_unchanged(env, "payment_preflight_recovery_lane_busy" if nonce == "9" else "payment_preflight_recovery_nonce_occupied")


def test_prepare_rejection_rolls_back_temporary_queue_even_if_caller_catches(failed_funding, monkeypatch):
    env = failed_funding
    def fail(*args):
        raise ValueError("fake codec rejects original ABI arguments")
    monkeypatch.setattr(env.gate, "prepare", fail)
    reject_unchanged(env, "payment_preflight_recovery_prepare_rejected")


def test_preflight_attention_rolls_back_instead_of_implicitly_maintaining_other_sources(failed_funding, monkeypatch):
    env = failed_funding
    def fail(session, ns, step, op, gate):
        session.get(PaymentResource, env.recovery_resource_id).status = "requires_attention"
        session.flush()
        raise APIError(409, "funding_requires_attention", "fake source freeze")
    monkeypatch.setattr("pog_api.payment_recovery.validate_pending_chain", fail)
    reject_unchanged(env, "funding_requires_attention")


def test_outage_does_not_positive_recover_or_clear_failure(failed_funding):
    env = failed_funding
    env.gate.mode = "offline"
    with env.session_factory() as session, session.begin():
        before = snapshot(session, env)
        with pytest.raises(ChainUnavailable):
            retry_funding_preflight(session, session.get(DeploymentInstance, env.namespace_id), env.recovery_op_id, env.gate)
        assert snapshot(session, env) == before


def test_recovery_history_is_required_for_replay_and_confirmed_is_noop(failed_funding):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        ns, op, _, _ = rows(session, env)
        retry_funding_preflight(session, ns, op.id, env.gate)
    with env.session_factory() as session, session.begin():
        ns, op, _, steps = rows(session, env)
        op.status = "confirmed"
        for step in steps:
            step.status = "confirmed"
    with env.session_factory() as session, session.begin():
        before = snapshot(session, env)
        assert retry_funding_preflight(session, session.get(DeploymentInstance, env.namespace_id), env.recovery_op_id, env.gate)["replayed"] is True
        assert snapshot(session, env) == before
    with env.session_factory() as session, session.begin():
        log = session.scalar(select(AuditLog).where(AuditLog.action == RECOVERY_ACTION))
        log.metadata_json = {**log.metadata_json, "scopeHash": "f" * 64}
    reject_unchanged(env)


def test_unknown_after_successful_recovery_is_never_automatically_retried(failed_funding):
    env = failed_funding
    with env.session_factory() as session, session.begin():
        ns, op, _, _ = rows(session, env)
        retry_funding_preflight(session, ns, op.id, env.gate)
    with env.session_factory() as session, session.begin():
        _, op, _, steps = rows(session, env)
        op.status = steps[0].status = "requires_attention"
        session.add(ChainTransaction(operation_id=op.id, step_id=steps[0].id, namespace_id=env.namespace_id,
            caller_address=env.gate.roles["relayer"], evm_nonce_text="1", status="requires_attention", canonical=False))
    reject_unchanged(env)
