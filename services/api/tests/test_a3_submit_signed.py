"""Local saved-signature bridge: real PostgreSQL, connection-free fake chain.

These tests prove HTTP authorization, durable idempotency and queue atomicity.
They do not prove deployment, broadcast, contract execution, Reserve or Release.
All signatures are ephemeral test EOAs; no HTTP assertion exposes their bytes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from threading import Barrier
from uuid import UUID, uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text

from _route_helpers import auth, route_env
from pog_api.app import create_app
from pog_api.hashing import payload_sha256
from pog_api.models import (
    AuditLog, ChainTransaction, DeploymentInstance, Document, DocumentVersion,
    Operation, OperationStep, Project, SigningRequest, WalletAuthorization,
)


KINDS = ("ai_pre", "reserve", "receipt")
ACTORS = {"ai_pre": "ai", "reserve": "admin", "receipt": "recipient"}
ACTIONS = {"ai_pre": "assessment.ai_pre", "reserve": "approval.reserve", "receipt": "receipt.submit"}
EVENTS = {"ai_pre": "AIAssessmentRecorded", "reserve": "HumanApprovalSubmitted", "receipt": "RecipientReceiptAccepted"}
HISTORY_TABLES = ("operations", "operation_steps", "signing_requests", "chain_transactions", "audit_logs")


def _prepare(env, kind="reserve", *, signed=True, index=0, key="prepare"):
    state, enum = {
        "ai_pre": ("po_recorded", 1), "reserve": ("pre_assessed", 2),
        "receipt": ("invoice_recorded", 5),
    }[kind]
    env.states(procurement=state, chain_enum=enum)
    actor = ACTORS[kind]
    body = {"kind": kind, "deadlineTtlSeconds": 60}
    if kind == "reserve":
        body["reserveAmountAtomic"] = "60000000"
    elif kind == "receipt":
        body["receiptEvidenceDocumentVersionId"] = str(env.documents["receipt_evidence"].id)
    response = env.client.post(
        f"/v2/procurements/{env.procurements[index].id}/signing-requests",
        json=body, headers=auth(env.tokens[actor], key),
    )
    assert response.status_code == 202
    request = response.json()["signingRequest"]
    if signed:
        response = env.client.post(
            f"/v2/signing-requests/{request['id']}/sign-demo", json={"confirm": True},
            headers=auth(env.tokens[actor], key + "-sign"),
        )
        assert response.status_code == 202
        request = response.json()["signingRequest"]
    return actor, request


def _saved_signature(env, request):
    with env.session_factory() as session:
        return session.get(SigningRequest, UUID(request["id"])).signature


def _local_signature(env, actor, request):
    # Independent EOA signing is test setup, not a server's automatic signing.
    value = Account.sign_message(
        encode_typed_data(full_message=request["typedData"]), env.accounts[actor].key,
    ).signature.hex()
    return "0x" + value.removeprefix("0x")


def _bridge(env, actor, request, key="bridge", *, body=None, client=None):
    return (client or env.client).post(
        f"/v2/signing-requests/{request['id']}/submit-signed",
        json={"confirm": True} if body is None else body,
        headers=auth(env.tokens[actor], key),
    )


def _external(env, actor, request, signature, key="external", *, client=None):
    return (client or env.client).post(
        f"/v2/signing-requests/{request['id']}/submit",
        json={"signature": signature}, headers=auth(env.tokens[actor], key),
    )


def _history(env):
    # PostgreSQL JSON snapshots include original signature/source/pointers/audit.
    # Fixed table names only; never print this private comparison value.
    with env.session_factory() as session:
        rows = {name: session.scalars(text(f"SELECT to_jsonb(t) FROM {name} t ORDER BY id")).all()
                for name in HISTORY_TABLES}
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _unchanged(env, expected_history):
    if _history(env) != expected_history:
        raise AssertionError("Rejected/replayed request changed durable private history")


def _counts(env):
    with env.session_factory() as session:
        return tuple(session.scalar(select(func.count(model.id))) for model in (
            Operation, OperationStep, SigningRequest, ChainTransaction, AuditLog,
        ))


def _redacted(response, env, signature=None):
    def visit(value):
        if isinstance(value, dict):
            if {"signature", "rawSignature", "token", "password"} & value.keys():
                raise AssertionError("HTTP response exposes a forbidden private field")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(response.json())
    if signature:
        if signature in response.text:
            raise AssertionError("HTTP response exposes private signing material")
    for token in env.tokens.values():
        if token in response.text:
            raise AssertionError("HTTP response exposes a bearer credential")
    for user in env.users.values():
        if user["password"] in response.text:
            raise AssertionError("HTTP response exposes an account credential")


def _denied_without_change(env, actor, request, *, status, code=None, key="denied", body=None, client=None):
    history = _history(env)
    signature = _saved_signature(env, request)
    sign_calls = env.gate.demo_sign_calls
    response = _bridge(env, actor, request, key, body=body, client=client)
    assert response.status_code == status
    if code:
        assert response.json()["error"]["code"] == code
    _redacted(response, env, signature)
    _unchanged(env, history)
    assert env.gate.demo_sign_calls == sign_calls
    assert env.gate.broadcast_calls == 0
    return response


def _assert_one_submission(env, request, response, signature, *, bridge=True):
    operation_id = UUID(response.json()["operation"]["operationId"])
    with env.session_factory() as session:
        row = session.get(SigningRequest, UUID(request["id"]))
        operation = session.get(Operation, operation_id)
        steps = session.scalars(select(OperationStep).where(OperationStep.operation_id == operation_id)).all()
        assert row.status == "queued" and row.submitted_operation_id == operation_id
        if row.signature != signature:
            raise AssertionError("Queue changed the saved signature")
        assert operation.status == "queued"
        expected_kind = f"signing_request.{'submit_signed' if bridge else 'submit'}.{row.kind}"
        assert operation.operation_kind == expected_kind
        assert operation.result_resource_type == "signing_request" and operation.result_resource_id == row.id
        assert len(steps) == 1 and steps[0].step_index == 0 and steps[0].status == "queued"
        if steps[0].kind != ACTIONS[row.kind] or steps[0].detail["action"] != ACTIONS[row.kind]:
            raise AssertionError("Queue does not contain the one expected authorization action")
        if steps[0].detail["expectedEvent"] != EVENTS[row.kind]:
            raise AssertionError("Queue expects a different authorization event")
        if steps[0].detail["caller"] != env.gate.roles["relayer"]:
            raise AssertionError("Queue caller is not the designated relayer")
        if steps[0].detail["args"][-1] != signature:
            raise AssertionError("Queue step does not use the saved signature")
        context = row.context_json
        frozen_context = (
            {"reserveAmountAtomic": context.get("reserveAmountAtomic")} if row.kind == "reserve" else
            {"receiptEvidenceDocumentVersionId": context.get("receiptEvidenceDocumentVersionId")}
            if row.kind == "receipt" else {}
        )
        material_hash = payload_sha256({
            "namespaceId": str(row.namespace_id), "procurementId": str(row.procurement_id),
            "signerUserId": str(row.signer_user_id), "kind": row.kind,
            "contract": row.contract_address, "signer": row.signer_wallet,
            "nonce": row.nonce_text, "deadline": row.deadline_text,
            "policyEpoch": row.policy_epoch, "digest": row.digest,
            "typedData": row.typed_data, "context": frozen_context,
            "signature": signature.lower(),
        })
        expected_payload = (
            {"requestId": str(row.id), "confirm": True, "materialHash": material_hash} if bridge else
            {"requestId": str(row.id), "signature": signature}
        )
        if operation.payload_hash != payload_sha256(expected_payload):
            raise AssertionError("Submission does not bind its exact immutable material")
        assert not session.scalars(select(ChainTransaction)).all()
        # Approval is one human vote, never an implicit reserve/release step.
        assert not session.scalars(select(OperationStep).where(
            OperationStep.kind.in_(("reserve.execute", "funds.release", "payment.attest")),
        )).all()
    _redacted(response, env, signature)
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("kind", KINDS)
def test_saved_signature_queues_only_the_explicit_authorization_without_signing(route_env, kind):
    env = route_env
    actor, request = _prepare(env, kind)
    signature = _saved_signature(env, request)
    before = _counts(env)
    sign_calls = env.gate.demo_sign_calls
    response = _bridge(env, actor, request)
    assert response.status_code == 202
    assert response.json()["operation"]["replayed"] is False
    assert response.json()["operation"]["chainVerified"] is False
    assert response.json()["signingRequest"]["typedData"] == request["typedData"]
    assert _counts(env) == (before[0] + 1, before[1] + 1, before[2], before[3], before[4] + 1)
    assert env.gate.demo_sign_calls == sign_calls
    _assert_one_submission(env, request, response, signature)


@pytest.mark.parametrize("body", [
    {}, {"confirm": False}, {"confirm": 1}, {"confirm": "true"}, {"confirm": None},
    {"confirm": True, "signature": "private-signature-fixture"},
    {"confirm": True, "caller": "private-caller-fixture"},
    {"confirm": True, "rpcUrl": "private-rpc-fixture"},
])
def test_bridge_requires_exact_true_confirmation_and_rejects_extra_inputs(route_env, body):
    env = route_env
    actor, request = _prepare(env)
    response = _denied_without_change(env, actor, request, status=422, code="validation_error", body=body)
    if any(value in response.text for value in (
        "private-signature-fixture", "private-caller-fixture", "private-rpc-fixture",
    )):
        raise AssertionError("Validation error echoes a forbidden input value")


@pytest.mark.parametrize("key,code", [
    (None, "idempotency_key_required"), ("", "invalid_idempotency_key"),
    ("x" * 129, "invalid_idempotency_key"),
])
def test_bridge_keeps_original_idempotency_header_validation(route_env, key, code):
    env = route_env
    actor, request = _prepare(env)
    _denied_without_change(env, actor, request, status=400, code=code, key=key)


@pytest.mark.parametrize("signature", [None, ""])
def test_missing_stored_signature_never_allocates_an_operation(route_env, signature):
    env = route_env
    actor, request = _prepare(env)
    with env.session_factory() as session, session.begin():
        session.get(SigningRequest, UUID(request["id"])).signature = signature
    _denied_without_change(env, actor, request, status=409, code="signing_signature_missing")


@pytest.mark.parametrize("malformation", ["short", "non_hex", "zero_r", "zero_s", "high_s", "bad_v", "other_signer"])
def test_malformed_or_wrong_saved_signature_is_stable_422_and_redacted(route_env, malformation):
    env = route_env
    actor, request = _prepare(env)
    original = _saved_signature(env, request)
    raw = bytes.fromhex(original[2:])
    malformed = {
        "short": "0xdeadbeef",
        "non_hex": "0x" + "z" * 130,
        "zero_r": "0x" + (bytes(32) + raw[32:]).hex(),
        "zero_s": "0x" + (raw[:32] + bytes(32) + raw[64:]).hex(),
        "high_s": "0x" + (raw[:32] + bytes.fromhex("ff" * 32) + raw[64:]).hex(),
        "bad_v": "0x" + (raw[:64] + b"\x00").hex(),
        "other_signer": _local_signature(env, "foundation", request),
    }[malformation]
    with env.session_factory() as session, session.begin():
        session.get(SigningRequest, UUID(request["id"])).signature = malformed
    _denied_without_change(env, actor, request, status=422, code="signature_invalid")


@pytest.mark.parametrize("status", ["prepared", "queued", "requires_attention", "confirmed", "failed", "expired", "invalidated_stale"])
def test_fresh_bridge_requires_signed_unsubmitted_state(route_env, status):
    env = route_env
    actor, request = _prepare(env)
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        row.status = status
        if status in {"queued", "requires_attention", "confirmed", "failed"}:
            row.submitted_operation_id = row.operation_id
    _denied_without_change(env, actor, request, status=409, code="signing_request_state")


@pytest.mark.parametrize("status", ["prepared", "signed"])
def test_existing_submission_pointer_protects_even_legacy_unqueued_state(route_env, status):
    env = route_env
    actor, request = _prepare(env)
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        row.status, row.submitted_operation_id = status, row.operation_id
    _denied_without_change(env, actor, request, status=409, code="signing_request_state")


@pytest.mark.parametrize("stored", [False, True])
def test_demo_disabled_bridge_is_forbidden_but_external_prepared_submission_is_compatible(route_env, stored):
    env = route_env
    actor, request = _prepare(env, signed=False)
    signature = _local_signature(env, actor, request)
    if stored:
        # The bridge must honor its demo setting even if a signature is saved.
        with env.session_factory() as session, session.begin():
            session.get(SigningRequest, UUID(request["id"])).signature = signature
    with TestClient(create_app(replace(env.settings, demo_signing_enabled=False)), raise_server_exceptions=False) as client:
        _denied_without_change(env, actor, request, status=403, code="demo_signing_disabled", client=client)
        response = _external(env, actor, request, signature, client=client)
        assert response.status_code == 202
        _assert_one_submission(env, request, response, signature, bridge=False)
    assert env.gate.demo_sign_calls == 0


def _change_identity(env, actor, change):
    with env.session_factory() as session, session.begin():
        wallet = session.scalar(select(WalletAuthorization).where(
            WalletAuthorization.user_id == UUID(env.users[actor]["id"]), WalletAuthorization.active.is_(True),
        ))
        if change == "inactive":
            wallet.active = False
        elif change == "changed":
            wallet.wallet_address = Account.create().address
        elif change == "ambiguous":
            session.add(WalletAuthorization(user_id=wallet.user_id, role_name="donor",
                                             wallet_address=Account.create().address, active=True))
        elif change == "role":
            wallet.role_name = "donor"
        elif change == "owner":
            project = session.get(Project, env.projects[0].id)
            if actor == "admin":
                project.human_approver_user_id = UUID(env.users["other_human"]["id"])
            elif actor == "recipient":
                project.recipient_user_id = UUID(env.users["other_recipient"]["id"])
            else:
                raise AssertionError("AI service has no designated project-owner slot")


@pytest.mark.parametrize("kind,change,status,code", [
    ("reserve", "role", 403, "role_forbidden"),
    ("reserve", "owner", 403, "role_forbidden"),
    ("receipt", "owner", 403, "role_forbidden"),
    ("reserve", "changed", 403, "signer_mismatch"),
    ("reserve", "inactive", 409, "ambiguous_role_wallet"),
    ("reserve", "ambiguous", 409, "ambiguous_role_wallet"),
])
@pytest.mark.parametrize("replay", [False, True])
def test_current_authority_is_required_for_new_queue_and_original_key_replay(route_env, kind, change, status, code, replay):
    env = route_env
    actor, request = _prepare(env, kind)
    if replay:
        response = _bridge(env, actor, request)
        assert response.status_code == 202
    _change_identity(env, actor, change)
    if replay:
        env.gate.mode = "offline"
    _denied_without_change(env, actor, request, status=status, code=code, key="bridge")


@pytest.mark.parametrize("actor,status,code", [
    (None, 401, "authentication_required"), ("foundation", 403, "role_forbidden"),
    ("other_human", 403, "role_forbidden"),
])
def test_bridge_authentication_and_intended_actor_are_not_bypassed(route_env, actor, status, code):
    env = route_env
    _actor, request = _prepare(env)
    history = _history(env)
    headers = {"Idempotency-Key": "wrong-actor"} if actor is None else auth(env.tokens[actor], "wrong-actor")
    response = env.client.post(f"/v2/signing-requests/{request['id']}/submit-signed",
                               json={"confirm": True}, headers=headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    _redacted(response, env, _saved_signature(env, request))
    _unchanged(env, history)
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("mode,code", [("offline", "chain_gate_failed"), ("mismatch", "chain_gate_failed")])
def test_new_bridge_needs_current_chain_gate_and_preserves_saved_bytes(route_env, mode, code):
    env = route_env
    actor, request = _prepare(env)
    env.gate.mode = mode
    _denied_without_change(env, actor, request, status=503, code=code)


def test_bridge_cannot_use_an_invalidated_namespace(route_env):
    env = route_env
    actor, request = _prepare(env)
    with env.session_factory() as session, session.begin():
        session.get(DeploymentInstance, env.namespace_id).active = False
    _denied_without_change(env, actor, request, status=409, code="deployment_instance_inactive")


def test_bridge_requires_the_request_in_the_current_instance_namespace(route_env):
    env = route_env
    actor, request = _prepare(env)
    # Deliberately fake a new instance identity, not a real reset/deployment.
    env.gate.manifest["chain"]["instanceId"] = "other-fake-instance"
    _denied_without_change(env, actor, request, status=404, code="signing_request_not_found")


@pytest.mark.parametrize("kind,change", [
    ("ai_pre", "nonce"), ("reserve", "nonce"), ("receipt", "nonce"),
    ("reserve", "expired"), ("reserve", "policy"), ("reserve", "approver"),
    ("reserve", "ai_signer"), ("reserve", "amount"), ("reserve", "digest"),
    ("reserve", "contract"), ("reserve", "caller"), ("reserve", "typed_data"),
    ("reserve", "policy_metadata"),
    ("receipt", "invoice"), ("receipt", "goods"), ("receipt", "recipient"),
    ("ai_pre", "evidence"),
])
def test_saved_authorization_still_passes_original_freshness_checks(route_env, kind, change):
    env = route_env
    actor, request = _prepare(env, kind)
    if change == "nonce":
        env.gate.nonces[{"ai_pre": "aiNonces", "reserve": "humanNonces", "receipt": "recipientNonces"}[kind]] += 1
    elif change == "expired":
        env.gate.now = int(request["deadline"]) + 1
    elif change == "policy":
        env.gate.policy_epoch += 1
    elif change == "approver":
        env.gate.is_approver = False
    elif change == "ai_signer":
        env.gate.ai_signer_allowed = False
    elif change in {"amount", "digest", "contract", "caller", "typed_data", "policy_metadata"}:
        with env.session_factory() as session, session.begin():
            row = session.get(SigningRequest, UUID(request["id"]))
            if change == "amount":
                row.context_json = {**row.context_json, "reserveAmountAtomic": "60000001"}
            elif change == "digest":
                row.digest = "0x" + "ee" * 32
            elif change == "contract":
                row.contract_address = env.gate.contract_address("PoGRegistryV2")
            elif change == "policy_metadata":
                row.policy_epoch += 1
            elif change == "typed_data":
                typed = deepcopy(row.typed_data)
                typed["domain"]["version"] = "different-fixture-version"
                row.typed_data = typed
                row.signature = _local_signature(env, actor, {"typedData": typed})
            else:
                typed = deepcopy(row.typed_data)
                typed["message"]["signer"] = env.accounts["foundation"].address
                row.typed_data = typed
                # Even a locally consistent different signer cannot claim this request.
                row.signature = _local_signature(env, "foundation", {"typedData": typed})
    elif change == "recipient":
        env.gate.projects[env.projects[0].business_id][2] = env.accounts["other_recipient"].address
    else:
        position = {"invoice": 10, "goods": 12, "evidence": 7}[change]
        env.gate.procurements[env.procurements[0].business_id][position] = bytes.fromhex("ee" * 32)
    _denied_without_change(env, actor, request, status=422 if change == "caller" else 409,
                          code="signature_invalid" if change == "caller" else "signing_material_stale")


@pytest.mark.parametrize("change,status,code", [
    ("uploader", 403, "receipt_evidence_uploader_mismatch"),
    ("category", 409, "signing_material_stale"), ("procurement", 409, "signing_material_stale"),
    ("hash", 409, "signing_material_stale"), ("missing_source", 409, "signing_material_stale"),
])
def test_receipt_bridge_revalidates_exact_recipient_version_not_just_hash(route_env, change, status, code):
    env = route_env
    actor, request = _prepare(env, "receipt")
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        version = session.get(DocumentVersion, env.documents["receipt_evidence"].id)
        document = session.get(Document, version.document_id)
        if change == "uploader":
            version.uploaded_by_user_id = UUID(env.users["foundation"]["id"])
            # A later legitimate same-hash version cannot substitute for the frozen one.
            session.add(DocumentVersion(
                document_id=document.id, version=2, original_filename="recipient-later.pdf",
                content_type=version.content_type, size_bytes=version.size_bytes,
                sha256_hex=version.sha256_hex, keccak256_hex=version.keccak256_hex,
                storage_key=f"fake-route/{uuid4()}.pdf", uploaded_by_user_id=row.signer_user_id,
                referenced=False,
            ))
        elif change == "category":
            document.category = "goods_evidence"
        elif change == "procurement":
            document.procurement_id = env.procurements[1].id
        elif change == "hash":
            version.keccak256_hex = "ee" * 32
        else:
            row.context_json = {}
    _denied_without_change(env, actor, request, status=status, code=code)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status", ["queued", "requires_attention", "confirmed"])
def test_original_bridge_key_acknowledges_durable_history_offline_after_deadline(route_env, kind, status):
    env = route_env
    actor, request = _prepare(env, kind)
    response = _bridge(env, actor, request)
    assert response.status_code == 202
    operation_id = UUID(response.json()["operation"]["operationId"])
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        row.status = status
        row.authorized_at = datetime(2026, 10, 3, tzinfo=UTC)
        row.context_json = {**row.context_json, "workerStatus": "unknown", "leaseOwner": "test-worker"}
        operation = session.get(Operation, operation_id)
        operation.status = status
        step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation_id))
        step.status = status
        step.detail = {**step.detail, "workerAttempt": "ambiguous-test-attempt"}
    env.gate.now = int(request["deadline"]) + 1
    env.gate.nonces[{"ai_pre": "aiNonces", "reserve": "humanNonces", "receipt": "recipientNonces"}[kind]] += 1
    env.gate.mode = "offline"
    history, sign_calls = _history(env), env.gate.demo_sign_calls
    replay = _bridge(env, actor, request)
    assert replay.status_code == 202
    assert replay.json()["operation"]["operationId"] == str(operation_id)
    assert replay.json()["operation"]["replayed"] is True
    assert replay.json()["signingRequest"]["status"] == status
    _redacted(replay, env, _saved_signature(env, request))
    _unchanged(env, history)
    assert env.gate.demo_sign_calls == sign_calls
    assert env.gate.broadcast_calls == 0
    # Fresh keys are never a history acknowledgement or permission to resend.
    env.gate.mode = "online"
    _denied_without_change(env, actor, request, status=409, code="signing_request_state", key="second-bridge")


@pytest.mark.parametrize("change", ["signature", "digest", "typed_data", "nonce", "deadline", "policy", "contract", "amount", "receipt_source"])
def test_original_bridge_key_rejects_changed_immutable_material_even_offline(route_env, change):
    env = route_env
    actor, request = _prepare(env, "receipt" if change == "receipt_source" else "reserve")
    response = _bridge(env, actor, request)
    assert response.status_code == 202
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        if change == "signature":
            row.signature = _local_signature(env, "foundation", request)
        elif change == "digest":
            row.digest = "0x" + "ee" * 32
        elif change == "typed_data":
            typed = deepcopy(row.typed_data)
            typed["domain"]["version"] = "different-fixture-version"
            row.typed_data = typed
        elif change == "nonce":
            row.nonce_text = "1"
        elif change == "deadline":
            row.deadline_text = str(int(row.deadline_text) + 1)
        elif change == "policy":
            row.policy_epoch += 1
        elif change == "contract":
            row.contract_address = env.gate.contract_address("PoGRegistryV2")
        elif change == "amount":
            row.context_json = {**row.context_json, "reserveAmountAtomic": "60000001"}
        else:
            row.context_json = {**row.context_json, "receiptEvidenceDocumentVersionId": str(env.documents["goods_evidence"].id)}
    env.gate.mode = "offline"
    _denied_without_change(env, actor, request, status=409, code="idempotency_payload_conflict", key="bridge")


@pytest.mark.parametrize("change", ["resource_type", "resource_id", "pointer"])
def test_bridge_replay_requires_exact_persisted_resource_and_submission_pointer(route_env, change):
    env = route_env
    actor, request = _prepare(env)
    response = _bridge(env, actor, request)
    assert response.status_code == 202
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(request["id"]))
        operation = session.get(Operation, UUID(response.json()["operation"]["operationId"]))
        if change == "resource_type":
            operation.result_resource_type = "procurement"
        elif change == "resource_id":
            operation.result_resource_id = env.procurements[1].id
        else:
            row.submitted_operation_id = row.operation_id
    env.gate.mode = "offline"
    _denied_without_change(env, actor, request, status=409, code="idempotency_payload_conflict", key="bridge")


def test_bridge_key_cannot_acknowledge_another_signing_request(route_env):
    env = route_env
    actor, first = _prepare(env, "ai_pre", key="first")
    response = _bridge(env, actor, first)
    assert response.status_code == 202
    env.gate.nonces["aiNonces"] = 1
    _actor, second = _prepare(env, "ai_pre", index=1, key="second")
    env.gate.mode = "offline"
    _denied_without_change(env, actor, second, status=409, code="idempotency_payload_conflict", key="bridge")


@pytest.mark.parametrize("first", ["bridge", "external"])
@pytest.mark.parametrize("same_key", [False, True])
def test_cross_route_submission_does_not_replay_or_resend_the_other_endpoint(route_env, first, same_key):
    env = route_env
    actor, request = _prepare(env)
    signature = _saved_signature(env, request)
    response = (_bridge(env, actor, request, "first") if first == "bridge"
                else _external(env, actor, request, signature, "first"))
    assert response.status_code == 202
    _assert_one_submission(env, request, response, signature, bridge=first == "bridge")
    history = _history(env)
    key = "first" if same_key else "second"
    second = (_external(env, actor, request, signature, key) if first == "bridge"
              else _bridge(env, actor, request, key))
    assert second.status_code == 409 and second.json()["error"]["code"] == "signing_request_state"
    _unchanged(env, history)
    _redacted(second, env, signature)
    env.gate.mode = "offline"
    original = (_bridge(env, actor, request, "first") if first == "bridge"
                else _external(env, actor, request, signature, "first"))
    assert original.status_code == 202 and original.json()["operation"]["replayed"] is True
    assert original.json()["operation"]["operationId"] == response.json()["operation"]["operationId"]
    _unchanged(env, history)
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("same_key", [False, True])
def test_bridge_and_external_race_with_independent_pg_sessions_queue_exactly_once(route_env, same_key):
    env = route_env
    actor, request = _prepare(env)
    signature = _saved_signature(env, request)
    before, sign_calls = _counts(env), env.gate.demo_sign_calls
    barrier = Barrier(2)
    row_lock_barrier = Barrier(2)
    apps = [create_app(env.settings), create_app(env.settings)]
    request_sessions, backend_pids = [[], []], [set(), set()]
    lock_arrivals = [0, 0]

    def make_session_dependency(app, index):
        # A zero-argument closure: captured fixtures are not HTTP query inputs.
        def independent_session():
            with app.state.session_factory() as session:
                request_sessions[index].append(session)
                yield session
        return independent_session

    def make_row_lock_probe(index):
        def before_cursor_execute(connection, _cursor, statement, _parameters, _context, _executemany):
            lowered = statement.lower()
            if "signing_requests" in lowered and "for update" in lowered:
                lock_arrivals[index] += 1
                backend_pids[index].add(connection.connection.driver_connection.info.backend_pid)
                # Both real transactions reach the exact request lock before
                # either SELECT executes; no fake ordering of product logic.
                row_lock_barrier.wait(timeout=10)
        return before_cursor_execute

    for index, app in enumerate(apps):
        route = next(item for item in app.routes if getattr(item, "path", None) ==
                     f"/v2/signing-requests/{{request_id}}/{'submit-signed' if index == 0 else 'submit'}")
        dependency = next(item.call for item in route.dependant.dependencies if item.call.__name__ == "_get_session")

        app.dependency_overrides[dependency] = make_session_dependency(app, index)
        event.listen(app.state.engine, "before_cursor_execute", make_row_lock_probe(index))

    with TestClient(apps[0], raise_server_exceptions=False) as bridge_client, \
            TestClient(apps[1], raise_server_exceptions=False) as external_client:
        def send(index):
            barrier.wait(timeout=10)
            if index == 0:
                return _bridge(env, actor, request, "race", client=bridge_client)
            return _external(env, actor, request, signature, "race" if same_key else "other-race", client=external_client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, range(2)))

    assert sorted(response.status_code for response in responses) == [202, 409]
    winner = next(response for response in responses if response.status_code == 202)
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["error"]["code"] == "signing_request_state"
    assert len(request_sessions[0]) == len(request_sessions[1]) == 1
    assert request_sessions[0][0] is not request_sessions[1][0]
    assert lock_arrivals == [1, 1]
    assert backend_pids[0] and backend_pids[1] and backend_pids[0].isdisjoint(backend_pids[1])
    assert _counts(env) == (before[0] + 1, before[1] + 1, before[2], before[3], before[4] + 1)
    _assert_one_submission(env, request, winner, signature, bridge=winner is responses[0])
    _redacted(loser, env, signature)
    assert env.gate.demo_sign_calls == sign_calls and env.gate.broadcast_calls == 0
