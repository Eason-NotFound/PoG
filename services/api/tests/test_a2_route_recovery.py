"""HTTP/PG recovery tests; the chain and its availability are explicit fakes."""
from __future__ import annotations

from contextlib import nullcontext
from io import BytesIO
import hashlib
import json
from uuid import UUID

from eth_account import Account
from eth_utils import keccak
from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest
from sqlalchemy import func, select

from _route_helpers import auth, route_env
from pog_api.app import create_app
from pog_api.chain import ChainMismatch
from pog_api.hashing import payload_sha256
from pog_api.models import DeploymentInstance, DocumentVersion, Operation, OperationStep, Project, SigningRequest, WalletAuthorization
from pog_api.schemas import SigningRequestCreate


CASES = (
    "project_create", "donation", "procurement_create", "purchase_order", "invoice_goods",
    "reserve_execute", "sign_ai", "sign_reserve", "sign_receipt",
)


def _case(env, name, index=0):
    project, procurement = env.projects[index], env.procurements[index]
    documents = env.documents
    specs = {
        "project_create": ("foundation", f"/v2/projects/{project.id}/chain/create", {}, "off_chain_draft", "created", 0),
        "donation": ("donor", f"/v2/projects/{project.id}/donations", {"amountAtomic": "50000000"}, "active", "created", 0),
        "procurement_create": ("foundation", f"/v2/procurements/{procurement.id}/chain/create", {}, "active", "off_chain_draft", 0),
        "purchase_order": ("foundation", f"/v2/procurements/{procurement.id}/chain/purchase-order", {
            "poDocumentVersionId": str(documents["purchase_order"].id),
            "requestDocumentVersionId": str(documents["request"].id),
            "goodsRequestDocumentVersionId": str(documents["goods_request"].id),
        }, "active", "created", 0),
        "invoice_goods": ("foundation", f"/v2/procurements/{procurement.id}/chain/invoice-and-goods", {
            "invoiceDocumentVersionId": str(documents["invoice"].id),
            "goodsDocumentVersionId": str(documents["goods_evidence"].id), "invoiceAmountAtomic": "50000000",
        }, "active", "reserved", 4),
        "reserve_execute": ("foundation", f"/v2/procurements/{procurement.id}/chain/reserve", {
            "reserveAmountAtomic": "60000000",
        }, "active", "reserve_approval_pending", 3),
        "sign_ai": ("ai", f"/v2/procurements/{procurement.id}/signing-requests", {"kind": "ai_pre"}, "active", "po_recorded", 1),
        "sign_reserve": ("admin", f"/v2/procurements/{procurement.id}/signing-requests", {
            "kind": "reserve", "reserveAmountAtomic": "60000000",
        }, "active", "pre_assessed", 2),
        "sign_receipt": ("recipient", f"/v2/procurements/{procurement.id}/signing-requests", {
            "kind": "receipt", "receiptEvidenceDocumentVersionId": str(documents["receipt_evidence"].id),
        }, "active", "invoice_recorded", 5),
    }
    return specs[name]


def _first(env, name, key="route-original"):
    actor, path, body, project_status, procurement_status, enum = _case(env, name)
    env.states(project=project_status, procurement=procurement_status, chain_enum=enum)
    response = env.client.post(path, json=body, headers=auth(env.tokens[actor], key))
    assert response.status_code == 202, response.text
    return actor, path, body, response.json()


def _advance(env, result):
    env.states(project="closing", procurement="receipt_confirmed", chain_enum=6)
    with env.session_factory() as session, session.begin():
        operation = session.get(Operation, UUID(result["operation"]["operationId"]))
        operation.status = "confirmed"
        if "signingRequest" in result:
            session.get(SigningRequest, UUID(result["signingRequest"]["id"])).status = "expired"


def _counts(env):
    with env.session_factory() as session:
        return tuple(session.scalar(select(func.count(model.id))) for model in (Operation, OperationStep, SigningRequest))


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
            session.add(WalletAuthorization(user_id=wallet.user_id, role_name="recipient" if wallet.role_name != "recipient" else "donor",
                                             wallet_address=Account.create().address, active=True))
        elif change == "wrong_role":
            wallet.role_name = "donor" if actor != "donor" else "foundation"
        elif change == "owner":
            project = session.get(Project, env.projects[0].id)
            if actor == "foundation":
                project.foundation_user_id = UUID(env.users["other_foundation"]["id"])
            elif actor == "admin":
                project.human_approver_user_id = UUID(env.users["other_human"]["id"])
            elif actor == "recipient":
                project.recipient_user_id = UUID(env.users["other_recipient"]["id"])
            else:
                raise AssertionError("Only designated project members have ownership to revoke")
        else:
            raise AssertionError(change)


@pytest.mark.parametrize("name", CASES)
def test_every_chain_resource_mutation_binds_target_and_replays_after_advance_offline(route_env, name):
    env = route_env
    actor, path, body, original = _first(env, name)
    counts = _counts(env)
    second_path = _case(env, name, 1)[1]
    conflict = env.client.post(second_path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_payload_conflict"
    assert _counts(env) == counts
    _advance(env, original)
    env.gate.mode = "offline"
    replay = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation"]["operationId"] == original["operation"]["operationId"]
    assert replay.json()["operation"]["replayed"] is True
    if "signingRequest" in original:
        assert replay.json()["signingRequest"]["id"] == original["signingRequest"]["id"]
        assert replay.json()["signingRequest"]["digest"] == original["signingRequest"]["digest"]
        assert replay.json()["signingRequest"]["status"] == "expired"
    new_request = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-new"))
    assert new_request.status_code == 503, new_request.text
    conflict = env.client.post(second_path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_payload_conflict"
    assert _counts(env) == counts and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("name", CASES)
def test_legacy_body_hash_replay_requires_saved_same_target(route_env, name):
    env = route_env
    actor, path, body, original = _first(env, name)
    normalized = SigningRequestCreate.model_validate(body).model_dump(mode="json", by_alias=True) if name.startswith("sign_") else body
    with env.session_factory() as session, session.begin():
        session.get(Operation, UUID(original["operation"]["operationId"])).payload_hash = payload_sha256(normalized)
    counts = _counts(env)
    _advance(env, original)
    env.gate.mode = "offline"
    replay = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation"]["operationId"] == original["operation"]["operationId"]
    conflict = env.client.post(_case(env, name, 1)[1], json=body, headers=auth(env.tokens[actor], "route-original"))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_payload_conflict"
    assert _counts(env) == counts


@pytest.mark.parametrize("name", CASES)
def test_hank_published_hash_replays_without_rewriting_saved_facts(route_env, name):
    env = route_env
    actor, path, body, original = _first(env, name)
    resource = env.projects[0] if name in {"project_create", "donation"} else env.procurements[0]
    normalized = SigningRequestCreate.model_validate(body).model_dump(mode="json", by_alias=True) if name.startswith("sign_") else body
    published_payload = (
        {"procurementId": str(resource.id), "body": normalized}
        if name.startswith("sign_") else
        {"resourceType": "project" if name in {"project_create", "donation"} else "procurement",
         "resourceId": str(resource.id), "body": normalized}
    )
    published_hash = payload_sha256(published_payload)
    operation_id = UUID(original["operation"]["operationId"])
    with env.session_factory() as session, session.begin():
        session.get(Operation, operation_id).payload_hash = published_hash
    counts = _counts(env)
    _advance(env, original)
    env.gate.mode = "offline"
    replay = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation"]["operationId"] == str(operation_id)
    assert replay.json()["operation"]["replayed"] is True
    wrong_target = env.client.post(_case(env, name, 1)[1], json=body, headers=auth(env.tokens[actor], "route-original"))
    assert wrong_target.status_code == 409, wrong_target.text
    assert wrong_target.json()["error"]["code"] == "idempotency_payload_conflict"
    with env.session_factory() as session:
        assert session.get(Operation, operation_id).payload_hash == published_hash
    assert _counts(env) == counts and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("name", CASES)
def test_scoped_hash_also_requires_independent_persisted_target(route_env, name):
    env = route_env
    actor, path, body, original = _first(env, name)
    with env.session_factory() as session, session.begin():
        operation = session.get(Operation, UUID(original["operation"]["operationId"]))
        operation.result_resource_id = env.procurements[1].id if name not in {"project_create", "donation"} else env.projects[1].id
    counts = _counts(env)
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "idempotency_payload_conflict"
    assert _counts(env) == counts and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("name", CASES)
def test_resource_mutation_replay_does_not_authorize_changed_current_wallet(route_env, name):
    env = route_env
    actor, path, body, _original = _first(env, name)
    counts = _counts(env)
    _change_identity(env, actor, "changed")
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert denied.status_code in {403, 409}, denied.text
    assert _counts(env) == counts and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("name", ["project_create", "donation", "reserve_execute", "sign_ai", "sign_reserve", "sign_receipt"])
def test_resource_replay_checks_current_role_before_result(route_env, name):
    env = route_env
    actor, path, body, _original = _first(env, name)
    counts = _counts(env)
    _change_identity(env, actor, "wrong_role")
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert denied.status_code == 403, denied.text
    assert _counts(env) == counts


@pytest.mark.parametrize("name", ["project_create", "procurement_create", "sign_reserve", "sign_receipt"])
def test_resource_replay_checks_current_project_ownership(route_env, name):
    env = route_env
    actor, path, body, _original = _first(env, name)
    counts = _counts(env)
    _change_identity(env, actor, "owner")
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert denied.status_code == 403, denied.text
    assert _counts(env) == counts


@pytest.mark.parametrize("action", ["sign-demo", "submit"])
def test_signature_mutation_binds_request_and_replays_original_while_offline(route_env, action):
    env = route_env
    actor, _path, _body, original = _first(env, "sign_ai", key="create-ai")
    request = original["signingRequest"]
    body = {"confirm": True} if action == "sign-demo" else {"signature": env.gate.sign_typed_data(request["signer"], request["typedData"])}
    path = f"/v2/signing-requests/{request['id']}/{action}"
    first = env.client.post(path, json=body, headers=auth(env.tokens[actor], "signature-original"))
    assert first.status_code == 202, first.text
    env.gate.nonces["aiNonces"] = 1
    second = env.client.post(f"/v2/procurements/{env.procurements[1].id}/signing-requests",
                            json={"kind": "ai_pre"}, headers=auth(env.tokens[actor], "create-ai-second"))
    assert second.status_code == 202, second.text
    second_path = f"/v2/signing-requests/{second.json()['signingRequest']['id']}/{action}"
    counts = _counts(env)
    sign_calls = env.gate.demo_sign_calls
    env.gate.mode = "offline"
    conflict = env.client.post(second_path, json=body, headers=auth(env.tokens[actor], "signature-original"))
    assert conflict.status_code == 409, conflict.text
    replay = env.client.post(path, json=body, headers=auth(env.tokens[actor], "signature-original"))
    assert replay.status_code == 202, replay.text
    assert replay.json()["operation"]["operationId"] == first.json()["operation"]["operationId"]
    assert replay.json()["operation"]["replayed"] is True
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "signature-new"))
    assert denied.status_code == 503, denied.text
    assert _counts(env) == counts and env.gate.demo_sign_calls == sign_calls and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("action", ["sign-demo", "submit"])
@pytest.mark.parametrize("change", ["inactive", "changed", "ambiguous", "wrong_role", "owner"])
def test_sign_or_submit_replay_rechecks_current_identity_role_wallet_and_owner(route_env, action, change):
    env = route_env
    actor, _path, _body, original = _first(env, "sign_reserve", key="create-reserve")
    request = original["signingRequest"]
    body = {"confirm": True} if action == "sign-demo" else {"signature": env.gate.sign_typed_data(request["signer"], request["typedData"])}
    path = f"/v2/signing-requests/{request['id']}/{action}"
    first = env.client.post(path, json=body, headers=auth(env.tokens[actor], "signature-original"))
    assert first.status_code == 202, first.text
    counts = _counts(env)
    sign_calls = env.gate.demo_sign_calls
    _change_identity(env, actor, change)
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "signature-original"))
    assert denied.status_code in {403, 409}, denied.text
    assert _counts(env) == counts and env.gate.demo_sign_calls == sign_calls and env.gate.broadcast_calls == 0


@pytest.mark.parametrize("signature", ["0x" + "gg" * 65, "0x" + "00" * 65,
                                       "0x" + "01" * 64 + "00", "0x" + "ff" * 64 + "1b",
                                       "0x" + "01" * 64 + "1b"])
def test_malformed_or_wrong_eoa_signature_is_stable_422_and_never_queues(route_env, signature):
    env = route_env
    actor, _path, _body, original = _first(env, "sign_reserve", key="create-reserve")
    counts = _counts(env)
    request_id = original["signingRequest"]["id"]
    denied = env.client.post(f"/v2/signing-requests/{request_id}/submit", json={"signature": signature},
                            headers=auth(env.tokens[actor], "bad-signature"))
    assert denied.status_code == 422, denied.text
    assert denied.json()["error"]["code"] == "signature_invalid"
    assert _counts(env) == counts and env.gate.broadcast_calls == 0
    with env.session_factory() as session:
        request = session.get(SigningRequest, UUID(request_id))
        assert request.status == "prepared" and request.signature is None and request.submitted_operation_id is None


@pytest.mark.parametrize("change", ["inactive", "manifest_changed"])
def test_offline_replay_cannot_select_inactive_or_changed_deployment_snapshot(route_env, change):
    env = route_env
    actor, path, body, _original = _first(env, "project_create")
    if change == "inactive":
        with env.session_factory() as session, session.begin():
            session.get(DeploymentInstance, env.namespace_id).active = False
    else:
        env.manifest_path.write_bytes(env.manifest_path.read_bytes() + b"\n")
    env.gate.mode = "offline"
    denied = env.client.post(path, json=body, headers=auth(env.tokens[actor], "route-original"))
    assert denied.status_code == (409 if change == "inactive" else 503), denied.text


def _private_file(env):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    payload = output.getvalue()
    version = env.documents["purchase_order"]
    path = env.settings.storage_root / version.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    with env.session_factory() as session, session.begin():
        stored = session.get(DocumentVersion, version.id)
        stored.size_bytes = len(payload)
        stored.sha256_hex = hashlib.sha256(payload).hexdigest()
        stored.keccak256_hex = keccak(payload).hex()
    return version.document_id, payload


@pytest.mark.parametrize("failure", ["runtime_mismatch", "startup_mismatch", "runtime_offline"])
def test_fresh_readiness_fails_and_existing_offchain_draft_private_file_remain_available(route_env, monkeypatch, failure):
    env = route_env
    env.states(project="off_chain_draft", procurement="off_chain_draft", chain_enum=0)
    document_id, payload = _private_file(env)
    healthy = env.client.get("/ready")
    assert healthy.status_code == 200, healthy.text
    assert healthy.json()["deployment"]["mode"] == "a2_local_chain_verified"
    assert healthy.json()["deployment"]["verified"] is True
    if failure == "startup_mismatch":
        def fail_constructor(*_args):
            raise ChainMismatch("fake startup verification mismatch")
        monkeypatch.setattr("pog_api.app.LocalChainGateway", fail_constructor)
        context = TestClient(create_app(env.settings), raise_server_exceptions=False)
    else:
        env.gate.mode = "offline" if failure == "runtime_offline" else "mismatch"
        context = nullcontext(env.client)
    with context as client:
        readiness = client.get("/ready")
        assert readiness.status_code == 503, readiness.text
        assert readiness.json()["ready"] is False
        assert readiness.json()["deployment"]["verified"] is False
        project = client.get(f"/v2/projects/{env.projects[0].id}", headers=auth(env.tokens["foundation"]))
        assert project.status_code == 200, project.text
        assert project.json()["chainState"]["status"] == "off_chain_draft"
        metadata = client.get(f"/v2/documents/{document_id}", headers=auth(env.tokens["foundation"]))
        content = client.get(f"/v2/documents/{document_id}/content", headers=auth(env.tokens["recipient"]))
        assert metadata.status_code == content.status_code == 200
        assert content.content == payload
        donor = client.get(f"/v2/documents/{document_id}", headers=auth(env.tokens["donor"]))
        assert donor.status_code == 403
        chain_write = client.post(f"/v2/projects/{env.projects[0].id}/chain/create", json={},
                                  headers=auth(env.tokens["foundation"], "unavailable-chain-write"))
        assert chain_write.status_code == 503, chain_write.text
        draft = client.post("/v2/projects", json={"title": "Still available offline", "publicSummary": "Offchain only",
                             "recipientUserId": env.users["recipient"]["id"],
                             "humanApproverUserId": env.users["admin"]["id"]},
                            headers=auth(env.tokens["foundation"], "still-available-draft"))
        assert draft.status_code == 202, draft.text
    assert env.gate.broadcast_calls == 0


@pytest.mark.parametrize("shape", [
    [], {"runId": "run", "chain": []},
    {"runId": {}, "chain": {"instanceId": "instance"}},
    {"runId": "run", "chain": {"instanceId": {}}},
    {"runId": "", "chain": {"instanceId": "instance"}},
    {"runId": "run", "chain": {"instanceId": ""}},
])
def test_startup_invalid_manifest_shape_falls_back_to_a1_drafts_not_500(route_env, monkeypatch, shape):
    env = route_env
    env.manifest_path.write_text(json.dumps(shape), encoding="utf-8")

    def fail_constructor(*_args):
        raise ChainMismatch("invalid startup manifest identity")

    monkeypatch.setattr("pog_api.app.LocalChainGateway", fail_constructor)
    with TestClient(create_app(env.settings), raise_server_exceptions=False) as client:
        ready = client.get("/ready")
        assert ready.status_code == 503 and ready.json()["deployment"]["verified"] is False
        draft = client.post("/v2/projects", json={
            "title": "A1 fallback with invalid manifest", "publicSummary": "Offchain only",
            "recipientUserId": env.users["recipient"]["id"],
            "humanApproverUserId": env.users["admin"]["id"],
        }, headers=auth(env.tokens["foundation"], "invalid-manifest-draft"))
        assert draft.status_code == 202, draft.text
        project_id = draft.json()["project"]["id"]
        fetched = client.get(f"/v2/projects/{project_id}", headers=auth(env.tokens["foundation"]))
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["chainState"]["status"] == "off_chain_draft"
        denied = client.post(f"/v2/projects/{project_id}/chain/create", json={},
                             headers=auth(env.tokens["foundation"], "invalid-manifest-chain"))
        assert denied.status_code == 503, denied.text
