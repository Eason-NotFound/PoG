"""Connection-free diagnostic tests. Fixtures are synthetic bytes, not model results."""
from datetime import UTC, datetime
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError
import pytest

import pog_api
from pog_api.ai_diagnostic_core import DiagnosticCreate, SOURCE_ROLES, authorize, build_input, request_bytes, save_report
from pog_api.ai_protocol.verify_service import Frozen
from pog_api.errors import APIError
from pog_api.file_store import PrivateFileStore
from pog_api.hash_vectors import pre_evidence_hash
from pog_api.models import Document, DocumentVersion


def context():
    return {"quantity": "1", "quoteAmountAtomic": "80000000", "unitPriceLimitAtomic": "80000000",
            "category": "TEST_ONLY", "periodStart": "2026-10-01", "periodEnd": "2026-10-31",
            "description": "Synthetic unit-test context, not a real project policy"}


def body(po_id, **changes):
    return {"purchaseOrderVersionId": str(po_id), "diagnosticContext": context(), "contextSource": "demo_generated", "confirm": True, **changes}


@pytest.fixture
def frozen():
    return Frozen(Path(pog_api.__file__).parent / "ai_protocol" / "repoSnapshot")


@pytest.fixture
def env(tmp_path, frozen):
    ns = SimpleNamespace(id=uuid4(), run_id="test-only-run", instance_id="test-only-instance", chain_id=31337)
    project = SimpleNamespace(id=uuid4(), business_id="0x" + "11" * 32, foundation_user_id=uuid4(), human_approver_user_id=uuid4(),
                              foundation_wallet="0x" + "12" * 20, recipient_wallet="0x" + "13" * 20)
    procurement = SimpleNamespace(id=uuid4(), project_id=project.id, namespace_id=ns.id, business_id="0x" + "21" * 32,
                                 vendor_wallet="0x" + "22" * 20, budget_cap_atomic=80000000, source_versions_json={"bindings": {}})
    file_store = PrivateFileStore(tmp_path / "private")
    records, hashes = {}, {}
    for i, (source, category, role, _index, _kind) in enumerate(SOURCE_ROLES):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), (i * 60, 2, 4)).save(buffer, format="PNG")
        raw = buffer.getvalue()
        staged = file_store.stage(BytesIO(raw), "test.png", "image/png")
        key, _ = file_store.commit(staged, str(ns.id))
        doc_id, ver_id = uuid4(), uuid4()
        doc = SimpleNamespace(id=doc_id, procurement_id=procurement.id, namespace_id=ns.id, category=category)
        version = SimpleNamespace(id=ver_id, document_id=doc_id, referenced=True, uploaded_by_user_id=project.foundation_user_id,
                                  keccak256_hex=staged.keccak256_hex, sha256_hex=staged.sha256_hex,
                                  content_type="image/png", size_bytes=len(raw), storage_key=key, version=1)
        records[Document, doc_id], records[DocumentVersion, ver_id] = doc, version
        procurement.source_versions_json[source] = str(ver_id)
        procurement.source_versions_json["bindings"][source] = {"versionId": str(ver_id), "hash": staged.keccak256_hex,
                                                               "uploaderId": str(project.foundation_user_id)}
        hashes[role] = "0x" + staged.keccak256_hex
    asset = "0x" + "14" * 20
    evidence = pre_evidence_hash(project_id=project.business_id, procurement_id=procurement.business_id,
        foundation=project.foundation_wallet, recipient=project.recipient_wallet, vendor=procurement.vendor_wallet,
        asset=asset, budget_cap=80000000, po_hash=hashes["poHash"], request_hash=hashes["requestHash"], goods_request_hash=hashes["goodsRequestHash"])
    view = [bytes.fromhex(procurement.business_id[2:]), bytes.fromhex(project.business_id[2:]), procurement.vendor_wallet,
            80000000, *(bytes.fromhex(hashes[name][2:]) for name in ("poHash", "requestHash", "goodsRequestHash")), bytes.fromhex(evidence[2:])]
    view += [bytes(32)] * 13 + [1]
    project_view = [bytes.fromhex(project.business_id[2:]), project.foundation_wallet, project.recipient_wallet, asset, 6]
    head = {"number": 42, "hash": bytes.fromhex("33" * 32)}
    class Gate:
        w3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _tag: head))
        def call(self, _contract, function, _id, **kwargs):
            assert kwargs.get("block_identifier") == 42
            return view if function == "getProcurement" else project_view
        def canonical(self, number, block_hash):
            return number == 42 and block_hash == "0x" + "33" * 32
        def contract_address(self, _name):
            return "0x" + "34" * 20
    class Session:
        previous = None
        def get(self, cls, key):
            return records.get((cls, key))
        def scalar(self, _statement):
            return self.previous
    session, gate = Session(), Gate()
    po_id = procurement.source_versions_json["poDocumentVersionId"]
    request = DiagnosticCreate.model_validate(body(po_id))
    def build():
        return build_input(session, ns, procurement, project, request, gate, file_store, frozen)
    return SimpleNamespace(ns=ns, project=project, procurement=procurement, records=records, store=file_store,
                           session=session, gate=gate, view=view, request=request, build=build)


def test_missing_context_and_fake_confirmation_rejected():
    po = uuid4()
    for value in (False, 1, "true", None):
        with pytest.raises(ValidationError):
            DiagnosticCreate.model_validate(body(po, confirm=value))
    invalid = body(po)
    del invalid["diagnosticContext"]
    with pytest.raises(ValidationError):
        DiagnosticCreate.model_validate(invalid)


@pytest.mark.parametrize("key,value", [("quantity", "0"), ("quoteAmountAtomic", 80000000), ("unitPriceLimitAtomic", "1.5"),
                                      ("periodStart", "2026-10-31T00:00:00Z"), ("periodStart", "2026-11-01"), ("category", " ")])
def test_context_never_fills_missing_or_noncanonical_values(key, value):
    candidate = body(uuid4())
    candidate["diagnosticContext"][key] = value
    with pytest.raises(ValidationError):
        DiagnosticCreate.model_validate(candidate)


def test_private_diagnostic_scope_does_not_infer_admin_or_recipient_authority(env):
    for role, user_id in (("foundation", env.project.foundation_user_id), ("human_approver", env.project.human_approver_user_id)):
        authorize(env.project, SimpleNamespace(role=role, user_id=user_id))
    for role, user_id in (("foundation", uuid4()), ("human_approver", uuid4()), ("recipient", env.project.foundation_user_id),
                         ("donor", env.project.foundation_user_id), ("service_ai", env.project.foundation_user_id)):
        with pytest.raises(APIError) as exc:
            authorize(env.project, SimpleNamespace(role=role, user_id=user_id))
        assert exc.value.status_code == 403


def test_frozen_request_uses_real_namespace_and_three_original_commitments(env, frozen):
    value, resources, fingerprint, _created = env.build()
    request, wire = request_bytes(frozen, value, resources, uuid4(), uuid4())
    parsed, input_value, input_raw = frozen.request(wire)
    assert input_value == value
    assert parsed == request and parsed["attempt"] == 1
    assert parsed["payloadHash"] == "0x" + hashlib.sha256(input_raw).hexdigest()
    assert value["registrySnapshot"]["budgetCap"] == "80000000"
    assert value["projectPolicy"]["version"] == "demo_generated-diagnostic-context/1"
    assert value["evidenceSnapshot"]["deployment"]["instanceId"] == env.ns.instance_id
    assert {item["evidenceRole"] for item in value["evidenceSnapshot"]["commitments"]} == {"poHash", "requestHash", "goodsRequestHash"}
    assert len(resources) == 3 and len(fingerprint) == 64
    assert env.procurement.source_versions_json["poDocumentVersionId"] == str(env.request.purchase_order_version_id)


def test_snapshot_version_reuses_exact_evidence_and_increments_after_change(env):
    first, _, fingerprint, at = env.build()
    env.session.previous = SimpleNamespace(evidence_version=3, snapshot_id="old-id", snapshot_created_at=at, snapshot_fingerprint=fingerprint)
    again, _, _, _ = env.build()
    assert again["evidenceSnapshot"]["evidenceVersion"] == "3"
    assert again["evidenceSnapshot"]["snapshotId"] == "old-id"
    env.session.previous.snapshot_fingerprint = "0" * 64
    changed, _, _, _ = env.build()
    assert changed["evidenceSnapshot"]["evidenceVersion"] == "4"
    assert changed["evidenceSnapshot"]["snapshotId"] != "old-id"


@pytest.mark.parametrize("change,code", [("different_po", "ai_diagnostic_po_version_mismatch"), ("pdf", "ai_diagnostic_image_required"),
                                       ("tamper", "ai_diagnostic_document_hash_mismatch"), ("late_state", "ai_diagnostic_pre_state_required"),
                                       ("same_hash_new_version", "ai_diagnostic_source_binding"), ("reorg", "ai_diagnostic_registry_unverified")])
def test_evidence_fail_closed(env, change, code):
    po = env.records[DocumentVersion, env.request.purchase_order_version_id]
    if change == "different_po":
        env.request.purchase_order_version_id = uuid4()
    elif change == "pdf":
        po.content_type = "application/pdf"
    elif change == "tamper":
        po.sha256_hex = "0" * 64
    elif change == "late_state":
        env.view[-1] = 6
    elif change == "same_hash_new_version":
        env.procurement.source_versions_json["bindings"]["poDocumentVersionId"]["versionId"] = str(uuid4())
    elif change == "reorg":
        env.gate.canonical = lambda *_: False
    with pytest.raises(APIError) as exc:
        env.build()
    assert exc.value.code == code


def test_report_storage_is_private_exact_and_never_overwrites(tmp_path):
    store = PrivateFileStore(tmp_path / "private")
    ns, diagnostic_id = uuid4(), uuid4()
    raw = b'{"test_only":"exact bytes"}'
    key = save_report(store, ns, diagnostic_id, raw)
    path = store.resolve(key)
    assert path.read_bytes() == raw and path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        save_report(store, ns, diagnostic_id, b"different")
    assert path.read_bytes() == raw
    with pytest.raises(ValueError):
        save_report(store, "../../outside", diagnostic_id, raw)
