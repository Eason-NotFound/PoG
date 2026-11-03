"""Pure arithmetic, immutable schema and deterministic canonical byte checks."""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from eth_utils import keccak
import pytest
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from pog_api.amounts import UINT256_MAX
from pog_api.errors import APIError
from pog_api.payment_domain import (ATOMIC_PER_CENT, atomic_to_cents, canonical_evidence_bytes,
                                   cents_to_atomic, positive_uint)
from pog_api.payment_models import PAYMENT_TABLES, PaymentResource


@pytest.mark.parametrize("value", ["1", "100", "100000", str(UINT256_MAX // ATOMIC_PER_CENT)])
def test_exact_cent_conversion(value):
    cents, atomic = cents_to_atomic(value)
    assert atomic == int(value) * 10_000
    assert atomic_to_cents(str(atomic)) == (cents, atomic)


@pytest.mark.parametrize("value", ["0", "01", "-1", "+1", "1.0", "1e2", " 1", "1 ", "", "NaN", None, True, False, 1.0, {}, []])
def test_execution_amount_rejects_noncanonical_or_nonpositive(value):
    with pytest.raises(APIError) as exc:
        positive_uint(value)
    assert exc.value.code == "payment_amount_invalid"


def test_conversion_cannot_overflow_uint256():
    with pytest.raises(APIError):
        cents_to_atomic(str(UINT256_MAX // ATOMIC_PER_CENT + 1))
    with pytest.raises(APIError):
        positive_uint(str(UINT256_MAX + 1))


@pytest.mark.parametrize("value", ["1", "9999", "10001", "72000001"])
def test_redemption_never_rounds_subcent(value):
    with pytest.raises(APIError) as exc:
        atomic_to_cents(value)
    assert exc.value.code == "payment_subcent_amount"


def evidence_material(kind="conversion"):
    identity = "00000000-0000-0000-0000-000000000001"
    address = "0x" + "11" * 20
    proof = {"operationId": identity, "transactionId": identity, "txHash": "0x" + "22" * 32,
             "receiptStatus": "1", "blockNumber": "10", "blockHash": "0x" + "33" * 32,
             "emitter": address, "logIndex": "0", "caller": address, "target": address}
    material = {"schemaVersion": "pog-conversion-evidence-v1", "kind": kind, "mode": "simulation",
        "binding": {"namespaceId": identity, "runId": "golden-run", "instanceId": "golden-instance",
                    "chainId": "31337", "registry": address, "escrow": address, "token": address},
        "projectId": identity, "projectChainId": "0x" + "44" * 32,
        "procurementId": identity, "procurementChainId": "0x" + "55" * 32,
        "foundation": address, "treasury": address, "vendor": address, "token": address,
        "amountAtomic": "72000000", "hkdCents": "7200", "invoiceDocumentVersionId": identity,
        "invoiceHash": "0x" + "66" * 32, "releaseOperationId": identity, "releaseProof": deepcopy(proof),
        "redemptionResourceId": identity, "redemptionOperationId": identity,
        "redemptionProof": deepcopy(proof), "redemptionJournalId": identity}
    if kind == "supplier_payment":
        material.update(schemaVersion="pog-supplier-payment-evidence-v1", paymentResourceId=identity,
                        paymentOperationId=identity, paymentJournalId=identity)
    return material


@pytest.mark.parametrize("kind", ["conversion", "supplier_payment"])
def test_canonical_evidence_is_ascii_json_sorted_without_newline(kind):
    material = evidence_material(kind)
    raw = canonical_evidence_bytes(material)
    assert raw == json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    assert raw.isascii() and not raw.endswith(b"\n") and not raw.startswith(b"\xef\xbb\xbf")
    reversed_keys = dict(reversed(list(material.items())))
    assert canonical_evidence_bytes(reversed_keys) == raw
    assert len(hashlib.sha256(raw).hexdigest()) == 64 and len(keccak(raw).hex()) == 64
    golden = {
        "conversion": (2331, "401f5d62dfef714b3c71e62d5d39f0e6eaebc835f31d33a262f3bd28413c2b9c",
                       "81a78fce7f636743897fa37a6d960c1391be19a227e4a88b0b40c4aa2b9c421f"),
        "supplier_payment": (2520, "9d9a23a147837f1734365062421d317801957db86247a89ded9400440037b5c2",
                             "bc61e3525c8a0455dd7a1e98897066beb8e7d7d137f1d2964988b6a95cff42bb"),
    }
    assert (len(raw), hashlib.sha256(raw).hexdigest(), keccak(raw).hex()) == golden[kind]
    assert b"signature" not in raw and b"storage" not in raw and b"password" not in raw


@pytest.mark.parametrize("change", [
    lambda m: m.update(signature="private"), lambda m: m["binding"].update(rpcUrl="private"),
    lambda m: m["releaseProof"].update(canonical="true"), lambda m: m.update(hkdCents="07200"),
    lambda m: m.update(amountAtomic=72000000), lambda m: m.update(hkdCents=72.0),
    lambda m: m.update(foundation="\u4e2d"), lambda m: m.update(vendor="line\n"),
    lambda m: m.update(mode="real"), lambda m: m["binding"].update(chainId="1"),
    lambda m: m["redemptionProof"].update(receiptStatus="0"),
])
def test_evidence_whitelist_never_accepts_extra_or_ambiguous_material(change):
    material = evidence_material()
    change(material)
    with pytest.raises((ValueError, TypeError)):
        canonical_evidence_bytes(material)


def test_payment_models_compile_namespace_composite_fks_and_amount_checks():
    ddl = {table.name: str(CreateTable(table).compile(dialect=dialect())) for table in PAYMENT_TABLES}
    assert len(ddl) == 6
    assert "FOREIGN KEY(operation_id, namespace_id)" in ddl["payment_resources"]
    assert "FOREIGN KEY(funding_resource_id, namespace_id)" in ddl["funded_claims"]
    assert "amount_atomic = hkd_cents * 10000" in ddl["payment_resources"]
    assert "debit_account_id <> credit_account_id" in ddl["sim_hkd_journals"]
    assert "fixture_equity" in ddl["sim_hkd_accounts"]
    assert "canonical_bytes BYTEA NOT NULL" in ddl["payment_evidence"]


def test_funding_resource_does_not_share_procurement_scope():
    resource = PaymentResource(id=uuid4(), kind="funding", procurement_id=None, source_operation_id=None)
    assert resource.procurement_id is None and resource.source_operation_id is None
