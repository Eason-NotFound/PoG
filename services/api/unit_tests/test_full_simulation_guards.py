"""No-engine source, opt-in and input-boundary regressions for the full flow."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pog_api import a2
from pog_api.errors import APIError
from pog_api.full_demo import FundingRequest, FundedDonationRequest, QuoteRequest, capabilities
from pog_api.models import Project, Document, SigningRequest, signing_nonce_family

NAMES=("poDocumentVersionId","requestDocumentVersionId","goodsRequestDocumentVersionId","invoiceDocumentVersionId","goodsDocumentVersionId")
FIELDS=("po_hash","request_hash","goods_request_hash","invoice_hash","goods_hash")


def source_fixture(monkeypatch):
    ns,owner,proc_id=uuid4(),uuid4(),uuid4()
    project=SimpleNamespace(foundation_user_id=owner)
    sources={}
    versions={}
    for name in NAMES:
        row=SimpleNamespace(id=uuid4(),document_id=uuid4(),uploaded_by_user_id=owner,referenced=True,keccak256_hex="aa"*32)
        versions[row.id]=row
        sources[name]=str(row.id)
    sources["bindings"]={name:{"versionId":sources[name],"hash":"aa"*32,"uploaderId":str(owner)} for name in NAMES}
    receipt=SimpleNamespace(id=uuid4(),namespace_id=ns,procurement_id=proc_id,kind="receipt",status="confirmed",submitted_operation_id=uuid4(),digest="0x"+"bb"*32)
    sources["receiptSigningRequestId"]=str(receipt.id)
    proc=SimpleNamespace(id=proc_id,namespace_id=ns,project_id=uuid4(),source_versions_json=sources,receipt_digest=receipt.digest,
                         **{field:"0x"+"aa"*32 for field in FIELDS})
    session=SimpleNamespace(get=lambda model, identifier: project if model is Project else
                            SimpleNamespace(namespace_id=ns) if model is Document else receipt if model is SigningRequest else None)
    monkeypatch.setattr(a2,"_document_version",lambda session,proc,identifier,category:versions[identifier])
    monkeypatch.setattr(a2,"_validate_receipt_source",lambda session,request:None)
    return session,proc,versions


def test_every_original_version_can_be_validated(monkeypatch):
    session,proc,_=source_fixture(monkeypatch)
    a2.validate_original_sources(session,proc,deepcopy(proc.source_versions_json))


@pytest.mark.parametrize("index",range(5))
@pytest.mark.parametrize("fault",("uploader","hash","referenced","binding"))
def test_each_original_version_fails_closed_independently(monkeypatch,index,fault):
    session,proc,versions=source_fixture(monkeypatch)
    row=versions[next(identifier for identifier in versions if str(identifier)==proc.source_versions_json[NAMES[index]])]
    if fault=="uploader":row.uploaded_by_user_id=uuid4()
    if fault=="hash":setattr(proc,FIELDS[index],"0x"+"cc"*32)
    if fault=="referenced":row.referenced=False
    if fault=="binding":proc.source_versions_json["bindings"][NAMES[index]]["hash"]="cc"*32
    with pytest.raises(APIError) as exc:
        a2.validate_original_sources(session,proc)
    assert exc.value.code=="signing_material_stale"


@pytest.mark.parametrize("kind,family",(("ai_pre","registry_ai"),("ai_final","registry_ai"),("receipt","registry_recipient"),
                                      ("reserve","escrow_human"),("release","escrow_human"),("settlement","escrow_human")))
def test_six_kinds_share_only_their_real_contract_nonce_family(kind,family):
    assert signing_nonce_family(kind)==family


@pytest.mark.parametrize("confirm",(False,1,"true",None))
def test_funding_requires_an_exact_explicit_boolean(confirm):
    with pytest.raises(ValidationError):
        FundingRequest(projectId=str(uuid4()),hkdCents="100",confirm=confirm)


@pytest.mark.parametrize("amount",("01","1e3","1.0","-1",1,1.0))
def test_no_float_or_implicit_amount_coercion(amount):
    with pytest.raises(ValidationError):
        FundingRequest(projectId=str(uuid4()),hkdCents=amount,confirm=True)


def test_quote_is_separate_and_accepts_no_execution_confirm_or_success_proof():
    with pytest.raises(ValidationError):
        QuoteRequest(direction="hkd_to_mock",projectId=str(uuid4()),hkdCents="100",confirm=True)
    with pytest.raises(ValidationError):
        FundedDonationRequest(fundingOperationId=str(uuid4()),amountAtomic="1",confirm=True,canonical=True)


def test_normal_capabilities_never_label_synthetic_fixtures_as_real_ai_or_faucet_credit():
    for role in ("foundation","recipient","donor","human_approver","service_ai"):
        caps=capabilities(SimpleNamespace(role=role))
        assert caps["ai.pre.detect"]["enabled"] is False
        assert caps["ai.final.detect"]["reasonCode"]=="ai_service_unavailable"
        assert caps["donation.deposit"]["enabled"] is False
        assert caps["supplier.payment.execute"]["enabled"] is (role=="foundation")
