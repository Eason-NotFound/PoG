"""HTTP + real PostgreSQL tests; fake getters are not real-chain evidence."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import select

from pog_api.models import DocumentVersion, Operation, SigningRequest, OperationStep, ChainTransaction
from _route_helpers import route_env, auth


def _setup(env, kind):
    state, enum = {"ai_pre": ("po_recorded", 1), "reserve": ("pre_assessed", 2),
                   "receipt": ("invoice_recorded", 5)}[kind]
    env.states(procurement=state, chain_enum=enum)
    body = {"kind": kind, "deadlineTtlSeconds": 60}
    if kind == "reserve":
        body["reserveAmountAtomic"] = "60000000"
    elif kind == "receipt":
        body["receiptEvidenceDocumentVersionId"] = str(env.documents["receipt_evidence"].id)
    return {"ai_pre": "ai", "reserve": "admin", "receipt": "recipient"}[kind], body


def _create(env, actor, body, key, index=0):
    return env.client.post(f"/v2/procurements/{env.procurements[index].id}/signing-requests",
                           json=body, headers=auth(env.tokens[actor], key))


@pytest.mark.parametrize("kind", ["ai_pre", "reserve", "receipt"])
@pytest.mark.parametrize("signed", [False, True])
def test_expired_unsubmitted_request_renews_without_deleting_history(route_env, kind, signed):
    env = route_env
    actor, body = _setup(env, kind)
    first = _create(env, actor, body, "old").json()["signingRequest"]
    old_signature = env.gate.sign_typed_data(first["signer"], first["typedData"])
    if signed:
        response = env.client.post(f"/v2/signing-requests/{first['id']}/sign-demo", json={"confirm": True},
                                   headers=auth(env.tokens[actor], "old-sign"))
        assert response.status_code == 202, response.text
    env.gate.now = int(first["deadline"]) + 1
    response = _create(env, actor, body, "renew")
    assert response.status_code == 202, response.text
    second = response.json()["signingRequest"]
    assert second["id"] != first["id"] and second["nonce"] == first["nonce"]
    assert int(second["deadline"]) > int(first["deadline"])
    assert second["digest"] != first["digest"]
    history = env.client.get(f"/v2/signing-requests/{first['id']}", headers=auth(env.tokens[actor]))
    assert history.status_code == 200 and history.json()["status"] == "expired"
    replay = _create(env, actor, body, "old")
    assert replay.status_code == 202 and replay.json()["signingRequest"]["id"] == first["id"]
    assert replay.json()["signingRequest"]["status"] == "expired"
    old_submit = env.client.post(f"/v2/signing-requests/{first['id']}/submit", json={"signature": old_signature},
                                headers=auth(env.tokens[actor], "old-submit"))
    assert old_submit.status_code == 409
    wrong_digest = env.client.post(f"/v2/signing-requests/{second['id']}/submit", json={"signature": old_signature},
                                  headers=auth(env.tokens[actor], "renew-old-signature"))
    assert wrong_digest.status_code == 422 and wrong_digest.json()["error"]["code"] == "signature_invalid"
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("kind", ["ai_pre", "reserve", "receipt"])
def test_equal_deadline_remains_live_and_cannot_renew(route_env, kind):
    env = route_env
    actor, body = _setup(env, kind)
    first = _create(env, actor, body, "old").json()["signingRequest"]
    env.gate.now = int(first["deadline"])
    response = _create(env, actor, body, "too-early")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "signing_nonce_in_use"


@pytest.mark.parametrize("kind", ["ai_pre", "reserve", "receipt"])
def test_changed_frozen_material_invalidates_only_unsubmitted_history(route_env, kind):
    env = route_env
    actor, body = _setup(env, kind)
    first = _create(env, actor, body, "old").json()["signingRequest"]
    if kind == "ai_pre":
        env.gate.procurements[env.procurements[0].business_id][7] = bytes.fromhex("bb" * 32)
    elif kind == "reserve":
        env.gate.policy_epoch += 1
    else:
        env.gate.procurements[env.procurements[0].business_id][10] = bytes.fromhex("bb" * 32)
    response = _create(env, actor, body, "renew-stale")
    assert response.status_code == 202, response.text
    assert response.json()["signingRequest"]["digest"] != first["digest"]
    with env.session_factory() as session:
        assert session.get(SigningRequest, UUID(first["id"])).status == "invalidated_stale"


@pytest.mark.parametrize("kind", ["ai_pre", "reserve", "receipt"])
@pytest.mark.parametrize("status", ["queued", "requires_attention", "confirmed", "failed"])
def test_submitted_or_unknown_request_guard_never_expires(route_env, kind, status):
    env = route_env
    actor, body = _setup(env, kind)
    first = _create(env, actor, body, "old").json()["signingRequest"]
    with env.session_factory() as session, session.begin():
        request = session.get(SigningRequest, UUID(first["id"]))
        request.status = status
        request.submitted_operation_id = request.operation_id
    env.gate.now = int(first["deadline"]) + 1
    response = _create(env, actor, body, "cannot-renew-submitted")
    assert response.status_code == 409 and response.json()["error"]["code"] == "signing_nonce_in_use"
    with env.session_factory() as session:
        assert session.get(SigningRequest, UUID(first["id"])).status == status


@pytest.mark.parametrize("kind", ["ai_pre", "reserve"])
def test_nonce_family_competes_across_procurements_atomically(route_env, kind):
    env = route_env
    actor, body = _setup(env, kind)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: _create(env, actor, body, f"parallel-{index}", index), range(2)))
    assert sorted(item.status_code for item in results) == [202, 409]
    assert next(item for item in results if item.status_code == 409).json()["error"]["code"] == "signing_nonce_in_use"
    with env.session_factory() as session:
        assert len(session.scalars(select(SigningRequest).where(SigningRequest.status == "prepared")).all()) == 1


@pytest.mark.parametrize("kind,enum,state", [("ai_pre", 2, "pre_assessed"), ("ai_pre", 3, "reserve_approval_pending"),
                                             ("reserve", 3, "reserve_approval_pending")])
def test_consumed_chain_nonce_can_form_new_authorization_in_renewal_states(route_env, kind, enum, state):
    env = route_env
    actor, body = _setup(env, kind)
    old = _create(env, actor, body, "old").json()["signingRequest"]
    with env.session_factory() as session, session.begin():
        row = session.get(SigningRequest, UUID(old["id"]))
        row.status, row.submitted_operation_id = "confirmed", row.operation_id
    function = "aiNonces" if kind == "ai_pre" else "humanNonces"
    env.gate.nonces[function] = 1
    env.gate.now = int(old["deadline"]) + 1
    env.states(procurement=state, chain_enum=enum)
    response = _create(env, actor, body, "new-consumed")
    assert response.status_code == 202, response.text
    assert response.json()["signingRequest"]["nonce"] == "1"
    with env.session_factory() as session:
        assert session.get(SigningRequest, UUID(old["id"])).status == "confirmed"


@pytest.mark.parametrize("uploader", ["foundation", "other_recipient"])
def test_receipt_evidence_requires_this_recipient_as_uploader(route_env, uploader):
    env = route_env
    actor, body = _setup(env, "receipt")
    with env.session_factory() as session, session.begin():
        session.get(DocumentVersion, env.documents["receipt_evidence"].id).uploaded_by_user_id = UUID(env.users[uploader]["id"])
    response = _create(env, actor, body, "wrong-uploader")
    assert response.status_code == 403 and response.json()["error"]["code"] == "receipt_evidence_uploader_mismatch"
    with env.session_factory() as session:
        assert not session.scalars(select(SigningRequest)).all()
        assert not session.scalars(select(Operation).where(Operation.operation_kind == "signing_request.receipt")).all()


@pytest.mark.parametrize("outcome", [1, 2])
def test_review_or_reject_is_risk_evidence_not_an_api_veto(route_env, outcome):
    env = route_env
    actor, body = _setup(env, "reserve")
    assessment_id = "0x" + env.gate.procurements[env.procurements[0].business_id][8].hex()
    env.gate.assessments[assessment_id][3] = outcome
    no_vote = env.client.post(f"/v2/procurements/{env.procurements[0].id}/chain/reserve",
                              json={"reserveAmountAtomic": "60000000"}, headers=auth(env.tokens["foundation"], "no-human-vote"))
    assert no_vote.status_code == 409
    response = _create(env, actor, body, "human-choice")
    assert response.status_code == 202, response.text
    request = response.json()["signingRequest"]
    signature = env.gate.sign_typed_data(request["signer"], request["typedData"])
    submitted = env.client.post(f"/v2/signing-requests/{request['id']}/submit", json={"signature": signature},
                               headers=auth(env.tokens[actor], "human-vote"))
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["operation"]["status"] == "queued"
    # HTTP queues explicit human authority; these fake getters do not prove funds moved.
    assert env.gate.broadcast_calls == 0


def test_revoked_assessment_signer_is_rejected_without_outcome_policy(route_env):
    env = route_env
    actor, body = _setup(env, "reserve")
    env.gate.ai_signer_allowed = False
    response = _create(env, actor, body, "revoked-ai-signer")
    assert response.status_code == 409 and response.json()["error"]["code"] == "signing_material_stale"


@pytest.mark.parametrize("preparation_only", [False, True])
def test_positive_unbroadcast_proof_allows_explicit_new_request_not_unknown(route_env, preparation_only):
    env = route_env
    actor, body = _setup(env, "reserve")
    old = _create(env, actor, body, "old").json()["signingRequest"]
    signature = env.gate.sign_typed_data(old["signer"], old["typedData"])
    submitted = env.client.post(f"/v2/signing-requests/{old['id']}/submit", json={"signature": signature},
                                headers=auth(env.tokens[actor], "old-submit"))
    assert submitted.status_code == 202, submitted.text
    operation_id = UUID(submitted.json()["operation"]["operationId"])
    with env.session_factory() as session, session.begin():
        request = session.get(SigningRequest, UUID(old["id"]))
        operation = session.get(Operation, operation_id)
        step = session.scalar(select(OperationStep).where(OperationStep.operation_id == operation_id))
        request.status = operation.status = step.status = "failed"
        operation.error_code = "chain_preparation_rejected" if preparation_only else "chain_not_broadcast"
        if not preparation_only:
            session.add(ChainTransaction(
                operation_id=operation.id, step_id=step.id, namespace_id=env.namespace_id,
                caller_address=env.gate.roles["relayer"], evm_nonce_text="7", status="not_broadcast", tx_hash=None,
            ))
    env.gate.now = int(old["deadline"]) + 1
    response = _create(env, actor, body, "explicit-renew-after-proof")
    assert response.status_code == 202, response.text
    assert response.json()["signingRequest"]["nonce"] == old["nonce"]
    with env.session_factory() as session:
        historical = session.get(SigningRequest, UUID(old["id"]))
        assert historical.status == "invalidated_not_broadcast"
        assert historical.submitted_operation_id == operation_id
