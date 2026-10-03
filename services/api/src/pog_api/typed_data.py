from __future__ import annotations

from typing import Any

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address


UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1
UINT256_MAX = 2**256 - 1

AI_FIELDS = [
    {"name": "stage", "type": "uint8"},
    {"name": "procurementId", "type": "bytes32"},
    {"name": "assessmentId", "type": "bytes32"},
    {"name": "outcome", "type": "uint8"},
    {"name": "riskScoreBps", "type": "uint16"},
    {"name": "evidenceHash", "type": "bytes32"},
    {"name": "reportHash", "type": "bytes32"},
    {"name": "signer", "type": "address"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint64"},
]
HUMAN_FIELDS = [
    {"name": "targetId", "type": "bytes32"},
    {"name": "action", "type": "uint8"},
    {"name": "termsHash", "type": "bytes32"},
    {"name": "assessmentId", "type": "bytes32"},
    {"name": "signer", "type": "address"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint64"},
    {"name": "policyEpoch", "type": "uint32"},
]
RECEIPT_FIELDS = [
    {"name": "projectId", "type": "bytes32"},
    {"name": "procurementId", "type": "bytes32"},
    {"name": "expectedRecipient", "type": "address"},
    {"name": "vendor", "type": "address"},
    {"name": "poHash", "type": "bytes32"},
    {"name": "invoiceHash", "type": "bytes32"},
    {"name": "invoiceAmount", "type": "uint256"},
    {"name": "goodsHash", "type": "bytes32"},
    {"name": "receiptEvidenceHash", "type": "bytes32"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint64"},
]


def _integer(value: Any, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError(f"{field} is outside its unsigned integer range")
    return value


def _bytes32(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        raise ValueError(f"{field} must be a 32-byte 0x-prefixed hex string")
    bytes.fromhex(value[2:])
    return value.lower()


def _address(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an address")
    return to_checksum_address(value)


def _domain(name: str, chain_id: int, verifying_contract: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": "2",
        "chainId": _integer(chain_id, UINT256_MAX, "chainId"),
        "verifyingContract": _address(verifying_contract, "verifyingContract"),
    }


def typed_data(
    primary_type: str,
    fields: list[dict[str, str]],
    message: dict[str, Any],
    *,
    name: str,
    chain_id: int,
    verifying_contract: str,
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            primary_type: fields,
        },
        "primaryType": primary_type,
        "domain": _domain(name, chain_id, verifying_contract),
        "message": message,
    }


def ai_assessment_typed(message: dict[str, Any], chain_id: int, contract: str) -> dict[str, Any]:
    normalized = {
        "stage": _integer(message["stage"], 255, "stage"),
        "procurementId": _bytes32(message["procurementId"], "procurementId"),
        "assessmentId": _bytes32(message["assessmentId"], "assessmentId"),
        "outcome": _integer(message["outcome"], 255, "outcome"),
        "riskScoreBps": _integer(message["riskScoreBps"], 65535, "riskScoreBps"),
        "evidenceHash": _bytes32(message["evidenceHash"], "evidenceHash"),
        "reportHash": _bytes32(message["reportHash"], "reportHash"),
        "signer": _address(message["signer"], "signer"),
        "nonce": _integer(message["nonce"], UINT256_MAX, "nonce"),
        "deadline": _integer(message["deadline"], UINT64_MAX, "deadline"),
    }
    return typed_data(
        "AIAssessment", AI_FIELDS, normalized,
        name="PoGRegistryV2", chain_id=chain_id, verifying_contract=contract,
    )


def human_intent_typed(message: dict[str, Any], chain_id: int, contract: str) -> dict[str, Any]:
    normalized = {
        "targetId": _bytes32(message["targetId"], "targetId"),
        "action": _integer(message["action"], 255, "action"),
        "termsHash": _bytes32(message["termsHash"], "termsHash"),
        "assessmentId": _bytes32(message["assessmentId"], "assessmentId"),
        "signer": _address(message["signer"], "signer"),
        "nonce": _integer(message["nonce"], UINT256_MAX, "nonce"),
        "deadline": _integer(message["deadline"], UINT64_MAX, "deadline"),
        "policyEpoch": _integer(message["policyEpoch"], UINT32_MAX, "policyEpoch"),
    }
    return typed_data(
        "HumanIntent", HUMAN_FIELDS, normalized,
        name="ProcurementEscrowV2", chain_id=chain_id, verifying_contract=contract,
    )


def recipient_receipt_typed(message: dict[str, Any], chain_id: int, contract: str) -> dict[str, Any]:
    normalized = {
        "projectId": _bytes32(message["projectId"], "projectId"),
        "procurementId": _bytes32(message["procurementId"], "procurementId"),
        "expectedRecipient": _address(message["expectedRecipient"], "expectedRecipient"),
        "vendor": _address(message["vendor"], "vendor"),
        "poHash": _bytes32(message["poHash"], "poHash"),
        "invoiceHash": _bytes32(message["invoiceHash"], "invoiceHash"),
        "invoiceAmount": _integer(message["invoiceAmount"], UINT256_MAX, "invoiceAmount"),
        "goodsHash": _bytes32(message["goodsHash"], "goodsHash"),
        "receiptEvidenceHash": _bytes32(message["receiptEvidenceHash"], "receiptEvidenceHash"),
        "nonce": _integer(message["nonce"], UINT256_MAX, "nonce"),
        "deadline": _integer(message["deadline"], UINT64_MAX, "deadline"),
    }
    return typed_data(
        "RecipientReceipt", RECEIPT_FIELDS, normalized,
        name="PoGRegistryV2", chain_id=chain_id, verifying_contract=contract,
    )


def digest(data: dict[str, Any]) -> str:
    signable = encode_typed_data(full_message=data)
    return "0x" + keccak(b"\x19" + signable.version + signable.header + signable.body).hex()


def recover(data: dict[str, Any], signature: str) -> str:
    if not isinstance(signature, str) or not signature.startswith("0x") or len(signature) != 132:
        raise ValueError("A2 supports only 65-byte EOA signatures")
    return Account.recover_message(encode_typed_data(full_message=data), signature=signature)


def abi_hash(types: list[str], values: list[Any]) -> str:
    return "0x" + keccak(encode(types, values)).hex()


def parties_hash(project_id: str, foundation: str, recipient: str, vendor: str, asset: str) -> str:
    return abi_hash(
        ["bytes32", "address", "address", "address", "address"],
        [bytes.fromhex(project_id[2:]), foundation, recipient, vendor, asset],
    )


def reserve_terms_hash(domain: str, parties: str, procurement_id: str, budget: int,
                       amount: int, evidence: str, assessment: str) -> str:
    return abi_hash(
        ["bytes32", "bytes32", "bytes32", "uint256", "uint256", "bytes32", "bytes32"],
        [bytes.fromhex(domain[2:]), bytes.fromhex(parties[2:]), bytes.fromhex(procurement_id[2:]),
         budget, amount, bytes.fromhex(evidence[2:]), bytes.fromhex(assessment[2:])],
    )


def release_terms_hash(domain: str, parties: str, procurement_id: str, reserved: int,
                       invoice: int, evidence: str, assessment: str) -> str:
    return abi_hash(
        ["bytes32", "bytes32", "bytes32", "uint256", "uint256", "bytes32", "bytes32"],
        [bytes.fromhex(domain[2:]), bytes.fromhex(parties[2:]), bytes.fromhex(procurement_id[2:]),
         reserved, invoice, bytes.fromhex(evidence[2:]), bytes.fromhex(assessment[2:])],
    )


def settlement_terms_hash(domain: str, parties: str, procurement_id: str,
                          invoice: int, settlement: str) -> str:
    return abi_hash(
        ["bytes32", "bytes32", "bytes32", "uint256", "bytes32"],
        [bytes.fromhex(domain[2:]), bytes.fromhex(parties[2:]), bytes.fromhex(procurement_id[2:]),
         invoice, bytes.fromhex(settlement[2:])],
    )


def cancellation_terms_hash(domain: str, parties: str, procurement_id: str, reserved: int,
                            evidence: str, reason: str) -> str:
    return abi_hash(
        ["bytes32", "bytes32", "bytes32", "uint256", "bytes32", "bytes32"],
        [bytes.fromhex(domain[2:]), bytes.fromhex(parties[2:]), bytes.fromhex(procurement_id[2:]),
         reserved, bytes.fromhex(evidence[2:]), bytes.fromhex(reason[2:])],
    )


def close_terms_hash(domain: str, project_id: str, foundation: str, recipient: str, asset: str,
                     state: int, unresolved: int, ledger_hash: str, epoch: int) -> str:
    return abi_hash(
        ["bytes32", "bytes32", "address", "address", "address", "uint8", "uint32", "bytes32", "uint32"],
        [bytes.fromhex(domain[2:]), bytes.fromhex(project_id[2:]), foundation, recipient, asset,
         state, unresolved, bytes.fromhex(ledger_hash[2:]), epoch],
    )
