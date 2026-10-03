from __future__ import annotations

import pytest

from pog_api.hash_vectors import assessment_id, final_evidence_hash, pre_evidence_hash


def _hash(number):
    return "0x" + f"{number:064x}"


def _address(number):
    return "0x" + f"{number:040x}"


PARTIES = {
    "project_id": _hash(1), "procurement_id": _hash(2),
    "foundation": _address(3), "recipient": _address(4),
    "vendor": _address(5), "asset": _address(6),
}
PRE = {**PARTIES, "budget_cap": 90000000, "po_hash": _hash(7),
       "request_hash": _hash(8), "goods_request_hash": _hash(9)}
FINAL = {**PARTIES, "reserved_amount": 80000000, "po_hash": _hash(7),
         "invoice_hash": _hash(10), "invoice_amount": 72000000,
         "goods_hash": _hash(11), "receipt_digest": _hash(12)}


def test_independent_pre_and_final_evidence_nonzero_fixed_vectors():
    assert pre_evidence_hash(**PRE) == "0x7e1faf7858dc080f2b3428eefd3a74999dd7f5b7e69e5bcddeeca644096a0c7a"
    assert final_evidence_hash(**FINAL) == "0x926e979405131d5fddb86cf4c1e5312056f159cc61d92a09fb1e40effbae3644"
    assert pre_evidence_hash(**{**PRE, "request_hash": _hash(10)}) != pre_evidence_hash(**PRE)
    assert final_evidence_hash(**{**FINAL, "receipt_digest": _hash(13)}) != final_evidence_hash(**FINAL)


def test_independent_assessment_id_nonzero_fixed_vector_and_unsigned_boundaries():
    inputs = {
        "stage": 1, "procurement_id": _hash(2), "outcome": 2, "risk_score_bps": 9999,
        "evidence_hash": final_evidence_hash(**FINAL), "report_hash": _hash(13),
        "signer": _address(14), "nonce": 2**256 - 1, "deadline": 2**64 - 1,
    }
    assert assessment_id(**inputs) == "0x6c6f4ad3dabaee41b45219bdc69fb3b495e80bafb791530a53da194232e2ac14"
    assert assessment_id(**{**inputs, "outcome": 1}) != assessment_id(**inputs)
    for field, value in (("nonce", 2**256), ("deadline", 2**64), ("stage", True),
                         ("risk_score_bps", 1.5)):
        with pytest.raises(ValueError):
            assessment_id(**{**inputs, field: value})
