"""Independent, read-only reference for the unchanged accepted PoG V2 hashes.

No API hashing/signing implementation is imported. EIP-712 is encoded directly
with eth_abi and Keccak; there is deliberately no transaction or signing API.
The caller must supply an already gated LocalChainGateway for an owned isolated
deployment. All views/helper reads in one check are pinned to one block.

RecipientReceipt has NO public digest(input) helper in accepted RegistryV2.
Before submission its digest can only be compared with the API's digest. After
confirmation we compare the immutable stored receiptDigest and its exact facts.
Computing later action helpers at ReceiptConfirmed is not a release/payment/
close execution test; zero stored late-stage fields are identified in proofs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from eth_abi import encode
from eth_utils import keccak, to_checksum_address


REGISTRY = "PoGRegistryV2"
ESCROW = "ProcurementEscrowV2"
ZERO_HASH = "0x" + "00" * 32
DOMAIN_FIELDS = (
    ("name", "string"), ("version", "string"), ("chainId", "uint256"),
    ("verifyingContract", "address"),
)
FIELDS = {
    "AIAssessment": (
        ("stage", "uint8"), ("procurementId", "bytes32"), ("assessmentId", "bytes32"),
        ("outcome", "uint8"), ("riskScoreBps", "uint16"), ("evidenceHash", "bytes32"),
        ("reportHash", "bytes32"), ("signer", "address"), ("nonce", "uint256"),
        ("deadline", "uint64"),
    ),
    "RecipientReceipt": (
        ("projectId", "bytes32"), ("procurementId", "bytes32"),
        ("expectedRecipient", "address"), ("vendor", "address"),
        ("poHash", "bytes32"), ("invoiceHash", "bytes32"), ("invoiceAmount", "uint256"),
        ("goodsHash", "bytes32"), ("receiptEvidenceHash", "bytes32"),
        ("nonce", "uint256"), ("deadline", "uint64"),
    ),
    "HumanIntent": (
        ("targetId", "bytes32"), ("action", "uint8"), ("termsHash", "bytes32"),
        ("assessmentId", "bytes32"), ("signer", "address"), ("nonce", "uint256"),
        ("deadline", "uint64"), ("policyEpoch", "uint32"),
    ),
}
PROJECT_FIELDS = (
    ("projectId", "bytes32"), ("foundation", "address"), ("recipient", "address"),
    ("asset", "address"), ("assetDecimals", "uint8"), ("policyEpoch", "uint32"),
    ("threshold", "uint16"), ("state", "uint8"), ("unresolvedProcurements", "uint32"),
    ("createdAt", "uint64"),
)
PROCUREMENT_FIELDS = (
    ("procurementId", "bytes32"), ("projectId", "bytes32"), ("vendor", "address"),
    ("budgetCap", "uint256"), ("poHash", "bytes32"), ("requestHash", "bytes32"),
    ("goodsRequestHash", "bytes32"), ("preEvidenceHash", "bytes32"),
    ("preAssessmentId", "bytes32"), ("reservedAmount", "uint256"),
    ("invoiceHash", "bytes32"), ("invoiceAmount", "uint256"), ("goodsHash", "bytes32"),
    ("receiptDigest", "bytes32"), ("finalEvidenceHash", "bytes32"),
    ("finalAssessmentId", "bytes32"), ("conversionEvidenceHash", "bytes32"),
    ("paymentEvidenceHash", "bytes32"), ("settlementHash", "bytes32"),
    ("cancellationReasonHash", "bytes32"), ("returnedAmount", "uint256"), ("state", "uint8"),
)
LEDGER_FIELDS = (
    ("asset", "address"), ("deposits", "uint256"), ("reserved", "uint256"),
    ("released", "uint256"), ("returned", "uint256"), ("refunded", "uint256"),
    ("refundPool", "uint256"), ("donorCount", "uint32"), ("claimedCount", "uint32"),
    ("policyEpoch", "uint32"), ("threshold", "uint16"), ("refundSnapshotted", "bool"),
)


class OracleMismatch(ValueError):
    """Malformed inputs or disagreement with an independent reference."""


def _value(kind: str, value: Any, label: str) -> Any:
    if kind.startswith("uint"):
        bits = int(kind[4:])
        if type(value) is not int or not 0 <= value < 2**bits:
            raise OracleMismatch(f"{label} must be a uint{bits} integer")
        return value
    if kind == "bool":
        if type(value) is not bool:
            raise OracleMismatch(f"{label} must be bool")
        return value
    if kind == "string":
        if not isinstance(value, str):
            raise OracleMismatch(f"{label} must be string")
        return value
    if kind in {"bytes32", "address"}:
        length = 32 if kind == "bytes32" else 20
        if kind == "bytes32" and isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        elif isinstance(value, str) and value.startswith("0x") and len(value) == 2 + 2 * length:
            try:
                raw = bytes.fromhex(value[2:])
            except ValueError as exc:
                raise OracleMismatch(f"{label} is not hexadecimal") from exc
        else:
            raise OracleMismatch(f"{label} must be exactly {length} bytes")
        if len(raw) != length:
            raise OracleMismatch(f"{label} must be exactly {length} bytes")
        # Address ABI bytes are case-independent, but Web3's getter encoder
        # requires checksum text. Keep the helper-call representation valid.
        return raw if kind == "bytes32" else to_checksum_address(raw)
    raise OracleMismatch(f"Unsupported reference type: {kind}")


def _hex(value: Any, label: str = "hash") -> str:
    return "0x" + _value("bytes32", value, label).hex()


def _type_string(name: str, fields: Sequence[tuple[str, str]]) -> str:
    return name + "(" + ",".join(kind + " " + field for field, kind in fields) + ")"


def _hash(fields: Sequence[tuple[str, Any]]) -> str:
    return "0x" + keccak(encode(
        [kind for kind, _ in fields],
        [_value(kind, value, f"argument[{i}]") for i, (kind, value) in enumerate(fields)],
    )).hex()


def _tag(text: str) -> bytes:
    return keccak(text=text)


def _view(raw: Any, fields: Sequence[tuple[str, str]], label: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        if not {name for name, _ in fields}.issubset(raw):
            raise OracleMismatch(f"Incomplete {label} view")
        items = [raw[name] for name, _ in fields]
    elif isinstance(raw, (list, tuple)) and len(raw) == len(fields):
        items = list(raw)
    else:
        raise OracleMismatch(f"{label} view does not match accepted ABI")
    return {
        name: (_hex(value, name) if kind == "bytes32" else _value(kind, value, name))
        for (name, kind), value in zip(fields, items)
    }


def _message(message: Any, fields: Sequence[tuple[str, str]]) -> list[Any]:
    if not isinstance(message, Mapping) or set(message) != {name for name, _ in fields}:
        raise OracleMismatch("Message fields do not match the exact accepted V2 schema")
    return [_value(kind, message[name], name) for name, kind in fields]


def compute_assessment_id(message: Mapping[str, Any]) -> str:
    """assessmentId excludes itself and is NOT the EIP-712 digest/domain."""
    values = _message(message, FIELDS["AIAssessment"])
    selected = [i for i in range(len(values)) if i != 2]
    return _hash([("bytes32", _tag("POG_V2_AI_ASSESSMENT_ID"))] + [
        (FIELDS["AIAssessment"][i][1], values[i]) for i in selected
    ])


def compute_typed_digest(typed: Mapping[str, Any]) -> str:
    """Direct EIP-712 \x19\x01/domain/struct hash; no eth_account encoder."""
    if not isinstance(typed, Mapping) or set(typed) != {"types", "primaryType", "domain", "message"}:
        raise OracleMismatch("Typed data must contain exactly types, primaryType, domain, message")
    primary = typed["primaryType"]
    if not isinstance(primary, str) or primary not in FIELDS:
        raise OracleMismatch("Unsupported V2 primary type")
    fields = FIELDS[primary]
    types = typed["types"]
    expected_types = {
        "EIP712Domain": [{"name": name, "type": kind} for name, kind in DOMAIN_FIELDS],
        primary: [{"name": name, "type": kind} for name, kind in fields],
    }
    if types != expected_types:
        raise OracleMismatch("Field order, widths or type names differ from accepted V2")
    domain = typed["domain"]
    values = _message(domain, DOMAIN_FIELDS)
    required_name = ESCROW if primary == "HumanIntent" else REGISTRY
    if domain["name"] != required_name or domain["version"] != "2":
        raise OracleMismatch("Wrong V2 signing domain name/version")
    domain_hash = _hash([
        ("bytes32", _tag(_type_string("EIP712Domain", DOMAIN_FIELDS))),
        ("bytes32", _tag(values[0])), ("bytes32", _tag(values[1])),
        ("uint256", values[2]), ("address", values[3]),
    ])
    message_hash = _hash([
        ("bytes32", _tag(_type_string(primary, fields))),
        *[(kind, value) for (_, kind), value in zip(fields, _message(typed["message"], fields))],
    ])
    return "0x" + keccak(b"\x19\x01" + bytes.fromhex(domain_hash[2:])
                           + bytes.fromhex(message_hash[2:])).hex()


def compute_view_hashes(project: Any, procurement: Any, ledger: Any,
                        reserve_amount: int) -> dict[str, str]:
    """Calculate evidence and helper terms from exact current accepted views.

    Later terms deliberately use the contract's STORED stage hashes/IDs, not a
    silently synthesized future assessment. closeTerms uses ACTUAL project.state.
    """
    p = _view(project, PROJECT_FIELDS, "Project")
    q = _view(procurement, PROCUREMENT_FIELDS, "Procurement")
    l = _view(ledger, LEDGER_FIELDS, "Ledger")
    _value("uint256", reserve_amount, "reserveAmount")
    if q["projectId"] != p["projectId"] or l["asset"] != p["asset"]:
        raise OracleMismatch("Views belong to different project/assets")
    if l["policyEpoch"] != p["policyEpoch"]:
        raise OracleMismatch("Registry/Escrow policy epochs disagree")
    pre = _hash([
        ("bytes32", _tag("POG_V2_PRE_EVIDENCE")), ("bytes32", p["projectId"]),
        ("bytes32", q["procurementId"]), ("address", p["foundation"]),
        ("address", p["recipient"]), ("address", q["vendor"]), ("address", p["asset"]),
        ("uint256", q["budgetCap"]), ("bytes32", q["poHash"]),
        ("bytes32", q["requestHash"]), ("bytes32", q["goodsRequestHash"]),
    ])
    final = _hash([
        ("bytes32", _tag("POG_V2_FINAL_EVIDENCE")), ("bytes32", p["projectId"]),
        ("bytes32", q["procurementId"]), ("address", p["foundation"]),
        ("address", p["recipient"]), ("address", q["vendor"]), ("address", p["asset"]),
        ("uint256", q["reservedAmount"]), ("bytes32", q["poHash"]),
        ("bytes32", q["invoiceHash"]), ("uint256", q["invoiceAmount"]),
        ("bytes32", q["goodsHash"]), ("bytes32", q["receiptDigest"]),
    ])
    parties = _hash([
        ("bytes32", p["projectId"]), ("address", p["foundation"]),
        ("address", p["recipient"]), ("address", q["vendor"]), ("address", p["asset"]),
    ])
    cancellation = _hash([
        ("bytes32", q["poHash"]), ("bytes32", q["invoiceHash"]),
        ("uint256", q["invoiceAmount"]), ("bytes32", q["goodsHash"]),
    ])
    # Same subtraction order as accepted Solidity; underflow is rejected instead
    # of computing a Python negative number or wrapping an unsigned value.
    if l["released"] < l["returned"]:
        raise OracleMismatch("Ledger returned exceeds released")
    refund_pool = l["deposits"] - (l["released"] - l["returned"]) - l["refunded"]
    _value("uint256", refund_pool, "computedRefundPool")
    ledger_hash = _hash([("uint256", l[key]) for key in
                         ("deposits", "returned", "released", "refunded", "reserved")]
                        + [("uint256", refund_pool), ("uint32", l["donorCount"])])
    return {
        "preEvidenceHash": pre,
        "finalEvidenceHash": final,
        "partiesHash": parties,
        "cancellationEvidenceHash": cancellation,
        "ledgerHash": ledger_hash,
        "reserveTermsHash": _hash([
            ("bytes32", _tag("POG_V2_RESERVE_TERMS")), ("bytes32", parties),
            ("bytes32", q["procurementId"]), ("uint256", q["budgetCap"]),
            ("uint256", reserve_amount), ("bytes32", q["preEvidenceHash"]),
            ("bytes32", q["preAssessmentId"]),
        ]),
        "releaseTermsHash": _hash([
            ("bytes32", _tag("POG_V2_RELEASE_TERMS")), ("bytes32", parties),
            ("bytes32", q["procurementId"]), ("uint256", q["reservedAmount"]),
            ("uint256", q["invoiceAmount"]), ("bytes32", q["finalEvidenceHash"]),
            ("bytes32", q["finalAssessmentId"]),
        ]),
        "settlementTermsHash": _hash([
            ("bytes32", _tag("POG_V2_SETTLEMENT_TERMS")), ("bytes32", parties),
            ("bytes32", q["procurementId"]), ("uint256", q["invoiceAmount"]),
            ("bytes32", q["settlementHash"]),
        ]),
        "cancellationTermsHash": _hash([
            ("bytes32", _tag("POG_V2_CANCEL_TERMS")), ("bytes32", parties),
            ("bytes32", q["procurementId"]), ("uint256", q["reservedAmount"]),
            ("bytes32", cancellation), ("bytes32", q["cancellationReasonHash"]),
        ]),
        "closeTermsHash": _hash([
            ("bytes32", _tag("POG_V2_CLOSE_TERMS")), ("bytes32", p["projectId"]),
            ("address", p["foundation"]), ("address", p["recipient"]),
            ("address", p["asset"]), ("uint8", p["state"]),
            ("uint32", p["unresolvedProcurements"]), ("bytes32", ledger_hash),
            ("uint32", l["policyEpoch"]),
        ]),
    }


def _compare(label: str, reference: Any, observed: Any, source: str) -> dict[str, Any]:
    expected, actual = _hex(reference, label), _hex(observed, label)
    if expected != actual:
        raise OracleMismatch(f"{label} disagrees with {source}: expected {expected}, got {actual}")
    return {"label": label, "reference": expected, "observed": actual,
            "source": source, "matches": True}


def _block(gateway: Any, block_identifier: Any) -> Any:
    # gateway.verify anchors accepted deployment and ABI; the oracle does not
    # provide a separate gateway that could bypass the deployment manager.
    gateway.verify()
    return gateway.w3.eth.block_number if block_identifier is None else block_identifier


def check_typed(gateway: Any, typed: Mapping[str, Any], digest: str, *,
                require_receipt_onchain: bool = False, block_identifier: Any = None) -> dict[str, Any]:
    """Compare one API typed-data response with this reference and chain facts.

    For RecipientReceipt call AGAIN after confirmed worker/indexer processing
    with require_receipt_onchain=True. Before then the proof explicitly contains
    no Solidity receipt digest comparison. This validates hashes, not permission,
    nonce availability, deadline freshness, signatures or transfer authorization.
    """
    reference = compute_typed_digest(typed)
    primary, message = typed["primaryType"], typed["message"]
    contract = ESCROW if primary == "HumanIntent" else REGISTRY
    block = _block(gateway, block_identifier)
    chain_id = _value("uint256", gateway.w3.eth.chain_id, "actualChainId")
    if typed["domain"]["chainId"] != chain_id:
        raise OracleMismatch("Typed domain chainId differs from the gated chain")
    address = _value("address", gateway.contract_address(contract), "actualVerifyingContract")
    if _value("address", typed["domain"]["verifyingContract"], "verifyingContract") != address:
        raise OracleMismatch("Typed domain verifyingContract differs from the gated contract")
    rows = [_compare("typedDigest", reference, digest, "API signing request")]
    call = lambda name, function, *args: gateway.call(name, function, *args, block_identifier=block)
    constant = {"AIAssessment": "AI_ASSESSMENT_TYPEHASH",
                "HumanIntent": "HUMAN_INTENT_TYPEHASH",
                "RecipientReceipt": "RECIPIENT_RECEIPT_TYPEHASH"}[primary]
    rows.append(_compare("typeHash", _tag(_type_string(primary, FIELDS[primary])),
                         call(contract, constant), f"{contract}.{constant}"))
    limitations = []
    receipt_compared = False
    values = tuple(_message(message, FIELDS[primary]))
    if primary == "AIAssessment":
        assessment = compute_assessment_id(message)
        rows.append(_compare("assessmentId.message", assessment, message["assessmentId"], "API message"))
        rows.append(_compare("assessmentId.helper", assessment,
                             call(REGISTRY, "computeAssessmentId", values), "Registry.computeAssessmentId"))
        rows.append(_compare("assessmentDigest.helper", reference,
                             call(REGISTRY, "assessmentDigest", values), "Registry.assessmentDigest"))
    elif primary == "HumanIntent":
        rows.append(_compare("intentDigest.helper", reference,
                             call(ESCROW, "intentDigest", values), "Escrow.intentDigest"))
    else:
        q = _view(call(REGISTRY, "getProcurement", message["procurementId"]),
                  PROCUREMENT_FIELDS, "Procurement")
        p = _view(call(REGISTRY, "getProject", q["projectId"]), PROJECT_FIELDS, "Project")
        required = {
            "projectId": p["projectId"], "procurementId": q["procurementId"],
            "expectedRecipient": p["recipient"], "vendor": q["vendor"],
            "poHash": q["poHash"], "invoiceHash": q["invoiceHash"],
            "invoiceAmount": q["invoiceAmount"], "goodsHash": q["goodsHash"],
        }
        for field, expected in required.items():
            kind = dict(FIELDS[primary])[field]
            if _value(kind, message[field], field) != _value(kind, expected, field):
                raise OracleMismatch(f"RecipientReceipt {field} differs from confirmed facts")
        if q["receiptDigest"] != ZERO_HASH:
            rows.append(_compare("receiptDigest.stored", reference, q["receiptDigest"],
                                 "Registry.getProcurement.receiptDigest"))
            receipt_compared = True
        elif require_receipt_onchain:
            raise OracleMismatch("No accepted receiptDigest exists at the pinned block")
        else:
            limitations.append("No public receipt digest(input) helper; receipt not yet accepted on chain")
    return {"primaryType": primary, "digest": reference, "blockIdentifier": block,
            "checks": rows, "onChainReceiptCompared": receipt_compared, "limitations": limitations}


def check_views(gateway: Any, project_id: str, procurement_id: str, reserve_amount: int, *,
                block_identifier: Any = None) -> dict[str, Any]:
    """Compare PRE/FINAL and all five human terms with accepted view helpers."""
    project_id = _hex(project_id, "projectId")
    procurement_id = _hex(procurement_id, "procurementId")
    if project_id == ZERO_HASH or procurement_id == ZERO_HASH:
        raise OracleMismatch("Verification targets must be nonzero IDs")
    block = _block(gateway, block_identifier)
    call = lambda name, function, *args: gateway.call(name, function, *args, block_identifier=block)
    p = _view(call(REGISTRY, "getProject", project_id), PROJECT_FIELDS, "Project")
    q = _view(call(REGISTRY, "getProcurement", procurement_id), PROCUREMENT_FIELDS, "Procurement")
    l = _view(call(ESCROW, "getLedger", project_id), LEDGER_FIELDS, "Ledger")
    if p["projectId"] != project_id or q["procurementId"] != procurement_id:
        raise OracleMismatch("Contract views do not identify the requested targets")
    hashes = compute_view_hashes(p, q, l, reserve_amount)
    helpers = (
        ("preEvidenceHash", REGISTRY, "computePreEvidenceHash", (procurement_id,)),
        ("finalEvidenceHash", REGISTRY, "computeFinalEvidenceHash", (procurement_id,)),
        ("reserveTermsHash", ESCROW, "reserveTermsHash", (procurement_id, reserve_amount)),
        ("releaseTermsHash", ESCROW, "releaseTermsHash", (procurement_id,)),
        ("settlementTermsHash", ESCROW, "settlementTermsHash", (procurement_id,)),
        ("cancellationTermsHash", ESCROW, "cancellationTermsHash", (procurement_id,)),
        ("closeTermsHash", ESCROW, "closeTermsHash", (project_id,)),
    )
    rows = [_compare(label, hashes[label], call(contract, function, *args),
                     contract + "." + function) for label, contract, function, args in helpers]
    for name in ("preEvidenceHash", "finalEvidenceHash"):
        if q[name] != ZERO_HASH:
            rows.append(_compare(name + ".stored", hashes[name], q[name], "Registry.getProcurement"))
    zero_late = [name for name in ("finalAssessmentId", "conversionEvidenceHash", "paymentEvidenceHash",
                                   "settlementHash", "cancellationReasonHash") if q[name] == ZERO_HASH]
    return {
        "projectId": project_id, "procurementId": procurement_id, "blockIdentifier": block,
        "projectState": p["state"], "procurementState": q["state"],
        "reserveAmount": reserve_amount, "checks": rows, "hashes": hashes,
        "zeroStoredLateStageFields": zero_late,
        "limitations": [
            "Read-only helper equality does not authorize or execute any human action",
            "Late-stage terms bind current stored values; no future nonzero assessment/payment is synthesized",
            "closeTermsHash binds actual projectState, even Active; successful helper read is not legal close execution",
        ],
    }
