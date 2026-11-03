"""Connection-free regressions; no DB reset, real signature or broadcast proof."""
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import pog_api.offline_demo as offline
from pog_api.config import Settings
from pog_api.errors import APIError, install_error_handlers
from pog_api.models import Operation, SigningRequest
from pog_api.security import Principal


def bundle(tmp_path):
    path = tmp_path / "samples.private.json"
    path.write_text(json.dumps({"schema": "pog-offline-report-samples-v1", "actualModelExecuted": False,
        "reports": {stage: {"title": stage + " sample", "markdown": "Historical mock " + stage,
            "sampleRiskScoreBps": score} for stage, score in (("PRE", 1600), ("FINAL", 1000))}}))
    return path


def test_bundle_keeps_historical_sample_separate_from_fixture(tmp_path):
    value = offline.load_reports(bundle(tmp_path))
    assert value["PRE"]["sampleRiskScoreBps"] == 1600
    assert value["FINAL"]["sampleRiskScoreBps"] == 1000
    assert all(row["actualModelExecuted"] is False and row["context"] == "historical_reference_not_current_evidence"
               for row in value.values())
    assert value["PRE"]["sourceSha256"] == "0x" + hashlib.sha256(value["PRE"]["markdown"].encode()).hexdigest()


@pytest.mark.parametrize("fault", ("missing", "relative", "model", "schema", "score"))
def test_bundle_rejects_invalid_or_model_mislabelled_input(tmp_path, fault):
    path = bundle(tmp_path)
    value = json.loads(path.read_text())
    if fault == "missing": path = tmp_path / "missing.json"
    elif fault == "relative": path = Path("samples.json")
    else:
        if fault == "model": value["actualModelExecuted"] = True
        if fault == "schema": value["schema"] = "model-result"
        if fault == "score": value["reports"]["PRE"]["sampleRiskScoreBps"] = True
        path.write_text(json.dumps(value))
    with pytest.raises((OSError, ValueError)):
        offline.load_reports(path)


def test_opt_in_defaults_and_exact_confirmation():
    settings = Settings(database_url="not-connected", storage_root=Path("/private/tmp/not-used"))
    assert settings.offline_demo_enabled is False
    for value in (False, 1, "true", None):
        with pytest.raises(ValueError):
            offline.OfflineCreate(stage="PRE", confirm=value, expectedNamespaceId=str(uuid4()),
                expectedRunId="run", expectedInstanceId="instance", chainId="31337")


@pytest.fixture
def route(tmp_path, monkeypatch):
    ns = SimpleNamespace(id=uuid4(), run_id="test-run", instance_id="test-instance", chain_id=31337)
    principal = Principal(uuid4(), "foundation", "foundation", "0x" + "01" * 20, uuid4())
    ai_user = SimpleNamespace(id=uuid4(), username="service-ai-fixture")
    ai_wallet = SimpleNamespace(wallet_address="0x" + "02" * 20)
    project = SimpleNamespace(id=uuid4(), foundation_user_id=principal.user_id,
        recipient_user_id=uuid4(), human_approver_user_id=uuid4())
    procurement = SimpleNamespace(id=uuid4(), project_id=project.id, business_id="0x" + "03" * 32,
        source_versions_json={"poDocumentVersionId": str(uuid4())})
    state = SimpleNamespace(operations={}, requests={}, calls=[], role_rows=[(ai_user, ai_wallet)],
        fail_at=None, fail_once=False, source_valid=True)
    class Session:
        def begin(self): return nullcontext()
        def get(self, model, identifier, **kwargs):
            return state.operations.get(identifier) if model is Operation else state.requests.get(identifier)
        def execute(self, query): return SimpleNamespace(all=lambda: state.role_rows)
        def scalars(self, query): return SimpleNamespace(all=lambda: list(state.requests.values()))
    session = Session()
    class Gate:
        roles = {"foundation": principal.wallet_address, "aiSigner": ai_wallet.wallet_address}
        def verify(self): pass
        def call(self, *args): return True
        def contract_address(self, name): return "0x" + "04" * 20
    gate = Gate()
    def begin_operation(session, **kwargs):
        encoded = json.dumps(kwargs["validated_payload"], sort_keys=True)
        for row in state.operations.values():
            if row.operation_kind == kwargs["operation_kind"] and row.idempotency_key == kwargs["idempotency_key"]:
                if row.payload_hash != encoded: raise APIError(409, "idempotency_payload_conflict", "Changed payload")
                return row, True
        row = SimpleNamespace(id=uuid4(), namespace_id=ns.id, principal_id=kwargs["principal_id"],
            operation_kind=kwargs["operation_kind"], idempotency_key=kwargs["idempotency_key"],
            payload_hash=encoded, status="awaiting_authorization", result_resource_id=None,
            result_resource_type=None, error_code=None, error_status=None, error_detail=None)
        state.operations[row.id] = row
        return row, False
    def sources(*args, **kwargs):
        if not state.source_valid: raise APIError(409, "offline_demo_source_unproven", "Sources unavailable")
    def perform(action):
        def handler(target, body, actor, session, key):
            assert actor.role == "service_ai" and actor.user_id != principal.user_id
            state.calls.append((action, actor.user_id, key))
            if state.fail_at == action and not state.fail_once:
                state.fail_once = True
                raise APIError(503, "dependency_unavailable", "Simulated interruption")
            if action == "prepare":
                kind = body.kind
                request = next((r for r in state.requests.values() if r.kind == kind), None)
                if request is None:
                    request = SimpleNamespace(id=uuid4(), kind=kind, status="prepared", context_json={},
                        submitted_operation_id=None, nonce_text="0", deadline_text="4600", typed_data={"message": {
                            "riskScoreBps":100, "reportHash":"0x" + "05"*32, "evidenceHash":"0x" + "06"*32}})
                    state.requests[request.id] = request
                return {"signingRequest": {"id": str(request.id)}}
            request = state.requests[target]
            if action == "sign": request.status = "signed"
            if action == "submit":
                if request.submitted_operation_id is None:
                    op, _ = begin_operation(session, principal_id=actor.user_id, operation_kind="signing_request.submit_signed."+request.kind,
                        idempotency_key=key, validated_payload={"requestId": str(target)})
                    op.status, op.result_resource_id = "queued", request.id
                    op.result_resource_type = "signing_request"
                    request.submitted_operation_id, request.status = op.id, "queued"
                return {"operation": offline._operation(state.operations[request.submitted_operation_id], False)}
            return {}
        return handler
    monkeypatch.setattr(offline, "_procurement", lambda *args: (procurement, project))
    monkeypatch.setattr(offline, "begin_operation", begin_operation)
    monkeypatch.setattr(offline, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(offline, "validate_pre_sources", sources)
    monkeypatch.setattr(offline, "validate_original_sources", sources)
    settings = Settings(database_url="not-connected", storage_root=tmp_path, offline_demo_enabled=True,
        full_demo_enabled=True, demo_signing_enabled=True, offline_demo_reports_file=bundle(tmp_path))
    def app_for(settings_override=None):
        app = FastAPI()
        install_error_handlers(app)
        offline.install_offline_demo_routes(app, get_session=lambda: session, current_principal=lambda: principal,
            namespace=lambda *args, **kwargs: ns, gateway=gate, gateway_error=None,
            settings=settings_override or settings, file_store=None,
            signing_actions={name: perform(name) for name in ("prepare", "sign", "submit")})
        return TestClient(app, raise_server_exceptions=True)
    return SimpleNamespace(client=app_for(), app_for=app_for, settings=settings, state=state, principal=principal,
        ai_user=ai_user, project=project, ns=ns, procurement=procurement,
        path=f"/v2/procurements/{procurement.id}/offline-demo-ai", body={"stage":"PRE","confirm":True,
            "expectedNamespaceId":str(ns.id),"expectedRunId":ns.run_id,"expectedInstanceId":ns.instance_id,"chainId":"31337"})


@pytest.mark.parametrize("stage", ("PRE", "FINAL"))
def test_explicit_action_uses_separate_ai_and_only_assessment_submission(route, stage):
    response = route.client.post(route.path, json={**route.body,"stage":stage}, headers={"Idempotency-Key":"attempt"})
    assert response.status_code == 202
    body = response.json()
    assert [row[0] for row in route.state.calls] == ["prepare","sign","submit"]
    assert body["assessment"]["technicalRiskScoreBps"] == 100
    assert body["reports"][stage]["sampleRiskScoreBps"] == (1600 if stage == "PRE" else 1000)
    assert body["operation"]["status"] == "queued" and body["operation"]["chainVerified"] is False
    assert body["actualModelExecuted"] is False
    assert body["items"] == [body["assessment"]]
    assert not any(word in response.text for word in ('"signature"','"bearerToken"','"privateKey"'))
    replay = route.client.post(route.path, json={**route.body,"stage":stage}, headers={"Idempotency-Key":"attempt"})
    assert replay.status_code == 202 and replay.json()["operation"]["replayed"] is True
    assert len(route.state.requests) == 1 and len(route.state.calls) == 3


@pytest.mark.parametrize("fault", ("disabled","signing","binding","role","identity","source","confirm"))
def test_refusals_cannot_sign_or_queue(route, fault):
    client, body = route.client, dict(route.body)
    if fault == "disabled": client = route.app_for(replace(route.settings,offline_demo_enabled=False))
    if fault == "signing": client = route.app_for(replace(route.settings,demo_signing_enabled=False))
    if fault == "binding": body["expectedInstanceId"] = "old-instance"
    if fault == "role": route.project.foundation_user_id = uuid4()
    if fault == "identity": route.state.role_rows = []
    if fault == "source": route.state.source_valid = False
    if fault == "confirm": body["confirm"] = "true"
    response = client.post(route.path,json=body,headers={"Idempotency-Key":"attempt"})
    assert response.status_code in (403,409,422)
    assert not route.state.calls and not route.state.requests


@pytest.mark.parametrize("at", ("prepare","sign","submit"))
def test_interrupted_action_resumes_same_intent_and_phase_keys(route, at):
    route.state.fail_at = at
    response = route.client.post(route.path,json=route.body,headers={"Idempotency-Key":"attempt"})
    assert response.status_code == 503
    response = route.client.post(route.path,json=route.body,headers={"Idempotency-Key":"attempt"})
    assert response.status_code == 202
    assert len(route.state.requests) == 1
    wrappers = [op for op in route.state.operations.values() if op.operation_kind.startswith("offline_demo.")]
    assert len(wrappers) == 1
    for phase in ("prepare","sign","submit"):
        assert len({key for name, _, key in route.state.calls if name == phase}) == 1


def test_same_key_different_stage_payload_conflicts(route):
    route.client.post(route.path,json=route.body,headers={"Idempotency-Key":"attempt"})
    # Same operation family but a changed frozen expected run is rejected before signing.
    response = route.client.post(route.path,json={**route.body,"expectedRunId":"old"},headers={"Idempotency-Key":"attempt"})
    assert response.status_code == 409 and len(route.state.calls) == 3


def test_get_reuses_contract_without_exposing_ai_signature(route):
    route.client.post(route.path,json=route.body,headers={"Idempotency-Key":"attempt"})
    response = route.client.get(route.path)
    assert response.status_code == 200
    assert response.json()["items"][0]["operation"]["status"] == "queued"
    assert response.json()["reports"]["PRE"]["provenance"] == "mock_demo"
    assert not any(word in response.text for word in ('"signature"','"bearerToken"','"privateKey"'))
    route.project.foundation_user_id = uuid4()
    assert route.client.get(route.path).status_code == 403


@pytest.mark.parametrize("fault", (None,"bytes","uploader","binding","referenced","missing"))
def test_selected_original_sources_verify_actual_bytes_and_owner(tmp_path, monkeypatch, fault):
    rows = {}
    owner_id = uuid4()
    source = {"bindings": {}}
    procurement = SimpleNamespace(source_versions_json=source)
    for index,(name,category,field) in enumerate((
        ("poDocumentVersionId","purchase_order","po_hash"),
        ("requestDocumentVersionId","request","request_hash"),
        ("goodsRequestDocumentVersionId","goods_request","goods_request_hash"),
        ("invoiceDocumentVersionId","invoice","invoice_hash"),
        ("goodsDocumentVersionId","goods_evidence","goods_hash"),
    )):
        raw = ("original-" + category).encode()
        path = tmp_path / (category + ".png")
        path.write_bytes(raw)
        row = SimpleNamespace(id=uuid4(), uploaded_by_user_id=owner_id, referenced=True,
            size_bytes=len(raw), sha256_hex=hashlib.sha256(raw).hexdigest(),
            keccak256_hex=offline.keccak256(raw), storage_key=category + ".png")
        source[name] = str(row.id)
        source["bindings"][name] = {"versionId":str(row.id),"hash":row.keccak256_hex,"uploaderId":str(owner_id)}
        setattr(procurement,field,"0x"+row.keccak256_hex)
        rows[row.id] = row
    monkeypatch.setattr(offline,"_document_version",lambda session,procurement,identifier,category:rows[identifier])
    row = rows[UUID(source["poDocumentVersionId"])]
    if fault == "bytes": (tmp_path / row.storage_key).write_bytes(b"changed")
    if fault == "uploader": row.uploaded_by_user_id = uuid4()
    if fault == "binding": source["bindings"]["poDocumentVersionId"]["hash"] = "aa"*32
    if fault == "referenced": row.referenced = False
    if fault == "missing": (tmp_path / row.storage_key).unlink()
    args = (None, procurement, SimpleNamespace(foundation_user_id=owner_id),
            SimpleNamespace(resolve=lambda key:tmp_path / key))
    if fault:
        with pytest.raises(APIError) as exc:
            offline.validate_pre_sources(*args,include_final=True)
        assert exc.value.code == "offline_demo_source_unproven"
    else:
        offline.validate_pre_sources(*args,include_final=True)
