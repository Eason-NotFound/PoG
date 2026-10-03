"""Real PG receipt provenance recovery; FakeRouteGate never broadcasts."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data
import pytest
from sqlalchemy import func, select, text

from _route_helpers import auth, route_env
from pog_api.hashing import canonical_json_bytes, payload_sha256
from pog_api.models import AuditLog, ChainTransaction, Document, DocumentVersion, Operation, OperationStep, SigningRequest
from pog_api.typed_data import digest, recipient_receipt_typed


def _version(env, *, uploader="recipient", procurement_index=0, category="receipt_evidence", hash_hex="ab" * 32):
    with env.session_factory() as session, session.begin():
        document = Document(namespace_id=env.namespace_id, procurement_id=env.procurements[procurement_index].id,
                            category=category, owner_user_id=UUID(env.users[uploader]["id"]))
        session.add(document)
        session.flush()
        version = DocumentVersion(document_id=document.id, version=1, original_filename="synthetic-history.pdf",
                                  content_type="application/pdf", size_bytes=1, sha256_hex="cd" * 32,
                                  keccak256_hex=hash_hex, storage_key=f"synthetic-source/{uuid4()}.pdf",
                                  uploaded_by_user_id=UUID(env.users[uploader]["id"]), referenced=False)
        session.add(version)
        session.flush()
        return version.id


def _signature(env, typed):
    raw = Account.sign_message(encode_typed_data(full_message=typed), env.accounts["recipient"].key).signature.hex()
    return "0x" + raw.removeprefix("0x")


def _legacy(env, status, *, context=None, protected=False):
    """Reconstruct 9189's legal DB record: original Foundation evidence, no source context."""
    env.states(procurement="invoice_recorded", chain_enum=5)
    source = _version(env, uploader="foundation")
    view = env.gate.procurements[env.procurements[0].business_id]
    message = {
        "projectId": env.projects[0].business_id, "procurementId": env.procurements[0].business_id,
        "expectedRecipient": env.accounts["recipient"].address, "vendor": view[2],
        "poHash": "0x" + bytes(view[4]).hex(), "invoiceHash": "0x" + bytes(view[10]).hex(),
        "invoiceAmount": view[11], "goodsHash": "0x" + bytes(view[12]).hex(),
        "receiptEvidenceHash": "0x" + "ab" * 32, "nonce": 0, "deadline": env.gate.now + 3600,
    }
    typed = recipient_receipt_typed(message, 31337, env.gate.contract_address("PoGRegistryV2"))
    signature = _signature(env, typed)
    with env.session_factory() as session, session.begin():
        operation = Operation(namespace_id=env.namespace_id, principal_id=UUID(env.users["recipient"]["id"]),
                              operation_kind="signing_request.receipt", idempotency_key="legacy-source-request",
                              payload_hash=payload_sha256({"kind": "receipt", "receiptEvidenceDocumentVersionId": str(source)}),
                              status="confirmed", result_resource_type="signing_request")
        session.add(operation)
        session.flush()
        request = SigningRequest(namespace_id=env.namespace_id, operation_id=operation.id,
                                 procurement_id=env.procurements[0].id, signer_user_id=UUID(env.users["recipient"]["id"]),
                                 kind="receipt", status=status, contract_address=env.gate.contract_address("PoGRegistryV2"),
                                 signer_wallet=env.accounts["recipient"].address, nonce_text="0", deadline_text=str(message["deadline"]),
                                 policy_epoch=0, typed_data=typed, context_json={} if context is None else context,
                                 digest=digest(typed), signature=None if status == "prepared" else signature,
                                 authorized_at=None if status == "prepared" else datetime(2026, 10, 3, tzinfo=UTC))
        session.add(request)
        session.flush()
        operation.result_resource_id = request.id
        session.add(AuditLog(principal_id=request.signer_user_id, operation_id=operation.id,
                             action="synthetic.9189.receipt_history", outcome=status,
                             resource_type="signing_request", resource_id=request.id,
                             metadata_json={"originalFoundationVersionId": str(source), "unmodifiedDigest": request.digest}))
        if protected:
            submitted = Operation(namespace_id=env.namespace_id, principal_id=request.signer_user_id,
                                  operation_kind="signing_request.submit.receipt", idempotency_key="legacy-source-submit",
                                  payload_hash=payload_sha256({"requestId": str(request.id), "signature": signature}),
                                  status="requires_attention" if status == "requires_attention" else "queued",
                                  result_resource_type="signing_request", result_resource_id=request.id)
            session.add(submitted)
            session.flush()
            step = OperationStep(operation_id=submitted.id, step_index=0, kind="receipt.submit", status=submitted.status,
                                 detail={"action": "receipt.submit", "caller": env.gate.roles["relayer"],
                                         "expectedEvent": "RecipientReceiptAccepted"})
            session.add(step)
            session.flush()
            if status == "requires_attention":
                session.add(ChainTransaction(namespace_id=env.namespace_id, operation_id=submitted.id, step_id=step.id,
                                             caller_address=env.gate.roles["relayer"], evm_nonce_text="17",
                                             status="requires_attention", tx_hash=None, canonical=False))
            request.submitted_operation_id = submitted.id
        return request.id, operation.id, source, signature


def _row_bytes(env, table, row_id):
    assert table in {"signing_requests", "operations"}
    with env.session_factory() as session:
        row = session.scalar(text(f"SELECT to_jsonb(value) FROM {table} AS value WHERE id=:id"), {"id": row_id})
        return canonical_json_bytes(row)


def _counts(env):
    with env.session_factory() as session:
        return tuple(session.scalar(select(func.count(model.id)))
                     for model in (Operation, SigningRequest, OperationStep, ChainTransaction, AuditLog))


def _mutate(env, request_id, action, signature, key):
    body = {"confirm": True} if action == "sign-demo" else {"signature": signature}
    return env.client.post(f"/v2/signing-requests/{request_id}/{action}", json=body,
                           headers=auth(env.tokens["recipient"], key))


def _prepare(env, version_id, key):
    return env.client.post(f"/v2/procurements/{env.procurements[0].id}/signing-requests",
                           json={"kind": "receipt", "receiptEvidenceDocumentVersionId": str(version_id)},
                           headers=auth(env.tokens["recipient"], key))


@pytest.mark.parametrize("status", ["prepared", "signed"])
@pytest.mark.parametrize("action", ["sign-demo", "submit"])
@pytest.mark.parametrize("later_same_hash", [False, True])
def test_legacy_missing_source_context_fails_closed_even_with_later_recipient_same_hash(route_env, status, action, later_same_hash):
    env = route_env
    request_id, operation_id, _source, signature = _legacy(env, status)
    if later_same_hash:
        _version(env, uploader="recipient")
    request_before, operation_before, counts = _row_bytes(env, "signing_requests", request_id), _row_bytes(env, "operations", operation_id), _counts(env)
    signs = env.gate.demo_sign_calls
    response = _mutate(env, request_id, action, signature, "legacy-new-action-key")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "signing_material_stale"
    assert _row_bytes(env, "signing_requests", request_id) == request_before
    assert _row_bytes(env, "operations", operation_id) == operation_before and _counts(env) == counts
    assert env.gate.demo_sign_calls == signs and env.gate.broadcast_calls == 0
    history = env.client.get(f"/v2/signing-requests/{request_id}", headers=auth(env.tokens["recipient"]))
    assert history.status_code == 200 and history.json()["status"] == status


@pytest.mark.parametrize("action", ["sign-demo", "submit"])
@pytest.mark.parametrize("source_change,expected_status", [
    ("foundation", 403), ("wrong_procurement", 409), ("wrong_category", 409),
    ("wrong_hash", 409), ("missing_version", 409), ("invalid_uuid", 409),
])
def test_explicit_receipt_source_is_rechecked_before_sign_or_submit(route_env, action, source_change, expected_status):
    env = route_env
    request_id, operation_id, original_foundation, signature = _legacy(env, "signed")
    source = {
        "foundation": lambda: original_foundation,
        "wrong_procurement": lambda: _version(env, procurement_index=1),
        "wrong_category": lambda: _version(env, category="goods_evidence"),
        "wrong_hash": lambda: _version(env, hash_hex="ee" * 32),
        "missing_version": uuid4, "invalid_uuid": lambda: "not-a-uuid",
    }[source_change]()
    # A matching later Recipient upload must not substitute for the exact ID.
    _version(env, uploader="recipient")
    with env.session_factory() as session, session.begin():
        session.get(SigningRequest, request_id).context_json = {"receiptEvidenceDocumentVersionId": str(source)}
    request_before, operation_before, counts = _row_bytes(env, "signing_requests", request_id), _row_bytes(env, "operations", operation_id), _counts(env)
    signs = env.gate.demo_sign_calls
    response = _mutate(env, request_id, action, signature, "wrong-source-new-action-key")
    assert response.status_code == expected_status, response.text
    assert response.json()["error"]["code"] == ("receipt_evidence_uploader_mismatch" if expected_status == 403 else "signing_material_stale")
    assert _row_bytes(env, "signing_requests", request_id) == request_before
    assert _row_bytes(env, "operations", operation_id) == operation_before and _counts(env) == counts
    assert env.gate.demo_sign_calls == signs and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("status", ["prepared", "signed"])
def test_new_key_can_retire_only_unsubmitted_legacy_missing_source_without_rewriting_frozen_bytes(route_env, status):
    env = route_env
    request_id, operation_id, _source, _signature_value = _legacy(env, status)
    with env.session_factory() as session:
        before = session.scalar(text("SELECT to_jsonb(value) FROM signing_requests AS value WHERE id=:id"), {"id": request_id})
    operation_before = _row_bytes(env, "operations", operation_id)
    legitimate = _version(env, uploader="recipient")
    env.gate.now += 1
    response = _prepare(env, legitimate, "explicit-new-source-key")
    assert response.status_code == 202, response.text
    fresh = response.json()["signingRequest"]
    assert fresh["id"] != str(request_id) and fresh["nonce"] == "0"
    with env.session_factory() as session:
        after = session.scalar(text("SELECT to_jsonb(value) FROM signing_requests AS value WHERE id=:id"), {"id": request_id})
        assert after["status"] == "invalidated_stale"
        after["status"] = before["status"]
        assert canonical_json_bytes(after) == canonical_json_bytes(before)
        saved_fresh = session.get(SigningRequest, UUID(fresh["id"]))
        assert saved_fresh.context_json["receiptEvidenceDocumentVersionId"] == str(legitimate)
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.resource_id == request_id,
                             AuditLog.outcome == "invalidated_stale")) == 1
    assert _row_bytes(env, "operations", operation_id) == operation_before
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("status", ["queued", "requires_attention", "prepared", "signed"])
def test_queued_unknown_or_submitted_legacy_nonce_cannot_be_released_by_new_source(route_env, status):
    env = route_env
    request_id, operation_id, _source, _signature_value = _legacy(env, status, protected=True)
    legitimate = _version(env, uploader="recipient")
    before, operation_before, counts = _row_bytes(env, "signing_requests", request_id), _row_bytes(env, "operations", operation_id), _counts(env)
    response = _prepare(env, legitimate, "cannot-retire-protected-history")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "signing_nonce_in_use"
    assert _row_bytes(env, "signing_requests", request_id) == before
    assert _row_bytes(env, "operations", operation_id) == operation_before and _counts(env) == counts
    assert env.gate.demo_sign_calls == 0 and env.gate.broadcast_calls == 0


def test_new_exact_recipient_version_remains_explicitly_signable_and_submittable(route_env):
    env = route_env
    env.states(procurement="invoice_recorded", chain_enum=5)
    version_id = _version(env)
    response = _prepare(env, version_id, "valid-source-request")
    assert response.status_code == 202, response.text
    request = response.json()["signingRequest"]
    with env.session_factory() as session:
        assert session.get(SigningRequest, UUID(request["id"])).context_json["receiptEvidenceDocumentVersionId"] == str(version_id)
    signed = _mutate(env, request["id"], "sign-demo", None, "valid-source-explicit-confirm")
    assert signed.status_code == 202, signed.text
    with env.session_factory() as session:
        signature = session.get(SigningRequest, UUID(request["id"])).signature
    submitted = _mutate(env, request["id"], "submit", signature, "valid-source-submit")
    assert submitted.status_code == 202, submitted.text
    with env.session_factory() as session:
        saved = session.get(SigningRequest, UUID(request["id"]))
        assert saved.status == "queued" and saved.submitted_operation_id is not None
        assert saved.context_json["receiptEvidenceDocumentVersionId"] == str(version_id)
        assert session.scalar(select(func.count(OperationStep.id))) == 1
        assert session.scalar(select(func.count(ChainTransaction.id))) == 0
    assert env.gate.demo_sign_calls == 1 and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("status", ["prepared", "signed"])
@pytest.mark.parametrize("action", ["sign-demo", "submit"])
def test_existing_submission_pointer_rejects_new_mutation_but_keeps_durable_replay(route_env, status, action):
    env = route_env
    request_id, operation_id, _source, signature = _legacy(env, status, protected=True)
    with env.session_factory() as session, session.begin():
        saved = session.get(SigningRequest, request_id)
        pointer = saved.submitted_operation_id
        assert pointer is not None
        if action == "submit":
            replay_key = "legacy-source-submit"
            replay_operation_id = pointer
        else:
            # A durable historical explicit-confirm fact is replayable even if
            # later recovery left a contradictory prepared/signed + pointer state.
            replay_key = "legacy-source-sign"
            prior = Operation(namespace_id=env.namespace_id, principal_id=saved.signer_user_id,
                              operation_kind="signing_request.sign_demo", idempotency_key=replay_key,
                              payload_hash=payload_sha256({"requestId": str(request_id), "confirm": True}),
                              status="confirmed", result_resource_type="signing_request", result_resource_id=request_id)
            session.add(prior)
            session.flush()
            replay_operation_id = prior.id
    request_before = _row_bytes(env, "signing_requests", request_id)
    operation_before = _row_bytes(env, "operations", operation_id)
    pointer_before = _row_bytes(env, "operations", pointer)
    counts, signs = _counts(env), env.gate.demo_sign_calls
    denied = _mutate(env, request_id, action, signature, "pointer-protected-new-key")
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "signing_request_state"
    assert _row_bytes(env, "signing_requests", request_id) == request_before
    assert _row_bytes(env, "operations", operation_id) == operation_before
    assert _row_bytes(env, "operations", pointer) == pointer_before and _counts(env) == counts
    assert env.gate.demo_sign_calls == signs and env.gate.broadcast_calls == 0
    replay = _mutate(env, request_id, action, signature, replay_key)
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation"]["replayed"] is True
    assert replay.json()["operation"]["operationId"] == str(replay_operation_id)
    assert _row_bytes(env, "signing_requests", request_id) == request_before
    assert _row_bytes(env, "operations", pointer) == pointer_before and _counts(env) == counts
    history = env.client.get(f"/v2/signing-requests/{request_id}", headers=auth(env.tokens["recipient"]))
    assert history.status_code == 200 and history.json()["status"] == status
    with env.session_factory() as session:
        assert session.get(SigningRequest, request_id).submitted_operation_id == pointer
    assert env.gate.demo_sign_calls == signs and env.gate.broadcast_calls == 0
