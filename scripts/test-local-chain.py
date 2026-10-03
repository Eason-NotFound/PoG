#!/usr/bin/env python3
"""Independent local-chain checks; --live adds isolated real Anvil tests.

The default suite never opens a listener or deploys contracts. Live tests use
only a private temporary directory and an unused 127.0.0.1 port; they never
touch the booth's default deployment, accepted contracts, or ABI files.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "local-chain.py"


def invoke(command: str, directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--state-dir", str(directory), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def decoded(result: subprocess.CompletedProcess[str]) -> dict:
    if result.returncode:
        raise AssertionError(f"CLI failed ({result.returncode}): {result.stderr[-3000:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"CLI did not return JSON: {result.stdout[-3000:]}") from error


def binary(name: str) -> str:
    installed = shutil.which(name)
    local = Path.home() / ".foundry" / "bin" / name
    if installed:
        return installed
    if local.is_file():
        return str(local)
    raise RuntimeError(f"Install pinned Foundry before --live: missing {name}")


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def rpc(port: int, method: str, params: list | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode(),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        result = json.load(response)
    if "error" in result:
        raise AssertionError(f"RPC {method} failed: {result['error']}")
    return result["result"]


def selector(signature: str) -> str:
    return subprocess.check_output([binary("cast"), "sig", signature], text=True, timeout=15).strip()


def keccak_bytes(raw: bytes) -> bytes:
    result = subprocess.check_output([binary("cast"), "keccak", "0x" + raw.hex()], text=True, timeout=15).strip()
    return bytes.fromhex(result.removeprefix("0x"))


def call(port: int, address: str, signature: str, *words: str) -> str:
    data = selector(signature) + "".join(word.removeprefix("0x").rjust(64, "0") for word in words)
    return rpc(port, "eth_call", [{"to": address, "data": data}, "latest"])


class LocalValidationTests(unittest.TestCase):
    """Black-box input and no-state checks, without a live chain."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pog-local-unit-", dir="/tmp")
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rejected(self, command: str, *arguments: str, directory: Path | None = None) -> None:
        result = invoke(command, directory or self.directory, *arguments)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stderr.strip(), "Rejected command must explain the error")

    def test_port_zero_rejected(self) -> None:
        self.rejected("up", "--port", "0")

    def test_negative_port_rejected(self) -> None:
        self.rejected("up", "--port", "-1")

    def test_out_of_range_port_rejected(self) -> None:
        self.rejected("up", "--port", "65536")

    def test_non_integer_port_rejected(self) -> None:
        self.rejected("up", "--port", "invalid")

    def test_public_host_option_rejected(self) -> None:
        self.rejected("up", "--host", "0.0.0.0")

    def test_public_rpc_url_option_rejected(self) -> None:
        self.rejected("up", "--rpc-url", "https://example.invalid")

    def test_root_state_directory_rejected(self) -> None:
        self.rejected("status", directory=Path("/"))

    def test_workspace_state_directory_rejected(self) -> None:
        self.rejected("status", directory=ROOT)

    def test_temporary_root_state_directory_rejected(self) -> None:
        self.rejected("status", directory=Path(tempfile.gettempdir()))

    def test_reset_requires_explicit_confirmation_without_state(self) -> None:
        marker = self.directory / "preserve.txt"
        marker.write_text("preserve unrelated local test data\n")
        self.rejected("reset")
        self.assertEqual(marker.read_text(), "preserve unrelated local test data\n")

    def test_status_without_state_is_stopped_without_network(self) -> None:
        specification = importlib.util.spec_from_file_location("pog_local_chain_unit", SCRIPT)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        # Unit checks must remain usable while the booth chain is running.
        # Foreign/live port refusal is independently exercised by --live.
        with mock.patch.object(module, "port_open", return_value=False), \
             mock.patch.object(module, "port_locked", side_effect=lambda port: contextlib.nullcontext()):
            result = module.main(["status", "--state-dir", str(self.directory)])
        self.assertEqual(result["status"], "stopped")
        self.assertFalse((self.directory / "state.json").exists())
        self.assertFalse((self.directory / "manifest.json").exists())

    def test_verify_without_state_fails(self) -> None:
        self.rejected("verify")

    def test_unknown_command_rejected(self) -> None:
        self.rejected("deploy-to-mainnet")


class LiveLifecycleTests(unittest.TestCase):
    """Reuse one isolated deployment; restore each tampered value immediately."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="pog-local-live-", dir="/tmp")
        cls.directory = Path(cls.temporary.name)
        cls.port = unused_port()
        cls.manifest_path = cls.directory / "manifest.json"
        try:
            result = invoke("up", cls.directory, "--port", str(cls.port))
            decoded(result)
            cls.initial_stdout = result.stdout
            cls.refresh_manifest()
        except BaseException:
            invoke("stop", cls.directory, "--port", str(cls.port))
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            result = invoke("stop", cls.directory, "--port", str(cls.port))
            if result.returncode:
                raise AssertionError(f"Owned Anvil cleanup failed: {result.stderr}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", cls.port)) == 0:
                    raise AssertionError("Owned Anvil still listens after stop")
        finally:
            cls.temporary.cleanup()

    @classmethod
    def refresh_manifest(cls) -> None:
        cls.manifest = json.loads(cls.manifest_path.read_text())

    def command(self, action: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return invoke(action, self.directory, "--port", str(self.port), *arguments)

    def role(self, name: str) -> str:
        value = self.manifest["roles"][name]
        return value if isinstance(value, str) else value["address"]

    def contract(self, name: str) -> str:
        return self.manifest["contracts"][name]["address"]

    def chain_fingerprint(self) -> tuple:
        token = self.contract("MockHKD")
        return (
            rpc(self.port, "eth_blockNumber"),
            rpc(self.port, "eth_getTransactionCount", [self.role("deployerOwner"), "latest"]),
            call(self.port, token, "totalSupply()"),
            call(self.port, token, "balanceOf(address)", self.role("donorA")),
            call(self.port, token, "balanceOf(address)", self.role("donorB")),
        )

    def verify_rejects_manifest_change(self, section: dict, key: str, bad_value) -> None:
        original = self.manifest_path.read_bytes()
        section[key] = bad_value
        self.manifest_path.write_text(json.dumps(self.manifest))
        try:
            result = self.command("verify")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(result.stderr.strip())
        finally:
            self.manifest_path.write_bytes(original)
            self.refresh_manifest()
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")

    def test_010_chain_is_local_31337(self) -> None:
        self.assertEqual(int(rpc(self.port, "eth_chainId"), 16), 31337)
        self.assertEqual(self.manifest["chain"]["chainId"], 31337)
        self.assertEqual(self.manifest["chain"]["rpcUrl"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(self.manifest["schemaVersion"], 1)
        self.assertTrue(self.manifest["runId"])
        state = json.loads((self.directory / "state.json").read_text())
        command = subprocess.check_output(["ps", "-p", str(state["pid"]), "-o", "command="], text=True, timeout=5)
        arguments = shlex.split(command.strip())
        self.assertIn("--no-cors", arguments)
        self.assertEqual(arguments[arguments.index("--host") + 1], "127.0.0.1")
        self.assertEqual(arguments[arguments.index("--chain-id") + 1], "31337")
        self.assertEqual(arguments[arguments.index("--accounts") + 1], "10")
        self.assertFalse(any(argument.startswith("--fork") for argument in arguments))
        self.assertNotIn("--disable-code-size-limit", arguments)
        self.assertNotIn("--auto-impersonate", arguments)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}",
            data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
            headers={"Content-Type": "application/json", "Origin": "https://example.invalid"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        output = "\n".join([self.initial_stdout, self.manifest_path.read_text(),
                             (self.directory / "state.json").read_text(),
                             (self.directory / "anvil.log").read_text()])
        for forbidden in ["Private Keys", "Mnemonic", "test test test test test test test test test test test junk"]:
            self.assertNotIn(forbidden, output)
        for document in [self.manifest, state, decoded(self.command("status"))]:
            serialized = json.dumps(document).lower()
            for field in ['"privatekey"', '"private_key"', '"mnemonic"', '"seedphrase"', '"seed_phrase"']:
                self.assertNotIn(field, serialized)

    def test_020_distinct_roles_match_ten_accounts(self) -> None:
        names = ["deployerOwner", "foundation", "recipient", "aiSigner", "humanApprover",
                 "donorA", "donorB", "vendor", "relayer", "mockRedemption"]
        roles = [self.role(name).lower() for name in names]
        accounts = [account.lower() for account in rpc(self.port, "eth_accounts")]
        self.assertEqual(len(set(roles)), 10)
        self.assertEqual(roles, accounts[:10])

    def test_030_binding_owner_ai_and_token_precision(self) -> None:
        registry = self.contract("PoGRegistryV2")
        escrow = self.contract("ProcurementEscrowV2")
        self.assertEqual(call(self.port, registry, "owner()")[-40:].lower(), self.role("deployerOwner")[2:].lower())
        self.assertEqual(call(self.port, registry, "escrow()")[-40:].lower(), escrow[2:].lower())
        self.assertEqual(call(self.port, escrow, "registry()")[-40:].lower(), registry[2:].lower())
        self.assertEqual(int(call(self.port, registry, "aiSigners(address)", self.role("aiSigner")), 16), 1)
        self.assertEqual(int(call(self.port, self.contract("MockHKD"), "decimals()"), 16), 6)

    def test_040_only_donors_minted_and_no_bootstrap_project(self) -> None:
        token = self.contract("MockHKD")
        for role in self.manifest["roles"]:
            with self.subTest(role=role):
                expected = 1_000_000_000 if role in {"donorA", "donorB"} else 0
                self.assertEqual(int(call(self.port, token, "balanceOf(address)", self.role(role)), 16), expected)
        self.assertEqual(int(call(self.port, token, "totalSupply()"), 16), 2_000_000_000)
        self.assertEqual(int(call(self.port, self.contract("PoGRegistryV2"), "projectCount()"), 16), 0)
        self.assertEqual(self.manifest["bootstrap"]["tokenDecimals"], 6)
        self.assertIsInstance(self.manifest["bootstrap"]["donorMintAtomic"], str)
        self.assertEqual(int(self.manifest["bootstrap"]["donorMintAtomic"]), 1_000_000_000)
        report = decoded(self.command("status"))
        for field in ["tokenBalancesAtomic", "nativeBalancesWei"]:
            for role, amount in report[field].items():
                with self.subTest(field=field, role=role):
                    self.assertIsInstance(amount, str)
                    self.assertTrue(amount.isdecimal())

    def test_050_receipts_and_runtime_hashes_match_real_node(self) -> None:
        for name, entry in self.manifest["contracts"].items():
            with self.subTest(contract=name):
                receipt = rpc(self.port, "eth_getTransactionReceipt", [entry["deployment"]["transactionHash"]])
                self.assertEqual(int(receipt["status"], 16), 1)
                self.assertEqual(receipt["contractAddress"].lower(), entry["address"].lower())
                self.assertEqual(receipt["blockHash"].lower(), entry["deployment"]["blockHash"].lower())
                runtime = rpc(self.port, "eth_getCode", [entry["address"], "latest"])
                raw = bytes.fromhex(runtime[2:])
                self.assertGreater(len(raw), 0)
                self.assertLessEqual(len(raw), 24_576)
                self.assertEqual(len(raw), entry["runtime"]["sizeBytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["runtime"]["sha256"])
                keccak = subprocess.check_output([binary("cast"), "keccak", runtime], text=True, timeout=15).strip()
                self.assertEqual(keccak.lower(), entry["runtime"]["keccak256"].lower())

    def test_060_abi_fingerprints_and_immutable_metadata(self) -> None:
        for name, entry in self.manifest["contracts"].items():
            with self.subTest(contract=name):
                abi_path = ROOT / entry["abi"]["path"]
                self.assertEqual(hashlib.sha256(abi_path.read_bytes()).hexdigest(), entry["abi"]["sha256"])
                self.assertTrue(entry["abi"]["artifactId"])
                self.assertTrue(entry["artifact"]["creationBytecodeKeccak256"].startswith("0x"))
                self.assertTrue(entry["artifact"]["compilerVersion"].startswith("0.8.24"))
        self.assertTrue(self.manifest["contracts"]["ProcurementEscrowV2"]["runtime"]["immutableReferences"])
        self.assertEqual(self.manifest["version"]["acceptedTag"], "blockchain-v0.2.0-m2")

    def test_065_compiled_runtime_template_and_exact_immutable_values(self) -> None:
        type_hash = keccak_bytes(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
        for name in ["MockHKD", "PoGRegistryV2", "ProcurementEscrowV2"]:
            with self.subTest(contract=name):
                artifact_path = ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
                artifact = json.loads(artifact_path.read_text())
                compiled = artifact["deployedBytecode"]
                template = bytearray.fromhex(compiled["object"].removeprefix("0x"))
                address = self.contract(name)
                deployed = bytearray.fromhex(rpc(self.port, "eth_getCode", [address, "latest"])[2:])
                self.assertEqual(len(template), len(deployed))
                immutable_values = set()
                for references in compiled.get("immutableReferences", {}).values():
                    values = set()
                    for reference in references:
                        start = reference["start"]
                        length = reference["length"]
                        self.assertEqual(length, 32)
                        values.add(bytes(deployed[start:start + length]))
                        template[start:start + length] = b"\0" * length
                        deployed[start:start + length] = b"\0" * length
                    self.assertEqual(len(values), 1, "Every use of one immutable must match")
                    immutable_values.update(values)
                self.assertEqual(template, deployed, "Non-immutable runtime must exactly match accepted build")
                if name == "MockHKD":
                    self.assertFalse(immutable_values)
                    continue
                name_bytes = name.encode()
                name_hash = keccak_bytes(name_bytes)
                version_hash = keccak_bytes(b"2")
                chain_word = (31337).to_bytes(32, "big")
                address_word = bytes.fromhex(address[2:]).rjust(32, b"\0")
                domain_hash = keccak_bytes(type_hash + name_hash + version_hash + chain_word + address_word)
                expected = {name_hash, version_hash, chain_word, address_word, domain_hash,
                            name_bytes.ljust(31, b"\0") + bytes([len(name_bytes)]),
                            b"2".ljust(31, b"\0") + b"\x01"}
                if name == "ProcurementEscrowV2":
                    expected.add(bytes.fromhex(self.contract("PoGRegistryV2")[2:]).rjust(32, b"\0"))
                self.assertEqual(immutable_values, expected, "EIP-712 V2/chain/address and Registry immutables must match")

    def test_070_repeated_up_sends_no_transactions_or_mints(self) -> None:
        before = self.chain_fingerprint()
        run_id = self.manifest["runId"]
        self.assertEqual(decoded(self.command("up"))["status"], "ready")
        self.assertEqual(before, self.chain_fingerprint())
        self.refresh_manifest()
        self.assertEqual(run_id, self.manifest["runId"])

    def test_080_status_and_verify_are_read_only(self) -> None:
        before = self.chain_fingerprint()
        self.assertEqual(decoded(self.command("status"))["status"], "ready")
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")
        self.assertEqual(before, self.chain_fingerprint())

    def test_170_concurrent_different_directories_one_port(self) -> None:
        port = unused_port()
        with tempfile.TemporaryDirectory(prefix="pog-local-race-a-", dir="/tmp") as first, \
             tempfile.TemporaryDirectory(prefix="pog-local-race-b-", dir="/tmp") as second:
            directories = [Path(first), Path(second)]
            processes = [subprocess.Popen(
                [sys.executable, str(SCRIPT), "up", "--state-dir", str(directory), "--port", str(port)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) for directory in directories]
            winner = None
            try:
                results = [process.communicate(timeout=60) for process in processes]
                successful = [index for index, process in enumerate(processes) if process.returncode == 0]
                self.assertEqual(len(successful), 1, f"Expected one owned winner: {[(p.returncode, r[1]) for p, r in zip(processes, results)]}")
                winner = directories[successful[0]]
                manifest = json.loads((winner / "manifest.json").read_text())
                token = manifest["contracts"]["MockHKD"]["address"]
                owner = manifest["roles"]["deployerOwner"]
                self.assertEqual(int(rpc(port, "eth_getTransactionCount", [owner, "latest"]), 16), 7)
                self.assertEqual(int(call(port, token, "totalSupply()"), 16), 2_000_000_000)
                self.assertEqual(decoded(invoke("verify", winner, "--port", str(port)))["status"], "ready")
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
                # Stop only a run whose own metadata identifies its actual
                # live Anvil; the rejected directory is never used to stop it.
                for directory in directories:
                    state_path = directory / "state.json"
                    if not state_path.is_file():
                        continue
                    state = json.loads(state_path.read_text())
                    try:
                        matches = rpc(port, "anvil_metadata")["instanceId"] == state.get("instanceId")
                    except (OSError, urllib.error.URLError):
                        matches = False
                    if matches:
                        result = invoke("stop", directory, "--port", str(port))
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_180_forged_state_never_signals_non_anvil_process(self) -> None:
        process = subprocess.Popen(["sleep", "45"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = unused_port()
        try:
            with tempfile.TemporaryDirectory(prefix="pog-local-fake-pid-", dir="/tmp") as temporary:
                directory = Path(temporary)
                fingerprint = subprocess.check_output(
                    ["ps", "-p", str(process.pid), "-o", "lstart=", "-o", "command="], text=True, timeout=5,
                ).strip()
                state = {"schemaVersion": 1, "runId": "1" * 32, "port": port,
                         "rpcUrl": f"http://127.0.0.1:{port}", "chainId": 31337, "phase": "ready",
                         "pid": process.pid, "processFingerprint": fingerprint,
                         "instanceId": "0x" + "11" * 32, "genesisHash": "0x" + "22" * 32}
                state_path = directory / "state.json"
                state_path.write_text(json.dumps(state))
                before = state_path.read_bytes()
                for action, arguments in [("stop", []), ("reset", ["--confirm-reset"])]:
                    with self.subTest(action=action):
                        result = invoke(action, directory, "--port", str(port), *arguments)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIsNone(process.poll(), "Forged state must never signal a non-Anvil PID")
                        self.assertEqual(state_path.read_bytes(), before)
                self.assertFalse((directory / "manifest.json").exists())
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

    def test_090_unconfirmed_reset_preserves_node_and_files(self) -> None:
        before = self.chain_fingerprint()
        manifest_bytes = self.manifest_path.read_bytes()
        result = self.command("reset")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.chain_fingerprint())
        self.assertEqual(manifest_bytes, self.manifest_path.read_bytes())

    def test_100_bad_abi_fingerprint_fails_closed(self) -> None:
        self.verify_rejects_manifest_change(self.manifest["contracts"]["MockHKD"]["abi"], "sha256", "0" * 64)

    def test_110_stale_instance_fails_closed_and_status_explains(self) -> None:
        original = self.manifest_path.read_bytes()
        self.manifest["chain"]["instanceId"] = "0x" + "00" * 32
        self.manifest_path.write_text(json.dumps(self.manifest))
        try:
            self.assertNotEqual(self.command("verify").returncode, 0)
            self.assertNotEqual(decoded(self.command("status"))["status"], "ready")
            self.assertNotEqual(self.command("up").returncode, 0)
        finally:
            self.manifest_path.write_bytes(original)
            self.refresh_manifest()

    def test_120_tampered_actual_runtime_fails_closed(self) -> None:
        address = self.contract("MockHKD")
        original = rpc(self.port, "eth_getCode", [address, "latest"])
        manifest_bytes = self.manifest_path.read_bytes()
        rpc(self.port, "anvil_setCode", [address, "0x60006000fd"])
        try:
            self.assertNotEqual(self.command("verify").returncode, 0)
            # A corrupted manifest must not authorize unrelated deployed code,
            # even if its recorded hash is rewritten to match that code.
            changed = bytearray.fromhex(original[2:])
            changed[-1] ^= 1
            runtime = "0x" + changed.hex()
            rpc(self.port, "anvil_setCode", [address, runtime])
            entry = self.manifest["contracts"]["MockHKD"]["runtime"]
            entry["sha256"] = hashlib.sha256(changed).hexdigest()
            entry["keccak256"] = "0x" + keccak_bytes(changed).hex()
            entry["sizeBytes"] = len(changed)
            self.manifest_path.write_text(json.dumps(self.manifest))
            self.assertNotEqual(self.command("verify").returncode, 0, "Self-reported hash must not bypass accepted runtime template")
        finally:
            rpc(self.port, "anvil_setCode", [address, original])
            self.manifest_path.write_bytes(manifest_bytes)
            self.refresh_manifest()
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")
        # Flipping only an immutable leaves the masked template unchanged.
        # Its manifest hashes must not bypass full constructor reconstruction.
        registry = self.contract("PoGRegistryV2")
        original = rpc(self.port, "eth_getCode", [registry, "latest"])
        artifact = json.loads((ROOT / "contracts/out/PoGRegistryV2.sol/PoGRegistryV2.json").read_text())
        reference = next(iter(artifact["deployedBytecode"]["immutableReferences"].values()))[0]
        changed = bytearray.fromhex(original[2:])
        changed[reference["start"]] ^= 1
        runtime = "0x" + changed.hex()
        rpc(self.port, "anvil_setCode", [registry, runtime])
        entry = self.manifest["contracts"]["PoGRegistryV2"]["runtime"]
        entry["sha256"] = hashlib.sha256(changed).hexdigest()
        entry["keccak256"] = "0x" + keccak_bytes(changed).hex()
        self.manifest_path.write_text(json.dumps(self.manifest))
        try:
            self.assertNotEqual(self.command("verify").returncode, 0, "Masked template alone must not approve corrupted immutables")
        finally:
            rpc(self.port, "anvil_setCode", [registry, original])
            self.manifest_path.write_bytes(manifest_bytes)
            self.refresh_manifest()
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")

    def test_130_foreign_http_listener_is_not_disrupted(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                response = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": "foreign-listener"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="pog-local-foreign-http-", dir="/tmp") as directory:
                port = server.server_address[1]
                result = invoke("up", Path(directory), "--port", str(port))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(rpc(port, "web3_clientVersion"), "foreign-listener")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_140_foreign_anvil_is_not_mutated_or_stopped(self) -> None:
        for chain_id in [1, 31337]:
            with self.subTest(chain_id=chain_id):
                port = unused_port()
                process = subprocess.Popen(
                    [binary("anvil"), "--host", "127.0.0.1", "--port", str(port), "--chain-id", str(chain_id), "--silent"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                try:
                    for _ in range(50):
                        try:
                            rpc(port, "eth_chainId")
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.1)
                    else:
                        self.fail("Isolated foreign Anvil failed to start")
                    before = rpc(port, "eth_blockNumber")
                    with tempfile.TemporaryDirectory(prefix="pog-local-foreign-anvil-", dir="/tmp") as directory:
                        self.assertNotEqual(invoke("up", Path(directory), "--port", str(port)).returncode, 0)
                    self.assertIsNone(process.poll())
                    self.assertEqual(rpc(port, "eth_blockNumber"), before)
                    self.assertEqual(int(rpc(port, "eth_chainId"), 16), chain_id)
                finally:
                    process.terminate()
                    process.wait(timeout=10)

    def test_150_confirmed_reset_archives_and_recreates(self) -> None:
        old_run = self.manifest["runId"]
        old_instance = self.manifest["chain"]["instanceId"]
        old_addresses = {name: entry["address"] for name, entry in self.manifest["contracts"].items()}
        result = decoded(self.command("reset", "--confirm-reset"))
        self.assertEqual(result["status"], "ready")
        self.refresh_manifest()
        self.assertNotEqual(self.manifest["runId"], old_run)
        self.assertNotEqual(self.manifest["chain"]["instanceId"], old_instance)
        self.assertEqual({name: entry["address"] for name, entry in self.manifest["contracts"].items()}, old_addresses)
        self.assertTrue((self.directory / "history" / old_run / "manifest.json").is_file())
        self.assertEqual(int(call(self.port, self.contract("MockHKD"), "balanceOf(address)", self.role("donorA")), 16), 1_000_000_000)
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")

    def test_160_stopped_up_refuses_silent_data_loss(self) -> None:
        self.assertEqual(decoded(self.command("stop"))["status"], "stopped")
        self.assertEqual(decoded(self.command("status"))["status"], "stopped")
        self.assertNotEqual(self.command("up").returncode, 0)
        self.assertTrue(self.manifest_path.is_file())
        self.assertEqual(decoded(self.command("reset", "--confirm-reset"))["status"], "ready")
        self.refresh_manifest()
        self.assertEqual(decoded(self.command("verify"))["status"], "ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also test a real, isolated Anvil lifecycle")
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(LocalValidationTests)
    if args.live:
        suite.addTests(loader.loadTestsFromTestCase(LiveLifecycleTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
