"""Pure fake-RPC regression candidates for the read-only A2 deployment gate.

No HTTP, database, chain, files under the repository, or signing key is used.
The RPC responses are explicit evidence fixtures, not live acceptance evidence.
"""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

from eth_abi import encode
from eth_utils import keccak, to_checksum_address
import pytest
import rlp

from pog_api.deployment_verification import verify_deployment


OWNER = to_checksum_address("0x1234567890abcdef1234567890abcdef12345678")
REGISTRY = to_checksum_address("0xabcdef1234567890abcdef1234567890abcdef12")
BLOCK_HASH = b"\x77" * 32
TX_HASH = "0x" + "66" * 32
COMPILER = "0.8.24+commit.e11b9ed9"


class FakeEth:
    def __init__(self, receipt, transaction, runtime):
        self.receipt = receipt
        self.transaction = transaction
        self.runtime = runtime
        self.reconstructed = runtime
        self.block_hash = BLOCK_HASH
        self.creation_calls = []

    def get_transaction_receipt(self, transaction_hash):
        assert transaction_hash == TX_HASH
        return self.receipt

    def get_transaction(self, transaction_hash):
        assert transaction_hash == TX_HASH
        return self.transaction

    def get_block(self, number):
        assert number == 7
        return {"hash": self.block_hash}

    def get_code(self, address):
        assert address.lower() == self.receipt["contractAddress"].lower()
        return self.runtime

    def call(self, envelope, block_identifier):
        self.creation_calls.append((deepcopy(envelope), block_identifier))
        return self.reconstructed


class FakeDomainCall:
    def __init__(self, domain):
        self.domain = domain

    def call(self):
        return self.domain


class FakeFunctions:
    def __init__(self, domain):
        self.domain = domain
        self.domain_calls = 0

    def eip712Domain(self):
        self.domain_calls += 1
        return FakeDomainCall(self.domain)


@pytest.fixture
def fixture_factory():
    def build(name="PoGRegistryV2"):
        nonce = 4
        deployed_address = to_checksum_address(
            "0x" + keccak(rlp.encode([bytes.fromhex(OWNER[2:]), nonce]))[-20:].hex()
        )
        creation = bytes.fromhex("600160005560006000f3")
        template = bytes.fromhex("6000000000600160005260206000f3")
        runtime = bytearray(template)
        runtime[1:5] = bytes.fromhex("11223344")
        runtime = bytes(runtime)
        references = {"compiler_id_87": [{"start": 1, "length": 4}]}
        artifact = {
            "bytecode": {"object": "0x" + creation.hex()},
            "deployedBytecode": {
                "object": "0x" + template.hex(), "immutableReferences": references,
            },
            "metadata": {"compiler": {"version": COMPILER}},
        }
        constructor_arguments = [] if name == "MockHKD" else [
            OWNER if name == "PoGRegistryV2" else REGISTRY
        ]
        input_bytes = creation + (encode(["address"], constructor_arguments) if constructor_arguments else b"")
        masked_template = bytearray(template)
        masked_template[1:5] = bytes(4)
        record = {
            "address": deployed_address,
            "artifact": {
                "path": f"contracts/out/{name}.sol/{name}.json",
                "creationBytecodeKeccak256": "0x" + keccak(creation).hex(),
                "compilerVersion": COMPILER,
            },
            "constructorArguments": constructor_arguments,
            "deployment": {
                "blockNumber": 7, "blockHash": "0x" + BLOCK_HASH.hex(),
                "transactionHash": TX_HASH, "contractAddress": deployed_address,
            },
            "runtime": {
                "keccak256": "0x" + keccak(runtime).hex(),
                "sha256": hashlib.sha256(runtime).hexdigest(),
                "sizeBytes": len(runtime),
                "immutableReferences": {"immutableGroup0": [{"start": 1, "length": 4}]},
                "templateMaskedSha256": hashlib.sha256(masked_template).hexdigest(),
                "verification": "accepted-template-mask AND historical-constructor-full-runtime",
            },
        }
        receipt = {
            "status": 1, "blockNumber": 7, "blockHash": BLOCK_HASH,
            "contractAddress": deployed_address,
        }
        transaction = {"from": OWNER, "to": None, "input": input_bytes, "nonce": nonce}
        eth = FakeEth(receipt, transaction, runtime)
        functions = FakeFunctions([b"\x0f", name, "2", 31337, deployed_address, bytes(32), []])
        return SimpleNamespace(
            name=name, record=record, artifact=artifact, roles={"deployerOwner": OWNER},
            registry=REGISTRY, eth=eth, w3=SimpleNamespace(eth=eth),
            contract=SimpleNamespace(functions=functions), input_bytes=input_bytes,
        )
    return build


def check(fixture):
    verify_deployment(
        fixture.w3, fixture.name, fixture.record, fixture.artifact, fixture.roles,
        fixture.registry, fixture.contract,
    )


@pytest.mark.parametrize("name", ["MockHKD", "PoGRegistryV2", "ProcurementEscrowV2"])
def test_valid_contract_families_and_historical_constructor_call(fixture_factory, name):
    fixture = fixture_factory(name)
    check(fixture)
    assert fixture.eth.creation_calls == [({
        "from": OWNER, "data": "0x" + fixture.input_bytes.hex(), "gas": 30_000_000,
    }, 6)]
    assert fixture.contract.functions.domain_calls == (0 if name == "MockHKD" else 1)


@pytest.mark.parametrize("name", ["PoGRegistryV2", "ProcurementEscrowV2"])
def test_lowercase_constructor_addresses_are_valid(fixture_factory, name):
    fixture = fixture_factory(name)
    fixture.record["constructorArguments"] = [value.lower() for value in fixture.record["constructorArguments"]]
    assert fixture.record["constructorArguments"][0] != (OWNER if name == "PoGRegistryV2" else REGISTRY)
    check(fixture)


@pytest.mark.parametrize("arguments", [[], [None], [REGISTRY], [OWNER, OWNER], OWNER])
def test_wrong_constructor_arguments_are_rejected(fixture_factory, arguments):
    fixture = fixture_factory()
    fixture.record["constructorArguments"] = arguments
    with pytest.raises(ValueError, match="Constructor arguments mismatch"):
        check(fixture)


@pytest.mark.parametrize("receipt", [None, {"status": 0}])
def test_missing_or_reverted_receipt_is_rejected(fixture_factory, receipt):
    fixture = fixture_factory()
    fixture.eth.receipt = receipt
    with pytest.raises(ValueError, match="Missing or reverted deployment receipt"):
        check(fixture)


@pytest.mark.parametrize("field,value", [("blockNumber", 8), ("blockHash", b"\x88" * 32)])
def test_stale_receipt_is_rejected(fixture_factory, field, value):
    fixture = fixture_factory()
    fixture.eth.receipt[field] = value
    with pytest.raises(ValueError, match="Stale deployment receipt"):
        check(fixture)


def test_noncanonical_receipt_is_rejected_even_when_manifest_matches(fixture_factory):
    fixture = fixture_factory()
    fixture.eth.block_hash = b"\x88" * 32
    with pytest.raises(ValueError, match="Deployment receipt is not canonical"):
        check(fixture)


@pytest.mark.parametrize("target", ["receipt", "manifest_deployment"])
def test_inconsistent_deployment_address_is_rejected(fixture_factory, target):
    fixture = fixture_factory()
    destination = fixture.eth.receipt if target == "receipt" else fixture.record["deployment"]
    destination["contractAddress"] = REGISTRY
    with pytest.raises(ValueError, match="Deployment address mismatch"):
        check(fixture)


@pytest.mark.parametrize("field,value", [
    ("from", REGISTRY), ("to", REGISTRY), ("input", b"\x00"),
])
def test_incorrect_create_transaction_is_rejected(fixture_factory, field, value):
    fixture = fixture_factory()
    fixture.eth.transaction[field] = value
    with pytest.raises(ValueError, match="Deployment creation transaction mismatch"):
        check(fixture)


def test_missing_creation_transaction_is_rejected(fixture_factory):
    fixture = fixture_factory()
    fixture.eth.transaction = None
    with pytest.raises(ValueError, match="Deployment creation transaction mismatch"):
        check(fixture)


def test_hex_creation_input_is_equivalent_to_bytes(fixture_factory):
    fixture = fixture_factory()
    fixture.eth.transaction["input"] = "0x" + fixture.input_bytes.hex().upper()
    check(fixture)


def test_create_nonce_must_derive_recorded_address(fixture_factory):
    fixture = fixture_factory()
    fixture.eth.transaction["nonce"] += 1
    with pytest.raises(ValueError, match="Deployment CREATE address mismatch"):
        check(fixture)


def test_tampered_immutable_cannot_pass_mask_with_self_reported_hashes(fixture_factory):
    fixture = fixture_factory()
    forged_runtime = bytearray(fixture.eth.runtime)
    forged_runtime[1] ^= 1
    fixture.eth.runtime = bytes(forged_runtime)
    fixture.record["runtime"]["keccak256"] = "0x" + keccak(fixture.eth.runtime).hex()
    fixture.record["runtime"]["sha256"] = hashlib.sha256(fixture.eth.runtime).hexdigest()
    # Immutable bytes are deliberately masked in the template comparison. The
    # accepted creation input's reconstructed FULL runtime is the second gate.
    with pytest.raises(ValueError, match="Constructor-reconstructed runtime mismatch"):
        check(fixture)


def test_nonimmutable_runtime_mutation_is_rejected_by_template(fixture_factory):
    fixture = fixture_factory()
    forged_runtime = bytearray(fixture.eth.runtime)
    forged_runtime[-1] ^= 1
    fixture.eth.runtime = bytes(forged_runtime)
    with pytest.raises(ValueError, match="Runtime differs from accepted template"):
        check(fixture)


@pytest.mark.parametrize("runtime", [b"", b"\x00", bytes(24_577)], ids=["empty", "wrong-size", "over-eip170-limit"])
def test_empty_wrong_size_and_eip170_runtime_are_rejected(fixture_factory, runtime):
    fixture = fixture_factory()
    fixture.eth.runtime = runtime
    with pytest.raises(ValueError, match="Runtime size mismatch"):
        check(fixture)


@pytest.mark.parametrize("field,value", [
    ("keccak256", "0x" + "00" * 32), ("sha256", "00" * 32),
    ("sizeBytes", 999), ("immutableReferences", {}),
    ("templateMaskedSha256", "00" * 32), ("verification", "manifest-only"),
])
def test_manifest_runtime_proof_cannot_replace_computed_evidence(fixture_factory, field, value):
    fixture = fixture_factory()
    fixture.record["runtime"][field] = value
    with pytest.raises(ValueError, match="Manifest runtime proof mismatch"):
        check(fixture)


@pytest.mark.parametrize("index,value", [
    (0, b"\x07"), (1, "PoGRegistry"), (2, "1"),
    (3, 31338), (4, REGISTRY),
])
def test_each_required_eip712_domain_field_is_verified(fixture_factory, index, value):
    fixture = fixture_factory()
    fixture.contract.functions.domain[index] = value
    with pytest.raises(ValueError, match="EIP712 V2 domain mismatch"):
        check(fixture)


def test_domain_verifying_contract_address_case_is_compatible(fixture_factory):
    fixture = fixture_factory()
    fixture.contract.functions.domain[4] = fixture.record["address"].lower()
    check(fixture)


@pytest.mark.parametrize("reference", [
    {"start": -1, "length": 4}, {"start": 1, "length": 0},
    {"start": 1, "length": 999}, {"start": True, "length": 4},
])
def test_invalid_immutable_ranges_fail_closed(fixture_factory, reference):
    fixture = fixture_factory()
    fixture.artifact["deployedBytecode"]["immutableReferences"] = {"bad": [reference]}
    with pytest.raises(ValueError, match="Invalid immutable reference"):
        check(fixture)
