"""Exact accepted V2 tail confirmations, pinned to the receipt block."""
from eth_abi import encode
from eth_utils import keccak
from web3 import Web3

TAIL_TARGETS = {
    "assessment.ai_final": "PoGRegistryV2", "approval.release": "ProcurementEscrowV2",
    "approval.settlement": "ProcurementEscrowV2", "release.execute": "ProcurementEscrowV2",
    "procurement.settlement": "PoGRegistryV2", "settlement.execute": "ProcurementEscrowV2",
    "payment.mint": "MockHKD", "payment.redeem": "MockHKD",
}
ZERO = "0x" + "00" * 32


def hx(value):
    if isinstance(value, str):
        return value.lower()
    return "0x" + bytes(value).hex()


def equal(a, b):
    return hx(a) == hx(b) if isinstance(a, (str, bytes, bytearray)) else a == b


def bundle_key(intent):
    return "0x" + keccak(encode(
        ["bytes32", "uint8", "bytes32", "bytes32", "uint32"],
        [bytes.fromhex(intent[0][2:]), int(intent[1]), bytes.fromhex(intent[2][2:]),
         bytes.fromhex(intent[3][2:]), int(intent[7])],
    )).hex()


def validate_tail(gate, detail, tx, receipt, event):
    action, args, ev = detail["action"], detail["args"], event["args"]
    block = int(receipt["blockNumber"])
    raw = gate.w3.eth.get_transaction(tx.tx_hash)
    if not (hx(raw["input"]) == hx(tx.calldata) and int(raw["nonce"]) == int(tx.evm_nonce_text)
            and int(raw["value"]) == int(tx.value_text) == 0
            and int(raw["chainId"]) == 31337
            and equal(event["blockHash"], receipt["blockHash"])
            and int(event["blockNumber"]) == block):
        raise ValueError("Canonical transaction differs from the persisted envelope")
    def call(contract, function, *values, at=None):
        return gate.call(contract, function, *values, block_identifier=block if at is None else at)
    if action in {"payment.mint", "payment.redeem"}:
        donor = args[0] if action == "payment.mint" else detail["caller"]
        sender = ZERO[:42] if action == "payment.mint" else donor
        recipient = args[0]
        amount = int(args[1])
        fixed_caller = gate.roles["relayer" if action == "payment.mint" else "foundation"]
        target = gate.roles["mockRedemption"] if action == "payment.redeem" else args[0]
        checks = [equal(detail["caller"], fixed_caller), equal(ev.get("from"), sender),
                  equal(ev.get("to"), target), int(ev.get("value", -1)) == amount]
        for wallet, delta in ((recipient, amount),) if action == "payment.mint" else ((donor, -amount), (recipient, amount)):
            before = int(call("MockHKD", "balanceOf", Web3.to_checksum_address(wallet), at=block - 1))
            after = int(call("MockHKD", "balanceOf", Web3.to_checksum_address(wallet)))
            checks.append(after - before == delta)
        if action == "payment.mint":
            checks.append(int(call("MockHKD", "totalSupply")) - int(call("MockHKD", "totalSupply", at=block - 1)) == amount)
    elif action == "assessment.ai_final":
        message = args[0]
        view = call("PoGRegistryV2", "getProcurement", message[1])
        assessment = call("PoGRegistryV2", "getAssessment", message[2])
        checks = [equal(ev.get(name), val) for name, val in zip(
            ("stage","procurementId","assessmentId","outcome","riskScoreBps","evidenceHash","reportHash","signer","deadline"),
            (*message[:8], message[9]))]
        stored = (message[2], message[1], message[0], *message[3:])
        checks += [equal(a,b) for a,b in zip(assessment, stored)]
        checks += [int(message[0]) == 1, equal(view[14], message[5]), equal(view[15], message[2]),
                   int(view[21]) == 7, bool(call("PoGRegistryV2", "aiSigners", message[7]))]
    elif action in {"approval.release", "approval.settlement"}:
        intent = args[1]
        view = call("PoGRegistryV2", "getProcurement", args[0])
        key = bundle_key(intent)
        release = action == "approval.release"
        terms = call("ProcurementEscrowV2", "releaseTermsHash" if release else "settlementTermsHash", args[0])
        ledger = call("ProcurementEscrowV2", "getLedger", view[1])
        checks = [equal(ev.get("targetId"), args[0]), int(ev.get("action",-1)) == (1 if release else 2),
                  equal(ev.get("bundleKey"), key), equal(ev.get("signer"), intent[4]),
                  int(ev.get("deadline",-1)) == int(intent[6]), equal(intent[0], args[0]),
                  equal(intent[2], terms), equal(intent[3], view[15] if release else ZERO),
                  int(intent[7]) == int(ledger[9]), int(view[21]) == (8 if release else 11),
                  bool(call("ProcurementEscrowV2", "isApprover", view[1], intent[4])),
                  int(call("ProcurementEscrowV2", "voteDeadline", key, intent[4])) == int(intent[6])]
    elif action == "release.execute":
        view = call("PoGRegistryV2", "getProcurement", args[0])
        project = call("PoGRegistryV2", "getProject", view[1])
        before = call("ProcurementEscrowV2", "getLedger", view[1], at=block - 1)
        after = call("ProcurementEscrowV2", "getLedger", view[1])
        amount = int(view[11])
        _, transfers = gate.receipt_with_events(tx.tx_hash, "Transfer")
        matches = [e for e in transfers if equal(e["address"], gate.contract_address("MockHKD"))
                   and equal(e["args"].get("from"), gate.contract_address("ProcurementEscrowV2"))
                   and equal(e["args"].get("to"), project[1]) and int(e["args"].get("value",-1)) == amount]
        checks = [equal(ev.get("procurementId"), args[0]), equal(ev.get("projectId"), view[1]),
                  equal(ev.get("foundation"), project[1]), int(ev.get("invoiceAmount",-1)) == amount,
                  int(ev.get("unusedReservation",-1)) == int(view[9]) - amount,
                  int(view[21]) == 9, int(view[20]) == 0, len(matches) == 1,
                  int(before[2])-int(after[2]) == int(view[9]), int(after[3])-int(before[3]) == amount]
        for wallet, delta in ((project[1], amount), (gate.contract_address("ProcurementEscrowV2"), -amount)):
            checks.append(int(call("MockHKD", "balanceOf", wallet)) -
                          int(call("MockHKD", "balanceOf", wallet, at=block-1)) == delta)
    elif action == "procurement.settlement":
        view = call("PoGRegistryV2", "getProcurement", args[0])
        project = call("PoGRegistryV2", "getProject", view[1])
        checks = [equal(ev.get("procurementId"), args[0]), equal(ev.get("conversionEvidenceHash"), args[1]),
                  equal(ev.get("paymentEvidenceHash"), args[2]), equal(ev.get("settlementHash"), view[18]),
                  equal(view[16],args[1]), equal(view[17],args[2]), int(view[21]) == 10,
                  int(view[20]) == 0, equal(detail["caller"],project[1])]
    elif action == "settlement.execute":
        view = call("PoGRegistryV2", "getProcurement", args[0])
        before = call("PoGRegistryV2", "getProject", view[1], at=block - 1)
        after = call("PoGRegistryV2", "getProject", view[1])
        checks = [equal(ev.get("procurementId"), args[0]), equal(ev.get("projectId"), view[1]),
                  int(ev.get("invoiceAmount",-1)) == int(view[11]), int(view[21]) == 12,
                  int(view[20]) == 0, int(before[8])-int(after[8]) == 1]
    else:
        raise ValueError("Unsupported tail confirmation")
    if not all(checks):
        raise ValueError("Tail event, balances or pinned getter differs from the queued intent")
