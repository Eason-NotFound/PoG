from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any
from urllib.parse import urlparse

from eth_utils import keccak
import requests
from web3 import HTTPProvider, Web3
from web3.exceptions import BadResponseFormat, ProviderConnectionError, TransactionNotFound, Web3RPCError
from web3.logs import DISCARD

from .deployment_verification import verify_deployment


class ChainUnavailable(RuntimeError):
    pass


class ChainMismatch(ChainUnavailable):
    pass


class ChainNotBroadcast(ChainUnavailable):
    """The adapter proved failure before attempting eth_sendTransaction."""


def _abi_addresses(inputs: list[dict], values: list[Any]) -> list[Any]:
    """Normalize only ABI address leaves, without changing identity or source data."""
    if len(inputs) != len(values):
        raise ValueError("ABI argument count mismatch")

    def normalize(spec, value):
        kind = spec["type"]
        if kind.endswith("]"):
            if not isinstance(value, (tuple, list)):
                raise ValueError("ABI array must be a sequence")
            element = {**spec, "type": kind[:kind.rindex("[")]}
            return [normalize(element, item) for item in value]
        if kind == "tuple":
            components = spec["components"]
            if isinstance(value, dict):
                if set(value) != {part["name"] for part in components}:
                    raise ValueError("ABI tuple fields mismatch")
                return {part["name"]: normalize(part, value[part["name"]]) for part in components}
            if not isinstance(value, (tuple, list)):
                raise ValueError("ABI tuple must be a sequence or named fields")
            return _abi_addresses(components, list(value))
        if kind == "address":
            if not isinstance(value, str) or not Web3.is_address(value):
                raise ValueError("ABI address must be a valid 20-byte hex address")
            return Web3.to_checksum_address(value)
        return value

    return [normalize(spec, value) for spec, value in zip(inputs, values)]


def _rpc_read(method):
    """Transport uncertainty is an unavailable dependency, never a missing fact."""
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ChainUnavailable:
            raise
        except (requests.RequestException, OSError, TimeoutError,
                BadResponseFormat, ProviderConnectionError, Web3RPCError) as exc:
            raise ChainUnavailable("Local RPC read is temporarily unavailable") from exc
    return guarded


class _LoopbackSession(requests.Session):
    """Never follow a redirect, even when a provider supplies its own kwargs."""

    def send(self, request: Any, **kwargs: Any) -> requests.Response:
        kwargs["allow_redirects"] = False
        response = super().send(request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise ChainMismatch("Loopback RPC redirects are forbidden")
        return response


def _loopback_port(rpc_url: Any) -> int:
    if not isinstance(rpc_url, str):
        raise ChainMismatch("A2 RPC must be loopback HTTP")
    try:
        parsed = urlparse(rpc_url)
        port = parsed.port
    except ValueError as exc:
        raise ChainMismatch("A2 RPC must be loopback HTTP") from exc
    # The accepted manager binds to this literal IP. Do not resolve localhost or
    # accept URL credentials, alternate paths, query strings or redirect targets.
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.params or parsed.query or parsed.fragment
        or port is None or not 1024 <= port <= 65535
        or rpc_url != f"http://127.0.0.1:{port}"
    ):
        raise ChainMismatch("A2 RPC must be the managed loopback HTTP URL")
    return port


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
    ROLE_NAMES = (
        "deployerOwner", "foundation", "recipient", "aiSigner", "humanApprover",
        "donorA", "donorB", "vendor", "relayer", "mockRedemption",
    )
    # Immutable accepted M3.1 verifier, not the manifest's own assertion of trust.
    ACCEPTED_VERIFIER_SHA256 = "e3dbeb6bb92bfc739b1a6582c6d7f5baf08ac05fa1164a683259ac6bc5143586"
    _verified_fingerprints: OrderedDict[str, None] = OrderedDict()
    _verification_lock = threading.Lock()

    def __init__(self, manifest_path: Path, repository_root: Path):
        if manifest_path.is_symlink():
            raise ChainMismatch("A2 manifest cannot be a symlink")
        self.manifest_path = manifest_path.resolve()
        self.repository_root = repository_root.resolve()
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ChainUnavailable(f"Cannot read A2 manifest: {exc}") from exc
        chain = self.manifest.get("chain", {})
        rpc_url = chain.get("rpcUrl", "")
        self._rpc_port = _loopback_port(rpc_url)
        if chain.get("chainId") != 31337:
            raise ChainMismatch("A2 requires chainId 31337")
        rpc_session = _LoopbackSession()
        rpc_session.trust_env = False
        self.w3 = Web3(HTTPProvider(
            rpc_url, request_kwargs={"timeout": 5, "allow_redirects": False}, session=rpc_session,
            exception_retry_configuration=None,
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
        self._verify_manifest_version()
        self._manifest_digest = _sha256(self.manifest_path)
        self._manifest_object_digest = hashlib.sha256(
            json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not self.w3.is_connected():
            raise ChainUnavailable("A2 local RPC is unavailable")
        self._startup_gate()
        self._verify_artifact_deployments()
        self.verify()

    def _verify_manifest_version(self) -> None:
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

    @_rpc_read
    def _verify_artifact_deployments(self) -> None:
        # Preserve the independent constructor/immutable proof at every gateway
        # startup, even when the accepted CLI verification fingerprint is cached.
        # Fresh receipt/runtime/domain/bootstrap checks below remain uncached.
        registry = self.contracts["PoGRegistryV2"]
        for name, contract in self.contracts.items():
            try:
                verify_deployment(
                    self.w3, name, self.manifest["contracts"][name],
                    self.artifacts[name], self.roles, registry.address, contract,
                )
            except TransactionNotFound as exc:
                raise ChainMismatch(f"{name} deployment transaction is missing") from exc
            except (requests.RequestException, OSError, TimeoutError,
                    BadResponseFormat, ProviderConnectionError, Web3RPCError):
                # Some provider decode errors also inherit ValueError. Preserve
                # transport uncertainty before interpreting proof failures below.
                raise
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ChainMismatch(f"{name} deployment verification failed: {exc}") from exc

    def _artifact_fingerprint(self) -> str:
        """Invalidate startup approval whenever local verification inputs change."""
        verifier = self.repository_root / "scripts/local-chain.py"
        if _sha256(verifier) != self.ACCEPTED_VERIFIER_SHA256:
            raise ChainMismatch("Accepted M3.1 verifier fingerprint mismatch")
        paths = {
            verifier, self.manifest_path, self.manifest_path.parent / "state.json",
            self.repository_root / "packages/contract-abis/v2/package.json",
            Path(__file__).with_name("deployment_verification.py"),
        }
        for relative in self.ABI_PATHS.values():
            paths.add(self.repository_root / relative)
        for name in self.ABI_PATHS:
            paths.add(self.repository_root / f"contracts/out/{name}.sol/{name}.json")
        for baseline in ("blockchain-m1.sha256", "blockchain-m2-v2-rc.1.sha256"):
            path = self.repository_root / "docs/baselines" / baseline
            paths.add(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#"):
                    paths.add(self.repository_root / line.split(maxsplit=1)[1].lstrip("*"))
        digest = hashlib.sha256()
        for path in sorted(paths):
            digest.update(str(path).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _run_accepted_verifier(self) -> None:
        # The accepted command anchors schema/version, owned PID, pinned tools,
        # artifacts and constructors (including all immutable slots), canonical
        # deployments/bootstrap, domain, owner, roles and mutual binding.
        if self.manifest_path.name != "manifest.json":
            raise ChainMismatch("A2 requires the managed manifest.json and adjacent owned state")
        environment = {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "TMPDIR", "SYSTEMROOT"}
        }
        try:
            result = subprocess.run(
                [sys.executable, str(self.repository_root / "scripts/local-chain.py"),
                 "verify", "--port", str(self._rpc_port),
                 "--state-dir", str(self.manifest_path.parent)],
                cwd=self.repository_root, env=environment,
                capture_output=True, text=True, timeout=45, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ChainUnavailable("Accepted local deployment verification is unavailable") from exc
        if result.returncode != 0:
            raise ChainMismatch("Accepted local deployment verification failed: " + result.stderr.strip()[:600])
        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise ChainMismatch("Accepted deployment verifier returned an invalid report") from exc
        if (
            report.get("status") != "ready" or report.get("runId") != self.run_id
            or report.get("instanceId") != self.instance_id
            or report.get("rpcUrl") != self.rpc_url or report.get("chainId") != 31337
            or Path(report.get("manifestPath", "")).resolve() != self.manifest_path
        ):
            raise ChainMismatch("Accepted deployment report does not identify this manifest")

    def _startup_gate(self) -> None:
        try:
            fingerprint = self._artifact_fingerprint()
            with self._verification_lock:
                if fingerprint not in self._verified_fingerprints:
                    self._run_accepted_verifier()
                    if self._artifact_fingerprint() != fingerprint:
                        raise ChainMismatch("Deployment inputs changed during verification")
                    self._verified_fingerprints[fingerprint] = None
                    if len(self._verified_fingerprints) > 32:
                        self._verified_fingerprints.popitem(last=False)
                self._verified_fingerprints.move_to_end(fingerprint)
            self._verified_fingerprint = fingerprint
        except ChainUnavailable:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ChainMismatch("Incomplete or invalid managed deployment inputs") from exc

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
        try:
            self._verify_fresh_deployment()
        except ChainUnavailable:
            raise
        except Exception as exc:
            raise ChainUnavailable("Fresh local deployment verification is unavailable or invalid") from exc

    def _manifest_receipt(self, recorded: dict[str, Any]) -> Any:
        try:
            receipt = self.w3.eth.get_transaction_receipt(recorded["transactionHash"])
        except TransactionNotFound as exc:
            raise ChainMismatch("Recorded deployment/bootstrap transaction is missing") from exc
        if receipt is None:
            raise ChainMismatch("Recorded deployment/bootstrap receipt is missing")
        return receipt

    def _verify_fresh_deployment(self) -> None:
        # Successful startup verification is reusable only for these exact local
        # inputs. Chain/instance/canonical/runtime/authority checks remain fresh.
        if (
            _sha256(self.manifest_path) != self._manifest_digest
            or hashlib.sha256(json.dumps(
                self.manifest, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest() != self._manifest_object_digest
            or self._artifact_fingerprint() != self._verified_fingerprint
        ):
            raise ChainMismatch("Verified deployment inputs changed; reopen the gateway")
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
        roles = self.roles
        accounts = self.w3.eth.accounts
        role_values = [value.lower() for value in roles.values()]
        if (
            set(roles) != set(self.ROLE_NAMES) or len(accounts) != 10
            or len(role_values) != 10 or len(set(role_values)) != 10
            or any(roles[name].lower() != accounts[index].lower()
                   for index, name in enumerate(self.ROLE_NAMES))
        ):
            raise ChainMismatch("Manifest roles must be ten distinct wallets")
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
            recorded = self.manifest["contracts"][name]["deployment"]
            receipt = self._manifest_receipt(recorded)
            if (
                int(receipt["status"]) != 1
                or _hex(receipt["blockHash"]).lower() != recorded["blockHash"].lower()
                or int(receipt["blockNumber"]) != recorded["blockNumber"]
                or (receipt.get("contractAddress") or "").lower() != contract.address.lower()
                or not self.canonical(recorded["blockNumber"], recorded["blockHash"])
            ):
                raise ChainMismatch(f"{name} deployment receipt is not canonical")
            if name != "MockHKD":
                domain = contract.functions.eip712Domain().call()
                if (
                    bytes(domain[0]) != b"\x0f" or domain[1] != name or domain[2] != "2"
                    or int(domain[3]) != 31337
                    or domain[4].lower() != contract.address.lower()
                ):
                    raise ChainMismatch(f"{name} EIP712 V2 domain mismatch")
        expected_bootstrap = {
            "bindEscrow": (registry, registry.functions.bindEscrow(escrow.address)),
            "allowAISigner": (registry, registry.functions.setAISigner(roles["aiSigner"], True)),
            "mintDonorA": (token, token.functions.mint(roles["donorA"], 1_000_000_000)),
            "mintDonorB": (token, token.functions.mint(roles["donorB"], 1_000_000_000)),
        }
        bootstrap = self.manifest["bootstrap"]["transactions"]
        if set(bootstrap) != set(expected_bootstrap):
            raise ChainMismatch("Invalid bootstrap transaction manifest")
        for name, (contract, function) in expected_bootstrap.items():
            recorded = bootstrap[name]
            receipt = self._manifest_receipt(recorded)
            try:
                transaction = self.w3.eth.get_transaction(recorded["transactionHash"])
            except TransactionNotFound as exc:
                raise ChainMismatch(f"{name} bootstrap transaction is missing") from exc
            if transaction is None:
                raise ChainMismatch(f"{name} bootstrap transaction is missing")
            if (
                int(receipt["status"]) != 1
                or _hex(receipt["blockHash"]).lower() != recorded["blockHash"].lower()
                or int(receipt["blockNumber"]) != recorded["blockNumber"]
                or not self.canonical(recorded["blockNumber"], recorded["blockHash"])
                or transaction["from"].lower() != roles["deployerOwner"].lower()
                or (transaction.get("to") or "").lower() != contract.address.lower()
                or _hex(transaction["input"]).lower() != function._encode_transaction_data().lower()
            ):
                raise ChainMismatch(f"{name} bootstrap transaction is not canonical or fixed")
        if registry.functions.owner().call().lower() != roles["deployerOwner"].lower():
            raise ChainMismatch("Registry owner mismatch")
        if registry.functions.escrow().call().lower() != escrow.address.lower():
            raise ChainMismatch("Registry/Escrow binding mismatch")
        if escrow.functions.registry().call().lower() != registry.address.lower():
            raise ChainMismatch("Escrow/Registry binding mismatch")
        if token.functions.decimals().call() != 6:
            raise ChainMismatch("MockHKD decimals mismatch")
        if token.functions.name().call() != "Mock Hong Kong Dollar" or token.functions.symbol().call() != "mHKD":
            raise ChainMismatch("MockHKD token metadata mismatch")
        if not registry.functions.aiSigners(self.roles["aiSigner"]).call():
            raise ChainMismatch("Manifest AI signer is not allowlisted")

    def contract_address(self, name: str) -> str:
        return self.contracts[name].address

    @_rpc_read
    def latest_timestamp(self) -> int:
        self.verify()
        return int(self.w3.eth.get_block("latest")["timestamp"])

    @_rpc_read
    def call(self, contract: str, function: str, *args: Any, block_identifier: Any = "latest") -> Any:
        self.verify()
        try:
            factory = self.contracts[contract].get_function_by_name(function)
            encoded_args = _abi_addresses(factory.abi["inputs"], list(args))
            return factory(*encoded_args).call(block_identifier=block_identifier)
        except ChainUnavailable:
            raise
        except Exception as exc:
            raise ChainUnavailable("Contract getter is unavailable or invalid") from exc

    @_rpc_read
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
            "assessment.ai_final": ("PoGRegistryV2", "submitAIAssessment"),
            "approval.release": ("ProcurementEscrowV2", "submitReleaseApproval"),
            "release.execute": ("ProcurementEscrowV2", "executeRelease"),
            "procurement.settlement": ("PoGRegistryV2", "recordSettlement"),
            "approval.settlement": ("ProcurementEscrowV2", "submitSettlementApproval"),
            "settlement.execute": ("ProcurementEscrowV2", "executeSettlementConfirmation"),
            "payment.mint": ("MockHKD", "mint"),
            "payment.redeem": ("MockHKD", "transfer"),
        }
        if action not in mapping:
            raise ValueError("Unsupported A2 chain action")
        if action in {"assessment.ai_final", "approval.release", "approval.settlement", "payment.mint"}:
            if caller.lower() != self.roles["relayer"].lower():
                raise ValueError("This action requires the fixed simulation relayer")
        if action in {"procurement.settlement", "payment.redeem"} and caller.lower() != self.roles["foundation"].lower():
            raise ValueError("This action requires the fixed owning Foundation")
        if action in {"release.execute", "settlement.execute"} and caller.lower() not in {
            self.roles["foundation"].lower(), self.roles["humanApprover"].lower()
        }:
            raise ValueError("Execution requires Foundation or independent human")
        if action == "payment.mint" and (len(args) != 2 or str(args[0]).lower() not in {
            self.roles["donorA"].lower(), self.roles["donorB"].lower()
        } or int(args[1]) <= 0):
            raise ValueError("Funding mint must target a fixed Donor with positive amount")
        if action == "payment.redeem" and (len(args) != 2 or str(args[0]).lower() != self.roles["mockRedemption"].lower() or int(args[1]) <= 0):
            raise ValueError("Redemption must target the fixed simulation treasury")
        contract_name, function_name = mapping[action]
        contract = self.contracts[contract_name]
        encoded_args = list(args)
        if action in {"payment.mint", "payment.redeem", "donation.approve", "donation.deposit"}:
            encoded_args[1] = int(encoded_args[1])
        factory = contract.get_function_by_name(function_name)
        function = factory(*_abi_addresses(factory.abi["inputs"], encoded_args))
        nonce = self.w3.eth.get_transaction_count(Web3.to_checksum_address(caller), "pending")
        return PreparedEnvelope(
            caller=Web3.to_checksum_address(caller), to=contract.address,
            chain_id=self.w3.eth.chain_id, nonce=nonce,
            data=function._encode_transaction_data(), value=0,
            action=action, expected_event=expected_event,
        )

    def send(self, envelope: PreparedEnvelope) -> str:
        transaction = {
            "from": envelope.caller, "to": envelope.to, "chainId": envelope.chain_id,
            "nonce": envelope.nonce, "data": envelope.data, "value": envelope.value,
        }
        try:
            self.verify()
            # An estimate rejection has no transaction side effect. Supplying the
            # estimate also prevents middleware doing an implicit preflight after
            # the worker has entered its ambiguous-send boundary.
            transaction["gas"] = int(self.w3.eth.estimate_gas(transaction))
            if transaction["gas"] <= 0:
                raise ChainMismatch("Invalid local gas estimate")
        except Exception as exc:
            raise ChainNotBroadcast(str(exc)) from exc
        # Every exception from this call is potentially accepted-with-response-
        # lost. It must retain the exact prepared nonce/envelope for reconciliation.
        tx_hash = self.w3.eth.send_transaction(transaction)
        return _hex(tx_hash)

    @_rpc_read
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

    @_rpc_read
    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        if receipt is None:
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
        from web3.exceptions import BlockNotFound

        try:
            block = self.w3.eth.get_block(block_number)
        except BlockNotFound:
            return False
        except Exception as exc:
            raise ChainUnavailable("Canonical block lookup is temporarily unavailable") from exc
        try:
            actual_hash = _hex(block["hash"])
            if (len(actual_hash) != 66 or not actual_hash.startswith("0x")
                    or len(bytes.fromhex(actual_hash[2:])) != 32
                    or int(block["number"]) != block_number):
                raise ValueError("Invalid canonical block identity")
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ChainUnavailable("Canonical block lookup returned an invalid identity") from exc
        return actual_hash.lower() == block_hash.lower()

    @_rpc_read
    def block_identity(self, block_number: int) -> tuple[str, str]:
        block = self.w3.eth.get_block(block_number)
        try:
            block_hash, parent_hash = _hex(block["hash"]), _hex(block["parentHash"])
            if int(block["number"]) != block_number or any(
                len(value) != 66 or not value.startswith("0x")
                or len(bytes.fromhex(value[2:])) != 32
                for value in (block_hash, parent_hash)
            ):
                raise ValueError("Invalid block identity")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ChainUnavailable("Local RPC block identity is invalid") from exc
        return block_hash, parent_hash

    def deployment_start_block(self) -> int:
        return min(
            int(value["deployment"]["blockNumber"])
            for value in self.manifest["contracts"].values()
        )

    @_rpc_read
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

    @_rpc_read
    def receipt_with_events(self, tx_hash: str, event_name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        try:
            raw = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None, []
        if raw is None:
            return None, []
        serial = {
            "transactionHash": _hex(raw["transactionHash"]),
            "transactionIndex": int(raw["transactionIndex"]),
            "blockHash": _hex(raw["blockHash"]),
            "blockNumber": int(raw["blockNumber"]),
            "from": raw["from"], "to": raw["to"], "status": int(raw["status"]),
        }
        return serial, self.decode_expected_event(raw, event_name)

    @_rpc_read
    def sign_typed_data(self, signer: str, data: dict[str, Any]) -> str:
        self.verify()
        response = self.w3.provider.make_request(
            "eth_signTypedData_v4", [Web3.to_checksum_address(signer), json.dumps(data)]
        )
        if response.get("error"):
            raise ChainUnavailable(str(response["error"]))
        return str(response["result"])
