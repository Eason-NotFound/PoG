"""Read-only counterpart of the accepted M3.1 deployment verifier.

Uses fixed local artifacts and reconstructs every immutable at the deployment
block. A manifest's self-reported runtime hash alone is not a trust anchor.
"""
from __future__ import annotations

import hashlib
from typing import Any

from eth_abi import encode
from eth_utils import keccak
import rlp


def _hex(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return "0x" + bytes(value).hex()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mask(runtime: bytes, references: dict) -> bytes:
    result = bytearray(runtime)
    for entries in references.values():
        for entry in entries:
            start, length = entry["start"], entry["length"]
            _require(type(start) is int and type(length) is int and 0 <= start and 0 < length
                     and start + length <= len(runtime), "Invalid immutable reference")
            result[start:start + length] = bytes(length)
    return bytes(result)


def _canonical_references(references: dict) -> dict:
    groups = [sorted(entries, key=lambda entry: (entry["start"], entry["length"]))
              for entries in references.values()]
    groups.sort(key=lambda entries: tuple((entry["start"], entry["length"]) for entry in entries))
    return {f"immutableGroup{index}": entries for index, entries in enumerate(groups)}


def verify_deployment(w3, name: str, record: dict, artifact: dict, roles: dict, registry: str, contract) -> None:
    sender = roles["deployerOwner"]
    arguments = [] if name == "MockHKD" else [sender if name == "PoGRegistryV2" else registry]
    creation = bytes.fromhex(artifact["bytecode"]["object"].removeprefix("0x"))
    compiler = artifact["metadata"]["compiler"]["version"]
    _require(compiler == "0.8.24+commit.e11b9ed9", "Deployment compiler mismatch")
    _require(record["artifact"] == {
        "path": f"contracts/out/{name}.sol/{name}.json",
        "creationBytecodeKeccak256": "0x" + keccak(creation).hex(), "compilerVersion": compiler,
    }, "Manifest artifact mismatch")
    recorded_arguments = record["constructorArguments"]
    _require(isinstance(recorded_arguments, list) and len(recorded_arguments) == len(arguments)
             and all(isinstance(actual, str) and actual.lower() == expected.lower()
                     for actual, expected in zip(recorded_arguments, arguments)), "Constructor arguments mismatch")
    if arguments:
        creation += encode(["address"], arguments)
    deployment = record["deployment"]
    block_number = deployment["blockNumber"]
    _require(type(block_number) is int and block_number > 0, "Invalid deployment block")
    receipt = w3.eth.get_transaction_receipt(deployment["transactionHash"])
    _require(receipt is not None and receipt["status"] == 1, "Missing or reverted deployment receipt")
    _require(receipt["blockNumber"] == block_number
             and _hex(receipt["blockHash"]) == deployment["blockHash"].lower(), "Stale deployment receipt")
    _require(_hex(w3.eth.get_block(block_number)["hash"]) == deployment["blockHash"].lower(),
             "Deployment receipt is not canonical")
    _require(receipt["contractAddress"].lower() == record["address"].lower()
             and deployment["contractAddress"].lower() == record["address"].lower(), "Deployment address mismatch")
    transaction = w3.eth.get_transaction(deployment["transactionHash"])
    _require(transaction is not None and transaction["from"].lower() == sender.lower()
             and transaction["to"] is None and _hex(transaction["input"]) == _hex(creation),
             "Deployment creation transaction mismatch")
    predicted = "0x" + keccak(rlp.encode([bytes.fromhex(sender[2:]), transaction["nonce"]]))[-20:].hex()
    _require(predicted.lower() == record["address"].lower(), "Deployment CREATE address mismatch")
    runtime = bytes(w3.eth.get_code(record["address"]))
    template = bytes.fromhex(artifact["deployedBytecode"]["object"].removeprefix("0x"))
    references = artifact["deployedBytecode"].get("immutableReferences", {})
    _require(0 < len(runtime) <= 24_576 and len(runtime) == len(template), "Runtime size mismatch")
    _require(_mask(runtime, references) == _mask(template, references), "Runtime differs from accepted template")
    reconstructed = w3.eth.call({"from": sender, "data": "0x" + creation.hex(), "gas": 30_000_000}, block_number - 1)
    _require(bytes(reconstructed) == runtime, "Constructor-reconstructed runtime mismatch")
    expected_runtime = {
        "keccak256": "0x" + keccak(runtime).hex(), "sha256": hashlib.sha256(runtime).hexdigest(),
        "sizeBytes": len(runtime), "immutableReferences": _canonical_references(references),
        "templateMaskedSha256": hashlib.sha256(_mask(template, references)).hexdigest(),
        "verification": "accepted-template-mask AND historical-constructor-full-runtime",
    }
    _require(record["runtime"] == expected_runtime, "Manifest runtime proof mismatch")
    if name != "MockHKD":
        domain = contract.functions.eip712Domain().call()
        _require(bytes(domain[0]) == b"\x0f" and domain[1] == name and domain[2] == "2"
                 and domain[3] == 31337 and domain[4].lower() == record["address"].lower(),
                 "EIP712 V2 domain mismatch")
