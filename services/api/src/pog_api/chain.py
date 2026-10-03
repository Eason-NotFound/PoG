from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from eth_utils import keccak
import requests
from web3 import HTTPProvider, Web3
from web3.logs import DISCARD

from .deployment_verification import verify_deployment


class ChainUnavailable(RuntimeError):
    pass


class ChainMismatch(ChainUnavailable):
    pass


@dataclass(frozen=True)
class PreparedEnvelope:
    caller: str
    to: str
    chain_id: int
    nonce: int
    data: str
    value: int
    action: str
    expected_event: str

    @property
    def hash(self) -> str:
        canonical = json.dumps(
            {
                "from": self.caller.lower(), "to": self.to.lower(), "chainId": self.chain_id,
                "nonce": str(self.nonce), "data": self.data.lower(), "value": str(self.value),
            },
            sort_keys=True, separators=(",", ":"),
        ).encode()
        return "0x" + keccak(canonical).hex()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hex(value: Any) -> str:
    if hasattr(value, "hex"):
        text = value.hex()
        return text if text.startswith("0x") else "0x" + text
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)) or hasattr(value, "hex") and not isinstance(value, str):
        return _hex(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class LocalChainGateway:
    """Restricted A2 adapter. It cannot accept arbitrary RPC, callers, targets or calldata."""

    ABI_PATHS = {
        "MockHKD": "packages/contract-abis/v2/MockHKD.json",
        "PoGRegistryV2": "packages/contract-abis/v2/PoGRegistryV2.json",
        "ProcurementEscrowV2": "packages/contract-abis/v2/ProcurementEscrowV2.json",
    }

    def __init__(self, manifest_path: Path, repository_root: Path):
        self.manifest_path = manifest_path.resolve()
        self.repository_root = repository_root.resolve()
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ChainUnavailable(f"Cannot read A2 manifest: {exc}") from exc
        self._manifest_sha256 = _sha256(self.manifest_path)
        chain = self.manifest.get("chain", {})
        rpc_url = chain.get("rpcUrl", "")
        parsed = urlparse(rpc_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ChainMismatch("A2 RPC must be loopback HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ChainMismatch("A2 RPC must be a plain loopback endpoint")
        if chain.get("chainId") != 31337:
            raise ChainMismatch("A2 requires chainId 31337")
        rpc_session = requests.Session()
        rpc_session.trust_env = False
        self.w3 = Web3(HTTPProvider(
            rpc_url, request_kwargs={"timeout": 5, "allow_redirects": False}, session=rpc_session,
        ))
        self.abis: dict[str, list[dict[str, Any]]] = {}
        self.contracts: dict[str, Any] = {}
        self.artifacts: dict[str, dict] = {}
        self._fixed_file_hashes: dict[Path, str] = {}
        for name, relative in self.ABI_PATHS.items():
            path = self.repository_root / relative
            record = self.manifest.get("contracts", {}).get(name, {})
            if record.get("abi", {}).get("path") != relative:
                raise ChainMismatch(f"{name} ABI path is not the accepted package")
            if record.get("abi", {}).get("sha256") != _sha256(path):
                raise ChainMismatch(f"{name} ABI digest mismatch")
            self.abis[name] = json.loads(path.read_text(encoding="utf-8"))
            address = Web3.to_checksum_address(record.get("address", ""))
            self.contracts[name] = self.w3.eth.contract(address=address, abi=self.abis[name])
            self._fixed_file_hashes[path] = _sha256(path)
        package_path = self.repository_root / "packages/contract-abis/v2/package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        self._fixed_file_hashes[package_path] = _sha256(package_path)
        for name, relative in self.ABI_PATHS.items():
            path = self.repository_root / f"contracts/out/{name}.sol/{name}.json"
            try:
                artifact = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ChainMismatch(f"Missing accepted {name} artifact; build the accepted contracts first") from exc
            if artifact["abi"] != self.abis[name]:
                raise ChainMismatch(f"{name} generated artifact ABI mismatch")
            record = self.manifest["contracts"][name]
            if record["abi"] != {"path": relative, "sha256": self._fixed_file_hashes[self.repository_root / relative],
                                 "artifactId": f"{package['name']}@{package['version']}"}:
                raise ChainMismatch(f"{name} ABI package identity mismatch")
            self.artifacts[name] = artifact
            self._fixed_file_hashes[path] = _sha256(path)
        self.verify()

    @property
    def run_id(self) -> str:
        return str(self.manifest["runId"])

    @property
    def instance_id(self) -> str:
        return str(self.manifest["chain"]["instanceId"])

    @property
    def manifest_sha256(self) -> str:
        return _sha256(self.manifest_path)

    @property
    def rpc_url(self) -> str:
        return str(self.manifest["chain"]["rpcUrl"])

    @property
    def roles(self) -> dict[str, str]:
        return {key: Web3.to_checksum_address(value) for key, value in self.manifest["roles"].items()}

    def verify(self) -> None:
        if _sha256(self.manifest_path) != self._manifest_sha256:
            raise ChainMismatch("Deployment manifest changed; restart and verify explicitly")
        for path, expected in self._fixed_file_hashes.items():
            if _sha256(path) != expected:
                raise ChainMismatch("Accepted artifact or ABI file changed")
        if not self.w3.is_connected():
            raise ChainUnavailable("A2 local RPC is unavailable")
        if self.w3.eth.chain_id != 31337:
            raise ChainMismatch("RPC chainId changed")
        genesis = _hex(self.w3.eth.get_block(0)["hash"])
        if genesis.lower() != str(self.manifest["chain"]["genesisHash"]).lower():
            raise ChainMismatch("RPC genesis hash changed")
        metadata = self.w3.provider.make_request("anvil_metadata", [])
        actual_instance = (metadata.get("result") or {}).get("instanceId")
        if actual_instance != self.instance_id:
            raise ChainMismatch("Anvil instanceId changed")
        role_values = [value.lower() for value in self.roles.values()]
        if len(role_values) != 10 or len(set(role_values)) != 10:
            raise ChainMismatch("Manifest roles must be ten distinct wallets")
        role_names = ("deployerOwner", "foundation", "recipient", "aiSigner", "humanApprover",
                      "donorA", "donorB", "vendor", "relayer", "mockRedemption")
        accounts = self.w3.eth.accounts
        if set(self.roles) != set(role_names) or len(accounts) != len(role_names) or any(
            self.roles[name].lower() != accounts[index].lower() for index, name in enumerate(role_names)
        ):
            raise ChainMismatch("Manifest role mapping differs from managed Anvil accounts")
        if self.manifest.get("schemaVersion") != 1 or self.manifest.get("version") != {
            "acceptedTag": "blockchain-v0.2.0-m2",
            "acceptedMerge": "61aa673653dd31188d2627d76cbba3f97fed6137",
            "technicalSnapshot": "810a54ea9d17c5ac80f1974035f690e49941e1e1",
        }:
            raise ChainMismatch("Manifest accepted contract version mismatch")
        if self.manifest.get("foundry") != {
            "version": "1.8.4", "commit": "50af4efe189dc64bad2b75ed6990b835de66c4ae",
            "hardfork": "cancun", "eip170LimitBytes": 24_576,
        }:
            raise ChainMismatch("Manifest pinned tooling declaration mismatch")
        registry = self.contracts["PoGRegistryV2"]
        escrow = self.contracts["ProcurementEscrowV2"]
        token = self.contracts["MockHKD"]
        for name, contract in self.contracts.items():
            code = bytes(self.w3.eth.get_code(contract.address))
            if not code:
                raise ChainMismatch(f"{name} has no runtime code")
            expected = self.manifest["contracts"][name]["runtime"]["keccak256"]
            if "0x" + keccak(code).hex() != expected:
                raise ChainMismatch(f"{name} runtime digest mismatch")
            try:
                verify_deployment(self.w3, name, self.manifest["contracts"][name],
                                  self.artifacts[name], self.roles, registry.address, contract)
            except Exception as exc:
                raise ChainMismatch(f"{name} deployment verification failed: {exc}") from exc
        if registry.functions.escrow().call().lower() != escrow.address.lower():
            raise ChainMismatch("Registry/Escrow binding mismatch")
        if escrow.functions.registry().call().lower() != registry.address.lower():
            raise ChainMismatch("Escrow/Registry binding mismatch")
        if token.functions.decimals().call() != 6:
            raise ChainMismatch("MockHKD decimals mismatch")
        if not registry.functions.aiSigners(self.roles["aiSigner"]).call():
            raise ChainMismatch("Manifest AI signer is not allowlisted")

    def contract_address(self, name: str) -> str:
        return self.contracts[name].address

    def latest_timestamp(self) -> int:
        self.verify()
        return int(self.w3.eth.get_block("latest")["timestamp"])

    def call(self, contract: str, function: str, *args: Any) -> Any:
        self.verify()
        return getattr(self.contracts[contract].functions, function)(*args).call()

    def prepare(self, action: str, caller: str, args: list[Any], expected_event: str) -> PreparedEnvelope:
        self.verify()
        mapping = {
            "project.create": ("PoGRegistryV2", "createProject"),
            "donation.approve": ("MockHKD", "approve"),
            "donation.deposit": ("ProcurementEscrowV2", "deposit"),
            "procurement.create": ("PoGRegistryV2", "createProcurement"),
            "procurement.po": ("PoGRegistryV2", "recordPurchaseOrder"),
            "assessment.ai_pre": ("PoGRegistryV2", "submitAIAssessment"),
            "approval.reserve": ("ProcurementEscrowV2", "submitReserveApproval"),
            "reserve.execute": ("ProcurementEscrowV2", "executeReserve"),
            "procurement.invoice": ("PoGRegistryV2", "recordInvoiceAndGoods"),
            "receipt.submit": ("PoGRegistryV2", "submitRecipientReceipt"),
        }
        if action not in mapping:
            raise ValueError("Unsupported A2 chain action")
        contract_name, function_name = mapping[action]
        contract = self.contracts[contract_name]
        function = getattr(contract.functions, function_name)(*args)
        nonce = self.w3.eth.get_transaction_count(Web3.to_checksum_address(caller), "pending")
        return PreparedEnvelope(
            caller=Web3.to_checksum_address(caller), to=contract.address,
            chain_id=self.w3.eth.chain_id, nonce=nonce,
            data=function._encode_transaction_data(), value=0,
            action=action, expected_event=expected_event,
        )

    def send(self, envelope: PreparedEnvelope) -> str:
        self.verify()
        tx_hash = self.w3.eth.send_transaction(
            {"from": envelope.caller, "to": envelope.to, "chainId": envelope.chain_id,
             "nonce": envelope.nonce, "data": envelope.data, "value": envelope.value}
        )
        return _hex(tx_hash)

    def find_envelope_transaction(self, envelope: PreparedEnvelope, lookback: int = 128) -> str | None:
        """Reconcile an unknown outcome by exact caller/nonce/to/data/value; never guesses by nonce alone."""
        self.verify()
        latest = self.w3.eth.block_number
        for number in range(latest, max(-1, latest - lookback), -1):
            block = self.w3.eth.get_block(number, full_transactions=True)
            for tx in block["transactions"]:
                if (
                    tx["from"].lower() == envelope.caller.lower()
                    and int(tx["nonce"]) == envelope.nonce
                    and (tx.get("to") or "").lower() == envelope.to.lower()
                    and _hex(tx["input"]).lower() == envelope.data.lower()
                    and int(tx["value"]) == envelope.value
                    and int(tx["chainId"]) == envelope.chain_id
                ):
                    return _hex(tx["hash"])
        return None

    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None
        return {
            "transactionHash": _hex(receipt["transactionHash"]),
            "transactionIndex": int(receipt["transactionIndex"]),
            "blockHash": _hex(receipt["blockHash"]),
            "blockNumber": int(receipt["blockNumber"]),
            "from": receipt["from"], "to": receipt["to"],
            "status": int(receipt["status"]),
            "logs": [dict(log) for log in receipt["logs"]],
        }

    def canonical(self, block_number: int, block_hash: str) -> bool:
        try:
            return _hex(self.w3.eth.get_block(block_number)["hash"]).lower() == block_hash.lower()
        except Exception:
            return False

    def block_identity(self, block_number: int) -> tuple[str, str]:
        block = self.w3.eth.get_block(block_number)
        return _hex(block["hash"]), _hex(block["parentHash"])

    def deployment_start_block(self) -> int:
        return min(
            int(value["deployment"]["blockNumber"])
            for value in self.manifest["contracts"].values()
        )

    def events_in_range(self, start: int, end: int) -> list[dict[str, Any]]:
        self.verify()
        found: list[dict[str, Any]] = []
        for contract in self.contracts.values():
            for item in contract.abi:
                if item.get("type") != "event":
                    continue
                event_name = item["name"]
                event = getattr(contract.events, event_name)()
                for entry in event.get_logs(from_block=start, to_block=end):
                    found.append({
                        "address": entry["address"], "event": event_name,
                        "args": _jsonable(dict(entry["args"])),
                        "logIndex": int(entry["logIndex"]),
                        "transactionIndex": int(entry["transactionIndex"]),
                        "transactionHash": _hex(entry["transactionHash"]),
                        "blockHash": _hex(entry["blockHash"]),
                        "blockNumber": int(entry["blockNumber"]),
                    })
        return sorted(found, key=lambda row: (
            row["blockNumber"], row["transactionIndex"], row["logIndex"]
        ))

    def decode_expected_event(self, receipt: dict[str, Any], event_name: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for contract in self.contracts.values():
            try:
                event = getattr(contract.events, event_name)()
            except Exception:
                continue
            try:
                for entry in event.process_receipt(receipt, errors=DISCARD):
                    found.append({
                        "address": entry["address"], "event": event_name,
                        "args": _jsonable(dict(entry["args"])),
                        "logIndex": int(entry["logIndex"]),
                        "transactionIndex": int(entry["transactionIndex"]),
                        "transactionHash": _hex(entry["transactionHash"]),
                        "blockHash": _hex(entry["blockHash"]),
                        "blockNumber": int(entry["blockNumber"]),
                    })
            except Exception:
                continue
        return found

    def receipt_with_events(self, tx_hash: str, event_name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        try:
            raw = self.w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None, []
        serial = {
            "transactionHash": _hex(raw["transactionHash"]),
            "transactionIndex": int(raw["transactionIndex"]),
            "blockHash": _hex(raw["blockHash"]),
            "blockNumber": int(raw["blockNumber"]),
            "from": raw["from"], "to": raw["to"], "status": int(raw["status"]),
        }
        return serial, self.decode_expected_event(raw, event_name)

    def sign_typed_data(self, signer: str, data: dict[str, Any]) -> str:
        self.verify()
        response = self.w3.provider.make_request(
            "eth_signTypedData_v4", [Web3.to_checksum_address(signer), json.dumps(data)]
        )
        if response.get("error"):
            raise ChainUnavailable(str(response["error"]))
        return str(response["result"])
