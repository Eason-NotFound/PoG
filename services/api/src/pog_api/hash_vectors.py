"""Independent accepted-V2 evidence formulas, used by verification fixtures.

These do not call contract helpers, construct HTTP actions, or authorize funds.
ABI field order follows PoGRegistryV2's frozen compute* methods.
"""
from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak, to_checksum_address


def _uint(value: int, bits: int, name: str) -> int:
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ValueError(f"{name} must be uint{bits}")
    return value


def _bytes32(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise ValueError("Evidence/identifier must be bytes32 hex")
    try:
        result = bytes.fromhex(value[2:])
    except ValueError as exc:
        raise ValueError("Evidence/identifier must be bytes32 hex") from exc
    if len(result) != 32:
        raise ValueError("Evidence/identifier must be bytes32 hex")
    return result


def _hash(types, values) -> str:
    return "0x" + keccak(encode(types, values)).hex()


def assessment_id(*, stage: int, procurement_id: str, outcome: int, risk_score_bps: int,
                  evidence_hash: str, report_hash: str, signer: str, nonce: int, deadline: int) -> str:
    """keccak(abi.encode(domain, stage, procurement, outcome, risk, evidence,
    report, signer, nonce, deadline)); assessmentId itself is intentionally absent.
    """
    return _hash(
        ["bytes32", "uint8", "bytes32", "uint8", "uint16", "bytes32", "bytes32",
         "address", "uint256", "uint64"],
        [keccak(text="POG_V2_AI_ASSESSMENT_ID"), _uint(stage, 8, "stage"),
         _bytes32(procurement_id), _uint(outcome, 8, "outcome"),
         _uint(risk_score_bps, 16, "riskScoreBps"), _bytes32(evidence_hash), _bytes32(report_hash),
         to_checksum_address(signer), _uint(nonce, 256, "nonce"), _uint(deadline, 64, "deadline")],
    )


def pre_evidence_hash(*, project_id: str, procurement_id: str, foundation: str, recipient: str,
                      vendor: str, asset: str, budget_cap: int, po_hash: str,
                      request_hash: str, goods_request_hash: str) -> str:
    return _hash(
        ["bytes32", "bytes32", "bytes32", "address", "address", "address", "address",
         "uint256", "bytes32", "bytes32", "bytes32"],
        [keccak(text="POG_V2_PRE_EVIDENCE"), _bytes32(project_id), _bytes32(procurement_id),
         to_checksum_address(foundation), to_checksum_address(recipient), to_checksum_address(vendor),
         to_checksum_address(asset), _uint(budget_cap, 256, "budgetCap"), _bytes32(po_hash),
         _bytes32(request_hash), _bytes32(goods_request_hash)],
    )


def final_evidence_hash(*, project_id: str, procurement_id: str, foundation: str, recipient: str,
                        vendor: str, asset: str, reserved_amount: int, po_hash: str,
                        invoice_hash: str, invoice_amount: int, goods_hash: str,
                        receipt_digest: str) -> str:
    return _hash(
        ["bytes32", "bytes32", "bytes32", "address", "address", "address", "address",
         "uint256", "bytes32", "bytes32", "uint256", "bytes32", "bytes32"],
        [keccak(text="POG_V2_FINAL_EVIDENCE"), _bytes32(project_id), _bytes32(procurement_id),
         to_checksum_address(foundation), to_checksum_address(recipient), to_checksum_address(vendor),
         to_checksum_address(asset), _uint(reserved_amount, 256, "reservedAmount"), _bytes32(po_hash),
         _bytes32(invoice_hash), _uint(invoice_amount, 256, "invoiceAmount"), _bytes32(goods_hash),
         _bytes32(receipt_digest)],
    )
