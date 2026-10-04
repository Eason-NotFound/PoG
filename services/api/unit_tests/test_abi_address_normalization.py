"""Actual pinned Web3 ABI codec; no RPC, keys, authorization or token effects."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from web3 import Web3

from pog_api.chain import LocalChainGateway, _abi_addresses

ROOT = Path(__file__).resolve().parents[3]
LOWER = "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc"


def contract(name):
    path = ROOT / "packages/contract-abis/v2" / (name + ".json")
    material = json.loads(path.read_text())
    abi = material["abi"] if isinstance(material, dict) else material
    return Web3().eth.contract(address=Web3.to_checksum_address("0x" + "ab" * 20), abi=abi)


def gateway():
    value = object.__new__(LocalChainGateway)
    value.verify = lambda: None
    value.manifest = {"roles": {name: Web3.to_checksum_address("0x" + f"{index+1:040x}")
                               for index, name in enumerate(value.ROLE_NAMES)}}
    value.manifest["roles"]["donorA"] = Web3.to_checksum_address(LOWER)
    value.contracts = {name: contract(name) for name in value.ABI_PATHS}
    value.w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337, get_transaction_count=lambda *_: 9))
    return value


def test_lowercase_standard_seed_mint_encodes_exact_checksum_oracle():
    gate = gateway()
    original = [LOWER, "100000000"]
    envelope = gate.prepare("payment.mint", gate.roles["relayer"].lower(), original, "Transfer")
    assert envelope.data == gate.contracts["MockHKD"].functions.mint(Web3.to_checksum_address(LOWER), 100000000)._encode_transaction_data()
    assert original == [LOWER, "100000000"] and envelope.caller == gate.roles["relayer"]


@pytest.mark.parametrize("action,role,contract_name,function,event", (
    ("payment.redeem", "foundation", "MockHKD", "transfer", "Transfer"),
    ("donation.approve", "donorA", "MockHKD", "approve", "Approval"),
))
def test_other_payment_address_arguments_encode_without_identity_change(action, role, contract_name, function, event):
    gate = gateway()
    target = gate.roles["mockRedemption"] if action == "payment.redeem" else gate.contract_address("ProcurementEscrowV2")
    original = [target.lower(), "72000000"]
    envelope = gate.prepare(action, gate.roles[role].lower(), original, event)
    assert envelope.data == getattr(gate.contracts[contract_name].functions, function)(target, 72000000)._encode_transaction_data()
    assert original == [target.lower(), "72000000"]


def test_normalizing_does_not_bypass_fixed_donor_target():
    gate = gateway()
    with pytest.raises(ValueError, match="fixed Donor"):
        gate.prepare("payment.mint", gate.roles["relayer"], [gate.roles["foundation"].lower(), "1"], "Transfer")


def test_human_intent_signer_leaf_encodes_same_accepted_abi_bytes():
    gate = gateway()
    zero = "0x" + "00" * 32
    intent = [zero, 1, zero, zero, gate.roles["humanApprover"].lower(), 2, 999, 0]
    before = deepcopy(intent)
    expected = [*intent[:4], gate.roles["humanApprover"], *intent[5:]]
    signature = b"codec-only-fixture"
    envelope = gate.prepare("approval.release", gate.roles["relayer"], [zero, intent, signature], "HumanApprovalAccepted")
    assert envelope.data == gate.contracts["ProcurementEscrowV2"].functions.submitReleaseApproval(zero, expected, signature)._encode_transaction_data()
    assert intent == before


def test_lowercase_getter_address_uses_same_actual_abi_call_data(monkeypatch):
    gate = gateway()
    token = gate.contracts["MockHKD"]
    observed = []
    def eth_call(transaction, block_identifier, **kwargs):
        observed.append((transaction, block_identifier))
        return (99).to_bytes(32, "big")
    monkeypatch.setattr(token.w3.eth, "call", eth_call)
    assert gate.call("MockHKD", "balanceOf", LOWER, block_identifier=7) == 99
    assert observed[0][0]["data"] == token.functions.balanceOf(Web3.to_checksum_address(LOWER))._encode_transaction_data()
    assert observed[0][1] == 7


def test_nested_tuple_arrays_only_normalize_address_leaves_without_mutating_sources():
    fields = [{"name": "intents", "type": "tuple[]", "components": [
        {"name": "signer", "type": "address"}, {"name": "targetId", "type": "bytes32"},
        {"name": "approvers", "type": "address[2]"}]}]
    raw = [[[LOWER, "0x" + "ef" * 32, [LOWER, LOWER]]]]
    before = deepcopy(raw)
    result = _abi_addresses(fields, raw)
    assert raw == before and result[0][0][0] == Web3.to_checksum_address(LOWER)
    assert result[0][0][1] == raw[0][0][1]
    assert result[0][0][2] == [Web3.to_checksum_address(LOWER)] * 2


@pytest.mark.parametrize("value", (None, "0x1234", "not-wallet", 1, b"a" * 20))
def test_invalid_address_still_rejected(value):
    with pytest.raises(ValueError, match="valid 20-byte"):
        _abi_addresses([{"type": "address"}], [value])


def test_named_tuple_and_shape_mismatch_remain_strict():
    fields = [{"type": "tuple", "components": [{"name": "signer", "type": "address"}]}]
    assert _abi_addresses(fields, [{"signer": LOWER}]) == [{"signer": Web3.to_checksum_address(LOWER)}]
    with pytest.raises(ValueError, match="fields mismatch"):
        _abi_addresses(fields, [{"signer": LOWER, "caller": LOWER}])
    with pytest.raises(ValueError, match="count mismatch"):
        _abi_addresses(fields, [])


@pytest.mark.parametrize("action", ("release.execute", "settlement.execute"))
@pytest.mark.parametrize("rejected", (False, True))
def test_fresh_execution_simulation_normalizes_caller_but_preserves_contract_rejection(monkeypatch, action, rejected):
    from uuid import uuid4
    from web3.exceptions import ContractLogicError
    from pog_api import full_checks
    from pog_api.errors import APIError
    from pog_api.models import Procurement

    gate = gateway()
    ns = SimpleNamespace(id=uuid4())
    proc = SimpleNamespace(id=uuid4(), project_id=uuid4(), namespace_id=ns.id,
                           business_id="0x" + "00" * 32)
    project = SimpleNamespace(id=proc.project_id, namespace_id=ns.id)
    session = SimpleNamespace(get=lambda model, key: proc if model is Procurement else project)
    caller = LOWER
    step = SimpleNamespace(detail={"action": action, "caller": caller,
                                   "procurementUuid": str(proc.id)})
    before = deepcopy(step.detail)
    monkeypatch.setattr("pog_api.a2.validate_original_sources", lambda *_: None)
    monkeypatch.setattr(full_checks, "validate_settlement_sources", lambda *_, **kw: [])
    observed = []

    def eth_call(transaction, *args, **kwargs):
        observed.append(transaction)
        if rejected:
            raise ContractLogicError("execution reverted")
        return b""

    monkeypatch.setattr(gate.contracts["ProcurementEscrowV2"].w3.eth, "call", eth_call)
    if rejected:
        with pytest.raises(APIError) as error:
            full_checks.validate_tail_step(session, ns, step, SimpleNamespace(), gate)
        assert error.value.code == "human_approval_not_current"
    else:
        full_checks.validate_tail_step(session, ns, step, SimpleNamespace(), gate)
    assert observed[0]["from"] == Web3.to_checksum_address(caller)
    assert step.detail == before
